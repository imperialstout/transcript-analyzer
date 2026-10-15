# UI Refresh Handover — branch `impeccable/ui-refresh`

## What has been done (this context)

### Session work
Two commits ahead of `main`:

1. **`994a32e` — structural rebuild**
   - CSS fully tokenized: 21 custom properties in `:root` covering all badge backgrounds, alert/danger/inbox surfaces, `--purple`/`--cyan`/`--gold`. Zero undocumented hex outside `:root`.
   - 8 button variants → 3: `.btn` (primary/accent), `.btn-secondary`, `.btn-destructive` + `.btn-sm` modifier. Old `.bs-btn`, `.btn-dismiss-all`, `.alert-dismiss` gone.
   - Obligations hero card on `/` — pending act items with overdue enrichment render above the stats tiles. Red border when any overdue. "View full Brief State →" link below.
   - Defer chips: `+1d`, `+3d`, `Mon` on every act item (home + Brief State). `deferQuick()` JS computes date and POSTs to existing defer endpoint. Calendar fallback alongside.
   - Nav order: Brief State first.
   - Scrollbar + text selection themed from palette. Header `position: sticky`.

2. **`41b497d` — page restructure**
   - **Home** stripped to: triage alerts → obligations hero → stats tiles → 25-row file table. Operations card removed.
   - **Analyzed Files table** capped at 25 rows with "Showing 25 of N — open Analyzed/ in Finder" footer.
   - **Brief State** is now the action surface: Operations card (Run Analysis, Daily Pulse, Weekly Delta, Catch Up, Career, Timecard) lives at the top. Job panel follows. Obligations, watches, delegated, they-owe, closed below.
   - Brief State route now passes job state so the panel renders and polls correctly.

### State of the app
- Server runs on port 7070. Restart: `kill $(lsof -ti :7070) && source ~/.venvs/transcript-analyzer/bin/activate && python -m analyzer ui`
- All pages parse/render clean (`python -c "from analyzer.ui import create_app; create_app()"`)
- Branch pushed to `origin/impeccable/ui-refresh`

---

## What still needs doing

### P0 — verified from critique (score 17/40, snapshot in `.impeccable/critique/2026-10-15T00-03-29Z__analyzer-ui-py.md`)

**Brief State defer row layout is too tall** (observed in session screenshots)
- Each act item currently has the Done/Drop buttons on one row and the defer chips + calendar on a separate `.bs-defer-row` below it — so each item takes 3 visual lines.
- Fix: collapse Done / Drop / +1d / +3d / Mon / date / Defer all onto a single `bs-actions` flex row. The calendar input is narrow enough (width ~120px) to sit inline.

**Home obligations hero has no "all clear" state**
- When `pending_acts` is empty, the hero card disappears entirely but there's no positive confirmation. A quiet "All clear — no pending acts" line in muted text would close the loop.

### P1 — remaining critique issues

| Issue | Fix |
|---|---|
| No persistent system health signal (critique P1) | Add a health strip or staleness color to the "Last analyzed" stat tile — if `last_analyzed` is >8h old, show the tile value in `--orange`; if >24h, `--red`. No new route needed — compute in the home route and pass a `last_analyzed_stale` flag. |
| `.badge-pulse` and `.badge-delta` were visually identical greens (noted in critique) | Already partially fixed by tokenization — verify both still distinct at runtime. `.badge-pulse` uses `--bg-pulse: #1e3020` / `--green`; `.badge-delta` uses `--bg-daily: #1e3a2c` / `--green`. They are slightly different but may still read as identical — consider giving delta `--cyan` text. |
| "Restart server" button styled as primary blue (critique minor) | Already fixed in this context — it's now `.btn-secondary`. Verify in browser. |
| 10px nav badge font borderline accessible (critique minor) | Nav badge now uses `font-size:11px` — verify. |

### P2 — next phase work (not started)

These are the items Brad flagged in the session as the next phase:

1. **`/impeccable polish`** — final quality pass once the layout is stable. Focus areas: transition timing consistency, focus rings (currently absent on most controls), empty states (no analyzed files yet).

2. **`/impeccable harden`** — production-readiness: error states on form submissions, the "all clear" obligations state, edge cases (job already running banner shown on Brief State when arriving mid-run).

3. **`/impeccable clarify`** — copy pass: synthesis mode labels in the Operations card could be clearer ("Weekly Delta" vs "Slack Delta" — the file tag says `[SLACK DELTA]`; the UI says "Weekly Delta" — make consistent). Date input labels are still tooltip-only.

4. **PR to `main`** — once P0/P1 above are resolved, open a PR from `impeccable/ui-refresh` into `main`.

---

## Key files

| File | Role |
|---|---|
| `analyzer/ui.py` | Entire UI — single Python file with inline Jinja2 templates. ~1350 lines after this session. |
| `DESIGN.md` | Design system source of truth. Tokens, components, do/don't rules. Read before any edit. |
| `PRODUCT.md` | Product context: sole user (Brad), operating environment, principles. |
| `.impeccable/critique/2026-10-15T00-03-29Z__analyzer-ui-py.md` | Heuristic audit snapshot: 17/40, p0/p1/p2 findings with exact fix commands. |

## How to run the impeccable skill in the next context

```
/impeccable polish analyzer/ui.py
```
or for the defer row fix specifically:
```
/impeccable layout analyzer/ui.py
```

The skill will run `impeccable context --target analyzer/ui.py` automatically and load DESIGN.md + PRODUCT.md. The critique snapshot is in `.impeccable/critique/` and will be referenced.
