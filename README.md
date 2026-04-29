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

## Configuration

Everything is in `.env`. See `.env.example` for the full list. Highlights:

- `MODEL_A2`, `MODEL_A1`, etc. — per-prompt model choice. Defaults: high-stakes prompts (A1, B2, B4) use Opus 4.7; the rest use Sonnet 4.6.
- `MODEL_OVERRIDE=claude-opus-4-7 python -m analyzer` — force a specific model for a one-off rerun.
- `EFFORT=high` — adaptive thinking effort. `low | medium | high | max` (max is Opus-only).

## Cost dashboard

```bash
jq '[.[] | .cost_usd] | add' .processed.json
```

## Scheduled runs (launchd + macOS notifications)

A wrapper script at `bin/analyze.sh` runs the analyzer and fires a macOS notification banner only when something actually happened (analyses written, or failures). Silent runs stay silent.

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
│   ├── main.py              # CLI flow
│   ├── config.py            # .env loading + model tiering
│   ├── filesystem.py        # list, move-to-_Processed, fuzzy backstop
│   ├── manifest.py          # .processed.json + cost calc
│   ├── prompts.py           # parse PromptLibrary.md, build frontmatter spec
│   ├── filing.py            # output filename per convention
│   └── anthropic_client.py  # streaming, prompt caching, adaptive thinking
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

## Plan reference

Full design rationale (model tiering, marking strategy, frontmatter taxonomy, deferred features) lives at `~/.claude/plans/have-a-little-project-jazzy-star.md`.
