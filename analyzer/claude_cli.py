"""Claude Code CLI backend — drives `claude -p` instead of the Anthropic API.

This routes analysis through a Claude Code **subscription seat** (e.g. a work
seat) rather than a personal `ANTHROPIC_API_KEY`. The seat covers token cost, so
nothing is billed to the personal API account.

`analyze()` mirrors `anthropic_client.analyze()` exactly (same keyword args, same
`AnalysisResult` return) so `main.py` swaps backends with no call-site change.
The cheap classifier (`router.py`) and the redaction pass (`redactor.py`) reuse
the hardened `run_claude_p()` wrapper so every CLI call shares one set of
fail-closed error semantics.

Reliability note: this fails CLOSED. Every failure mode (binary missing, not
authenticated, non-zero exit, malformed JSON, empty result, timeout) raises a
clear, typed error so the caller's `except` leaves the source file in place and
writes no manifest entry — mirroring the Drive/EDEADLK fallback style elsewhere.

CLI-version caveat: flag names and the `--output-format json` schema can vary by
Claude Code version. Tool-disabling flags are configurable via
`CONFIG.claude_extra_args` (env `CLAUDE_EXTRA_ARGS`) and JSON parsing is
defensive (`.get()` with fallbacks). Confirm both on the target machine — see the
Phase 0 checks in the project plan.
"""

import json
import os
import subprocess

from . import anthropic_client as ac
from .config import CONFIG
from .manifest import Usage


def _seat_env() -> dict:
    """Environment for `claude -p`, with ANTHROPIC_API_KEY stripped.

    The claude-cli backend bills the Claude Code seat/subscription, never a
    personal API key. But the analyzer loads ANTHROPIC_API_KEY from `.env` (the
    `api` fallback backend needs it), and a subprocess would otherwise inherit
    it and silently bill the key instead of the seat. Stripping it here makes
    the "zero personal-key usage" guarantee structural — matches the
    `env -u ANTHROPIC_API_KEY` the phase0 probe uses.
    """
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    return env

# A single analysis can take minutes (extended thinking + long output). Generous
# ceiling; a hung call should fail closed rather than wedge the whole batch.
_TIMEOUT_SECONDS = 1800

# Pre-flight guard against oversized input. The CLI rejects anything past the
# model's context window by exiting 1 with NO stderr — indistinguishable from an
# auth/transient failure in the log, and (because the pipeline fails closed) it
# then retries the same doomed file on every scheduled run. We catch the obvious
# case first so the log says *why*. ~3.5 chars/token, 200K window, ~75% headroom
# for the system prefix + output → ~525K char ceiling on the prompt body.
_MAX_PROMPT_CHARS = 525_000


class ClaudeCliError(RuntimeError):
    """A `claude -p` invocation failed. Raised so callers fail closed."""


def run_claude_p(
    prompt: str,
    *,
    model: str,
    system: str | None = None,
    timeout: int = _TIMEOUT_SECONDS,
) -> dict:
    """Run `claude -p` headless and return the parsed `--output-format json` dict.

    `prompt` is passed on **stdin** (never argv — the system prefix + transcript
    can be tens of KB and would blow ARG_MAX / break quoting). `system`, when
    given, is passed via `--append-system-prompt`.

    Raises ClaudeCliError / FileNotFoundError on any failure. Returns the raw
    JSON object on success; callers pull `result` etc. via `result_text()`.
    """
    if len(prompt) > _MAX_PROMPT_CHARS:
        # Fail closed with a legible reason rather than the opaque CLI exit-1.
        raise ClaudeCliError(
            f"prompt is {len(prompt):,} chars (~{len(prompt)//4:,} tokens), past "
            f"the {_MAX_PROMPT_CHARS:,}-char ceiling for model={model} — the CLI "
            f"would reject this with a bare exit 1. If this is a Gemini/Plaud "
            f"export, it likely contains inline base64 images; filesystem."
            f"read_text strips those, so a too-big prompt here means genuinely "
            f"long content. Split the source or raise _MAX_PROMPT_CHARS."
        )

    cmd = [CONFIG.claude_bin, "-p", "--model", model, "--output-format", "json"]
    cmd.extend(CONFIG.claude_extra_args)
    if system:
        cmd.extend(["--append-system-prompt", system])

    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=_seat_env(),
        )
    except FileNotFoundError as e:
        # `claude` not on PATH — the work seat CLI isn't installed/visible to
        # this process (note: launchd has a minimal PATH; set CLAUDE_BIN to an
        # absolute path there).
        raise FileNotFoundError(
            f"claude CLI not found ({CONFIG.claude_bin!r}) — is the Claude Code "
            f"seat installed and on PATH? Set CLAUDE_BIN to an absolute path."
        ) from e
    except subprocess.TimeoutExpired as e:
        raise ClaudeCliError(
            f"claude -p timed out after {timeout}s (model={model})"
        ) from e

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        last = stderr.splitlines()[-1] if stderr else "<no stderr>"
        # Auth failures, rate limits, and "please run /login" surface here.
        raise ClaudeCliError(
            f"claude -p exited {proc.returncode} (model={model}): {last}"
        )

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        preview = (proc.stdout or "")[:200].replace("\n", "\\n")
        raise ClaudeCliError(
            f"claude -p returned malformed JSON (model={model}): {preview!r}"
        ) from e

    if not isinstance(data, dict):
        raise ClaudeCliError(
            f"claude -p JSON is not an object (got {type(data).__name__})"
        )

    # Headless error envelope: `{"is_error": true, "subtype": "...", ...}`.
    if data.get("is_error"):
        subtype = data.get("subtype") or data.get("result") or "unknown error"
        raise ClaudeCliError(f"claude -p reported error (subtype={subtype})")

    return data


def result_text(data: dict) -> str:
    """Pull the assistant's text out of the `--output-format json` envelope.

    The text lives under `result` on current CLI versions; fall back across a
    couple of plausible keys so a minor schema rename doesn't break us.
    """
    for key in ("result", "text", "response"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def analyze(
    *,
    transcript_text: str,
    prompt_body: str,
    context_brief: str,
    frontmatter_instruction: str,
    model: str,
    rolodex: str = "",
    vocabulary: str = "",
) -> ac.AnalysisResult:
    """Drop-in for anthropic_client.analyze() that runs on the Claude Code seat.

    Composes the identical system prefix (framing + brief + rolodex + glossary +
    frontmatter + prompt body) via the shared helper, passes the transcript on
    stdin, and returns the same AnalysisResult shape. Token usage from the CLI is
    best-effort (the JSON may not expose per-call token counts); cost is
    informational only since the seat covers it.
    """
    system = ac.system_prompt_text(
        context_brief, prompt_body, frontmatter_instruction, rolodex, vocabulary
    )
    data = run_claude_p(
        f"Transcript:\n\n{transcript_text}", model=model, system=system
    )
    text = result_text(data)
    if not text.strip():
        # Never write an empty analysis file — fail closed so the source is
        # retried next run.
        raise ClaudeCliError(f"claude -p returned empty result (model={model})")

    usage = _usage_from_json(data)
    return ac.AnalysisResult(text=text, usage=usage)


def _usage_from_json(data: dict) -> Usage:
    """Best-effort token usage from the CLI JSON.

    Current CLI versions expose a nested `usage` block; older ones may not. We
    zero anything missing. Manifest cost on the seat is informational, so partial
    usage is acceptable.
    """
    usage = data.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    return Usage(
        input_tokens=int(usage.get("input_tokens", 0) or 0),
        output_tokens=int(usage.get("output_tokens", 0) or 0),
        cache_creation_input_tokens=int(
            usage.get("cache_creation_input_tokens", 0) or 0
        ),
        cache_read_input_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
    )
