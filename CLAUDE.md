# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Setup (venv lives outside the repo because the launchd wrapper expects it there):

```bash
python3 -m venv ~/.venvs/transcript-analyzer
source ~/.venvs/transcript-analyzer/bin/activate
pip install -r requirements.txt
```

Run the analyzer once:

```bash
source ~/.venvs/transcript-analyzer/bin/activate
python -m analyzer
```

Run synthesis after transcripts are analyzed:

```bash
python -m analyzer synthesize --mode daily    # D1 Daily Pulse — today's files
python -m analyzer synthesize --mode weekly   # D2 Slack Delta — this week's files
```

Launch the web dashboard (opens browser automatically at http://localhost:7070):

```bash
python -m analyzer ui              # default port 7070
python -m analyzer ui --port 7071  # custom port
```

Shell alias shortcut (add to `~/.zshrc`):

```bash
alias ta="source ~/.venvs/transcript-analyzer/bin/activate && python -m analyzer ui"
```

One-off model override (wins over per-prompt defaults):

```bash
MODEL_OVERRIDE=claude-opus-4-7 python -m analyzer
```

Interactive Drive OAuth bootstrap (run once before launchd can fetch `.gdoc` bodies):

```bash
python -m analyzer.drive_client
```

Scheduled-run wrapper (the script launchd invokes; reproduces a single cron tick):

```bash
./bin/analyze.sh
```

Force a launchd-scheduled run immediately:

```bash
launchctl start com.bradgross.transcript-analyzer
```

Cost dashboard (totals across `.processed.json`):

```bash
jq '[.[] | .cost_usd] | add' .processed.json
```

There is no test suite, linter config, or build step — runtime behavior is the contract.

## Configuration locations

- **`.env` lives at `~/.config/transcript-analyzer/.env`, NOT in the repo.** `analyzer/config.py` actively warns if `.env` is found at the repo root.
- **OAuth credentials** (`google-credentials.json`, `google-token.json`) also live in `~/.config/transcript-analyzer/`.
- **Do not put the working tree inside iCloud Drive.** macOS TCC blocks launchd-spawned processes from executing files in iCloud paths, so the LaunchAgent fails with `Operation not permitted`. The `~/code/` location is load-bearing for scheduled runs.

## Architecture

### Entry point dispatch (`__main__.py`)

`python -m analyzer` → `main.main()` (transcript + notes intake pipeline)
`python -m analyzer synthesize --mode daily|weekly` → `synthesize.main()` (D1/D2 synthesis)

### Two-pipeline flow (main.py)

`analyzer/main.py` orchestrates two independent pipelines in sequence per invocation:

1. **Notes intake** (`notes_intake.py`): Gemini-summarized meeting notes dropped into `Call Transcripts/notes/`. **No LLM call** — Gemini's summary is already lossy compression; re-summarizing would dilute nuggets. Pure file plumbing: parse metadata, file into `Analyzed/` with canonical naming, move source to `notes/_Processed/<YYYY-MM>/`.
2. **Transcript pass**: `.txt` and `.md` files at the root of `Call Transcripts/` (subfolders intentionally ignored). Each is auto-routed to a category prompt, analyzed (via the configured backend), and lands in `Analyzed/`. Output is always `.txt` regardless of source extension. With the shareable pass on, each transcript yields **two** outputs: the internal `[ANALYZED].txt` and a redacted `[SHAREABLE].txt` sibling.

A summary line `All done. N succeeded, M failed. Total cost: $X.` is printed at the end. `bin/analyze.sh` parses this exact line to decide whether to fire a macOS notification — keep the format stable.

### Synthesis pipeline (synthesize.py)

`synthesize.run()` is invoked manually (not by launchd) at end-of-day or end-of-week:

1. Loads D1 or D2 prompt from `Workcall/dailyAndWeeklyPrompts.md` (parsed by the same `### KEY.` + fenced-block convention as `PromptLibrary.md`; keys are `D1` and `D2`).
2. Collects `[ANALYZED]` files whose meeting date falls in the target window (today for daily; ISO week Mon–today for weekly). Skips `[SHAREABLE]`, `[DAILY PULSE]`, `[WEEKLY SUMMARY]`, and `[SLACK DELTA]` files.
3. Prepends the most recent prior synthesis of the same type as continuity context.
4. Bundles everything into a single `claude -p` call on Sonnet (inputs are already structured).
5. Writes output to `Analyzed/` with a `[DAILY PULSE]` or `[SLACK DELTA]` suffix (`.md`).
6. Archives the bundled `[ANALYZED]` input files to `Analyzed/_Archive/YYYY-MM/`. Archive is keyed on the meeting date in each filename. Refuses to overwrite existing archive targets. If archive fails after a successful write, logs a warning but does not fail the synthesis — nothing is lost.

Synthesis files are **not** archived — they stay in `Analyzed/` as context for future runs.

### Content lives in Drive, not the repo

Drive base: `~/Library/CloudStorage/GoogleDrive-brad.gross@salesforce.com/My Drive/Workcall/`

- **`PromptLibrary.md`** — prompt library parsed by `prompts.load_prompts()` looking for `### KEY.` headings followed by fenced code blocks. Active keys: `DAILY` / `STANDUP` / `SOLUTION` / `EXEC` (routed category prompts) and `REDACT` (shareable pass). C-series prompts are cross-transcript and run in Claude.ai chat, not here.
- **`dailyAndWeeklyPrompts.md`** — D1 (Daily Pulse) and D2 (Weekly Slack Delta) synthesis prompts. Parsed by `synthesize._load_synthesis_prompts()` using the same heading convention.
- **`Program_Context_Brief.md`** — program-wide context injected as the system prefix on every run.
- **`04_people_rolodex.md`** (**optional**) — named-individual index that complements the brief, incl. Plaud-mangled name variants. `prompts.load_rolodex()` reads best-effort (`""` if absent).
- **`05_plaud_vocabulary.md`** (**optional**) — canonical spellings of names/acronyms/product terms. Fed to the model to normalize mangled terms in non-Plaud (Gemini/Teams/Slack) transcripts.

Paths are configurable via `PROMPT_LIBRARY_PATH` / `CONTEXT_BRIEF_PATH` / `ROLODEX_PATH` / `VOCABULARY_PATH`. **Editing prompts is a Drive operation, not a code change.**

### Execution backends (`BACKEND`)

Analysis runs through one of two interchangeable backends, both producing the same `AnalysisResult`:

- **`claude-cli`** (`claude_cli.py`, the default): shells out to `claude -p` (headless Claude Code), billing to a **Claude Code seat** instead of a personal `ANTHROPIC_API_KEY`. The seat covers token cost, so `cost_usd` is effectively informational. Requires the `claude` binary (`CLAUDE_BIN`, absolute path under launchd). The transcript is passed on **stdin** (never argv — the system prefix can be tens of KB); tool use is disabled via `CLAUDE_EXTRA_ARGS`. Caching is handled internally by Claude Code.
- **`api`** (`anthropic_client.py`, legacy/fallback): the original direct Anthropic API path with explicit `cache_control`. Requires `ANTHROPIC_API_KEY`. Selecting `api` reproduces the pre-routing behavior exactly (single default prompt, no shareable pass) — the regression guard.

Both compose the **identical** system prefix via `anthropic_client.system_prompt_text()` so output is backend-independent.

### Routing → 4 category prompts (`claude-cli` only)

`router.classify()` runs a cheap Haiku-tier `claude -p` call to put each transcript into one of `DAILY` / `STANDUP` / `SOLUTION` / `EXEC`, then runs the matching prompt. Classification **never raises** — any error/ambiguity collapses to `FALLBACK` (`STANDUP`). If a routed category prompt is missing, `main._resolve_prompt()` falls back to `STANDUP` then `DEFAULT_PROMPT_KEY`.

Model tiering per category:
- `EXEC` → `claude-opus-4-7` (highest-stakes; political read, executive dynamics)
- `SOLUTION` → `claude-sonnet-4-6` (technical/design sessions)
- `STANDUP` → `claude-sonnet-4-6`
- `DAILY` → `claude-sonnet-4-6`
- Classifier → `claude-haiku-4-5-20251001` (bare alias rejected by the seat — use dated id)
- Redaction / synthesis → `claude-sonnet-4-6`

All ids confirmed on the work seat via `bin/phase0_check.sh`. A different seat may expose different ids — re-run the probe. `model_for(key)` honors `MODEL_OVERRIDE`.

### Shareable redaction pass (`SHAREABLE_PASS`)

After the internal `[ANALYZED]` file is written, `redactor.redact()` runs a second `claude -p` pass over the analysis text to strip internal politics / career-path notes, writing a `[SHAREABLE]` sibling. The instruction comes from a `### REDACT.` block in `PromptLibrary.md` (a built-in default is used if absent). **Best-effort by design**: if it fails, only the shareable file is dropped — the internal analysis is still recorded and the source moved. Every internal prompt ends with a `## Private read — internal only` section that REDACT targets — keep that heading verbatim across all prompts.

### Prompt caching (`api` backend)

The `api` backend packs the entire stable prefix — framing + Program Context Brief + frontmatter spec + prompt body — into a single system block with `cache_control: {type: ephemeral, ttl: "1h"}`. The **1-hour TTL is deliberate**: each analysis takes 3+ minutes, so the default 5-minute TTL expires mid-batch. (`claude-cli` backend lets Claude Code cache internally.)

If you change the prefix composition (add/remove a section, reorder), every existing cache entry invalidates — expect a one-time cost bump on the next batch.

### Dedup: manifest + fuzzy backstop

`.processed.json` (at the repo root) is the source of truth for what's been analyzed — keyed by source filename. `filesystem.fuzzy_is_analyzed()` is a secondary safety net: it requires **both** a date-token match AND the transcript's core title as a substring of an `Analyzed/` filename.

### Failure semantics

Both pipelines **fail closed**: on any error the source file stays in place and no manifest entry is written, so the next run picks it up again. `filesystem.move_to_processed()` refuses to overwrite an existing target. `filesystem.write_text()` writes to `.tmp` and renames atomically.

### `.gdoc` body fetching (notes intake)

A `.gdoc` in the local Drive cache is a JSON shortcut, not the body. `_process_gdoc` resolves the Drive document id through a three-step fallback chain:

1. Read the local `.gdoc` JSON for `doc_id` / `resource_id`.
2. Read the `com.google.drivefs.item-id#S` xattr via `/usr/bin/xattr -p`.
3. Search Drive by exact filename match.

This chain exists because **launchd-spawned processes hit EDEADLK** from Drive's File Provider for freshly-synced `.gdoc` bodies. Don't simplify without reproducing the failure on a clean launchd run.

The Gemini-export response starts with a UTF-8 BOM — `_fetch_gdoc_text` strips it so `parse_gemini_header` can read line 1 as `MMM DD, YYYY`.

### Frontmatter contract

`prompts.frontmatter_instruction()` defines the YAML frontmatter the LLM must emit: `meeting_date`, `participants`, `workstream`, `meeting_type`, plus optional `tags`/`decisions_count`/`risks_surfaced`/`key_stakeholders_absent`. The `workstream` and `meeting_type` enums are fixed vocabularies — changes ripple into downstream consumers outside this repo.

### Output filename convention

`filing.build_output_filename()` produces `[ISO-timestamp] - [Title] - [Date] [ANALYZED].txt`. `LEADING_PREFIX` strips both date+time prefix blocks that Plaud/Zap source filenames carry. Path separators in the title are sanitized. The same `LEADING_PREFIX` is reused by `filesystem._core_title()` for fuzzy matching — keep them coupled.

Synthesis outputs use `.md` extension and `[DAILY PULSE]` / `[SLACK DELTA]` tags instead of `[ANALYZED]`.
