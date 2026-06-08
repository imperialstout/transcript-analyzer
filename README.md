# Transcript Analyzer

An automated tool that reads your meeting transcripts and notes, runs them through Claude AI, and produces structured summaries, shareable briefs, and career-level synthesis — all organized in your Google Drive.

---

## What this tool does

Every time it runs, it looks at your `Call Transcripts/` folder in a local drive that is also connected to Google Drive and:

1. **Analyzes transcripts** — classifies each meeting (standup? executive? solution design?), runs the matching AI prompt, and writes a structured `[ANALYZED]` file.
2. **Processes documents** — PDFs, DOCX, and Markdown files you drop in `docs/` get analyzed and their key facts merged into a living `[PROGRAM REFERENCE]` knowledge base.
3. **Handles Gemini notes** — meeting summaries from Google Meet / Gemini get filed without re-analyzing (they're already summarized).
4. **Synthesizes across meetings** — a separate command bundles the day's or week's analyses into a Daily Pulse or Slack Delta, or bundles everything into a Career Trajectory review.

Outputs land in `Analyzed/` alongside the originals. Nothing is deleted — sources move to `_Processed/` after handling.

---

## Before you start: two machines, one codebase

This tool is designed to run on **two different machines** with slightly different behavior, controlled entirely by a config file (`.env`) on each machine:

| | **Work machine** | **Personal machine** |
|---|---|---|
| Google Drive account | `…@salesforce.com` | `…@your personal account` |
| Meeting categories | `DAILY` / `STANDUP` / `SOLUTION` / `EXEC` | `B4` (Political) / `A3` (Career) |
| Shareable pass | On — produces a `[SHAREABLE]` version for leads | Off — keeps sensitive content private |
| Scheduling | Runs automatically every 30 min | Runs on demand via the UI |
| Primary use | Per-meeting analysis + daily/weekly synthesis | Career Trajectory synthesis |

Both machines run the same code. The `.env` file is what makes each machine behave differently.

---

## Part 1: Setting up your Mac (first time only)

### Step 1 — Open Terminal

Terminal is the app you'll use to type commands. It's already on your Mac.

1. Press **Command + Space** to open Spotlight.
2. Type `Terminal` and press **Enter**.
3. A black or white window with a blinking cursor appears. That's Terminal.

You'll type commands here. After typing each command, press **Enter** to run it. Output (results, errors) prints below your command.

> **Tip:** Lines that start with `#` in the instructions below are comments — explanations for you to read, not commands to type.

---

### Step 2 — Check that Python is installed

In Terminal, type:

```bash
python3 --version
```

You should see something like `Python 3.11.9`. If you get `command not found`, install Python:

1. Go to [python.org/downloads](https://python.org/downloads) and download Python 3.11 or newer.
2. Run the installer (it's a standard `.pkg` file — double-click and follow the prompts).
3. Reopen Terminal and run `python3 --version` again to confirm.

---

### Step 3 — Check that Claude Code CLI is installed

This tool calls Claude AI via the Claude Code command-line program. Test it:

```bash
claude --version
```

If you see a version number, you're good. If you see `command not found`, install Claude Code:

```bash
npm install -g @anthropic-ai/claude-code
```

If `npm` itself isn't found, install Node.js first from [nodejs.org](https://nodejs.org) (download the LTS version, run the installer), then try again.

> On the work machine, Claude Code bills to your Salesforce seat — you do not need a personal Anthropic API key for normal operation.

---

### Step 4 — Get the code onto your Mac

The code lives on GitHub. You'll copy ("clone") it to your Mac. First check if `git` is available:

```bash
git --version
```

If prompted to install Xcode Command Line Tools, click **Install** and wait for it to finish.

Then clone the repo into a `code` folder in your home directory:

```bash
# Create the ~/code folder if it doesn't exist
mkdir -p ~/code

# Copy the repository
git clone https://github.com/YOUR_ORG/transcript-analyzer ~/code/transcript-analyzer

# Move into the folder
cd ~/code/transcript-analyzer
```

> **Important:** Keep this repo under `~/code/`, not inside iCloud Drive or Desktop. macOS blocks automated (scheduled) runs from iCloud-synced paths.

---

### Steps 5 & 6 — Run the setup script

The repo includes a setup script that handles everything remaining in one go:

```bash
bash ~/code/transcript-analyzer/bin/setup.sh
```

It will:
1. Create the Python virtual environment at `~/.venvs/transcript-analyzer`
2. Install all Python dependencies
3. Create `~/.config/transcript-analyzer/.env` from the example template
4. Auto-detect your Google Drive path (or ask you to paste it)
5. Create the full `Call Transcripts/` and `Analyzed/` folder structure in your Drive
6. Copy the starter content files (`PromptLibrary.md`, `Program_Context_Brief.md`, `04_people_rolodex.md`, `05_vocabulary.md`) into your Drive — skipping any that already exist
7. Verify your Claude Code seat works

When it finishes, it prints the two commands you need to get started.

> **If the script fails** (proxy issues, permissions, etc.), the manual steps are:
> ```bash
> python3 -m venv ~/.venvs/transcript-analyzer
> source ~/.venvs/transcript-analyzer/bin/activate
> pip install -r ~/code/transcript-analyzer/requirements.txt
> mkdir -p ~/.config/transcript-analyzer
> cp ~/code/transcript-analyzer/.env.example ~/.config/transcript-analyzer/.env
> open -e ~/.config/transcript-analyzer/.env   # set DRIVE_BASE to your Workcall path
> ```

---

### Step 7 — Google Drive API access (Salesforce machines: skip this step)

> **Salesforce work machines cannot create Google Cloud API keys** due to IT restrictions. Skip this step entirely.
>
> The Drive API is only needed to fetch the body of `.gdoc` shortcut files (Gemini meeting summaries saved as Google Docs). Without it, the notes intake pipeline falls back gracefully — `.gdoc` files are skipped, but plain `.txt` notes and full transcript files work normally. If you want to use the Gemini notes workflow, drop the summary as a `.txt` file with YAML frontmatter instead (see **Part 5** below).
>
> On a **personal machine** where you can create API credentials, the setup is: create a Google Cloud project, enable the Drive API, create an OAuth 2.0 Desktop credential, save the downloaded JSON to `~/.config/transcript-analyzer/google-credentials.json`, then run `python -m analyzer.drive_client` once to complete the browser consent flow.

---

## Part 2: Using the tool day to day

### The quickest path: web dashboard

The dashboard is the easiest way to use the tool. Open Terminal and type:

```bash
source ~/.venvs/transcript-analyzer/bin/activate
python -m analyzer ui
```

Your browser opens to `http://localhost:7070` automatically. Keep the Terminal window open while you use it — closing Terminal stops the server.

**To save yourself two commands every time**, add a shortcut to your shell config:

```bash
# Open your shell config in a text editor
open -e ~/.zshrc
```

Add this line at the end of the file:

```bash
alias ta="source ~/.venvs/transcript-analyzer/bin/activate && python -m analyzer ui"
```

Save and close TextEdit. Then run:

```bash
source ~/.zshrc
```

From now on, just type `ta` in any Terminal window to launch the dashboard.

---

### Dashboard features

- **Stats strip** at the top: total files processed, transcript count, notes count, cumulative cost.
- **File table**: every analyzed file with its category badge (EXEC, SOLUTION, STANDUP, DAILY, B4, A3…), the model used, cost, and whether a shareable version exists.
- **Synthesis buttons**: Daily Pulse, Weekly Slack Delta, and Career Trajectory — run synthesis right in the browser with live output.
- **Settings tab**: configure the shared meeting files URL, rolodex/vocabulary paths, shareable toggle, backend, and model override — no `.env` editing required.
- **Content file shortcuts**: one-click to open PromptLibrary, Context Brief, Rolodex, and Vocabulary in your default editor.
- **Open launchd log**: shortcut to the scheduler's log file for troubleshooting.

---

### Running analysis manually

If you prefer the command line (or need to run it outside the UI):

```bash
source ~/.venvs/transcript-analyzer/bin/activate
python -m analyzer
```

Each run processes all three pipelines in sequence:
1. **Notes intake** — Gemini summaries in `Call Transcripts/notes/`
2. **Document pipeline** — files in `Call Transcripts/docs/`
3. **Transcript pass** — `.txt` and `.md` files at the root of `Call Transcripts/`

Files already processed (tracked in `.processed.json`) are skipped automatically.

When it finishes, you'll see a summary line like:
```
All done. 3 succeeded, 0 failed. Total cost: $0.12.
```

---

### Running synthesis

Synthesis bundles your analyzed files into a higher-level summary. Run it after your day's transcripts are processed:

```bash
source ~/.venvs/transcript-analyzer/bin/activate

# D1 — Daily Pulse: bundles today's [ANALYZED] files into a ~250 word pulse
python -m analyzer synthesize --mode daily

# D2 — Weekly Slack Delta: bundles this week's files into a paste-ready Slack update
python -m analyzer synthesize --mode weekly

# D3 — Career Trajectory: bundles ALL current analyses into a strategic career review
python -m analyzer synthesize --mode career
```

Or use the buttons in the dashboard — same result.

---

## Part 3: Folder and file guide

### Where your content lives (Google Drive)

All the *content you tune* — prompts, context briefs, rolodex — lives in your **Google Drive `Workcall/` folder**, not in the code repo. That means editing prompts is a Drive operation (open the file in Docs, edit, save), not a code change.

| Drive file | What it is | When to edit it |
|---|---|---|
| `PromptLibrary.md` | The AI prompts used for each meeting category. Each prompt starts with `### CATEGORY.` and contains a fenced code block. | When you want to change how a meeting type is analyzed — what to look for, what format to use, what to emphasize. |
| `dailyAndWeeklyPrompts.md` | The synthesis prompts for D1 (Daily Pulse), D2 (Slack Delta), D3 (Career Trajectory). | When you want to change what the synthesis outputs — different structure, different focus, different audience. |
| `Program_Context_Brief.md` | Program-wide context injected into *every* analysis run as background knowledge — who the stakeholders are, what the program is doing, key terminology. | When the program structure changes: new workstreams, new stakeholders, major decisions. |
| `04_people_rolodex.md` | Index of named individuals, including variants of how speech-to-text mangles their names. Optional but helpful. | When someone new joins or their name keeps getting mangled by the transcription tool. |
| `05_vocabulary.md` | Canonical spellings of product names, acronyms, and terms. Optional. | When the transcription tool consistently misspells something important. |

### The `Call Transcripts/` folder (in Drive)

This is the **inbox**. You drop files here; the analyzer picks them up.

```
Call Transcripts/
├── ← drop .txt or .md transcripts here (root level only, not in subfolders)
├── notes/         ← drop Gemini-summary .gdoc shortcuts or .txt notes here
│   └── _Processed/
│       └── 2026-05/   ← notes move here after processing
├── docs/          ← drop PDFs, DOCX, MD, or TXT documents here
│   └── _Processed/
│       └── 2026-05/   ← documents move here after processing
└── _Processed/
    └── 2026-05/   ← processed transcripts move here
```

**Root level** (`Call Transcripts/*.txt`, `*.md`) — transcripts from recorded meetings. The filename doesn't matter much; the analyzer reads the content to classify and date the meeting.

**`notes/`** — Gemini meeting summaries (for meetings you didn't record, or where recording failed). The analyzer files these with no AI call — Gemini's summary is already processed; re-analyzing it would dilute the signal.

**`docs/`** — strategic documents: program decks, SOWs, architecture docs, process briefs. These get analyzed with an Opus-level prompt and their key facts merged into the `[PROGRAM REFERENCE]` knowledge base.

**`_Processed/` subfolders** — after a file is handled, it moves here. Files are never deleted — they just move. If something goes wrong, the source file stays in place and the next run retries it.

### The `Analyzed/` folder (in Drive)

This is the **output**. The analyzer writes here; you read from here.

```
Analyzed/
├── [PROGRAM REFERENCE].md                  ← living knowledge base, pipeline-maintained
├── 2026-06-08T09-30 - Exec Sync - ... [ANALYZED].txt      ← internal analysis
├── 2026-06-08T09-30 - Exec Sync - ... [SHAREABLE].txt     ← redacted version for leads
├── 2026-06-08T10-00 - Ways of Working ... [ANALYZED].md   ← document analysis
├── 2026-06-08T18-00 - Daily Pulse - ... [DAILY PULSE].md  ← synthesis output
└── _Archive/
    └── 2026-05/
        └── (archived [ANALYZED] files after daily/weekly synthesis runs)
```

**`[ANALYZED]`** — the internal analysis of a meeting or document. Contains structured YAML frontmatter (date, participants, workstream, meeting type) plus a full write-up with political/strategic notes. **Never shared** externally.

**`[SHAREABLE]`** — a redacted version of `[ANALYZED]`, with internal politics and career-path notes stripped. Safe to paste into a shared doc or send to leads. Only generated when `SHAREABLE_PASS=true` (work machine).

**`[DAILY PULSE]`** / **`[SLACK DELTA]`** / **`[CAREER TRAJECTORY]`** — synthesis outputs. These stay in the `Analyzed/` root permanently — they're used as continuity context for the *next* synthesis run of the same type.

**`[PROGRAM REFERENCE].md`** — the pipeline-maintained program knowledge base. Every document you process in `docs/` contributes durable facts here: team structure, milestones, process decisions, capability ownership. It's injected as background context into every synthesis run. You don't edit this directly — the pipeline maintains it.

**`_Archive/`** — after a daily or weekly synthesis runs, the `[ANALYZED]` files it bundled move here. This keeps the `Analyzed/` root clean while preserving history. Career synthesis does *not* archive — it needs all analyses in place for a cumulative view.

### The code repo (`~/code/transcript-analyzer/`)

You shouldn't need to edit most of this, but here's what each piece does:

```
transcript-analyzer/
├── analyzer/
│   ├── __main__.py        # Entry point — routes `python -m analyzer [command]` to the right module
│   ├── main.py            # Runs the three pipelines: notes, documents, transcripts
│   ├── synthesize.py      # Handles D1/D2/D3 synthesis and archiving
│   ├── ui.py              # Flask web dashboard
│   ├── config.py          # Reads .env, sets model tiers, Drive paths
│   ├── router.py          # Classifies each transcript into a category (Haiku call)
│   ├── redactor.py        # Produces the [SHAREABLE] version
│   ├── filesystem.py      # File ops: list, move, read, fuzzy matching, program reference merge
│   ├── manifest.py        # Tracks what's been processed in .processed.json
│   ├── prompts.py         # Parses PromptLibrary.md, builds the system prompt prefix
│   ├── filing.py          # Generates output filenames from meeting metadata
│   ├── notes_intake.py    # Handles Gemini-summary notes → Analyzed/
│   ├── drive_client.py    # Google Drive OAuth + API for .gdoc body fetching
│   ├── claude_cli.py      # Backend: shells out to `claude -p` (Claude Code seat)
│   └── anthropic_client.py  # Backend: direct Anthropic API (legacy fallback)
├── bin/
│   └── analyze.sh         # Shell script launchd calls — runs analyzer, fires macOS notification
├── workcall-templates/    # ← Starting-point files to copy into your Drive Workcall/ folder
│   ├── PromptLibrary.md           # Analysis prompts: DAILY/STANDUP/SOLUTION/EXEC + B4/A3 + REDACT
│   ├── dailyAndWeeklyPrompts.md   # Synthesis prompts: D1 Daily Pulse, D2 Slack Delta, D3 Career
│   ├── Program_Context_Brief.md   # Fillable template for program context, stakeholders, workstreams
│   ├── 04_people_rolodex.md       # Template for tracking named individuals across the program
│   └── 05_vocabulary.md           # Template for canonical spellings fed to the analyzer
├── docs/
│   └── prompt_starters.md # Reference prompt bodies to paste into PromptLibrary.md
├── .env.example           # Template for ~/.config/transcript-analyzer/.env
├── requirements.txt       # Python package dependencies
└── README.md              # This file
```

---

## Part 3b: Setting up your Drive content files (first time)

The analyzer reads four files from your `Workcall/` folder in Drive. None of them exist yet — you need to create them. The repo includes ready-to-use templates in `workcall-templates/`.

### Step 1 — Copy the templates into Drive

In Finder, open `~/code/transcript-analyzer/workcall-templates/`. Copy all four files into your `Workcall/` folder in Google Drive (the same folder you set as `DRIVE_BASE` in your `.env`).

```
Workcall/
├── PromptLibrary.md            ← copy from workcall-templates/
├── Program_Context_Brief.md    ← copy from workcall-templates/
├── 04_people_rolodex.md        ← copy from workcall-templates/
└── 05_vocabulary.md            ← copy from workcall-templates/
```

### Step 2 — Fill in `Program_Context_Brief.md`

This is the most important file. Open it in your text editor and fill in:

- Your program name and client
- Key client stakeholders (names, roles, what they care about)
- Active workstreams and their leads
- Your delivery team
- Key decisions and constraints the model should always know about
- Program-level risks
- Key acronyms and terms

This file is injected into every analysis run as background context. The better it is, the more specific and useful every analysis will be. You don't need to be exhaustive — a one-page brief is better than a five-page one that never gets updated.

### Step 3 — Customize `PromptLibrary.md` (optional at first)

The template prompts in `PromptLibrary.md` work out of the box for most programs — DAILY, STANDUP, SOLUTION, and EXEC cover the four main meeting types. You can run the analyzer with them as-is and refine later.

When you do customize: each prompt is a fenced code block under a `### KEY.` heading. The framing and frontmatter are injected automatically — you only write the analysis structure. The `## Private read — internal only` section at the end of each prompt is where political and positioning signal goes; it's stripped by the `REDACT` pass for the shareable version.

### Step 4 — Populate `04_people_rolodex.md` and `05_vocabulary.md` over time

These are optional and build up incrementally. Start empty (the templates have placeholder entries) and add:

- **Rolodex:** Add an entry for each person who recurs across your meetings. The "Name variants" field is where you note how your transcription tool spells their name.
- **Vocabulary:** Add product names, acronyms, and terms your transcription tool consistently misspells. One term per line inside the fenced blocks.

---

## Part 3c: Running political and career analysis on a work machine

Nothing in the tool prevents this — it's a config choice. If you want the B4 (Political Read) and A3 (1:1/Career) prompts alongside your work analysis, here's what to set up.

### Why you'd want this

The work profile (`DAILY`/`STANDUP`/`SOLUTION`/`EXEC`) captures *what happened* in meetings. The personal profile adds a second lens: *what does this mean for me politically and for my career*. You can run both on a work machine — you just need to tell the tool which profile to use and make sure sensitive output never ends up in a shareable file.

### Step 1 — Turn off the shareable pass

This is the critical one. The B4 political read contains candid analysis that should never land in a `[SHAREABLE]` file. Open `~/.config/transcript-analyzer/.env` and set:

```
SHAREABLE_PASS=false
```

Do this before you run the personal profile for the first time.

### Step 2 — Switch the routing profile

```
ROUTING_PROFILE=personal
```

This switches the classifier to route meetings into `B4` (Political Read) or `A3` (1:1/Career) instead of `DAILY`/`STANDUP`/`SOLUTION`/`EXEC`. You can switch back and forth between `work` and `personal` at any time — it's a one-line change in `.env`.

### Step 3 — Verify the prompts are in your Drive

`bin/setup.sh` already copied `PromptLibrary.md` to your Drive. Open it and confirm the `### B4.` and `### A3.` sections are present at the bottom. They were included in the template — if you customized the file heavily, you may need to add them manually. The prompts are at the bottom of `workcall-templates/PromptLibrary.md` in the repo.

### Step 4 — Add the synthesis prompts

Career synthesis (`python -m analyzer synthesize --mode career`) requires a `### D3.` block in `Workcall/dailyAndWeeklyPrompts.md`. If `setup.sh` copied that file for you, it's already there. If not, copy `workcall-templates/dailyAndWeeklyPrompts.md` into your Drive `Workcall/` folder — it has all three synthesis prompts (D1 Daily Pulse, D2 Slack Delta, D3 Career Trajectory).

### Running it

```bash
source ~/.venvs/transcript-analyzer/bin/activate
python -m analyzer          # analyzes transcripts through the B4/A3 lens
python -m analyzer synthesize --mode career   # career trajectory synthesis
```

Or use the dashboard — the synthesis buttons work the same regardless of profile.

> **Model note:** `B4` defaults to `claude-opus-4-7`, which is available on Salesforce work seats. The personal machine uses `claude-opus-4-8` (a newer model not available on all seats) — if you want to match that, check whether your seat has it with `bin/phase0_check.sh`, then add `MODEL_B4=claude-opus-4-8` to your `.env`.

---

## Part 4: Scheduled runs (work machine only)

On the work machine, you can have the analyzer run automatically every 30 minutes in the background — no Terminal required after setup.

```bash
# Make the run script executable
chmod +x ~/code/transcript-analyzer/bin/analyze.sh

# Copy the scheduler config to the right place
cp ~/code/transcript-analyzer/examples/com.transcript-analyzer.plist \
   ~/Library/LaunchAgents/

# Register it with macOS
launchctl load -w ~/Library/LaunchAgents/com.transcript-analyzer.plist
```

From now on, macOS launches the analyzer every 30 minutes. A notification fires only when something was actually processed — silent runs don't interrupt you.

**Useful scheduler commands:**

```bash
# Force a run right now (don't wait for the 30-min tick)
launchctl start com.transcript-analyzer

# Pause the scheduler
launchctl unload ~/Library/LaunchAgents/com.transcript-analyzer.plist

# Resume it
launchctl load -w ~/Library/LaunchAgents/com.transcript-analyzer.plist
```

**Log files** (if something goes wrong):

```bash
# Open the rolling log in Terminal
cat ~/Library/Logs/transcript-analyzer.log

# Or just the most recent run
cat ~/Library/Logs/transcript-analyzer-last.log
```

> **Personal machine:** The scheduler is intentionally disabled. Use the `ta` alias and the web dashboard instead.

---

## Part 5: Adding meeting notes without a recording

For meetings where you weren't present, or the recording failed, use Gemini summaries:

**Via Google Docs (recommended):**
1. In Google Drive, create a new Doc inside `Workcall/Call Transcripts/notes/`.
2. Paste the Gemini summary into the doc.
3. Add two lines anywhere in the doc:
   ```
   Workstream: SI RCA
   Meeting Type: internal-sync
   ```
4. Close the doc. Within 30 minutes (or on your next manual run), the analyzer fetches it via the Drive API, writes to `Analyzed/`, and moves the `.gdoc` to `notes/_Processed/`.

**Via plain text (fallback):**
Create a `.txt` file with this header and drop it in `notes/`:

```
---
meeting_date: 2026-06-08
workstream: SI RCA
meeting_type: internal-sync
source: gemini-summary
---

[Paste Gemini summary here]
```

---

## Part 6: Reference

### Model tiers

Different meeting types use different AI models — more powerful (and more expensive) for high-stakes meetings, cheaper for routine syncs.

| Category | Model | Why |
|---|---|---|
| `EXEC` | `claude-opus-4-7` | Highest-stakes; executive dynamics |
| `DOCUMENT` | `claude-opus-4-7` | Dense strategic content |
| `SOLUTION` / `STANDUP` / `DAILY` | `claude-sonnet-4-6` | Technical design + internal syncs |
| `B4` (Political Read) | `claude-opus-4-7` / `opus-4-8` on personal | Political dynamics; strategic reasoning |
| `A3` (1:1/Career) | `claude-sonnet-4-6` | People and career notes |
| Classifier | `claude-haiku-4-5-20251001` | Routing only; cheapest available |
| Redaction | `claude-sonnet-4-6` | Post-processing structured text |
| Synthesis D1/D2 | `claude-sonnet-4-6` | Recap; inputs already structured |
| Synthesis D3 (career) | B4 model (`opus-4-8` on personal) | Strategic trajectory reasoning |

Override any model via `.env`: `MODEL_EXEC=claude-opus-4-7`, `MODEL_B4=claude-opus-4-8`, etc.

For a single one-off override run:

```bash
MODEL_OVERRIDE=claude-opus-4-7 python -m analyzer
```

### Full `.env` reference

All settings live in `~/.config/transcript-analyzer/.env`:

```bash
# Which Google Drive root to use (REQUIRED — set to this machine's path)
DRIVE_BASE=~/Library/CloudStorage/GoogleDrive-you@yourcompany.com/My Drive/Workcall

# "work" = DAILY/STANDUP/SOLUTION/EXEC routing; "personal" = B4/A3 routing
ROUTING_PROFILE=work

# "claude-cli" bills to Claude Code seat; "api" uses ANTHROPIC_API_KEY directly
BACKEND=claude-cli

# Required for launchd (scheduled runs) — absolute path, no ~ expansion
CLAUDE_BIN=/usr/local/bin/claude

# true = generate [SHAREABLE] redacted siblings; false = skip (personal machine)
SHAREABLE_PASS=true

# Per-category model overrides
MODEL_EXEC=claude-opus-4-7
MODEL_B4=claude-opus-4-8      # personal only — latest Opus available on that seat

# Wins over all per-category defaults for the duration of one run
MODEL_OVERRIDE=claude-opus-4-7

# Reasoning effort: low | medium | high | max (max is Opus-only)
EFFORT=high
```

> On the personal machine: set `DRIVE_BASE` to `…@yourpersonaldomain.com`, `ROUTING_PROFILE=personal`, `SHAREABLE_PASS=false`, and add `MODEL_B4=claude-opus-4-8`.

### Cost dashboard

```bash
# Total cost across all processed files
jq '[.[] | .cost_usd] | add' ~/code/transcript-analyzer/.processed.json
```

Cost figures are informational only on the `claude-cli` backend — the Claude Code seat covers the actual spend.
