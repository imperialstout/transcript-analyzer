# Cross-Repo Operations Reference

This document is written for Claude Code sessions operating in **SiemensRCAGov** or **Siemens3RR**. It describes the intelligence pipeline that runs in `~/code/transcript-analyzer` and what these repos can do with it.

Read this when:
- Brad asks "what's on my plate" or "what do I owe someone"
- You're about to start work in SiemensRCAGov and want current signal for a program
- Brad asks you to update or run the program pulse
- You need to know what's happened in meetings recently (beyond what's in git)

---

## 1. The intelligence pipeline at a glance

```
Meeting recordings / Slack digests / documents
    ↓  python -m analyzer  (runs every 30 min on work machine)
Analyzed/ in Google Drive  →  [ANALYZED].txt, [SHAREABLE].txt, [PROGRAM REFERENCE].md
    ↓  python -m analyzer synthesize --mode daily|weekly
[DAILY PULSE].md, [SLACK DELTA].md  (in Analyzed/)
    ↓  /morning-brief  (Claude Code command, run manually)
~/.cache/transcript-analyzer/brief-state.json  (machine-global act items)
    ↓  bin/brief-state-hook.py  (session-start hook, every project)
"Open from morning brief" block injected into every Claude Code conversation
    ↓  python -m analyzer synthesize --mode program-pulse
~/code/SiemensRCAGov/**/STATUS.md  (Open signals section appended)
```

Analyzed/ path: `~/Library/CloudStorage/GoogleDrive-brad.gross@salesforce.com/My Drive/Workcall/Analyzed/`

---

## 2. brief-state.json — open act items

**Path:** `~/.cache/transcript-analyzer/brief-state.json`

This is the machine-global record of what Brad committed to act on during morning triage. Every Claude Code session on this machine gets a hook-injected summary at startup. You can also query it directly.

### Read pending acts
```bash
python3 -c "
import json
from pathlib import Path
items = json.loads(Path('~/.cache/transcript-analyzer/brief-state.json').expanduser().read_text())
acts = [i for i in items if i.get('type')=='act' and i.get('status')=='pending']
for a in acts:
    prog = a.get('program') or 'unassigned'
    due = f\" [due {a['deadline']}]\" if a.get('deadline') else ''
    print(f\"  [{prog}]{due} {a['text']}\")
    if a.get('notes'): print(f\"    → {a['notes']}\")
"
```

### Read acts for a specific program
```bash
python3 -c "
import json; from pathlib import Path
items = json.loads(Path('~/.cache/transcript-analyzer/brief-state.json').expanduser().read_text())
prog = 'SI-RCA'  # change to DISW-RCA, presales-energy, siemens-general, etc.
acts = [i for i in items if i.get('type')=='act' and i.get('status')=='pending' and i.get('program')==prog]
for a in acts: print(f\"  {a.get('deadline','?')} — {a['text']}\")
"
```

### Or use the Python API directly
```python
import sys; sys.path.insert(0, '/Users/brad.gross/code/transcript-analyzer')
from analyzer.brief_state import pending_acts, pending_by_program

# All open acts
for a in pending_acts():
    print(a['text'], '→', a.get('program'))

# Grouped by program
by_prog = pending_by_program()
for prog, acts in by_prog.items():
    print(prog, len(acts), 'item(s)')
```

### Schema — fields you need to act on

| Field | Type | What it means |
|---|---|---|
| `text` | str | One-line label of what Brad needs to do |
| `type` | str | `act` = Brad owns it; `they-owe` = someone owes Brad; `watch` = monitor; `delegate` = assigned out |
| `status` | str | `pending` = open; `complete` / `deferred` / `expired` / `dropped` = closed |
| `deadline` | str\|null | ISO date or null |
| `owner` | str | `"Brad"` for act/watch; person name for delegate/they-owe |
| `program` | str\|null | Program key (see §4); `null` = unassigned |
| `notes` | str | Approach, context, or blocking info from triage |
| `source_brief` | str | Basename of the `[MORNING BRIEF]` file that surfaced this item |

**Don't** write to this file directly from another repo. The brief-state API lives in `~/code/transcript-analyzer/analyzer/brief_state.py` — import it, don't hand-edit JSON.

### Hook output format

When there are pending items, the session-start hook emits:
```
---
**Open from morning brief**

⚠ OVERDUE (1 item):
  • [2026-10-15] SPARK R1.1 success criteria — write and send to Koti

Pending act items (2):
  • Colin position paper on pricing gaps [due 2026-10-17] — needs review before board
  • ...

_Brief from 2026-10-13. Run /morning-brief to triage today._
---
```
If you see this block at the top of the conversation, read it before starting work — it's the current accountability state.

---

## 3. program-pulse — sweep Analyzed/ into STATUS.md

The program pulse reads new `[ANALYZED]` files from Analyzed/, classifies them to a program, calls Claude for a 3–5 bullet signal summary, and appends it under `## Open signals` in the matching STATUS.md.

### Run it
```bash
source ~/.venvs/transcript-analyzer/bin/activate

# Dry run — shows what would be processed, writes nothing
python -m analyzer synthesize --mode program-pulse --dry-run

# Live run — updates all STATUS.md files with new signals
python -m analyzer synthesize --mode program-pulse
```

**When to run it:** Any time you're about to do governance work in SiemensRCAGov and want current signal. The watermark (`last_analyzed_file` in each STATUS.md frontmatter) means re-running is safe and idempotent — it only processes files newer than the last run.

### What it writes

```markdown
## Open signals

### 2026-10-13 — 3 new file(s)
- Colin ratified the pricing gap position: attribute-propagation deferred to R2.0; no R1.1 backlog impact.
- Fadoua flagged sprint 14 scope creep risk — 3 stories without completed HLDs entered the sprint.
- Brad committed to naming the CML/Apex rebuild owner before next sprint planning (due 2026-10-17).
- Open: Siemens has not confirmed same-country scope satisfies May 2027 need (Multi-BU quoting gate).
```

Blocks are prepended (newest-first). The `## Open signals` header is load-bearing — do not delete it.

---

## 4. Program key map

Used in `program` field of brief-state items, and as the `program:` frontmatter key in STATUS.md files.

| Program key | What it covers | STATUS.md location |
|---|---|---|
| `SI-RCA` | SherpaX SI track — design board, architecture, cross-pod, BuildingX, SolSys | `SI-RCA/STATUS.md` |
| `DISW-RCA` | DI-SW RCA POC + Migration Assistant; Chandan/Sanjay meetings | `DISW-RCA/STATUS.md` |
| `DISW-CPQ` | DI-SW CPQ+; Neeraj, Tequila, Kortney, Digital Industries meetings | `DISW-CPQ/STATUS.md` |
| `siemens-general` | Cross-division exec; Hana, Eike, Tara, Gunnar; workstream "Siemens All" | `siemens-general/STATUS.md` |
| `presales-energy` | Siemens Energy CPQ→RCA opportunity | `presales/Energy/STATUS.md` |
| `presales-healthineers` | Siemens Healthineers opportunity | `presales/Healthineers/STATUS.md` |
| `presales-financial-services` | Siemens Financial Services opportunity | `presales/Financial-Services/STATUS.md` |
| `presales-sx-prd` | SherpaX Product — Subscription Management / Honeycomb opportunity; Madalina | `presales/SX-PRD/STATUS.md` |
| `presales-disw-spm` | DI-SW SPM PoC — territory planning; Jeff Nercesian, Bill Schermer | `presales/DISW-SPM/STATUS.md` |
| `presales-siesales-cpqplus` | SieSales SI CPQ+ → RCA migration (Grid Software / GSW); si-cpqdev2 source org | `presales/SieSales-CPQplus/STATUS.md` |
| `_admin` | Internal Salesforce, practice team, non-project — **not written to any STATUS.md** | — |

Classification priority (first match wins): SX-PRD → DISW-SPM → SieSales → Energy → Healthineers → Financial-Services → DISW-RCA → DISW-CPQ → siemens-general → SI-RCA catch-all.

---

## 5. STATUS.md anatomy

Every program folder in SiemensRCAGov has a `STATUS.md`. The program pulse sweep owns the `## Open signals` section and the frontmatter watermark fields. Everything else is yours to edit manually.

### Frontmatter (machine-maintained fields — don't hand-edit these two)

```yaml
---
program: SI-RCA                          # machine key — must match §4 table exactly
label: "Siemens SI — Revenue Cloud Advanced (SherpaX)"
timecard_row: "Competency Center / SI RCA"
last_updated: "2026-10-13"               # set by sweep on each run
last_analyzed_file: "2026-10-13T09-30-00 - ...  [ANALYZED].txt"  # watermark
---
```

`last_analyzed_file` is a basename (not a path). The sweep compares ISO prefixes lexicographically — files whose leading timestamp is newer than the watermark get processed. Do not delete or blank this field; set it to `null` to force a full re-sweep from the beginning.

### Body structure

```markdown
# [Program Name] Status

*[Description paragraph — edit freely]*

## Open signals

### 2026-10-13 — 3 new file(s)
- ...

### 2026-10-06 — 1 new file(s)
- ...

## [Other sections — decisions, active threads, etc. — edit freely]
```

The sweep finds the `## Open signals` header by exact string match and inserts the new dated block immediately after it (newest-first). If the header is missing, the sweep re-creates it at the end of the file. **Do not rename this header.**

---

## 6. What to do in SiemensRCAGov

### Starting a session — standard preamble
1. Check the hook-injected brief block at the top of context (§2). Any SI-RCA or DISW-RCA acts open?
2. Run `--dry-run` to see if there are unprocessed signals: `python -m analyzer synthesize --mode program-pulse --dry-run`
3. If there are new files, run the live sweep before reading STATUS.md — you'll have current signal.
4. Read the relevant STATUS.md's `## Open signals` section for the program you're working on.

### Linking decisions to open acts
When Brad closes a brief-state act in SiemensRCAGov context (e.g., drafts a decision log entry), you can note the resolution in the act's `notes` field:
```python
import sys; sys.path.insert(0, '/Users/brad.gross/code/transcript-analyzer')
from analyzer.brief_state import close
close("043ae258511f", "complete", notes="RCA-DEC-010 ratified 2026-10-14")
```

### When the program pulse runs automatically
It doesn't — it's manual. Brad or a Claude Code session runs it. There's no scheduler for it. The work machine's 30-min launchd job runs `python -m analyzer` (transcript analysis only), not synthesis modes.

---

## 7. What to do in Siemens3RR

Siemens3RR is strategic/narrative — no STATUS.md files, no governance sweep. Its link to the intelligence pipeline is read-only: current program state from Analyzed/ and brief-state context.

### Before drafting executive narrative or deck content

1. Check the hook block — is there a pending act relevant to 3RR content (e.g., a deadline on a narrative deliverable)?
2. Pull current signal from the Analyzed/ files:
   - `[PROGRAM REFERENCE].md` — consolidated canonical SherpaX snapshot (always read this first)
   - Most recent `[DAILY PULSE].md` — what moved this week
   - Most recent `[SLACK DELTA].md` — week-level drift scan
3. If Brad has been in meetings about a specific workstream, grep for relevant `[ANALYZED]` files:
   ```bash
   ls -lt "$DRIVE_BASE/Analyzed/" | grep -i "ANALYZED" | head -10
   ```

### The DRIVE_BASE shortcut
```bash
DRIVE_BASE="$HOME/Library/CloudStorage/GoogleDrive-brad.gross@salesforce.com/My Drive/Workcall"
ls "$DRIVE_BASE/Analyzed/" | grep "DAILY PULSE" | tail -3
cat "$DRIVE_BASE/Analyzed/[PROGRAM REFERENCE].md" | head -100
```

### Alignment between 3RR narrative and current program state
3RR artifacts are 18–24 month vision. When program state changes (a decision is made, a workstream descopes, a new risk surfaces), the 3RR narrative may need a revision. The signal to watch for in the Daily Pulse / Open signals:
- Position papers ratified (locks the design path → may define what's in the vision horizon)
- Workstream scope changes (descope = no longer in the vision; new scope = add to horizon)
- Executive relationship signals from `siemens-general` STATUS.md (Hana/Tara dynamics shape which narratives land)

---

## 8. Quick reference — commands that matter across repos

```bash
# Activate the venv (required for all python commands)
source ~/.venvs/transcript-analyzer/bin/activate

# Check what's open
python -m analyzer synthesize --mode program-pulse --dry-run

# Update all STATUS.md files with new signals
python -m analyzer synthesize --mode program-pulse

# Read current open acts (all programs)
python3 -c "
import json; from pathlib import Path
items = json.loads(Path('~/.cache/transcript-analyzer/brief-state.json').expanduser().read_text())
for i in [x for x in items if x.get('status')=='pending' and x.get('type')=='act']:
    print(f\"[{i.get('program','?')}] {i.get('deadline','no deadline')} — {i['text']}\")
"

# Mark an act complete (get the id from the JSON or hook output)
python3 -c "
import sys; sys.path.insert(0, '/Users/brad.gross/code/transcript-analyzer')
from analyzer.brief_state import close
close('ITEM_ID_HERE', 'complete', notes='optional resolution note')
"

# Reset a program's watermark to re-sweep everything
# Edit the STATUS.md frontmatter: last_analyzed_file: null
# Then re-run the pulse
python -m analyzer synthesize --mode program-pulse
```
