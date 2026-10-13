# Morning Brief — Forced Triage (Claude Code slash command)

A Claude Code slash command (`/morning-brief`) that turns the passive daily
pulse read into an active triage session. The user must assign a disposition
to every outstanding item before they get their action list — no passive
reading, no details falling through.

**Install:** copy to `~/.claude/commands/morning-brief.md` (global) or
`.claude/commands/morning-brief.md` (project-local). Customize the paths
marked `[CONFIGURE]` for your own setup.

**Companion pieces** (also in this repo):
- `bin/morning-brief-notify.sh` + a 7:30am LaunchAgent — macOS banner nudge
- `analyzer/alerts.py` — real-time triage alerts extracted during analysis
- `dailyAndWeeklyPrompts.md § M1` — the same logic as a synthesizer prompt

---

## How it works

1. **Pulse check** — verifies a current Daily Pulse exists; offers to generate
   one if it's missing before proceeding
2. **Monday mode** — on Mondays also reads the weekly Slack Delta so the full
   prior week is covered, not just Friday
3. **Dedup filter** — reads dismissed alerts from the alert store and prior
   morning brief files to suppress items you already handled during the day;
   `watch` items stay live
4. **Triage queue** — each item is a two-part entry: a ≤15-word label line
   plus 2–3 sentences of context so you can decide without going back to the
   source
5. **One-shot triage** — reply with all dispositions in one message
   (`1:act, 2:watch, 3:delegate:Name, 4:drop`)
6. **Action list + file** — outputs only your `act` items as imperatives,
   writes a `[MORNING BRIEF]` record to the Analyzed folder

---

```markdown
# Morning Brief — Forced Triage

You are running a morning program brief for a senior delivery leader. They
want a triage pass, not a summary — they must make a decision on each item,
not just read about it.

## Step 0 — Check for a current Daily Pulse

Before doing anything else, list the files in `[CONFIGURE: path to Analyzed/]`
sorted descending by filename. Find the most recent `[DAILY PULSE]` file.

Determine the last business day (Mon–Fri) relative to today. Skip Saturday
and Sunday — if today is Monday, last business day is Friday.

**If the most recent pulse is dated the last business day or today:** proceed
to Step 1.

**If the most recent pulse is older than the last business day:** stop and
tell the user:

> "No Daily Pulse found for [last business day]. The most recent pulse is
> from [date of latest pulse].
>
> You have two options:
> - **Generate it now:** I'll run `python -m analyzer synthesize --mode daily`
>   in `[CONFIGURE: repo path]` to build yesterday's pulse from the analyzed
>   files, then continue with the brief.
> - **Skip it:** Continue the brief using the [date] pulse — you may be
>   missing items from [last business day].
>
> Which would you like?"

If the user says generate it (or any affirmative), run:
```
cd [CONFIGURE: repo path] && source [CONFIGURE: venv path]/bin/activate && python -m analyzer synthesize --mode daily
```
Wait for it to complete, confirm the new pulse file was written to Analyzed/,
then proceed to Step 1 using that new file.

If the user says skip, proceed to Step 1 with the stale pulse but note at the
top of the triage queue: ⚠️ *Pulse from [date] — [last business day] not
covered.*

## Step 1 — Load context

Read these files in order:
1. The Daily Pulse confirmed in Step 0
2. If today is **Monday**: also read the most recent `[SLACK DELTA]` file —
   it covers the full prior week and will surface items the Friday pulse alone
   won't have. If the Slack Delta is missing for last week, note it but
   continue.
3. Any `[ANALYZED]` files from today (same folder, matching today's date in
   the filename)
4. `[CONFIGURE: path to a rolling context file]` — for current standing open
   items (optional; omit this step if you don't have one)

## Step 2 — Load already-handled items (dedup filter)

Before extracting anything, build an exclusion list so items already dealt
with during the day don't reappear the next morning.

**Source A — dismissed alerts:** Run this command and capture the output:
```
python3 -c "
import json, pathlib
p = pathlib.Path('~/.cache/transcript-analyzer/alerts.json').expanduser()
if p.exists():
    alerts = json.loads(p.read_text())
    for a in alerts:
        if a.get('dismissed'):
            print(a['text'][:120])
"
```

**Source B — prior morning brief files:** List all `[MORNING BRIEF]` files in
the Analyzed folder. Read the most recent one (if it exists and is from today
or the last business day). Extract every item triaged as `act`, `delegate`, or
`drop` — these are handled. Items triaged as `watch` are NOT excluded (still
live).

Hold both lists in memory as the exclusion set for Step 3.

## Step 3 — Build triage queue

Extract every item that requires the user's attention, decision, or stance.
Include:
- Commitments the user owes to someone (with who and when)
- Decisions that are decaying (delay = someone else decides)
- Risks that surfaced with no named owner
- Items from the pulse's "Suggested dispositions" that weren't acted on
- Anything a key stakeholder is actively waiting on

Exclude:
- Items in flight with a clear owner that isn't the user
- Background watch items with no action needed today
- Things that closed yesterday
- **Any item substantively the same as something in the Step 2 exclusion
  list.** Match by meaning, not exact text. If genuinely uncertain, include
  with a note: *(previously handled — confirm still open?)*

For each item, write a **two-part entry**:

**[N] [one-line label]** — `[WHO IS WAITING / WHAT DECAYS] — [what] — [why
today]` (≤15 words)
> [2–3 sentences of context: what the situation is, what specifically needs to
> be done or decided, and what breaks if not acted on today. Cite the source
> file in brackets.]

This gives the user enough to triage without going back to the source file.

## Step 4 — Present and ask for triage

Show the numbered queue. Then say exactly this:

---
**Triage each item:** reply with the number and one of:
- `act [notes]` — doing it today; notes are optional
- `watch` — no action yet, keep monitoring
- `delegate:NAME` — someone else owns it; name them
- `drop` — no longer relevant

Example: `1:act (draft the scope language), 2:watch, 3:delegate:Alex, 4:drop`

Reply with all items in one message.

---

## Step 5 — Confirm and write

After the user replies:
1. Echo back a clean action list — only the `act` items, each as a single
   imperative sentence with owner and deadline (today or explicit date if
   stated)
2. Write a `[MORNING BRIEF]` file to the Analyzed folder with today's date,
   the full triage queue, and dispositions. Filename format:
   `YYYY-MMDDThh-mm-ss - Morning Brief - YYYY-MM-DD [MORNING BRIEF].md`
3. Confirm the file was written and show the filename.

Do not summarize program state. Do not add context paragraphs.
Triage queue → user decides → action list → file written.
```
