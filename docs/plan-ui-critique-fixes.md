# UI Improvement Plan — Post-Critique Fixes + Meeting Prep Panel

**Source:** `/impeccable critique` run on 2026-10-15, score 25/40.  
**File:** `analyzer/ui.py` (1675 lines, single inline HTML template).  
**Execute with:** `/impeccable polish analyzer/ui.py` or tell Claude Code to follow this plan.

---

## Context for a fresh session

This plan was written after an Impeccable dual-agent critique of `analyzer/ui.py`. The app is a Flask dashboard at localhost:7070 — Brad Gross's personal intelligence tool. It processes meeting transcripts, surfaces obligations (Brief State), and runs synthesis (Daily Pulse, Weekly Delta, etc.). The critique found 2 P0s, 3 P1s, several P2s, plus a new feature request (Meeting Prep panel). All changes are to `analyzer/ui.py` unless noted.

No test suite. Runtime is the contract. Run `python -m analyzer ui` to verify.

---

## Phase 1 — Fix sweep (P0 → P2, all in `analyzer/ui.py`)

### 1A. Layout (P0 + P1 ergonomics)

**1A-1: Collapse the Operations card into a `<details>` drawer (P0)**  
The Operations card (7 synthesis modes + date inputs) sits below the act list on Brief State, competing for attention with the obligation surface. Between meetings, Brad needs to clear acts — the synthesis controls are a distraction unless explicitly needed.

- Wrap the existing Operations card `<div class="card">` in a `<details>` element with `<summary>Run synthesis…</summary>`.  
- Default: `closed` (no `open` attribute).  
- Keep the header quick-run strip (Run Analysis + Daily Pulse buttons) as-is — that's the ambient shortcut.  
- The `<summary>` label: `"Run synthesis"` in 11px uppercase muted style, with a `▶` chevron that rotates to `▼` on `details[open]` (match the existing `bs-section-chevron` pattern).

**1A-2: Inline defer quick-buttons (P1)**  
The `+1d`, `+3d`, `Mon` quick-defer buttons are hidden behind a "Defer ▾" toggle, requiring 2 clicks per item. These should be always visible inline with Done and Drop.

- Remove the `.defer-toggle` button and the `.defer-panel` reveal animation.  
- Show `+1d`, `+3d`, `Mon` directly in the act-item button row (same row as Done and Drop).  
- Keep the custom date-picker form accessible but collapse it behind a `"…"` overflow button or a small inline `<details>` — it's the rare case.  
- The three quick buttons should use `.btn-secondary` style with smaller padding (`4px 8px`) to fit inline without crowding.

**1A-3: Fix header right-alignment (P3)**  
The header uses conditional `margin-left: auto` and a hidden empty `<span>` as a flex spacer — brittle and produces inconsistent layouts depending on which combination of render_warnings, shared_url, and page == 'brief-state' is truthy.

- Replace with an unconditional `<div class="header-spacer" style="flex:1"></div>` between the nav links and the right-side controls group.  
- Wrap render-warn-chip and shared-url button in a `<div class="header-right" style="display:flex;gap:8px;align-items:center">`.  
- Remove all the conditional `margin-left:auto` and empty span placeholders.

---

### 1B. Harden (P0 indicator + P1 guards + accessibility)

**1B-1: Last-pulse freshness indicator on Brief State (P0)**  
Brad's primary question on arrival: "Do I need to run a Daily Pulse before this meeting?" There is no signal for this on Brief State. The last-analyzed stat lives on `/files`, unreachable at a glance.

Backend:
- In `ui.py`, add a helper `_last_pulse_mtime()` that scans the `ANALYZED_DIR` for files matching `*[DAILY PULSE]*`, returns the most recent mtime as a `datetime` (or `None`).
- Compute `hours_ago` from `datetime.now()` minus mtime.
- Pass `last_pulse_age` (a dict: `{"label": "Oct 14, 2:15 PM", "hours": 3.2, "status": "green"|"orange"|"red"}`) to the Brief State template context. Status: green < 4h, orange 4–12h, red > 12h, gray if None.

Template:
- In the header quick-run strip (the `<div class="header-quick-run">` strip on Brief State), add a small status chip to the right of the Daily Pulse button:
  ```html
  <span class="pulse-freshness pulse-{{ last_pulse_age.status }}">
    Last pulse: {{ last_pulse_age.label }}
  </span>
  ```
- CSS: `.pulse-freshness` — 11px, muted, no background. Color classes: `.pulse-green { color: var(--green) }`, `.pulse-orange { color: var(--orange) }`, `.pulse-red { color: var(--red) }`, `.pulse-gray { color: var(--muted) }`.

**1B-2: Career + "full" re-read confirmation guard (P1)**  
The "full" checkbox triggers an Opus run over all analyzed files — potentially $5–15, 10+ minutes. It has no warning.

- Add a JS `onsubmit` handler on the Career form: if the `full` checkbox is checked, `confirm("Full re-read will process all analyzed files through Opus. This may take 10+ minutes. Proceed?")`. If the user cancels, `return false`.
- Also: toggle the Career button's class to include a subtle visual cue when `full` is checked — e.g. change its border color to `var(--orange)` via JS class toggle (`career-full-active`).

**1B-3: Accessibility fixes (P1/P2)**  
- `role="alert"` on the static overdue banner fires a screen-reader interrupt on every page load. Change to `role="region" aria-label="Overdue obligations"`.  
- Defer toggle button uses JS text mutation (`textContent = 'Defer ▴'`) to signal state. Add `aria-expanded="false"` (updated to `"true"` by JS when open).  
  _Note: if 1A-2 is implemented, the defer toggle is removed entirely — skip this fix._
- Quick-defer buttons (`+1d`, `+3d`, `Mon`) need `aria-label`: `aria-label="Defer 1 day"`, `aria-label="Defer 3 days"`, `aria-label="Defer to Monday"`.

---

### 1C. Audit fixes (P1 semantic + P2 token drift)

**1C-1: Alert Dismiss button semantic (P1)**  
The per-alert Dismiss button uses `.btn-destructive` (red background). Dismissing an alert is a completion action, not a destructive one. The Dismiss-all button is already `.btn-secondary` — make them match.

- Change all per-alert Dismiss buttons from `.btn-destructive` to `.btn-secondary`.
- This applies wherever `Dismiss` forms appear in the triage alert section.

**1C-2: Tokenize the 4 undocumented one-off colors (P2)**  
Detector confirmed these raw hex values at specific lines:
- `#4a2828` (line 333) — dark tint for EXEC badge background (a darker variant of `--bg-exec`)
- `#1b3628` (line 350) — dark tint for a daily/green badge alt background  
- `#1e1e1e` (line 402) — near-void surface used in the job output panel or settings code area
- `#5a3e1f` (line 497) — dark orange tint for deferred badge background

Define CSS custom properties in the `:root` block:
```css
--bg-exec-deep: #4a2828;
--bg-daily-deep: #1b3628;
--bg-void-deep: #1e1e1e;
--bg-deferred-deep: #5a3e1f;
```
Replace the 4 literal hex values with these tokens.

**1C-3: Settings Save → primary style (P2)**  
"Save settings" is the only action on the Settings form, yet it uses `.btn-secondary`. Change to `.btn-primary` (accent background).

**1C-4: H1 size fix (P2)**  
Header H1 is 15px (line 317, confirmed by detector). The DESIGN.md ramp is 14px for body/UI. Change to 14px.

**1C-5: Page `<title>` per route (P2)**  
The page title is always "Transcript Analyzer" regardless of route. Update the `<title>` template to reflect current page:
- Brief State: `"Brief State — Transcript Analyzer"`
- Files: `"Files — Transcript Analyzer"`
- Slack Paste: `"Slack Paste — Transcript Analyzer"`
- Settings: `"Settings — Transcript Analyzer"`

**1C-6: Badge collision fixes (P2)**  
- `badge-delta` and `badge-standup` both render with `--orange` text and similar dark backgrounds — indistinguishable in the files table. Give `badge-delta` a distinct color: use `--cyan` text with `--bg-watch-deep` (define as `#1a3030` if not already present), matching the "Watch" semantic.
- `badge-synthesis` and `badge-daily` both use `--green` — synthesis output files look identical to daily transcript category in the file table. Give `badge-synthesis` a distinct treatment: use `--purple` text with the existing `--bg-notes` dark background.

---

## Phase 1D — Additional P1 reliability and ergonomic fixes

### 1D-1: Orphaned subprocess on server restart mid-job (P1)

`_job` stores job state but not the `Popen` reference. If "Restart server" is clicked while a synthesis is running, the subprocess keeps running — billing Claude quota with no UI visibility. The new server instance starts with `_job = None` and has no knowledge of the orphan.

- In `_run_job()`, store the `Popen` object in `_job["proc"]` before the output loop.
- In the `/restart` route handler, before re-execing: acquire `_job_lock`, check if `_job and not _job.get("done")`, if so call `_job["proc"].terminate()` and wait up to 3 seconds for it to exit.
- Remove `"proc"` from `_job` before exposing it over `/job-status` (don't JSON-serialize a Popen object).

### 1D-2: `/brief-state/re-own` bypasses module API (P1)

The `/brief-state/re-own/<id>` route reads and writes `brief-state.json` directly using raw `json.load`/`json.dump` instead of going through the `brief_state` module API. This creates a race condition with concurrent writes (the session hook, a form submit, and a re-own all writing simultaneously can corrupt the file).

- Rewrite the re-own route to use `brief_state.upsert(item)` — same pattern as accept, close, and defer routes.
- If the item is not found, return a 404 flash rather than silently doing nothing.

### 1D-3: No bulk dismiss on inbox candidates (P1)

After a vacation backlog or a busy week, the inbox can accumulate 20–30 candidates with no escape except one-by-one dismissal. Unlike triage alerts (which have "Dismiss all"), the inbox card has no bulk action.

- Add a "Dismiss all candidates" button at the top-right of the inbox card header (same position as "Dismiss all" on the alert banner).
- POST to a new `/brief-state/dismiss-all-candidates` route that calls `brief_state.close(id, "dropped")` for each item with `status == "candidate"` (or equivalent — check the brief_state module for the correct candidate status value).
- Add a JS `confirm()` guard: `"Dismiss all N candidate items? They will be marked dropped."`.

---

## Phase 2 — Meeting Prep panel (new feature)

### Overview

A program-scoped prep brief on Brief State. Brad selects a program (from a dropdown populated by STATUS.md files in `~/code/SiemensRCAGov`), optionally adds meeting context (topic/agenda), clicks "Prep" → backend returns 3–5 bullets inline on the page.

### Backend (`ui.py` + a new route)

**Program discovery:**
- At server startup (or lazily on first request to `/brief-state`), scan `~/code/SiemensRCAGov` for `STATUS.md` files using `glob.glob`.
- For each STATUS.md, read the YAML frontmatter to extract `program` (key) and `label` (human-readable name).
- STATUS.md files and their `label:` values (current):
  - `DISW-CPQ/STATUS.md` → label from frontmatter
  - `DISW-RCA/STATUS.md` → label from frontmatter
  - `SI-RCA/STATUS.md` → `"Siemens SI — Revenue Cloud Advanced (SherpaX)"`
  - `presales/Energy/STATUS.md` → label from frontmatter
  - `presales/Financial-Services/STATUS.md` → label from frontmatter
  - `presales/Healthineers/STATUS.md` → label from frontmatter
  - `presales/SX-PRD/STATUS.md` → label from frontmatter
  - `siemens-general/STATUS.md` → label from frontmatter
- Sort by label alphabetically.

**New route: `POST /meeting-prep`**
- Accepts: `program` (key), `context` (optional free text).
- Reads the STATUS.md for that program (full file, not just frontmatter).
- Reads brief-state pending acts filtered to that program (`brief_state.pending_by_program(program)`).
- Optionally reads the 2 most recent `[ANALYZED]` files classified to that program (use `program_pulse.classify_file()` to filter).
- Assembles a prompt:
  ```
  You are preparing Brad Gross (Revenue Cloud CTO) for a meeting about [program label].
  
  [If context provided]: Meeting context: {context}
  
  ## Program status (from STATUS.md):
  {STATUS.md open signals section — last 2 blocks}
  
  ## Open act items for this program:
  {pending acts list, if any}
  
  Return exactly 3-5 bullet points. Each bullet: one concrete thing Brad should know or do in this meeting. No preamble, no hedging, no padding. Start each bullet with a bold keyword.
  ```
- Runs via `claude_cli.run_prompt()` (same pattern as other Claude calls, use Sonnet tier).
- Returns JSON: `{"bullets": ["string", ...], "program_label": "string"}`.
- Streams or returns synchronously (synchronous is fine — this is a fast Sonnet call, <10s).

**Security note:** No shell injection risk — program key is validated against the discovered STATUS.md list before use.

### Template (Brief State page)

Add a "Meeting Prep" card above the overdue banner or between the header quick-run strip and the act list (discuss placement — above the banner keeps urgency first; below the banner keeps the obligation flow intact). Recommendation: **below the quick-run strip, above the overdue banner** so it's the first intentional action before triage.

```html
<div class="card" id="meeting-prep-card">
  <h2>MEETING PREP</h2>
  <div class="prep-controls">
    <select id="prep-program" name="program">
      <option value="">Select program…</option>
      {% for p in programs %}
      <option value="{{ p.key }}">{{ p.label }}</option>
      {% endfor %}
    </select>
    <input type="text" id="prep-context" placeholder="Meeting topic or agenda (optional)" />
    <button class="btn-primary" id="prep-submit" onclick="runMeetingPrep()">Prep</button>
  </div>
  <div id="prep-output" style="display:none">
    <!-- rendered bullets go here -->
  </div>
</div>
```

JS:
```javascript
async function runMeetingPrep() {
  const program = document.getElementById('prep-program').value;
  if (!program) { alert('Select a program first'); return; }
  const context = document.getElementById('prep-context').value;
  const btn = document.getElementById('prep-submit');
  btn.disabled = true; btn.textContent = 'Prepping…';
  const resp = await fetch('/meeting-prep', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({program, context})
  });
  const data = await resp.json();
  const out = document.getElementById('prep-output');
  out.innerHTML = data.bullets.map(b => `<div class="prep-bullet">${b}</div>`).join('');
  out.style.display = 'block';
  btn.disabled = false; btn.textContent = 'Prep';
}
```

CSS additions (in the `:root` / global styles block):
```css
.prep-controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
.prep-controls select, .prep-controls input { flex: 1; min-width: 180px; }
.prep-bullet { padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px; line-height: 1.5; }
.prep-bullet:last-child { border-bottom: none; }
```

---

## Files touched

| File | Changes |
|------|---------|
| `analyzer/ui.py` | All template, CSS, JS, and route changes |
| `analyzer/brief_state.py` | Read-only — `pending_by_program()` already exists |
| `analyzer/program_pulse.py` | Read-only — `classify_file()` already exists |
| `analyzer/claude_cli.py` | Read-only — `run_prompt()` already exists |
| `~/code/SiemensRCAGov/*/STATUS.md` | Read-only — scanned for program list and content |

No new files required. All changes are additive except the defer toggle removal (1A-2) and the Operations card wrapper (1A-1).

---

## Phase 3 — Additional features (from post-critique review)

### 3A. Keyboard triage mode (Brief State)

The act list is the highest-frequency surface in the tool, used multiple times per day. Currently mouse-only — each item requires clicking Done/Drop or expanding the defer panel. For 8 acts between meetings, keyboard-first is the difference between 45 seconds and 3 minutes.

**Implementation:**
- Add a `data-act-id` attribute to each act item row.
- JS: when Brief State is focused and no input is active, bind:
  - `k` = submit Done on the focused act item
  - `d` = submit Drop on the focused act item (with the existing confirm guard)
  - `1` = defer +1d
  - `3` = defer +3d
  - `m` = defer to Monday
  - `↓` / `j` = move focus to next act item
  - `↑` / `i` = move focus to previous act item
- Visually indicate the "focused" act item with a `2px var(--accent)` left border and a subtle `--surface` background shift.
- Show a single `[k] done  [d] drop  [1/3/m] defer  [↓↑] navigate` hint line at the bottom of the act list (11px muted, only visible when the list has 2+ items).
- Guard: suppress keybindings when focus is inside an `<input>`, `<textarea>`, or `<select>`.

### 3B. Source linkage on act items

Act items have a `source_pulse` field (the filename of the Daily Pulse or analysis that generated them). Currently there is no way to navigate from an act item back to the intelligence that created it — you know *what* to do but not *why* or *what was said*.

**Implementation:**
- In the act item template, if `item.source_pulse` is set and a file matching that name exists in `ANALYZED_DIR`, render a small `↗ source` link after the item text.
- The link opens `/files#<slugified-filename>` or, simpler: a GET `/source/<item_id>` route that reads the source file and returns its content in a read-only modal/drawer on the page.
- The drawer should render the source file as preformatted text (SF Mono, same as job panel) with a close button. No editing.
- This is purely additive — items without a `source_pulse` show nothing.

### 3C. Proactive pulse prompt (not just freshness chip)

The 1B-1 freshness chip tells you how old the last pulse is. This goes further: the app already knows when new `[ANALYZED]` files have landed since the last pulse was written. That's a trigger condition, not just a timestamp.

**Implementation:**
- In `_last_pulse_mtime()` (written for 1B-1), also count `[ANALYZED]` files newer than the pulse mtime. Call this `analyses_since_pulse`.
- If `analyses_since_pulse > 0`, replace the freshness chip text with an actionable prompt:
  `"3 new analyses since last pulse — run now?"`
  where "run now?" is a link/button that submits the Daily Pulse form (same as the header quick-run button, just wired differently).
- If `analyses_since_pulse == 0`, show the plain freshness chip as designed in 1B-1.
- This replaces the staleness color as the primary signal when there's actual new content — the question is "is there new intelligence?" not "is it old?"

### 3D. Weekly Delta prompt alongside Daily Pulse

Brad's stated habit: runs Daily Pulse but forgets to run Weekly Delta (the Slack-style summary). The two are related but cognitively separate — Daily Pulse is automatic, Weekly Delta requires remembering it exists.

**Implementation:**
- Add a `_last_weekly_mtime()` helper (same pattern as `_last_pulse_mtime()`, matching `*[SLACK DELTA]*` or `*[WEEKLY SUMMARY]*` files).
- Compute `days_since_weekly` from mtime to now.
- In the header quick-run strip, add a small nudge beside the Weekly Delta button when `days_since_weekly > 5` (i.e., it's been more than a work week):
  `"Last: Mon Oct 7"` in orange — same staleness pattern as the pulse chip but keyed to weekly cadence, not daily.
- If `days_since_weekly` is None (never run), show `"Not yet run"` in muted.
- No nudge when the weekly is fresh (< 5 days). The button alone is sufficient.

### 3E. Business-day-aware staleness (not calendar days)

The freshness chip (1B-1) and weekly nudge (3D) both use raw hour/day counts. This produces false urgency on weekends and after holidays — a Monday morning with a Friday pulse reads as "3 days stale" when it's actually current for the work cadence.

**Implementation:**
- Write a `_business_days_since(dt)` helper that counts weekdays between `dt` and `datetime.now()`, treating Saturday and Sunday as non-working. No holiday calendar needed — weekends alone cover 90% of the false-positive cases.
- Feed this into the staleness color thresholds:
  - Daily pulse: green < 1 business day, orange 1–2 business days, red > 2 business days. (Monday morning after a Friday pulse = 1 business day = green.)
  - Weekly delta: nudge after 5 business days.
- The displayed label stays human (`"Fri Oct 11, 4:30 PM"`) — the business-day logic only affects the color/urgency, not the copy.
- For catch-up scenarios (returning from vacation): if `_business_days_since` > 5, the staleness chip text changes from `"Last pulse: ..."` to `"Catch-up needed — N days of meetings"` in red, linking to the Catch Up button. This replaces the raw "12 days stale" alarm with an actionable framing.

### 3F. Program Pulse freshness in Meeting Prep dropdown

The Meeting Prep card (Phase 2) reads STATUS.md files, but those files only update when `synthesize --mode program-pulse` is run from the terminal. If the last program pulse was 2 days ago, the prep brief is silently stale.

**Implementation:**
- When building the program list for the Meeting Prep dropdown, also read `last_updated:` from each STATUS.md frontmatter.
- Display it inline in the `<option>` label: `"Siemens SI — RCA (updated Oct 13)"`.
- If `last_updated` is > 3 business days ago, append a `⚠` to the option label: `"Siemens SI — RCA ⚠ (updated Oct 10)"`.
- Add a small `<button>` beside the dropdown: `"Refresh signals"` — submits a POST to `/run-synthesis` with `mode=program-pulse`. This runs the program pulse sweep so the STATUS.md files are current before the prep brief is generated. The button disables during the run and shows "Refreshing…".
- The prep brief generation should only proceed after the refresh completes (or the user explicitly skips it).

### 3G. Weekly Delta catch-up framing

When Brad returns from time off, the Weekly Delta synthesis covers the full stretch — it's not a rolling 7-day window, it covers everything since the last weekly. The UI currently has no concept of this: it shows the same "Weekly Delta" button whether it's been 5 days or 15.

**Implementation:**
- Compute `days_since_weekly` (from 3D's `_last_weekly_mtime()` helper).
- If `days_since_weekly > 8` (i.e., more than one week gap):
  - Change the Weekly Delta button label to `"Weekly Delta (${N}d catch-up)"` where N is the business days since last run.
  - Add a tooltip/title: `"Covers ${N} business days of analyses in one summary — not limited to this week."` This sets the right expectation so Brad isn't surprised when the summary is unusually long or covers multiple topic threads.
- No behavior change — just label and tooltip. The synthesis runs identically regardless.

---

## Revised execution order

1. **Phase 1A** (layout): Collapse Operations drawer + inline defer buttons + header flex fix
2. **Phase 1B** (harden): Last-pulse freshness indicator + Career guard + accessibility
3. **Phase 1C** (audit): Alert dismiss semantic + token colors + settings button + H1 + title + badge collisions
4. **Phase 1D** (reliability + ergonomics): Orphaned subprocess fix + re-own API fix + inbox bulk dismiss
5. **Phase 2** (Meeting Prep): Backend route + template card + JS + program dropdown
5. **Phase 3A** (keyboard triage): Keybindings + focus indicator + hint line
6. **Phase 3B** (source linkage): `↗ source` link + read-only drawer
7. **Phase 3C** (proactive pulse prompt): `analyses_since_pulse` counter + "run now?" trigger
8. **Phase 3D** (weekly delta nudge): `_last_weekly_mtime()` + staleness label on Weekly Delta button
9. **Phase 3E** (business-day staleness): `_business_days_since()` helper + recalibrate all thresholds + catch-up framing
10. **Phase 3F** (program pulse freshness): Stale warning in prep dropdown + "Refresh signals" button
11. **Phase 3G** (weekly catch-up label): Dynamic button label when > 8 days since last weekly

After all phases: `python -m analyzer ui` — verify Brief State loads, keyboard triage works, source links appear, pulse chip shows correct business-day color, Meeting Prep dropdown shows update dates, Weekly Delta button relabels correctly after simulating a gap.
