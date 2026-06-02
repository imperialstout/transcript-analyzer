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

- **`.env` lives at `~/.config/transcript-analyzer/.env`, NOT in the repo.** The repo is intended to be cloned anywhere under `~/code/`, but `analyzer/config.py` actively warns if `.env` is found at the repo root because the original deployment path was iCloud-synced and would have leaked the API key. Keep `.env` out of the repo even after moving off iCloud.
- **OAuth credentials** (`google-credentials.json`, `google-token.json`) also live in `~/.config/transcript-analyzer/`.
- **Do not put the working tree inside iCloud Drive.** macOS TCC blocks launchd-spawned processes from executing files in iCloud paths, so the LaunchAgent fails with `Operation not permitted`. The `~/code/` location is load-bearing for scheduled runs.

## Architecture

### Two-pipeline flow

`analyzer/main.py` orchestrates two independent pipelines in sequence per invocation:

1. **Notes intake** (`notes_intake.py`): Gemini-summarized meeting notes dropped into `Call Transcripts/notes/`. **No LLM call** — Gemini's summary is already lossy compression; re-summarizing would dilute nuggets. Pure file plumbing: parse metadata, file into `Analyzed/` with canonical naming, move source to `notes/_Processed/<YYYY-MM>/`.
2. **Transcript pass**: `.txt` and `.md` files at the root of `Call Transcripts/` (subfolders intentionally ignored). Each runs through the Anthropic API and lands in `Analyzed/`. Output is always `.txt` regardless of source extension.

A summary line `All done. N succeeded, M failed. Total cost: $X.` is printed at the end. `bin/analyze.sh` parses this exact line to decide whether to fire a macOS notification — keep the format stable.

### Content lives in Drive, not the repo

Two files the analyzer reads at runtime are intentionally outside the repo:

- `Workcall/PromptLibrary.md` — prompt library keyed by `A1`/`A2`/`B1` etc. Parsed by `prompts.load_prompts()` looking for `### KEY.` headings followed by fenced code blocks. Only A1–A3 and B1–B4 are picked up; C-series prompts are cross-transcript and run in Claude.ai chat, not here.
- `Workcall/Program_Context_Brief.md` — program-wide context cached as a system prompt on every run.

Both paths are configurable via `PROMPT_LIBRARY_PATH` / `CONTEXT_BRIEF_PATH`. **Editing prompts is a Drive operation, not a code change.**

### Prompt → model tiering

`config.py` maps each prompt key to a model (`MODEL_A1`…`MODEL_B4`). The default tiering keeps high-stakes prompts (A1, B2, B4) on Opus and routes the rest to Sonnet. The default prompt is `A2` (Sonnet) — currently every transcript runs A2; there is no router yet.

`model_for(key)` honors `MODEL_OVERRIDE` for one-off reruns. `supports_thinking(model)` gates adaptive-thinking and `EFFORT` (`low|medium|high|max`, max is Opus-only) — keep this in sync with which model IDs support extended thinking.

### Prompt caching

`anthropic_client.py` packs the entire stable prefix — framing + Program Context Brief + frontmatter spec + prompt body — into a single system block with `cache_control: {type: ephemeral, ttl: "1h"}`. The transcript itself is the only thing in the user message, so it varies per call while the prefix hits cache. The **1-hour TTL is deliberate**: each analysis takes 3+ minutes, so the default 5-minute TTL expires mid-batch and forces redundant cache writes. 1h write costs 2× input rate (paid once); reads are 0.1×.

If you change the prefix composition (add/remove a section, reorder), every existing cache entry invalidates — expect a one-time cost bump on the next batch.

### Dedup: manifest + fuzzy backstop

`.processed.json` (at the repo root) is the source of truth for what's been analyzed — keyed by source filename. `filesystem.fuzzy_is_analyzed()` is a secondary safety net for when the manifest is missing or out of sync (e.g., re-clone, manifest deleted): it requires **both** a date-token match AND the transcript's core title as a substring of an `Analyzed/` filename. Date overlap prevents short common titles like "Sync" from collapsing distinct meetings.

### Failure semantics

Both pipelines **fail closed**: on any error the source file stays in place and no manifest entry is written, so the next run picks it up again. Errors go to stderr; the wrapper script surfaces the first few in the macOS notification banner.

`filesystem.move_to_processed()` refuses to overwrite an existing target (POSIX rename would silently overwrite). `filesystem.write_text()` writes to `.tmp` and renames, so a crash mid-write never leaves a partial analysis at the destination.

### `.gdoc` body fetching (notes intake)

A `.gdoc` in the local Drive cache is a JSON shortcut, not the body. `_process_gdoc` resolves the Drive document id through a three-step fallback chain, in this order:

1. Read the local `.gdoc` JSON for `doc_id` / `resource_id`.
2. Read the `com.google.drivefs.item-id#S` xattr via `/usr/bin/xattr -p`.
3. Search Drive by exact filename match (`drive_service.files().list(q="name = '<title>' and mimeType = 'application/vnd.google-apps.document'")`).

This chain exists because **launchd-spawned processes hit EDEADLK** from Drive's File Provider when reading freshly-synced `.gdoc` bodies. The xattr path uses a different syscall and usually survives; the Drive name search is the final safety net. Don't simplify this chain without reproducing the failure on a clean launchd run.

The Gemini-export response starts with a UTF-8 BOM — `_fetch_gdoc_text` strips it so `parse_gemini_header` can read line 1 as `MMM DD, YYYY`.

### Frontmatter contract

`prompts.frontmatter_instruction()` defines the YAML frontmatter the LLM must emit at the top of every analysis: `meeting_date`, `participants`, `workstream`, `meeting_type`, plus optional `tags`/`decisions_count`/`risks_surfaced`/`key_stakeholders_absent`. The `workstream` and `meeting_type` enums are fixed vocabularies the cross-transcript synthesis (in Claude.ai) groups by — changes to the enum lists ripple into downstream consumers outside this repo.

Notes intake writes its own frontmatter directly with `source: gemini-summary` so the synthesis can weight notes differently from full transcripts later.

### Output filename convention

`filing.build_output_filename()` produces `[ISO-timestamp] - [Title] - [Date] [ANALYZED].txt`. `LEADING_PREFIX` strips both date+time prefix blocks that Plaud/Zap source filenames carry (the regex is dual-block because some sources only have one). Path separators in the title are sanitized so the assembled name can't escape `Analyzed/`. The same `LEADING_PREFIX` is reused by `filesystem._core_title()` for fuzzy matching — keep them coupled.
