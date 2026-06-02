# Work-machine setup: Claude Code seat backend + routing + shareable summaries

This is the runbook for moving analysis off the personal Anthropic API key and onto
the **work Claude Code seat**, with 4-way routing and a shareable redaction pass.
Steps marked **(work machine)** must run on the box that has the seat — the code is
already done; these are the things only you can do.

## 0. Verify the seat works headless **(work machine)** — DO THIS FIRST

```bash
./bin/phase0_check.sh
```

This confirms: `claude` is installed, `claude -p --output-format json` works **without**
a personal API key, the JSON exposes a `result`, and the tool-disabling flag is accepted.
It also prints a **manual launchd check** — run it. If the seat doesn't authenticate
under launchd, schedule the pipeline foreground instead of via the LaunchAgent.

If `phase0_check.sh` flags a different JSON shape or a rejected flag, that's the only
place the code needs a tweak: `analyzer/claude_cli.py:result_text()` (key name) or
`CLAUDE_EXTRA_ARGS` (flag). Note the absolute `claude` path it prints.

## 1. Configure `.env` **(work machine)**

Lives at `~/.config/transcript-analyzer/.env` (NOT in the repo). Minimum for the seat:

```bash
BACKEND=claude-cli
CLAUDE_BIN=/absolute/path/to/claude     # from step 0
# ANTHROPIC_API_KEY=                     # leave blank — not used by claude-cli
# SHAREABLE_PASS=true                    # default; set false to skip [SHAREABLE]
# CLAUDE_EXTRA_ARGS=--allowed-tools ""   # only if step 0 said the default is rejected

# Model IDs — step 0's "model-ID probe" tells you which resolve on this seat.
# Everything defaults to claude-sonnet-4-6 (safe). Optimize once you have IDs:
# CLASSIFIER_MODEL=<cheapest passing model, e.g. a Haiku id>
# MODEL_EXEC=<a passing Opus id>
# MODEL_SOLUTION=<a passing Opus id>
```

The seat authenticates `claude` independently of `ANTHROPIC_API_KEY` (you proved
this with `env -u APIKEY claude -p …`), and may route through Bedrock under the
hood — that's fine, the CLI backend never touches the key or Bedrock directly.
The only thing that varies is **model ID strings**; the probe pins them.

Point the `*_PATH` vars at wherever the transcripts live on this machine (work Google
Drive, a synced folder, etc.) if it isn't the default personal-Drive layout.

**Content files this machine needs in `Workcall/`** (none are in the repo — they
travel separately, and on a work machine without the personal Drive synced you must
place them yourself or point the `*_PATH` vars at them):

| File | `*_PATH` var | Required? | What it does |
| --- | --- | --- | --- |
| `PromptLibrary.md` | `PROMPT_LIBRARY_PATH` | yes | category + REDACT prompts |
| `Program_Context_Brief.md` | `CONTEXT_BRIEF_PATH` | yes | who's-who system prefix |
| `04_people_rolodex.md` | `ROLODEX_PATH` | optional | named-individual index incl. mangled name variants; appended after the brief. Absent = skipped with a stderr note. |
| `05_plaud_vocabulary.md` | `VOCABULARY_PATH` | optional | canonical spellings of names/acronyms/product terms; fed as a Term Glossary so the model normalizes mangled terms. Absent = skipped with a stderr note. |

`05_plaud_vocabulary.md` does double duty: it's **also** the list you paste into the Plaud
app's Custom Vocabulary setting on the recording device. But because most of these
transcripts come from Gemini/Teams/Slack (which never get Plaud's device-level
correction), the file now feeds the analyzer too — so place it in `Workcall/` (or point
`VOCABULARY_PATH` at it), not just into the Plaud app.

## 2. Add the consolidated prompts to Drive **(Drive edit, not code)**

In `Workcall/PromptLibrary.md`, add five `### KEY.` sections, each with a single fenced
block (same format as the existing A/B prompts). The analyzer picks these up automatically.

```
### DAILY.
` ``
<your daily-digest analysis prompt>
` ``

### STANDUP.
` ``
<your team stand-up analysis prompt — this is also the routing FALLBACK>
` ``

### SOLUTION.
` ``
<your solutioning / architecture / design-review analysis prompt>
` ``

### EXEC.
` ``
<your exec / steerco alignment prompt — highest stakes, most thorough>
` ``

### REDACT.
` ``
<optional: override the built-in redaction instruction. If omitted, the
 built-in default in analyzer/redactor.py is used.>
` ``
```

(Remove the spaces inside the ``` fences — they're escaped here only so this doc renders.)

Until these exist, routed transcripts fall back to `STANDUP`, then `DEFAULT_PROMPT_KEY`
(`A2`) — so nothing breaks, you just won't get category-specific output. `STANDUP` is the
fallback, so add it first.

## 3. Smoke-test before scheduling **(work machine)**

```bash
source ~/.venvs/transcript-analyzer/bin/activate
# Dry the seat path on one transcript (drop a small .txt in Call Transcripts/):
python -m analyzer
```

Confirm in `Analyzed/`: an `[ANALYZED].txt` AND a `[SHAREABLE].txt` sibling, the routed
category in the run log (`→ EXEC (…)`), and `cost_usd` ≈ $0 (seat-covered). Open the
`[SHAREABLE]` file and check the internal-politics / career-path content is gone while
decisions/risks survive.

## 4. Schedule **(work machine)**

Install the LaunchAgent from `examples/com.bradgross.transcript-analyzer.plist` (PATH now
includes the Claude install dir; or set `CLAUDE_BIN` in the plist). Only do this after the
step-0 launchd check passed.

---

## Rollback / comparison

`BACKEND=api` restores the exact prior behavior (personal key, single `A2` prompt, no
shareable pass) — useful to compare output quality side by side. Everything is one env var.

## Reliability notes (by design)

- Every new LLM call **fails closed**: binary missing, not authenticated, non-zero exit,
  malformed JSON, empty result → the transcript is left in place, no manifest entry, retried
  next run. (Verified against stub binaries.)
- Routing **never fails a transcript** — it falls back to `STANDUP`.
- The shareable pass is **best-effort**: if redaction fails, the internal analysis is still
  kept and recorded (we don't re-pay for it); only the `[SHAREABLE]` file is skipped.

## Future: sharing with other leads

The framing (`anthropic_client.py`) and `Program_Context_Brief.md` are user-specific. For
another lead to run this: their own `.env` paths, their own `PromptLibrary.md` +
`Program_Context_Brief.md` (+ optional `04_people_rolodex.md`) + `REDACT` prompt. Their `[SHAREABLE]` files are what they send
you. No code changes needed — all paths are `*_PATH`-configurable. Build/validate the
single-user seat path first.
