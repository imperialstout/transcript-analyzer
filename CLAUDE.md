# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Setup (venv lives outside the repo because the launchd wrapper expects it there):

```bash
python3 -m venv ~/.venvs/transcript-analyzer
source ~/.venvs/transcript-analyzer/bin/activate
pip install -r requirements.txt
```

Run the analyzer once (processes transcripts, notes, and any documents in `docs/`):

```bash
source ~/.venvs/transcript-analyzer/bin/activate
python -m analyzer
```

Run synthesis after transcripts are analyzed:

```bash
python -m analyzer synthesize --mode daily    # D1 Daily Pulse — today's files
python -m analyzer synthesize --mode weekly   # D2 Slack Delta — this week's files
python -m analyzer synthesize --mode career   # D3 Career Trajectory — all current files (personal machine)
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
launchctl start com.transcript-analyzer
```

Cost dashboard (totals across `.processed.json`):

```bash
jq '[.[] | .cost_usd] | add' .processed.json
```

There is no test suite, linter config, or build step — runtime behavior is the contract.

## Configuration locations

- **`.env` lives at `~/.config/transcript-analyzer/.env`, NOT in the repo.** `analyzer/config.py` actively warns if `.env` is found at the repo root. **Each machine's `.env` is what makes it "work" or "personal"** (`DRIVE_BASE`, `ROUTING_PROFILE`, `SHAREABLE_PASS`, model overrides) — see the two-deployment section above.
- **OAuth credentials** (`google-credentials.json`, `google-token.json`) also live in `~/.config/transcript-analyzer/`.
- **Do not put the working tree inside iCloud Drive.** macOS TCC blocks launchd-spawned processes from executing files in iCloud paths, so the LaunchAgent fails with `Operation not permitted`. The `~/code/` location is load-bearing for scheduled runs.
- **The personal machine's launchd agent is intentionally disabled** (plist renamed `…transcript-analyzer.plist.disabled`); it's UI-driven on demand. Re-enable with `launchctl bootstrap gui/$(id -u) <plist>` after moving the name back. The work machine remains on the 30-min schedule.

## Architecture

### Two deployments, one codebase (work + personal)

This repo runs on **two machines with two different Google Drives**, differentiated entirely by `.env` — **not** a fork. The engine is identical; reliability fixes land once and serve both. Two env knobs do the splitting:

- **`DRIVE_BASE`** — the Drive root for all content paths. **Must not be hardcoded** (the default in `config.py` keeps the work layout so an unconfigured work checkout still works; the personal machine overrides it). Individual `*_PATH` vars still win if set.
  - Work: `~/Library/CloudStorage/GoogleDrive-you@yourcompany.com/My Drive/Workcall`
  - Personal: `~/Library/CloudStorage/GoogleDrive-you@yourpersonaldomain.com/My Drive/Workcall`
- **`ROUTING_PROFILE`** (`router.PROFILES`) — which classifier taxonomy the `claude-cli` backend routes into:
  - `work` (default): operational meeting set — `DAILY` / `STANDUP` / `SOLUTION` / `EXEC`.
  - `personal`: the career+political lens — **`B4` (Political Read)** vs **`A3` (1:1/Career)**, with `SHAREABLE_PASS=false` (it keeps sensitive content rather than sharing it).

Division of labor: **work** machine does per-meeting `analyze` + daily/weekly `synthesize`, shareable on (sharing with leads). **Personal** machine does the bigger-picture career read — primarily `synthesize --mode career` over summaries — shareable off, and is **UI-driven, not scheduled** (its launchd agent is disabled; runs are triggered on demand via `python -m analyzer ui`). The personal seat also has `claude-opus-4-8` available (the work seat does not — it's `opus-4-7` only).

### Entry point dispatch (`__main__.py`)

`python -m analyzer` → `main.main()` (transcript + notes intake pipeline)
`python -m analyzer synthesize --mode daily|weekly|career` → `synthesize.main()` (D1/D2/D3 synthesis)
`python -m analyzer ui [--port N]` → `ui.main()` (Flask dashboard)

### Three-pipeline flow (main.py)

`analyzer/main.py` orchestrates three independent pipelines in sequence per invocation:

1. **Notes intake** (`notes_intake.py`): Gemini-summarized meeting notes dropped into `Call Transcripts/notes/`. **No LLM call** — Gemini's summary is already lossy compression; re-summarizing would dilute nuggets. Pure file plumbing: parse metadata, file into `Analyzed/` with canonical naming, move source to `notes/_Processed/<YYYY-MM>/`.
2. **Document pipeline**: Documents dropped into `Call Transcripts/docs/`. Supported formats: `.pdf` (pypdf text extraction), `.docx` (python-docx), `.md` / `.txt` (plain read). Each is analyzed with the `DOCUMENT` prompt (Opus) and written to `Analyzed/` as `[ANALYZED].md`. After each analysis, a second Claude call extracts the `## Reference Updates` section and rewrites `Analyzed/[PROGRAM REFERENCE].md` with the durable facts — the pipeline-maintained program knowledge base. Source moves to `docs/_Processed/<YYYY-MM>/`.
3. **Transcript pass**: `.txt` and `.md` files at the root of `Call Transcripts/` (subfolders intentionally ignored). Each is auto-routed to a category prompt, analyzed (via the configured backend), and lands in `Analyzed/`. Output is always `.txt` regardless of source extension. With the shareable pass on, each transcript yields **two** outputs: the internal `[ANALYZED].txt` and a redacted `[SHAREABLE].txt` sibling.

A summary line `All done. N succeeded, M failed. Total cost: $X.` is printed at the end. `bin/analyze.sh` parses this exact line to decide whether to fire a macOS notification — keep the format stable.

### Synthesis pipeline (synthesize.py)

`synthesize.run()` is invoked manually (not by launchd) at end-of-day, end-of-week, or for a career check-in. Three modes, each keyed to a prompt in `Workcall/dailyAndWeeklyPrompts.md` (same `### KEY.` + fenced-block convention as `PromptLibrary.md`):

| Mode | Key | Window | Model | Archives inputs? |
|---|---|---|---|---|
| `daily` | `D1` Daily Pulse | today | Sonnet | yes |
| `weekly` | `D2` Slack Delta | this ISO week (Mon–today) | Sonnet | yes |
| `career` | `D3` Career & Position Trajectory | **all current `[ANALYZED]` files** | **B4 model (Opus on personal)** | **no** |

Common flow: collect the in-window `[ANALYZED]` files (skipping `[SHAREABLE]`/`[DAILY PULSE]`/`[WEEKLY SUMMARY]`/`[SLACK DELTA]`/`[CAREER TRAJECTORY]`/`[PROGRAM REFERENCE]`), prepend the most recent prior synthesis **of the same type** as continuity context, bundle into one `claude -p` call, write to `Analyzed/` with the mode's tag (`.md`). The **program reference** (`[PROGRAM REFERENCE].md`) is injected as system context, not bundled as an input file.

- **daily/weekly** then **archive** the bundled inputs to `Analyzed/_Archive/YYYY-MM/` (keyed on the meeting date; refuses to overwrite; an archive failure after a successful write logs a warning but doesn't fail the synthesis — nothing is lost).
- **career is deliberately different**: it bundles *all* current analyses (no date window), runs on the higher tier because it's strategic reasoning rather than a recap, and **does not archive** — the trajectory is cumulative, so inputs stay put and each review chains off the prior `[CAREER TRAJECTORY]`. This is the personal machine's primary tool.

Synthesis files themselves are **never** archived — they stay in `Analyzed/` as context for future runs.

### Content lives in Drive, not the repo

Drive base: **`$DRIVE_BASE`** (env-driven — set per machine in `.env`; see the two-deployment section).

- **`PromptLibrary.md`** — prompt library parsed by `prompts.load_prompts()` looking for `### KEY.` headings followed by fenced code blocks. The **work** profile uses `DAILY` / `STANDUP` / `SOLUTION` / `EXEC` + `REDACT` + `DOCUMENT`; the **personal** profile routes into `B4` (Political Read) / `A3` (1:1/Career) from the legacy A/B set. The Drive copy must contain whichever keys the active `ROUTING_PROFILE` needs. A built-in default is used for `DOCUMENT` if the key is absent. C-series prompts are cross-transcript and run in Claude.ai chat, not here.
- **`dailyAndWeeklyPrompts.md`** — synthesis prompts D1 (Daily Pulse), D2 (Weekly Slack Delta), **D3 (Career & Position Trajectory)**. Parsed by `synthesize._load_synthesis_prompts()` (`### D\d.` headings + fenced blocks). **`synthesize.py` reads this Drive copy, not the repo `docs/dailyAndWeeklyPrompts.md`** — the repo file is gitignored reference material only. Each machine's Drive needs the keys for the modes it runs (personal needs D3).
- **`Program_Context_Brief.md`** — program-wide context injected as the system prefix on every run.
- **`04_people_rolodex.md`** (**optional**) — named-individual index that complements the brief, incl. Plaud-mangled name variants. `prompts.load_rolodex()` reads best-effort (`""` if absent).
- **`05_vocabulary.md`** (**optional**) — canonical spellings of names/acronyms/product terms. Fed to the model to normalize mangled terms in Gemini/Teams/Slack transcripts.

Paths are configurable via `PROMPT_LIBRARY_PATH` / `CONTEXT_BRIEF_PATH` / `ROLODEX_PATH` / `VOCABULARY_PATH`. **Editing prompts is a Drive operation, not a code change.**

### Execution backends (`BACKEND`)

Analysis runs through one of two interchangeable backends, both producing the same `AnalysisResult`:

- **`claude-cli`** (`claude_cli.py`, the default): shells out to `claude -p` (headless Claude Code), billing to a **Claude Code seat** instead of a personal `ANTHROPIC_API_KEY`. The seat covers token cost, so `cost_usd` is effectively informational. Requires the `claude` binary (`CLAUDE_BIN`, absolute path under launchd). The transcript is passed on **stdin** (never argv — the system prefix can be tens of KB); tool use is disabled via `CLAUDE_EXTRA_ARGS`. Caching is handled internally by Claude Code.
- **`api`** (`anthropic_client.py`, legacy/fallback): the original direct Anthropic API path with explicit `cache_control`. Requires `ANTHROPIC_API_KEY`. Selecting `api` reproduces the pre-routing behavior exactly (single default prompt, no shareable pass) — the regression guard.

Both compose the **identical** system prefix via `anthropic_client.system_prompt_text()` so output is backend-independent.

### Routing → profile-selected category prompts (`claude-cli` only)

`router.classify()` runs a cheap Haiku-tier `claude -p` call to put each transcript into one of the **active profile's** categories, then runs the matching prompt (the category name IS the `PromptLibrary.md` key). The taxonomy is **not hardcoded** — it comes from `router.PROFILES[CONFIG.routing_profile]` (selected by `ROUTING_PROFILE`):

- **`work`** (default): `DAILY` / `STANDUP` / `SOLUTION` / `EXEC`, fallback `STANDUP`.
- **`personal`**: `B4` (Political Read) / `A3` (1:1/Career), fallback `B4`.

`router.CATEGORIES` / `router.FALLBACK` and the classifier system prompt all derive from the active profile (an unknown `ROUTING_PROFILE` warns and falls back to `work`). Classification **never raises** — any error/ambiguity collapses to `FALLBACK`. If a routed prompt is missing from the Drive library, `main._resolve_prompt()` falls back to `FALLBACK` then `DEFAULT_PROMPT_KEY`.

Model tiering (`config.models`, each overridable via `MODEL_<KEY>`):
- `EXEC` → `claude-opus-4-7`; `SOLUTION` / `STANDUP` / `DAILY` → `claude-sonnet-4-6`
- `DOCUMENT` → `claude-opus-4-7` (dense strategic content; override with `MODEL_DOCUMENT`)
- `B4` → `claude-opus-4-7` by default; **the personal `.env` sets `MODEL_B4=claude-opus-4-8`** (the latest Opus, available on the personal seat but not the work seat). `A3` → `claude-sonnet-4-6`.
- Classifier → `claude-haiku-4-5-20251001` (bare alias rejected by the seat — use dated id); Redaction → `claude-sonnet-4-6`; Synthesis → Sonnet for D1/D2, the **B4 model** for D3.
- `supports_thinking()` (api backend only) recognizes `opus-4-8` / `opus-4-7` / `opus-4-6` / `sonnet-4-6`.

All work-seat ids confirmed via `bin/phase0_check.sh`. A different seat may expose different ids — re-run the probe. `model_for(key)` honors `MODEL_OVERRIDE`.

### Shareable redaction pass (`SHAREABLE_PASS`)

After the internal `[ANALYZED]` file is written, `redactor.redact()` runs a second `claude -p` pass over the analysis text to strip internal politics / career-path notes, writing a `[SHAREABLE]` sibling. The instruction comes from a `### REDACT.` block in `PromptLibrary.md` (a built-in default is used if absent). **Best-effort by design**: if it fails, only the shareable file is dropped — the internal analysis is still recorded and the source moved. Every internal prompt ends with a `## Private read — internal only` section that REDACT targets — keep that heading verbatim across all prompts.

### Prompt caching (`api` backend)

The `api` backend packs the entire stable prefix — framing + Program Context Brief + frontmatter spec + prompt body — into a single system block with `cache_control: {type: ephemeral, ttl: "1h"}`. The **1-hour TTL is deliberate**: each analysis takes 3+ minutes, so the default 5-minute TTL expires mid-batch. (`claude-cli` backend lets Claude Code cache internally.)

If you change the prefix composition (add/remove a section, reorder), every existing cache entry invalidates — expect a one-time cost bump on the next batch.

### Dedup: manifest + fuzzy backstop

`.processed.json` (at the repo root) is the source of truth for what's been analyzed — keyed by source filename. `filesystem.fuzzy_is_analyzed()` is a secondary safety net: it requires **both** a date-token match AND the transcript's core title as a substring of an `Analyzed/` filename.

### Failure semantics

Both pipelines **fail closed**: on any error the source file stays in place and no manifest entry is written, so the next run picks it up again. `filesystem.move_to_processed()` refuses to overwrite an existing target. `filesystem.write_text()` writes to `.tmp` and renames atomically.

### Transcript read fallback (Drive × launchd EDEADLK)

`filesystem.read_text()` reads each transcript through a layered fallback because Drive's File Provider returns **EDEADLK** to launchd-spawned processes for freshly-synced files: (1) `open()` + retry, (2) `/bin/cat` + retry (different syscall path), (3) `drive_client.fetch_text_file_by_name()` — the Drive API, bypassing the FUSE mount entirely.

The Drive-API layer matches by filename, and **macOS stores filenames NFD-decomposed (`R` + combining U+0301) while Drive stores them NFC-composed (`é` = U+00E9), and Drive's `name =` query is byte-sensitive** — so a naive exact match silently returns 0 results for any accented name (a `…Réunion hebdomadaire…` transcript sat unprocessed for 6 days because of this). `fetch_text_file_by_name` now (a) tries exact `name =` across NFC/NFD/raw forms, then (b) falls back to `name contains '<leading ASCII prefix>'` and disambiguates candidates in Python via NFC-normalized casefold (exact match preferred, else newest `modifiedTime`). Don't revert to a single exact-match query.

(Known still-open gap: the retry predicate only fires on errno 11/EDEADLK — errno 60 timeouts and Drive-API `BrokenPipeError` bypass the chain.)

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

`filing.build_output_filename()` produces `[ISO-timestamp] - [Title] - [Date] [ANALYZED]<ext>`. The `extension` parameter defaults to `.txt` for transcripts; document analyses use `.md`. `LEADING_PREFIX` strips both date+time prefix blocks that Plaud/Zap source filenames carry. Path separators in the title are sanitized. The same `LEADING_PREFIX` is reused by `filesystem._core_title()` for fuzzy matching — keep them coupled.

Synthesis outputs use `.md` extension and `[DAILY PULSE]` / `[SLACK DELTA]` / `[CAREER TRAJECTORY]` tags instead of `[ANALYZED]`. The program reference file uses `[PROGRAM REFERENCE].md` and lives permanently in `Analyzed/` root — never archived, never bundled into synthesis inputs, but injected as system context.

### Program reference (`[PROGRAM REFERENCE].md`)

Pipeline-maintained file in `Analyzed/`. Created and updated automatically when documents are processed. Contains durable program facts (team structure, milestones, process decisions, capability ownership) that survive across synthesis windows. Injected into all three synthesis modes (D1/D2/D3) between the context brief and the rolodex in the system prefix. The DOCUMENT prompt instructs the model to emit a `## Reference Updates` section; `main._extract_reference_updates()` splits on that heading and feeds the content to a second Claude call that merges it into the reference file. Override the document prompt via `### DOCUMENT.` in `PromptLibrary.md`.
