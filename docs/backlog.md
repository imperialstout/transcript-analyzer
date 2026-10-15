# UI Backlog

Tracks all known improvements, bugs, and feature ideas for `analyzer/ui.py` and supporting backend code. Items in the active plan (`docs/plan-ui-critique-fixes.md`) are marked **[IN PLAN]**.

Priority definitions:
- **P0** — blocks the core workflow or risks data loss; fix before next session
- **P1** — meaningful friction or reliability gap; fix in the next planned work window
- **P2** — annoyance with a workaround; fix when nearby
- **P3** — polish or nice-to-have; fix if time permits
- **Idea** — not yet sized; needs design thought before committing

---

## P0

_(none open)_

---

## P1

### Reliability

- **[IN PLAN] Orphaned subprocess on server restart mid-job** — `_job` is in-memory; a restart mid-synthesis leaves the `Popen` process running (billing Claude quota) with no tracking. Fix: store `Popen` reference in `_job`, kill it in the restart handler before re-execing.

- **[IN PLAN] `/brief-state/re-own` bypasses module API** — writes raw JSON directly; concurrent writes (e.g., session hook + UI form simultaneously) can corrupt `brief-state.json`. Fix: route through `brief_state.upsert()` like every other mutating endpoint.

### Layout / Ergonomics

- **[IN PLAN] Operations card competes with act list** — 7 synthesis controls on the hero page divide attention from the obligation surface. Collapse into `<details>` drawer, default closed.

- **[IN PLAN] Defer requires 2 clicks per item** — `+1d`/`+3d`/`Mon` hidden behind toggle. Inline them with Done/Drop; move custom date picker to overflow.

- **[IN PLAN] No "last daily pulse" freshness indicator** — Brad's primary arrival decision ("do I need to run a pulse?") has no signal on Brief State. Add timestamp + staleness color near the Daily Pulse button.

- **[IN PLAN] No "clear all" on inbox candidates** — inbox has no bulk dismiss; after a vacation backlog, 30+ candidates stack up with no escape. Add "Dismiss all candidates" with confirm guard (same pattern as triage alerts dismiss-all).

### Correctness

- **[IN PLAN] Triage alert Dismiss uses `.btn-destructive` (red) for a completion action** — erodes One Hue One Role; change to `.btn-secondary`.

---

## P2

### Reliability / Data

- **[IN PLAN] Four undocumented one-off colors** — `#4a2828` (L333), `#1b3628` (L350), `#1e1e1e` (L402), `#5a3e1f` (L497) are drift against the token system. Define named CSS tokens.


- **Inbox Accept has no type correction** — Accept always uses the auto-classified type. If a candidate is misclassified (`watch` should be `act`, etc.), the only fix today is editing `brief-state.json` by hand. Add a type `<select>` to the Accept form defaulting to the auto-classified value — one dropdown, no new routes needed.

- **Career full re-read has no confirmation guard** — potentially $5–15, 10+ minutes. No warning. Add JS confirm dialog when `full` checkbox is checked.

- **`brief-state.json` mid-write returns 500** — if the file is partially written when a page request arrives, the endpoint raises an unhandled exception. Wrap reads in a try/except with a user-visible error flash instead of a bare 500.

### Accessibility

- **[IN PLAN] `role="alert"` on static overdue banner** — fires screen-reader interrupt on every page load. Change to `role="region" aria-label="Overdue obligations"`.

- **[IN PLAN] Quick-defer buttons missing `aria-label`** — `+1d`/`+3d`/`Mon` read as ambiguous strings. Add `aria-label="Defer 1 day"` etc.

- **`aria-live="polite"` on job output panel is too noisy** — announces every streamed line to screen readers during a 3-minute job. Change to `aria-live="off"` on the output container; add a separate `aria-live="polite"` status region that only announces job start/complete/failed.

### Visual Polish

- **[IN PLAN] H1 at 15px** — off ramp; should be 14px (detector: L317).

- **[IN PLAN] Settings "Save settings" uses `.btn-secondary`** — only action on the form; should be `.btn-primary` (accent).

- **[IN PLAN] Page `<title>` never reflects current route** — always "Transcript Analyzer". Update per-route.

- **[IN PLAN] `badge-delta` and `badge-standup` visually identical** — both use `--orange`; DELTA and STANDUP indistinguishable in the file table. Give `badge-delta` distinct color.

- **[IN PLAN] `badge-synthesis` and `badge-daily` both use `--green`** — synthesis outputs look identical to daily transcript category. Give `badge-synthesis` distinct treatment.

- **`.bs-stale-badge` defined in CSS but never rendered** — dead code or a cut feature. Remove or wire up.

- **Closed items table always visible** — shows whenever any recent closures exist; takes real estate confirming completed work that doesn't drive action. Collapse into a `<details>` by default.

- **Six unactionable stats on Files page** — Total analyzed, Transcripts, Notes filed, Syntheses are backward-looking counters that don't drive action. Reduce to 2–3 high-signal stats: Last analyzed (staleness), Pending in inbox, and optionally this week's cost.

---

## P3 — Polish

- **[IN PLAN] `badge-dropped` uses raw `#1e1e1e`** — tokenize to `--bg-void-deep`.

- **3px scrollbar radius** — off the documented scale (4px min). Change to 4px (detector: L310).

- **28px empty-state emoji** — outside component system; use a smaller CSS-drawn icon or match the 14–22px scale.

- **`bs-notes` truncation has no expand affordance** — long notes are silently truncated at `max-width: 600px`. Add a toggle or a title attribute at minimum.

- **Header right-alignment is brittle** — conditional `margin-left: auto` + empty span placeholder produces different layouts depending on which combination of `render_warnings`, `shared_url`, and `page == 'brief-state'` is truthy. Replace with an unconditional flex spacer + a fixed right-side control group.

---

## Planned features (from `plan-ui-critique-fixes.md`)

These are scoped and ready to build. Listed here for visibility; details in the plan file.

- **[IN PLAN — Phase 1A]** Collapse Operations card into drawer; inline defer buttons; header flex fix
- **[IN PLAN — Phase 1B]** Last-pulse freshness indicator; Career full re-read guard; accessibility fixes
- **[IN PLAN — Phase 1C]** Alert Dismiss semantic fix; tokenize 4 undocumented colors; Settings Save primary; H1 fix; page title per route; badge collision fixes
- **[IN PLAN — Phase 2]** Meeting Prep panel with program selector (from `SiemensRCAGov/*/STATUS.md`), optional context input, inline Claude prep brief
- **[IN PLAN — Phase 3A]** Keyboard triage mode (`k`/`d`/`1`/`3`/`m`/`↓↑` bindings on act list)
- **[IN PLAN — Phase 3B]** Source linkage on act items (`↗ source` → read-only drawer)
- **[IN PLAN — Phase 3C]** Proactive pulse prompt: count analyses since last pulse, show "N new — run now?" trigger
- **[IN PLAN — Phase 3D]** Weekly Delta staleness nudge after 5 business days
- **[IN PLAN — Phase 3E]** Business-day-aware staleness (`_business_days_since()`) — no false alarms on weekends or after holidays
- **[IN PLAN — Phase 3F]** Program Pulse freshness in Meeting Prep dropdown + "Refresh signals" button
- **[IN PLAN — Phase 3G]** Weekly Delta catch-up label when > 8 days since last run

---

## Ideas (not yet sized)

- **Job run history surface** — the rolling log at `~/Library/Logs/transcript-analyzer.log` is never surfaced in the UI. A lightweight "Recent runs" section on `/files` or Settings showing the last 10 run timestamps and their outcomes (success, N files, cost) would answer "did anything new land?" better than the current stats row.

- **Settings health check** — a "Check config" button on Settings that probes: Claude CLI binary reachable, Drive mount accessible, prompt library keys present (DAILY, STANDUP, SOLUTION, EXEC, REDACT), context brief readable. Green/red per dependency. Currently "it works until it doesn't."

- **Shareable copy on Meeting Prep output** — the prep brief is ephemeral (lost on navigation). A "Copy as text" button (one click, copies bullets to clipboard) would make it immediately shareable over Slack/email without reformatting.

- **"New since last visit" indicator on file table** — no concept of "I've seen this analysis." A subtle "NEW" badge on file table rows newer than Brad's last page load would make it immediately scannable what arrived since he was last here.

- **Candidate item aging / auto-expiry** — candidates currently have no expiry. A soft expiry (dim + move to bottom after 5 business days; offer "Dismiss aged candidates" in bulk) would prevent inbox buildup without forcing manual triage.

- **Program Pulse trigger from Meeting Prep** — added as 3F, but longer term: should the program pulse run automatically on a schedule (e.g., daily alongside analysis), or at minimum prompt Brad when it's been more than 2 days? Currently it's a manual CLI operation that most people forget.

- **Cancel running job** — store `Popen` reference in `_job` dict and expose a `/job/cancel` route that sends `SIGTERM`. Currently there's no escape from a 3-minute Opus run that started on the wrong mode. (Blocked on the P1 orphan fix above — fix that first, then cancellation is a small addition.)

- **Keyboard shortcut for Weekly Delta** — `w` key, or `shift+d` for Delta, as a companion to the planned triage keybindings. Low effort once the keyboard triage mode (3A) is wired up.

- **Dark/ambient mode toggle** — currently hardcoded dark-only. Not a real need (Brad uses it in a dim environment) but occasionally useful for screen sharing. Low priority.
