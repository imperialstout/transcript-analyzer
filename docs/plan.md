# Transcript Analyzer — v1 Build Plan

## Context

Brad runs an enterprise Salesforce program (SherpaX at Siemens) and produces 10–25 meeting transcripts/week via Plaud. Today, transcripts land in a Google Drive folder via a Zap, and analysis happens by pasting them into Claude.ai chat with a named prompt from his prompt library. Heavy weeks crash chat sessions on context-window pressure. The fix: move per-transcript analysis into an unattended local script that calls the Anthropic API directly, while keeping Drive as source/destination so the existing weekly cross-transcript synthesis (run in Claude.ai against the Analyzed folder) keeps working.

A solution doc already exists at [transcript_analyzer_solution_doc.md](transcript_analyzer_solution_doc.md). This plan refines it against what's actually on disk and what the user just confirmed.

## Findings from environment exploration

- **Drive paths confirmed**: transcripts at [Workcall/Call Transcripts/](/Users/bradgross/Library/CloudStorage/GoogleDrive-brad@bradgross.org/My%20Drive/Workcall/Call%20Transcripts/), analyses at [Workcall/Analyzed/](/Users/bradgross/Library/CloudStorage/GoogleDrive-brad@bradgross.org/My%20Drive/Workcall/Analyzed/).
- **Solution doc assumption broken — but user is fixing it**: every existing transcript is a `.gdoc` pointer file (`{"doc_id":"..."}`), not plain text. **User decided: change the Zap to write `.txt` instead.** Script ignores `.gdoc` and processes only `.txt`.
- **Prompt library and context brief are already in place** at [Workcall/PromptLibrary.md](/Users/bradgross/Library/CloudStorage/GoogleDrive-brad@bradgross.org/My%20Drive/Workcall/PromptLibrary.md) (9 prompts: A1–A3, B1–B4, C1–C2) and [Workcall/Program_Context_Brief.md](/Users/bradgross/Library/CloudStorage/GoogleDrive-brad@bradgross.org/My%20Drive/Workcall/Program_Context_Brief.md). Script reads them in-place — single source of truth, edits picked up on next run.
- **32 existing analyses with inconsistent naming** (mixed `.gdoc` and extensionless, some with full ISO timestamp + date, some without). User chose **fuzzy-match** strategy for the "already analyzed?" check.
- **`ANTHROPIC_API_KEY` not in shell env** — needs `.env` in the repo.
- **No code yet** — greenfield. Python 3.14.4 available at `/opt/homebrew/bin/python3`.

## v1 scope (committed)

Ship "folder mode + A2 only + manifest + frontmatter + tiered models" as the smallest useful version. Combines doc-v1 + doc-v2 plus the three follow-up decisions below:

- **Folder mode CLI**: `analyze` scans `Call Transcripts/` root, finds unanalyzed `.txt` files, processes each, writes to `Analyzed/`.
- **Hardcoded A2 prompt** (Decision & Direction). No routing classifier yet — that's v2.
- **Manifest as primary** "already analyzed?" check (`transcript-analyzer/.processed.json`); fuzzy-match against `Analyzed/` filenames as a backstop only.
- **Move-after-analyze**: source `.txt` relocated to `Call Transcripts/_Processed/<YYYY-MM>/` after a successful run.
- **Tiered model defaults** with `.env` overrides; v1's A2 default = `claude-sonnet-4-6`.
- **Prompt caching** on the system prompt (mandatory — see Model selection §).
- **YAML frontmatter** at the top of every analysis with seeded taxonomy (workstream, participants, meeting_type, tags).
- **Streaming + adaptive thinking** (`thinking={"type": "adaptive"}`, `effort: "high"`) — required for the analysis-quality bar and for outputs >16K tokens.
- **Full Program Context Brief** loaded into the system prompt on every call.
- Sequential processing (no concurrency); per-file progress to stdout.

Routing, concurrency, cron silent-mode, error notifications, search CLI: deferred.

## Prerequisites (Brad's side, before first run)

1. **Update the Zap** to write transcripts as plain `.txt` files, not as native Google Docs. The script skips `.gdoc` files with a warning until this is done.
2. **Drive for Desktop** must be running and syncing both folders (already true).
3. **`ANTHROPIC_API_KEY`** added to `.env` in the repo.

## Repo structure

Create at `/Users/bradgross/Library/Mobile Documents/com~apple~CloudDocs/WorkTranscripts/transcript-analyzer/` (per user's "Inside WorkTranscripts/" choice).

```text
transcript-analyzer/
├── analyzer/
│   ├── __init__.py
│   ├── main.py              # CLI entry point: scan, dispatch, report
│   ├── config.py            # load .env, expose paths, API key, per-prompt models
│   ├── filesystem.py        # list_transcripts, move_to_processed, fuzzy_is_analyzed, read/write
│   ├── manifest.py          # load/save .processed.json, query/record entries
│   ├── prompts.py           # parse PromptLibrary.md → {key: body}; load_brief(); frontmatter taxonomy
│   ├── filing.py            # build_output_filename per convention
│   └── anthropic_client.py  # analyze() with caching, streaming, adaptive thinking
├── .processed.json          # runtime — gitignored
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

## File-by-file plan

### `analyzer/config.py`

- Load `.env` via `python-dotenv`.
- Paths: `CALL_TRANSCRIPTS_PATH`, `PROCESSED_PATH` (defaults to `<call_transcripts>/_Processed`), `ANALYZED_PATH`, `PROMPT_LIBRARY_PATH`, `CONTEXT_BRIEF_PATH`, `MANIFEST_PATH` (defaults to `<repo>/.processed.json`).
- API: `ANTHROPIC_API_KEY`.
- Per-prompt models with defaults baked in:
  - `MODEL_A1` → `claude-opus-4-7`
  - `MODEL_A2` → `claude-sonnet-4-6`
  - `MODEL_A3` → `claude-sonnet-4-6`
  - `MODEL_B1` → `claude-sonnet-4-6`
  - `MODEL_B2` → `claude-opus-4-7`
  - `MODEL_B3` → `claude-sonnet-4-6`
  - `MODEL_B4` → `claude-opus-4-7`
  - `MODEL_OVERRIDE` (optional) — if set, used for every prompt this run.
- `DEFAULT_PROMPT_KEY` → `A2`.
- `EFFORT` → `high` (passes to `output_config.effort`).
- All paths use `pathlib.Path`. Defaults point at the discovered `Workcall/` paths so a fresh `.env` works out of the box.

### `analyzer/filesystem.py`

- `list_unanalyzed_transcripts() -> list[Path]`: returns `*.txt` files at the **root** of `CALL_TRANSCRIPTS_PATH` (does not descend into `_Processed/`). Logs a count of `.gdoc` files seen and skipped (warns Brad if the Zap update isn't done).
- `move_to_processed(transcript_path: Path, meeting_date: date) -> Path`: moves the source into `PROCESSED_PATH / f"{meeting_date.year}-{meeting_date.month:02d}/"` (auto-creates parents). Returns the new path. Called only on successful analysis.
- `fuzzy_is_analyzed(transcript_path: Path) -> bool`: backstop check. Derives a "core title" from the transcript filename (strip leading `YYYY-MM-DDTHH:MM:SSZ - ` or `YYYY-MM-DD - `, strip extension, lowercase, normalize whitespace). Returns True if any file in `ANALYZED_PATH` contains that core title as a substring. Used only when the manifest doesn't have an entry for the file — protects against repo resets while preserving the manifest as primary truth.
- `read_text(path: Path) -> str`, `write_text(path: Path, content: str) -> None`: UTF-8 wrappers.

### `analyzer/manifest.py`

- `load() -> dict[str, dict]`: reads `MANIFEST_PATH`, returns `{}` if missing.
- `is_recorded(source_filename: str) -> bool`: True if the manifest has an entry.
- `record(source_filename: str, entry: dict) -> None`: append-and-save (fsync). Entry shape:

  ```json
  {
    "analyzed_at": "ISO-8601 with offset",
    "output_filename": "...",
    "prompt_key": "A2",
    "model": "claude-sonnet-4-6",
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_creation_tokens": 0,
    "cache_read_tokens": 0,
    "cost_usd": 0.0,
    "duration_seconds": 0.0
  }
  ```

- Cost computed inline using current per-model rates (Opus 4.7 $5/$25, Sonnet 4.6 $3/$15, Haiku 4.5 $1/$5 per 1M tokens; cache reads at 0.1×, cache writes at 1.25×).

### `analyzer/prompts.py`

- `load_prompts() -> dict[str, str]`: parse [PromptLibrary.md](/Users/bradgross/Library/CloudStorage/GoogleDrive-brad@bradgross.org/My%20Drive/Workcall/PromptLibrary.md) by `### A1.`, `### A2.`, etc. headings (regex `^### ([A-C]\d)\.`), capturing only the fenced code block body. Returns `{"A1": "...", "A2": "...", ..., "B4": "..."}`. C1/C2 are cross-transcript and excluded.
- `load_context_brief() -> str`: read the full [Program_Context_Brief.md](/Users/bradgross/Library/CloudStorage/GoogleDrive-brad@bradgross.org/My%20Drive/Workcall/Program_Context_Brief.md) as-is. The first ~47 lines are imported chat-sidebar cruft; substantive content starts at `# Project Instructions:`. Pass the whole file — model handles the noise fine, trimming risks dropping signal.
- `frontmatter_instruction() -> str`: returns the static instruction block prepended to the prompt body, telling the model to emit YAML frontmatter as the first thing in its response. See **Frontmatter taxonomy (v1 seed)** below for the exact controlled vocabularies.

### `analyzer/filing.py`

- `build_output_filename(transcript_filename: str, analysis_run_time: datetime) -> str`: produces `[ISO-timestamp] - [Original Title] - [Date] [ANALYZED].txt` per the doc's exact convention.
  - ISO timestamp: `analysis_run_time.strftime("%Y-%m-%dT%H-%M-%S")` — hyphens in the time portion (filesystem-safe).
  - Original Title: source filename minus extension, with any leading `YYYY-MM-DD - ` or `YYYY-MM-DDTHH:MM:SSZ - ` stripped so it doesn't double up.
  - Date: meeting date parsed from the source filename (regex `\d{4}-\d{2}-\d{2}`), falling back to today's date. Returned alongside the filename so `move_to_processed` can use it for the `<YYYY-MM>` subfolder.
  - `[ANALYZED]` literal, `.txt` extension.

### `analyzer/anthropic_client.py`

- `analyze(transcript_text: str, prompt_key: str, prompts: dict, context_brief: str, model: str) -> AnalysisResult`:
  - System prompt = framing text + context brief + frontmatter instruction + prompt body.
  - System passed as a list of text blocks; **last block carries `cache_control: {"type": "ephemeral"}`** so the framing+brief+frontmatter+prompt prefix is cached across all calls in the batch (90% savings on repeat).
  - User message = `f"Transcript:\n\n{transcript_text}"`.
  - Streaming via `client.messages.stream(...)` with `max_tokens=64000`, `thinking={"type": "adaptive"}`, `output_config={"effort": "high"}`.
  - Returns `AnalysisResult(text, usage)` where `usage` carries `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens` for manifest cost tracking.
  - SDK retries left at default. Surface errors as exceptions; main loop catches and continues.

### `analyzer/main.py`

- Parses no args for v1 — just `python -m analyzer` or an `analyze` console-script entry.
- Flow:
  1. Load config, prompts, context brief, manifest.
  2. `transcripts = list_unanalyzed_transcripts()` — only `.txt` at the source-folder root.
  3. Filter out files where `manifest.is_recorded(name) or fuzzy_is_analyzed(path)`.
  4. Print `Found N unanalyzed transcripts. Skipped M .gdoc files (waiting on Zap update).`
  5. For each: read → resolve `model = MODEL_OVERRIDE or MODEL_<prompt_key>` → `analyze(...)` → build output filename → write analysis to `ANALYZED_PATH / output_filename` → `manifest.record(...)` → `move_to_processed(source, meeting_date)` → print `[i/N] done — filed as ... ($X.XX)`. Per-file exceptions print `FAILED: ...` and continue (do **not** move source on failure; do **not** record manifest entry).
  6. Print final summary `All done. X succeeded, Y failed. Total cost: $Z.ZZ.`.

### `requirements.txt`
- `anthropic` (current Python SDK)
- `python-dotenv`

### `.env.example`
Sample paths pre-filled, `ANTHROPIC_API_KEY=` blank.

### `.gitignore`
`.env`, `__pycache__/`, `.venv/`, `*.pyc`.

### `README.md`
Short: prereqs (Drive for Desktop, Zap update), install (`python3 -m venv .venv && pip install -r requirements.txt`), `.env` setup, usage (`python -m analyzer`).

## Verification

Manual end-to-end test on a real transcript:

1. **Stage a test file**: until the Zap update lands, manually open one of the 16 `.gdoc` transcripts in Google Docs, File → Download → Plain Text, place the resulting `.txt` at the **root** of [Workcall/Call Transcripts/](/Users/bradgross/Library/CloudStorage/GoogleDrive-brad@bradgross.org/My%20Drive/Workcall/Call%20Transcripts/). Use one whose title clearly does *not* match any existing Analyzed file.
2. **Run**: `cd transcript-analyzer && python -m analyzer`.
3. **Expected stdout** (numbers vary):

   ```text
   Found 1 unanalyzed transcript.
   Skipped 16 .gdoc files (waiting on Zap update to .txt).
   [1/1] <filename>.txt → A2 (claude-sonnet-4-6)
   [1/1] done — filed as 2026-04-28T... - <title> - 2026-04-28 [ANALYZED].txt ($0.08)
   All done. 1 succeeded, 0 failed. Total cost: $0.08.
   ```

4. **Verify output file**: open the produced `.txt` in [Workcall/Analyzed/](/Users/bradgross/Library/CloudStorage/GoogleDrive-brad@bradgross.org/My%20Drive/Workcall/Analyzed/). Confirm:
   - First lines are a YAML frontmatter block bounded by `---` with the required fields populated.
   - Body matches the structure and posture of existing analyses (attributed, specific, non-neutralized — see [2026-04-23T11:00:55Z - Brad Gunnar Imad CML update](/Users/bradgross/Library/CloudStorage/GoogleDrive-brad@bradgross.org/My%20Drive/Workcall/Analyzed/2026-04-23T11:00:55Z%20-%20Brad%20Gunnar%20Imad%20CML%20update%20-%2023%20Apr%202026%20%5BANALYZED%5D) for comparison).
5. **Verify source moved**: the staged `.txt` is gone from `Call Transcripts/` root and now lives in `Call Transcripts/_Processed/<YYYY-MM>/`.
6. **Verify manifest**: `cat transcript-analyzer/.processed.json` shows an entry for the source filename with non-zero `cache_creation_tokens` (first call writes the cache) and a `cost_usd` value matching what was printed.
7. **Verify Drive sync**: the new analysis appears in Drive web UI within ~30 seconds.
8. **Verify idempotency**: run a second time. Expected: `Found 0 unanalyzed transcripts.` (manifest hit, no API call).
9. **Stage a second transcript and run again**: this time `cache_read_input_tokens` should be substantially > 0 in the manifest entry, proving prompt caching works across the batch.

## Marking analyzed transcripts

**Strategy: move-after-analyze + lightweight manifest.**

After a successful analysis, the script moves the source `.txt` from `Call Transcripts/` to `Call Transcripts/_Processed/<YYYY-MM>/` (auto-created). A `.processed.json` manifest in the repo (`transcript-analyzer/.processed.json`) records per-file metadata:

```json
{
  "2026-04-28 product call to discuss guided selling.txt": {
    "analyzed_at": "2026-04-28T14:30:12-04:00",
    "output_filename": "2026-04-28T14-30-12 - product call to discuss guided selling - 2026-04-28 [ANALYZED].txt",
    "prompt_key": "A2",
    "model": "claude-sonnet-4-6",
    "input_tokens": 38421,
    "output_tokens": 4203,
    "cache_read_tokens": 9876,
    "cost_usd": 0.082
  }
}
```

**Benefits:**
- Visual: glance at the Drive folder, see what's pending at the root.
- Cost dashboard: total run cost is `jq '[.[] | .cost_usd] | add' .processed.json`.
- "Already analyzed?" check becomes trivial: file at root + not in manifest = analyze.
- Leading underscore on `_Processed/` keeps it sorted last in Drive's alphabetical view.

**Backstop:** if the manifest is lost (repo reset, migration), fall back to fuzzy-match against `Analyzed/` filenames before re-running — prevents accidental duplicate analyses.

**Existing 16 `.gdoc` files**: untouched. Only `.txt` files at the source-folder root are candidates. After Brad updates the Zap, future transcripts arrive as `.txt`; the existing `.gdoc` corpus stays where it is.

## Model selection

**Per-prompt model config with sensible defaults**, all overridable via `.env`:

| Prompt class | Default | Why |
|---|---|---|
| Routing classifier (v2) | `claude-haiku-4-5` | Cheap, fast classification on first ~2K tokens |
| A1 Client Signal | `claude-opus-4-7` | Attribution + political nuance critical |
| B2 Escalation & Issue | `claude-opus-4-7` | High stakes, post-incident clarity |
| B4 Political Read | `claude-opus-4-7` | Whole point is reading dynamics |
| A2 Decision & Direction | `claude-sonnet-4-6` | Structure-heavy, lower political stakes |
| B1 Discovery & Requirements | `claude-sonnet-4-6` | Information capture |
| B3 What Did We Commit To | `claude-sonnet-4-6` | Commitment extraction |
| A3 1:1 & Team Pulse | `claude-sonnet-4-6` | Short, internal |

**v1 default for A2: `claude-sonnet-4-6`** (saves ~40% vs Opus, minimal quality risk on this prompt class). Configurable via `MODEL_A2=` in `.env`. If output quality disappoints on Sonnet, flip to Opus per-prompt without code change.

**Per-run override:** `MODEL_OVERRIDE=claude-opus-4-7 python -m analyzer` forces a specific model for a one-off rerun (e.g., re-analyzing a critical meeting on Opus after a Sonnet pass).

**Cost projection** (25 transcripts/week, with prompt caching on the 35KB context brief):
- Opus 4.7 only: ~$5–8/week
- Tiered (Opus on stakes, Sonnet on rest): ~$4–5/week
- Sonnet 4.6 only: ~$3–4/week
- Haiku 4.5 only: ~$1/week (not recommended for analysis depth)

**Prompt caching is mandatory** — without it, repeating the 35KB context brief 10–25× per batch is pure waste. The `claude-api` skill confirms this is a 90% saving on cached tokens. See `analyzer/anthropic_client.py` plan above; it places `cache_control: {type: "ephemeral"}` on the last system text block.

## Filing system / scaling for the backlog

At ~25 transcripts/week, the Analyzed folder hits 500–1,300 files in year 1. Two cheap moves now, defer the heavy tooling.

**v1 (cheap, do now): YAML frontmatter at the top of every analysis.**

The model emits a structured block as the first thing in the output:

```yaml
---
meeting_date: 2026-04-28
participants: [Brad Gross, Christoph Reichenbach, Colin Wahl]
workstream: SI RCA
meeting_type: internal-sync
prompt_key: A2
tags: [si-rca, devops, escalation]
decisions_count: 3
risks_surfaced: 2
---
```

- **Greppable now** (`rg -l "workstream: SI RCA" Analyzed/`).
- **Parseable later** when richer querying is needed.
- **No new tooling** — the LLM produces it; standard Markdown convention; renders cleanly in any reader.
- Implemented by adding a frontmatter-emit instruction to the system prompt, ahead of the prompt body.

The taxonomy (workstreams, meeting_types, valid tags) lives in the system prompt — let the model self-classify against it. Refine the taxonomy as patterns emerge.

### Frontmatter taxonomy (v1 seed)

Seeded from the Program Context Brief; refine on first run if the model picks values that don't match Brad's mental model. Lives in `prompts.frontmatter_instruction()` as a fixed text block prepended to the prompt body.

**Required fields:**

- `meeting_date` — ISO date (YYYY-MM-DD). Use the date in the source filename if present, else infer from transcript content.
- `participants` — list of full names. If a Plaud-mistranscribed name is unclear, use the most likely full name from the Cast of Characters in the brief (e.g., "Ikem" → "Eike-Oliver Steffen").
- `prompt_key` — set automatically by the script, not the model.
- `workstream` — one of: `DI-SW`, `SI`, `SI RCA`, `SI CPQ+`, `SI BuildingX`, `SI Services`, `SI SolSys`, `XMP`, `SFS`, `RCA-PoC`, `cross-stream`, `internal-salesforce`, `unclassified`.
- `meeting_type` — one of: `client-steerco`, `client-working-session`, `internal-sync`, `1-on-1`, `escalation`, `design-review`, `discovery`, `architecture`, `planning`, `retrospective`, `interview`, `other`.

**Optional fields:**

- `tags` — free-form list. Suggested vocabulary the model can draw from (and extend): `escalation`, `devops`, `integration`, `data-cloud`, `agentforce`, `governance`, `staffing`, `deadline-risk`, `pricing`, `cml`, `mdm`, `sit`, `uat`, `roadmap`, `political`, `commitment`, `unresolved`, `decision-deferred`.
- `decisions_count` — integer count of decisions captured in the analysis (zero is valid).
- `risks_surfaced` — integer count of new or escalated risks captured.
- `key_stakeholders_absent` — list of named people whose absence is materially relevant to interpreting the meeting.

**Format:** YAML block as the very first thing in the analysis output, fenced by `---` lines. The rest of the analysis (the structured-record sections from the prompt body) follows after a blank line.

**Backfill posture:** existing 32 analyses don't have frontmatter. They stay searchable via filename. New analyses going forward are richer. No retroactive rewrite.

**v3+ (when grep gets slow / Drive UI gets unwieldy):**
- **Date subfolders** (`Analyzed/<YYYY>/<MM>/`) — only when the flat folder actually hurts. This forces a workflow change for any cross-transcript reads in Claude.ai (would need to attach the parent folder to recurse), so don't do it prematurely.
- **`analyze search` CLI subcommand** — walks the tree, parses frontmatter, filters: `analyze search --workstream "SI RCA" --since 2026-03-01 --tag escalation`.
- **SQLite index** if frontmatter-grep gets slow (>2K analyses).
- **Archive policy** — auto-move analyses older than N months to `Analyzed/Archive/<YYYY>/<MM>/` to keep the active folder navigable.

The frontmatter is the load-bearing decision; everything in v3+ builds on it. Skip frontmatter and you're locked into either filename-mining or a re-analysis pass to extract structure later.

## Deferred (not in v1)

- **Routing classifier** (v2): parse all 7 A/B prompts, add `route_prompt()` Haiku call per solution doc §8, default to A2 on garbage. Per-prompt model config (above) lights up here.
- **Concurrency** (v3): 3-worker `ThreadPoolExecutor` once batches get big enough to matter.
- **Cron silent mode + error notifications** (v4).
- **Search CLI + date subfolders** (v3+, see filing system above).
- **Drive API migration** (future, only if/when this moves to a headless server).
