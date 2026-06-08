# Transcript Analyzer

Local CLI that runs meeting-transcript analyses through a Claude Code seat, replacing the chat-based workflow that was crashing on heavy weeks.

## Two deployments, one codebase

The same engine runs on **two machines with two Google Drives**, differentiated only by `.env` — not a fork, so reliability fixes land once:

| | **Work machine** | **Personal machine** |
|---|---|---|
| Drive (`DRIVE_BASE`) | `…@salesforce.com` | `…@bradgross.org` |
| `ROUTING_PROFILE` | `work` — `DAILY`/`STANDUP`/`SOLUTION`/`EXEC` | `personal` — `B4` (Political) / `A3` (Career) |
| Primary use | per-meeting analyze + daily/weekly synthesis | **Career Trajectory** synthesis (bigger picture) |
| `SHAREABLE_PASS` | on (sharing with leads) | off (keeps sensitive content) |
| Scheduling | launchd, every 30 min | **UI-driven on demand** (launchd disabled) |
| Top Opus model | `opus-4-7` | `opus-4-8` (available on this seat) |

Everything below applies to both; differences are called out inline.

## Prereqs

1. **Google Drive for Desktop** running and syncing `Workcall/`. Set `DRIVE_BASE` in `.env` to this machine's Drive root (work `…@salesforce.com` or personal `…@bradgross.org`).
2. **Write transcripts as plain `.txt` or `.md` files** at the root of `Call Transcripts/`. Subfolders are ignored. `.gdoc` shortcuts are handled via Drive API (see Notes intake below).
3. **Python 3.11+** and the **Claude Code CLI** (`claude`) on PATH (or set `CLAUDE_BIN` to an absolute path for launchd).
4. **Optional:** a `Call Transcripts/docs/` folder in Drive — drop PDFs here to trigger the document pipeline.

## Setup

```bash
python3 -m venv ~/.venvs/transcript-analyzer
source ~/.venvs/transcript-analyzer/bin/activate
pip install -r requirements.txt

mkdir -p ~/.config/transcript-analyzer
cp .env.example ~/.config/transcript-analyzer/.env
# Edit ~/.config/transcript-analyzer/.env — set path overrides if needed.
# ANTHROPIC_API_KEY is only required for BACKEND=api (legacy). Default backend
# is claude-cli, which bills to the Claude Code seat instead.
```

## Usage

### Web dashboard

The quickest way to use the analyzer day-to-day:

```bash
source ~/.venvs/transcript-analyzer/bin/activate
python -m analyzer ui              # opens http://localhost:7070 automatically
python -m analyzer ui --port 7071  # custom port
```

**Bookmark `http://localhost:7070`** in your browser. The server runs until you kill the terminal.

For a one-click shortcut on macOS, add this to `~/.zshrc` (or equivalent):

```bash
alias ta="source ~/.venvs/transcript-analyzer/bin/activate && python -m analyzer ui"
```

Then `ta` in any terminal opens the dashboard.

Dashboard features:
- Stats strip: total files, transcript count, notes count, cumulative cost
- Table of all analyzed files with category badge, model, cost, shareable status
- **Daily Pulse**, **Weekly Slack Delta**, and **Career Trajectory** buttons — run synthesis in-browser with live output
- **Settings tab** — configure the shared meeting files URL, rolodex/vocabulary paths, shareable toggle, backend, model override
- **Edit content files** — one-click open for PromptLibrary, Context Brief, Rolodex, and Vocabulary in your default editor
- **Open launchd log** shortcut for troubleshooting

### Analyze transcripts (normal run)

```bash
source ~/.venvs/transcript-analyzer/bin/activate
python -m analyzer
```

Each run:
1. Processes any pending Gemini-summary notes in `Call Transcripts/notes/`.
2. Processes any PDFs in `Call Transcripts/docs/` — see **Document pipeline** below.
3. Lists `.txt` / `.md` files at the root of `Call Transcripts/`.
4. Skips files already in `.processed.json` or fuzzy-matched against `Analyzed/`.
5. Routes each transcript to a category prompt via a cheap Haiku classifier call — `DAILY`/`STANDUP`/`SOLUTION`/`EXEC` on the work profile, or `B4` (Political) / `A3` (Career) on the personal profile (`ROUTING_PROFILE`).
6. Runs the matching prompt via `claude -p` on the Claude Code seat.
7. Writes `[ANALYZED].txt`, plus a redacted `[SHAREABLE].txt` sibling when `SHAREABLE_PASS=true` (off on the personal machine).
8. Records token usage in `.processed.json` and moves the source to `Call Transcripts/_Processed/<YYYY-MM>/`.

### Document pipeline

Drop PDFs into `Call Transcripts/docs/` (create the folder if it doesn't exist — the pipeline creates `_Processed/` automatically on first run). Each document:

1. Text is extracted via `pypdf` (text-layer PDFs — scanned/image-only PDFs are not supported).
2. Analyzed with the `DOCUMENT` prompt (Opus) — produces `[ANALYZED].md` in `Analyzed/`, participates in D1/D2/D3 synthesis like any transcript.
3. The `## Reference Updates` section is extracted from the analysis and merged into `Analyzed/[PROGRAM REFERENCE].md` — the pipeline-maintained program knowledge base of durable facts (team structure, milestones, process decisions, capability ownership).
4. Source moves to `docs/_Processed/<YYYY-MM>/`.

**`[PROGRAM REFERENCE].md`** is injected as system context into all synthesis runs — it makes every Daily Pulse, Weekly Delta, and Career Trajectory aware of the current program state without re-reading every document. It is never archived, never bundled as an input file.

Customize the document prompt by adding `### DOCUMENT.` with a fenced body to `PromptLibrary.md` in Drive. A built-in default is used if the key is absent.

### Synthesis: Daily Pulse, Weekly Slack Delta, Career Trajectory

After transcripts are analyzed, run synthesis manually (or via the UI buttons):

```bash
# D1 — Daily Pulse: bundles today's [ANALYZED] files, writes a 200-300 word pulse
python -m analyzer synthesize --mode daily

# D2 — Weekly Slack Delta: bundles this week's [ANALYZED] files, writes a paste-ready Slack delta
python -m analyzer synthesize --mode weekly

# D3 — Career Trajectory: bundles ALL current analyses, writes a position/career review
python -m analyzer synthesize --mode career
```

All three bundle `[ANALYZED]` files plus the most recent prior synthesis of the same kind (continuity), and write to `Analyzed/` with a `[DAILY PULSE]` / `[SLACK DELTA]` / `[CAREER TRAJECTORY]` suffix.

| Mode | Window | Model | Archives inputs? |
|---|---|---|---|
| `daily` / `weekly` | today / this ISO week | Sonnet (cheap recap) | **yes** → `_Archive/YYYY-MM/` |
| `career` | **all current `[ANALYZED]` files** | **Opus** (B4 model — strategic) | **no** (trajectory is cumulative) |

`career` is the personal machine's primary tool: it answers *"how is **my** position and career going"* rather than *"how is the project going,"* and deliberately keeps its inputs so each review chains off the prior one. It needs meaningful material in `Analyzed/` to be useful — feed it summaries/analyses first.

The synthesis prompts (D1, D2, D3) live in `Workcall/dailyAndWeeklyPrompts.md` on Drive — edit them there, no code change needed. (The repo's `docs/dailyAndWeeklyPrompts.md` is a gitignored reference copy; the code reads the Drive one.)

### One-off model override

```bash
MODEL_OVERRIDE=claude-opus-4-7 python -m analyzer
```

## Model tiering

| Category | Default model | Rationale |
|---|---|---|
| `EXEC` | `claude-opus-4-7` | Highest-stakes; executive dynamics (work) |
| `DOCUMENT` | `claude-opus-4-7` | Dense strategic content; decks + program docs |
| `SOLUTION` / `STANDUP` / `DAILY` | `claude-sonnet-4-6` | Technical/design + internal syncs/digests (work) |
| `B4` (Political Read) | `claude-opus-4-7`, **`opus-4-8` on personal** | Political dynamics; strategic (personal) |
| `A3` (1:1/Career) | `claude-sonnet-4-6` | People/career notes (personal) |
| Classifier | `claude-haiku-4-5-20251001` | Routing only; cheap |
| Redaction | `claude-sonnet-4-6` | Post-process pass over structured text |
| Reference update | `DOCUMENT` model | Merges new facts into `[PROGRAM REFERENCE].md` |
| Synthesis D1/D2 | `claude-sonnet-4-6` | Cheap recap; inputs already structured |
| Synthesis D3 (career) | B4 model (**`opus-4-8` on personal**) | Strategic trajectory reasoning |

Override any per-key model via `.env` (`MODEL_EXEC`, `MODEL_B4`, etc.). The personal `.env` sets `MODEL_B4=claude-opus-4-8` (the latest Opus, available on the personal seat but not the work seat).

## Content lives in Drive, not the repo

Runtime files are at **`$DRIVE_BASE/`** (work `…@salesforce.com`, personal `…@bradgross.org`):

| File | Purpose |
|---|---|
| `PromptLibrary.md` | Category prompts parsed by `### KEY.` headings + fenced blocks. Work profile: `### DAILY.` / `### STANDUP.` / `### SOLUTION.` / `### EXEC.` + `### REDACT.` + optional `### DOCUMENT.`. Personal profile: `### B4.` (Political) / `### A3.` (Career). |
| `dailyAndWeeklyPrompts.md` | Synthesis prompts `### D1.` (Daily Pulse) / `### D2.` (Slack Delta) / `### D3.` (Career Trajectory). Used by `python -m analyzer synthesize`. The repo `docs/` copy is gitignored reference only. |
| `Program_Context_Brief.md` | Program-wide context injected as the system prefix on every analysis run. Manually maintained — high-level, strategic. |
| `04_people_rolodex.md` | Named-individual index (optional). Appended after the brief; helps resolve Plaud-mangled names. |
| `05_plaud_vocabulary.md` | Canonical spellings of names/acronyms/product terms (optional). Normalizes mangled terms in non-Plaud transcripts. |

Paths are configurable via `.env` (`PROMPT_LIBRARY_PATH`, `CONTEXT_BRIEF_PATH`, `ROLODEX_PATH`, `VOCABULARY_PATH`). The repo is the engine; Drive is the content you tune.

## Gemini-summary notes intake

Meetings you didn't record (absent, async, recording failed) arrive as Gemini summaries. Drop them into `Call Transcripts/notes/` — the analyzer files them with no LLM call (Gemini's summary is already lossy; re-summarizing would dilute signal).

**Google Doc workflow:**
1. Create a new Doc inside `Workcall/Call Transcripts/notes/` in Drive.
2. Paste the Gemini summary.
3. Add two lines anywhere: `Workstream: SI RCA` and `Meeting Type: internal-sync`.
4. Close the doc. Within 30 min the analyzer fetches via Drive API, files to `Analyzed/`, and moves the `.gdoc` to `notes/_Processed/<YYYY-MM>/`.

**Plain `.txt` fallback** — drop a `.txt` with YAML frontmatter directly into `notes/`:

```yaml
---
meeting_date: 2026-04-28
workstream: SI RCA
meeting_type: internal-sync
source: gemini-summary
---

[Gemini summary body]
```

**Drive API setup (one-time):**
1. Create a Google Cloud project, enable the Drive API, create OAuth 2.0 Desktop credentials.
2. Save the downloaded JSON to `~/.config/transcript-analyzer/google-credentials.json`.
3. Bootstrap once: `python -m analyzer.drive_client` — a browser opens for consent; the token saves to `~/.config/transcript-analyzer/google-token.json` and refreshes silently thereafter.

## Analyzed/ folder layout

```
Analyzed/
├── [PROGRAM REFERENCE].md                                          ← pipeline-maintained program knowledge base
├── 2026-05-14T09-30-00 - RCA Weekly - 2026-05-14 [ANALYZED].txt
├── 2026-05-14T09-30-00 - RCA Weekly - 2026-05-14 [SHAREABLE].txt
├── 2026-06-08T10-00-00 - Ways of Working - 2026-06-08 [ANALYZED].md   ← document analysis
├── 2026-05-14T18-30-00 - Daily Pulse - 2026-05-14 [DAILY PULSE].md
├── 2026-05-16T17-00-00 - Slack Delta - Week of 2026-05-12 [SLACK DELTA].md
├── 2026-06-08T09-00-00 - Career Trajectory - 2026-06-08 [CAREER TRAJECTORY].md
└── _Archive/
    └── 2026-05/
        └── (archived [ANALYZED] files after daily/weekly synthesis)
```

`[ANALYZED]` files are archived to `_Archive/YYYY-MM/` when **daily/weekly** synthesis runs (the `career` review keeps its inputs). Synthesis outputs and `[PROGRAM REFERENCE].md` stay in the root permanently — they are never archived.

## Scheduled runs (launchd)

```bash
chmod +x bin/analyze.sh
cp examples/com.bradgross.transcript-analyzer.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.bradgross.transcript-analyzer.plist
```

Schedules a run every 30 min. **This is the work machine's setup** — the personal machine runs on demand via the UI and keeps its launchd agent disabled (`…transcript-analyzer.plist.disabled`); re-enable with `launchctl bootstrap gui/$(id -u) <plist>` after restoring the name. The wrapper at `bin/analyze.sh` fires a macOS notification only when something happened. Logs:

- `~/Library/Logs/transcript-analyzer.log` — rolling history
- `~/Library/Logs/transcript-analyzer-last.log` — most recent run only
- `~/Library/Logs/transcript-analyzer-launchd.log` — launchd bookkeeping

```bash
launchctl start com.bradgross.transcript-analyzer   # force a run now
launchctl unload ~/Library/LaunchAgents/com.bradgross.transcript-analyzer.plist  # pause
```

> **Do not put this repo inside iCloud Drive.** macOS TCC blocks launchd-spawned processes from executing files in iCloud paths. Keep it under `~/code/`.

## Cost dashboard

```bash
jq '[.[] | .cost_usd] | add' .processed.json
```

Cost on the `claude-cli` backend is informational only — the Claude Code seat covers it.

## Configuration reference

All settings in `~/.config/transcript-analyzer/.env`. Key overrides:

```bash
DRIVE_BASE=~/Library/CloudStorage/GoogleDrive-brad@bradgross.org/My Drive/Workcall  # this machine's Drive root
ROUTING_PROFILE=personal        # "work" (DAILY/STANDUP/SOLUTION/EXEC) or "personal" (B4/A3)
BACKEND=claude-cli              # or "api" for legacy direct-API path
CLAUDE_BIN=/usr/local/bin/claude  # absolute path required under launchd
SHAREABLE_PASS=false            # true on work (sharing); false on personal (keeps sensitive content)
MODEL_EXEC=claude-opus-4-7      # per-category overrides
MODEL_B4=claude-opus-4-8        # personal: latest Opus for the political/career read
MODEL_OVERRIDE=claude-opus-4-7  # wins over per-category defaults for one run
EFFORT=high                     # low | medium | high | max (max is Opus-only)
```

> The example above shows a **personal** machine's `.env`. On the work machine, set `DRIVE_BASE` to the `…@salesforce.com` path, `ROUTING_PROFILE=work`, and `SHAREABLE_PASS=true`.

## File layout

```
transcript-analyzer/
├── analyzer/
│   ├── __main__.py          # entry point: dispatches to main, synthesize, or ui
│   ├── main.py              # notes + document + transcript pipelines
│   ├── synthesize.py        # D1/D2/D3 synthesis (+ archive for daily/weekly)
│   ├── ui.py                # Flask web dashboard (python -m analyzer ui)
│   ├── config.py            # .env loading, DRIVE_BASE, model tiering
│   ├── router.py            # Haiku classifier; work/personal routing profiles
│   ├── redactor.py          # [SHAREABLE] redaction pass
│   ├── filesystem.py        # list, move-to-_Processed, fuzzy backstop, PDF read, program reference
│   ├── manifest.py          # .processed.json + cost calc
│   ├── prompts.py           # parse PromptLibrary.md, build frontmatter spec, default DOCUMENT prompt
│   ├── filing.py            # output filename convention
│   ├── notes_intake.py      # Gemini-summary notes → Analyzed/
│   ├── drive_client.py      # OAuth + Drive API for .gdoc body fetch
│   ├── claude_cli.py        # claude -p backend (work seat)
│   └── anthropic_client.py  # direct API backend (legacy/fallback)
├── bin/
│   └── analyze.sh           # launchd wrapper (notifications)
├── docs/
│   └── prompt_starters.md   # canonical prompt bodies to paste into PromptLibrary.md
├── .env.example
├── requirements.txt
└── README.md
```
