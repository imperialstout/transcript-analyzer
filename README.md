# Transcript Analyzer

Local CLI that runs Anthropic-API analyses against meeting transcripts in Google Drive, replacing the chat-based workflow that was crashing on heavy weeks.

## Prereqs

1. **Google Drive for Desktop** running and syncing both `Workcall/Call Transcripts/` and `Workcall/Analyzed/`.
2. **Update the Zap** to write transcripts as plain `.txt` files (not Google Docs `.gdoc`). The script ignores `.gdoc` and processes only `.txt`.
3. **Python 3.11+** (tested on 3.14).

## Setup

```bash
cd transcript-analyzer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# .env lives OUTSIDE the repo — this repo path is iCloud-synced and would
# back up your API key. Store the .env at ~/.config/transcript-analyzer/.env:
mkdir -p ~/.config/transcript-analyzer
cp .env.example ~/.config/transcript-analyzer/.env
# Then edit that file and set ANTHROPIC_API_KEY (and any path overrides).
```

If you accidentally created a repo-level `.env`, move it:

```bash
mv .env ~/.config/transcript-analyzer/.env
```

The script will warn at startup if it ever loads `.env` from the repo path.

## Usage

```bash
source .venv/bin/activate
python -m analyzer
```

The script:

1. Lists `.txt` files at the root of `Call Transcripts/` (subfolders ignored).
2. Skips ones already in `.processed.json`, or fuzzy-matched against an existing `Analyzed/` filename.
3. Runs the configured prompt (default `A2`) via `claude-sonnet-4-6`, with the full Program Context Brief in a cached system prompt (~90% savings on the brief across the batch).
4. Writes the analysis to `Analyzed/` per the strict naming convention.
5. Records token usage and cost in `.processed.json`.
6. Moves the source from `Call Transcripts/` to `Call Transcripts/_Processed/<YYYY-MM>/`.

A failed analysis leaves the source file in place and does not record a manifest entry, so the next run picks it up again.

## Gemini-summary notes intake

Some weeks include meetings that arrive as Gemini-generated meeting summaries instead of full Plaud transcripts (you weren't present, recording failed, the meeting was async, etc.). The analyzer files them through the same pipeline as transcripts but **skips the LLM call** — Gemini's summary is already lossy compression; re-summarizing would only dilute nuggets.

**Primary workflow (Google Doc on a locked-down work machine):**

1. In Google Docs (browser), create a new doc inside `Workcall/Call Transcripts/notes/`.
2. Paste the Gemini meeting summary into the doc.
3. Type two lines anywhere in the body:

   ```text
   Workstream: SI RCA
   Meeting Type: internal-sync
   ```

4. Close the doc. Drive syncs a `.gdoc` shortcut to your personal Mac.
5. Within 30 minutes (next launchd run), the analyzer fetches the doc body via the Drive API, parses the Gemini header for date + title, files into `Analyzed/`, and moves the `.gdoc` to `Call Transcripts/notes/_Processed/<YYYY-MM>/`.

The Gemini header (line 1 = `MMM DD, YYYY`, line 2 = title) is parsed automatically; participants are kept inline in the body and the cross-transcript synthesis reads them there.

**Drive API setup (one-time):**

A `.gdoc` in the local Drive cache is a JSON shortcut, not the document body — so the analyzer needs Drive API access to fetch the actual text.

1. Create a Google Cloud project at <https://console.cloud.google.com/> and enable the Google Drive API.
2. Create OAuth 2.0 Client ID credentials (application type: **Desktop app**).
3. Download the JSON and save it to `~/.config/transcript-analyzer/google-credentials.json`.
4. Run the bootstrap once interactively:

   ```bash
   source .venv/bin/activate
   pip install -r requirements.txt
   python -m analyzer.drive_client
   ```

   A browser window opens for consent. On approval, a refresh token is saved to `~/.config/transcript-analyzer/google-token.json`. Subsequent runs (including launchd-driven ones) refresh silently.

If the token is missing or revoked, the analyzer skips `.gdoc` notes with a stderr message and continues with everything else — `.txt` notes and transcripts still process.

**Fallback: hand-written `.txt` with YAML frontmatter**

You can still drop a `.txt` directly into `Call Transcripts/notes/` with frontmatter at the top:

```yaml
---
meeting_date: 2026-04-28
workstream: SI RCA
meeting_type: internal-sync
tags: [escalation]
source: gemini-summary
---

[Gemini summary body below]
```

Required fields: `meeting_date`, `workstream`, `meeting_type`. (`participants` is optional — listed in the body anyway.) Missing or malformed frontmatter leaves the file in place with a stderr message — no data loss. The `source: gemini-summary` marker distinguishes notes from real transcripts in `Analyzed/` for any future weighting by the cross-transcript synthesis prompts.

Notes intake runs first on each invocation, then transcripts. The "All done" summary line combines both pipelines so the macOS notification fires for either.

## Content lives in Drive, not the repo

Two files the analyzer reads at runtime are intentionally **not** stored in this repo:

- `Workcall/PromptLibrary.md` — the prompt library, keyed by `A1`/`A2`/`B1`/etc. Edit this in Google Docs / Drive when you want to tune a prompt; no redeploy needed.
- `Workcall/Program_Context_Brief.md` — the program-wide context that gets cached as a system prompt on every run.

Both paths are configurable via `PROMPT_LIBRARY_PATH` / `CONTEXT_BRIEF_PATH` in `.env`. The repo holds the engine; Drive holds the content you tune.

## Workstream and meeting_type — what to write

These are freeform short tags **you** pick. The analyzer doesn't validate against a taxonomy. They land in the YAML frontmatter of the filed `Analyzed/` output so the weekly cross-transcript synthesis (in Claude.ai) can group meetings by theme.

Pick values you'll reuse across meetings — consistency matters more than precision. Examples:

- `Workstream:` — the program/initiative the meeting belongs to. e.g., `ACD`, `Price Propagation`, `SI RCA`, `Internal`.
- `Meeting Type:` — the shape of the meeting. e.g., `status sync`, `working session`, `escalation`, `1-on-1`, `external`, `decision`.

Keep them short (1-3 words), lowercase or consistently cased. If you're not sure, copy whatever you used for the most similar prior meeting.

## Configuration

Everything is in `.env`. See `.env.example` for the full list. Highlights:

- `MODEL_A2`, `MODEL_A1`, etc. — per-prompt model choice. Defaults: high-stakes prompts (A1, B2, B4) use Opus 4.7; the rest use Sonnet 4.6.
- `MODEL_OVERRIDE=claude-opus-4-7 python -m analyzer` — force a specific model for a one-off rerun.
- `EFFORT=high` — adaptive thinking effort. `low | medium | high | max` (max is Opus-only).
- `NOTES_PATH`, `NOTES_PROCESSED_PATH` — override the Gemini-summary notes intake folders. Defaults: `Call Transcripts/notes/` and `Call Transcripts/notes/_Processed/`.

## Cost dashboard

```bash
jq '[.[] | .cost_usd] | add' .processed.json
```

## Scheduled runs (launchd + macOS notifications)

A wrapper script at `bin/analyze.sh` runs the analyzer and fires a macOS notification banner only when something actually happened (analyses written, or failures). Silent runs stay silent.

> **Important: do not put this repo inside iCloud Drive** (`~/Library/Mobile Documents/com~apple~CloudDocs/...`). macOS TCC blocks launchd-spawned processes from executing files in iCloud paths, so the LaunchAgent will fail with `Operation not permitted`. Keep the working tree under `~/code/` or another non-protected location and use git/GitHub for cross-device sync.

To install on a stock macOS Mac:

```bash
chmod +x bin/analyze.sh
cp examples/com.bradgross.transcript-analyzer.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.bradgross.transcript-analyzer.plist
```

That schedules a run every 30 min. Logs land at:

- `~/Library/Logs/transcript-analyzer.log` — rolling history of every run
- `~/Library/Logs/transcript-analyzer-last.log` — just the most recent run
- `~/Library/Logs/transcript-analyzer-launchd.log` — launchd's own bookkeeping

To pause or remove:

```bash
# Pause (stops scheduled runs, keeps the plist on disk):
launchctl unload ~/Library/LaunchAgents/com.bradgross.transcript-analyzer.plist

# Remove entirely:
launchctl unload ~/Library/LaunchAgents/com.bradgross.transcript-analyzer.plist
rm ~/Library/LaunchAgents/com.bradgross.transcript-analyzer.plist
```

To force a run right now without waiting for the schedule:

```bash
launchctl start com.bradgross.transcript-analyzer
```

The first time the launch agent fires `osascript`, macOS may prompt for notification permission. Allow it once and future runs will banner silently.

## File layout

```text
transcript-analyzer/
├── analyzer/
│   ├── __main__.py          # python -m analyzer entry
│   ├── main.py              # CLI flow (notes intake → transcript pass)
│   ├── config.py            # .env loading + model tiering
│   ├── filesystem.py        # list, move-to-_Processed, fuzzy backstop
│   ├── manifest.py          # .processed.json + cost calc
│   ├── prompts.py           # parse PromptLibrary.md, build frontmatter spec
│   ├── filing.py            # output filename per convention
│   ├── notes_intake.py      # Gemini-summary notes (.gdoc + .txt) → Analyzed/
│   ├── drive_client.py      # OAuth + Drive API for .gdoc body fetch
│   └── anthropic_client.py  # streaming, prompt caching, adaptive thinking
├── bin/
│   └── analyze.sh           # launchd wrapper (notifications)
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

## Plan reference

Full design rationale (model tiering, marking strategy, frontmatter taxonomy, deferred features) lives at `~/.claude/plans/have-a-little-project-jazzy-star.md`.
