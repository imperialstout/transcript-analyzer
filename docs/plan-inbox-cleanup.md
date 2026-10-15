# Plan: Inbox cleanup + backlog quick wins

**Context:** This plan picks up after commit `6a70bf2` (merged triage alerts into brief-state inbox, moved Meeting Prep to /prep). All changes go in `analyzer/ui.py` (inline Jinja template + routes) unless noted.

---

## Already done (this session)

- Triage Alerts merged into Brief State inbox as urgent candidates (`urgency="high"` tier)
- Meeting Prep moved to `/prep` + nav tab
- All `/alerts/*` routes and `alerts.json` store retired

---

## Task 1 — Inbox Accept type correction (P2, ~15 min)

**File:** `analyzer/ui.py` — `_BRIEF_STATE_BODY`, the normal-items loop in the inbox section.

**Problem:** Accept always promotes with the auto-classified type. No way to override without editing JSON by hand.

**Fix:** Add a `<select>` to the Accept form for normal (non-urgent) candidates, defaulting to the item's auto-classified type.

```html
<!-- replace the hidden type input on the normal-items Accept form -->
<select name="type" class="btn-secondary btn-sm" style="padding:3px 6px;font-size:11px">
  <option value="act"     {% if item.type == 'act' %}selected{% endif %}>act</option>
  <option value="watch"   {% if item.type == 'watch' %}selected{% endif %}>watch</option>
  <option value="they-owe"{% if item.type == 'they-owe' %}selected{% endif %}>they-owe</option>
  <option value="delegate"{% if item.type == 'delegate' %}selected{% endif %}>delegate</option>
  <option value="insight" {% if item.type == 'insight' %}selected{% endif %}>insight</option>
</select>
```

No backend changes needed — the route already reads `request.form.get("type", "act")`.

---

## Task 2 — Career full re-read confirmation guard (P2, ~5 min)

**File:** `analyzer/ui.py` — the `careerSubmit()` JS function (already exists, ~line 834).

**Problem:** The full re-read checkbox triggers a potentially $5–15 / 10-min Opus run with no warning. The `careerSubmit()` function already has the confirm guard wired — but checking the rendered template: the `#career-full` checkbox exists and `careerSubmit` is called `onsubmit`. Verify the guard fires correctly:

```js
function careerSubmit(form, startPollFn) {
  const full = form.querySelector('#career-full');
  if (full && full.checked) {
    if (!confirm('Full re-read will process all analyzed files through Opus. This may take 10+ minutes and cost $5–15. Proceed?')) return false;
  }
  startPollFn('Running Career Trajectory review…');
  return true;
}
```

Check whether the form's `onsubmit` actually passes `startPoll` correctly — the Operations drawer template may wire it as `onsubmit="return careerSubmit(this, startPoll)"`. Verify and fix if broken.

---

## Task 3 — `.bs-stale-badge` dead CSS (P3, ~2 min)

**File:** `analyzer/ui.py` — CSS block.

**Problem:** `.bs-stale-badge` is defined in CSS but never rendered anywhere in the templates. Confirmed by grepping: `grep -n "bs-stale-badge" analyzer/ui.py` — it appears in CSS but not in any template block.

**Fix:** Remove the CSS rule entirely. No template change needed.

---

## Task 4 — Closed items table collapse (P3, ~5 min)

**File:** `analyzer/ui.py` — `_BRIEF_STATE_BODY`, the recent-closed section near the bottom.

**Problem:** The closed-items table is always visible whenever any closures exist in the past 7 days. It takes real estate confirming done work.

**Fix:** Wrap in `<details>` defaulting closed:

```html
<details>
  <summary style="cursor:pointer;font-size:12px;color:var(--muted);padding:8px 0">
    {{ recent_closed|length }} recently closed
  </summary>
  <!-- existing table here -->
</details>
```

---

## Task 5 — `brief-state.json` mid-write 500 guard (P2, ~10 min)

**File:** `analyzer/brief_state.py` — `_load()` function.

**Problem:** If the file is partially written when a page request arrives, `json.loads()` raises and the endpoint returns a bare 500.

**Fix:** `_load()` already has a bare `except Exception: return []` — that should swallow the parse error. Audit whether the real failure path is in `_save()` (non-atomic write). The fix is to make `_save()` write atomically:

```python
def _save(items: list[dict]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=2), encoding="utf-8")
    tmp.replace(_STATE_PATH)
```

`filesystem.write_text()` already does this pattern — replicate it here.

---

## Execution order

1. Task 5 (atomic save) — highest correctness risk, 10 min, do first
2. Task 1 (type select) — next session quality-of-life
3. Task 2 (career guard) — verify existing JS is wired, fix if not
4. Task 3 (dead CSS) — trivial, bundle with any other edit
5. Task 4 (closed items collapse) — last, purely cosmetic

After these are done, the backlog P1/P2 items are clear. Remaining work is all "Ideas" tier.
