"""Redaction pass — derive a shareable summary from an internal analysis.

The internal `[ANALYZED]` output is tuned to be attributed, specific, and
non-neutralized: it surfaces internal politics and the user's own career-path /
positioning notes. That's exactly what other team leads should NOT see. This pass
takes the finished internal analysis and produces a `[SHAREABLE]` version that
keeps the substance (decisions, risks, actions, status) but strips the
internal-only material — same quality, safe to circulate.

Runs as a second `claude -p` call over the analysis TEXT (not the transcript), so
it's cheap. Fails CLOSED (empty/garbled result raises) — callers decide whether a
redaction failure should drop just the shareable file or fail the whole record.

The instruction body can be supplied from `PromptLibrary.md` (key `REDACT`) so
the user tunes redaction without a code change; `_DEFAULT_INSTRUCTION` is used
when that key is absent, so the shareable pass works out of the box.
"""

from . import claude_cli
from .config import CONFIG

_DEFAULT_INSTRUCTION = """\
You are converting an INTERNAL meeting analysis into a SHAREABLE version that
other team leads can read. You will receive the full internal analysis. Produce a
cleaned version that:

KEEP (same depth and quality):
- Decisions, action items, owners, and due dates.
- Risks, blockers, dependencies, and open questions about the WORK.
- Status, progress, and substantive technical/program content.
- The YAML frontmatter block at the top, unchanged.

REMOVE / NEUTRALIZE:
- The entire "## Private read — internal only" section, wherever it appears.
- Internal politics framing, personality read-outs, and "who's positioning
  against whom" commentary.
- The reader's own career-path, positioning, or influence notes.
- Speculation about individuals' motives, competence, or standing.
- Anything that would be awkward or damaging if the named person read it.

Keep it factual, attributed to the work (not to internal maneuvering), and
professional. Do not add a preamble or note that content was removed — just emit
the cleaned analysis, starting with the frontmatter.
"""


class RedactionError(RuntimeError):
    """The redaction pass failed. Raised so callers fail closed."""


def redact(
    internal_text: str, *, model: str | None = None, instruction: str | None = None
) -> str:
    """Return a shareable version of `internal_text`. Raises on failure."""
    model = model or CONFIG.redaction_model
    system = instruction or _DEFAULT_INSTRUCTION
    data = claude_cli.run_claude_p(
        f"Internal analysis to convert into a shareable version:\n\n{internal_text}",
        model=model,
        system=system,
    )
    text = claude_cli.result_text(data)
    if not text.strip():
        raise RedactionError(f"redaction returned empty result (model={model})")
    return text
