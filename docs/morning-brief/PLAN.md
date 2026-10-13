# Morning Brief — Enhancement Plan

## Problem statement

The daily triage tool surfaces items but doesn't track whether Brad acted on them. Act items from the morning brief die at end-of-session. There is no carryforward, no accountability for commitments others made, no expiry on watch items, and the sharpest analysis (Private Read) never surfaces in the queue. Volume (10–12 items) exceeds reliable execution.

---

## Architecture

### Data layer (universal, machine-global)

All state lives at `~/.cache/transcript-analyzer/brief-state.json`. This is the same pattern as `alerts.json`. No project owns it — any Claude Code session on this machine can read it via the session-start hook.

The synthesized `[MORNING BRIEF]` file in `Analyzed/` remains the human-readable record. `brief-state.json` is the machine-readable twin used for carryforward, expiry, and cross-project injection.

### Discoverability

A session-start hook (`bin/brief-state-hook.py`) runs at the start of every Claude Code session and injects pending act items into the conversation context. This means SiemensRCAGov, Siemens3RR, and every other project automatically see what's open — without any project-specific wiring.

Global `~/.claude/CLAUDE.md` holds the canonical pointer so Claude can query brief-state.json on demand in addition to the hook injection.

```
Daily Pulse (analyzer/synthesize.py)
    ↓
Morning Brief (claude command)
    ↓ after triage
brief-state.json (~/.cache/transcript-analyzer/)   ← machine-global
    ↓
brief-state-hook.py (session start)
    ↓
Context injection in every project (SiemensRCAGov, Siemens3RR, etc.)
```

---

## Phases

### Phase 0 — Prompt fixes (no code)

**Deliverable:** Rewritten `~/.claude/commands/morning-brief.md`

Changes:
1. **Carryforward:** Step 2 reads prior brief's `act` items from `brief-state.json` (fallback: last `[MORNING BRIEF]` file). Any item with status `pending` resurfaces at the top of the queue tagged `[OVERDUE]`.
2. **They-owe-you bucket:** Step 3 extracts commitments others made (identified by pattern: "X will do Y by Z"). These are a separate section below Brad's queue — not competing with his act items.
3. **Private Read feed:** Step 3 pulls the top 1–2 items from the pulse's "Private read" section into the queue as `[INSIGHT]` entries. No action required — framing for how to handle other items.
4. **Queue cap:** Step 3 presents max 7 actionable items. Remainder goes to a `## Held` section; Brad can request it with `more`.
5. **Step 5 writes brief-state.json:** After Brad's triage reply, the command writes each item to the store with status and type.

**Files changed:** `~/.claude/commands/morning-brief.md` only.

---

### Phase 1 — State store module

**Deliverable:** `analyzer/brief_state.py`

New module, parallel to `alerts.py`. Manages `brief-state.json`.

**Schema:** see [brief-state-schema.md](brief-state-schema.md)

**API surface:**
- `upsert(item: dict) -> None` — write or update one item by id
- `pending_acts() -> list[dict]` — items with type=act, status=pending
- `pending_they_owe() -> list[dict]` — items with type=they-owe, status=pending
- `close(item_id: str, status: str, notes: str) -> None` — mark complete/deferred/expired
- `expire_stale(business_days: int) -> list[dict]` — returns items past their deadline; marks them expired

Integration point: `synthesize.py` calls `brief_state.expire_stale()` at the top of each daily pulse run so the morning brief always sees fresh expiry state.

---

### Phase 2 — EOD close command

**Deliverable:** `~/.claude/commands/brief-close.md`

A 2-minute evening command:
1. Reads `brief_state.pending_acts()` — today's open act items
2. Presents them as a numbered list
3. Brad replies: `1:done, 2:defer:tomorrow, 3:still-open`
4. Command writes status back to `brief-state.json`

This closes the accountability loop: tomorrow's morning brief sees what actually got done vs. what deferred.

---

### Phase 3 — Expiry and escalation rules

**Deliverable:** Logic in `brief_state.py` + prompt updates

Rules:
- `act` items pending > 1 business day → resurface as `[OVERDUE]` in next brief
- `watch` items with no pulse mention for 5 business days → resurface as `[WATCH EXPIRED]`
- `they-owe` items past deadline with no pulse evidence of delivery → promote to Brad's act queue as `[OVERDUE: follow up on X]`
- `delegate` items > 7 days → prompt Brad to confirm still delegated or re-own

Expiry logic runs during daily pulse synthesis so state is always current before the next brief.

---

### Phase 4 — UI panel

**Deliverable:** New "Brief State" tab in `analyzer/ui.py`

Low priority — the command-line close is sufficient for daily use. Add when the data layer is stable.

Contents:
- Today's pending act items with inline close buttons
- They-owe items with deadline and status
- EOD close button (triggers brief-close logic without needing Claude Code)
- 7-day act item history

---

## File map

```
transcript-analyzer/
  docs/
    morning-brief/
      PLAN.md                         ← this file
      brief-state-schema.md           ← Phase 1 data model spec
  analyzer/
    alerts.py                         ← existing (model for brief_state.py)
    brief_state.py                    ← Phase 1 (new)
  bin/
    brief-state-hook.py               ← session-start hook (new)

~/.cache/transcript-analyzer/
  alerts.json                         ← existing
  brief-state.json                    ← Phase 1 (new)

~/.claude/
  CLAUDE.md                           ← add brief-state pointer (this session)
  commands/
    morning-brief.md                  ← Phase 0 rewrite
    brief-close.md                    ← Phase 2 (new)
  settings.json (global)              ← add session-start hook
```

---

## Sequencing rationale

Phase 0 delivers immediate value (better triage, fewer missed items) with zero code. Phase 1 is the data foundation everything else builds on. Phases 2–4 are additive and can be done in any order once Phase 1 is stable. The hook (Phase 0 + global settings change) is what makes SiemensRCAGov and Siemens3RR aware of the state without project-specific wiring.

---

## Open questions before Phase 1

1. Should `they-owe` items with no deadline ever expire? (Suggested: 10 business days → watchlist, not auto-drop)
2. Should brief-state.json be committed to the repo (`.gitignore` it)? It contains program-sensitive content. Decision: gitignore, stays local.
3. Does Phase 2 (EOD close) need a separate Claude command or can it be an option flag on the morning brief? (`/morning-brief --close`)
