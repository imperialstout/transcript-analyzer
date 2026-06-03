# Transcript Analyzer

Local CLI that runs meeting-transcript analyses through a Claude Code work seat, replacing the chat-based workflow that was crashing on heavy weeks.

## Prereqs

1. **Google Drive for Desktop** running and syncing `Workcall/` under `GoogleDrive-brad.gross@salesforce.com`.
2. **Write transcripts as plain `.txt` or `.md` files** at the root of `Call Transcripts/`. Subfolders are ignored. `.gdoc` shortcuts are handled via Drive API (see Notes intake below).
3. **Python 3.11+** and the **Claude Code CLI** (`claude`) on PATH (or set `CLAUDE_BIN` to an absolute path for launchd).

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
- **Daily Pulse** and **Weekly Slack Delta** buttons — run synthesis in-browser with live output
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
2. Lists `.txt` / `.md` files at the root of `Call Transcripts/`.
3. Skips files already in `.processed.json` or fuzzy-matched against `Analyzed/`.
4. Routes each transcript to a category prompt (`DAILY` / `STANDUP` / `SOLUTION` / `EXEC`) via a cheap Haiku classifier call.
5. Runs the matching prompt via `claude -p` on the Claude Code seat.
6. Writes `[ANALYZED].txt` and a redacted `[SHAREABLE].txt` sibling to `Analyzed/`.
7. Records token usage in `.processed.json` and moves the source to `Call Transcripts/_Processed/<YYYY-MM>/`.

### Daily Pulse and Weekly Slack Delta

After transcripts are analyzed, run synthesis manually at end-of-day or end-of-week:

```bash
# D1 — Daily Pulse: bundles today's [ANALYZED] files, writes a 200-300 word pulse
python -m analyzer synthesize --mode daily

# D2 — Weekly Slack Delta: bundles this week's [ANALYZED] files, writes a paste-ready Slack delta
python -m analyzer synthesize --mode weekly
```

Both commands:
- Bundle the relevant `[ANALYZED]` files (today's or this ISO week's) plus the most recent prior synthesis for continuity context.
- Run on Sonnet (inputs are already structured; these calls are cheap).
- Write the output to `Analyzed/` with a `[DAILY PULSE]` or `[SLACK DELTA]` suffix.
- **Archive** the bundled input files to `Analyzed/_Archive/YYYY-MM/` after a successful write.

The synthesis prompts (D1, D2) live in `Workcall/dailyAndWeeklyPrompts.md` on Drive — edit them there, no code change needed.

### One-off model override

```bash
MODEL_OVERRIDE=claude-opus-4-7 python -m analyzer
```

## Model tiering

| Category | Default model | Rationale |
|---|---|---|
| `EXEC` | `claude-opus-4-7` | Highest-stakes; political read, executive dynamics |
| `SOLUTION` | `claude-sonnet-4-6` | Technical/design sessions; Sonnet handles these well |
| `STANDUP` | `claude-sonnet-4-6` | Internal syncs |
| `DAILY` | `claude-sonnet-4-6` | Daily digests |
| Classifier | `claude-haiku-4-5-20251001` | Routing only; cheap |
| Redaction | `claude-sonnet-4-6` | Post-process pass over structured text |
| Synthesis (D1/D2) | `claude-sonnet-4-6` | Inputs are already structured |

Override any per-category model via `.env` (`MODEL_EXEC`, `MODEL_SOLUTION`, etc.).

## Content lives in Drive, not the repo

Runtime files are at `~/Library/CloudStorage/GoogleDrive-brad.gross@salesforce.com/My Drive/Workcall/`:

| File | Purpose |
|---|---|
| `PromptLibrary.md` | Four routed category prompts (`### DAILY.` / `### STANDUP.` / `### SOLUTION.` / `### EXEC.`) plus `### REDACT.`. Parsed by `### KEY.` headings + fenced blocks. |
| `dailyAndWeeklyPrompts.md` | D1 Daily Pulse and D2 Weekly Slack Delta prompts (`### D1.` / `### D2.`). Used by `python -m analyzer synthesize`. |
| `Program_Context_Brief.md` | Program-wide context injected as the system prefix on every analysis run. |
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
├── 2026-05-14T09-30-00 - RCA Weekly - 2026-05-14 [ANALYZED].txt
├── 2026-05-14T09-30-00 - RCA Weekly - 2026-05-14 [SHAREABLE].txt
├── 2026-05-14T18-30-00 - Daily Pulse - 2026-05-14 [DAILY PULSE].md
├── 2026-05-16T17-00-00 - Slack Delta - Week of 2026-05-12 [SLACK DELTA].md
└── _Archive/
    └── 2026-05/
        └── (archived [ANALYZED] files after synthesis)
```

`[ANALYZED]` files are archived to `_Archive/YYYY-MM/` when synthesis runs. `[SHAREABLE]` siblings are archived alongside them. Synthesis outputs (`[DAILY PULSE]`, `[SLACK DELTA]`) stay in the root as context for future synthesis runs.

## Scheduled runs (launchd)

```bash
chmod +x bin/analyze.sh
cp examples/com.bradgross.transcript-analyzer.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.bradgross.transcript-analyzer.plist
```

Schedules a run every 30 min. The wrapper at `bin/analyze.sh` fires a macOS notification only when something happened. Logs:

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
BACKEND=claude-cli              # or "api" for legacy direct-API path
CLAUDE_BIN=/usr/local/bin/claude  # absolute path required under launchd
SHAREABLE_PASS=true             # set false to skip [SHAREABLE] output
MODEL_EXEC=claude-opus-4-7      # per-category overrides
MODEL_SOLUTION=claude-sonnet-4-6
MODEL_OVERRIDE=claude-opus-4-7  # wins over per-category defaults for one run
EFFORT=high                     # low | medium | high | max (max is Opus-only)
```

## File layout

```
transcript-analyzer/
├── analyzer/
│   ├── __main__.py          # entry point: dispatches to main, synthesize, or ui
│   ├── main.py              # transcript + notes intake pipeline
│   ├── synthesize.py        # D1/D2 synthesis + archive
│   ├── ui.py                # Flask web dashboard (python -m analyzer ui)
│   ├── config.py            # .env loading + model tiering
│   ├── router.py            # Haiku-based transcript classifier
│   ├── redactor.py          # [SHAREABLE] redaction pass
│   ├── filesystem.py        # list, move-to-_Processed, fuzzy backstop
│   ├── manifest.py          # .processed.json + cost calc
│   ├── prompts.py           # parse PromptLibrary.md, build frontmatter spec
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
