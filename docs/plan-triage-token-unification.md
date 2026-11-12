# Plan: Unify /brief-triage and /morning-brief action vocabulary

**Problem:** `/morning-brief` triage uses `N:act[:DATE]`, `N:watch`, `N:delegate:NAME`, `N:drop` (position numbers). `/brief-triage` uses `id:a|d|x|w<date>|p|u|k` (6-char IDs). Brad types the morning-brief vocabulary into brief-triage and it fails or has to be hand-translated. Also: no delegate action, no natural-language dates, and `w` means "watch" in one and "defer" in the other.

**Approach:** Keep the short codes as they are (the `list` output and the command doc already teach them). Add long-word aliases and the missing actions in `bin/brief-triage.py` so both vocabularies work. Do not change `/morning-brief` or its position-number scheme.

**Files:**
- `bin/brief-triage.py` (parser + apply)
- `analyzer/brief_state.py` (two small helpers)
- `~/.claude/commands/brief-triage.md` (shorthand line, step 3)
- `docs/morning-brief/brief-state-schema.md` (only if new fields are written; see Task 2)

Repo state note: `bin/brief-triage.py` is untracked and `analyzer/brief_state.py`, `CLAUDE.md`, and the schema doc already have uncommitted edits. Do not revert or fold unrelated changes; work on top.

---

## Task 1: Token normalizer in brief-triage.py

Add `normalize(action: str) -> tuple[str, str | None]` returning `(code, arg)`. Run it on every token before validation. Validation and the backup still happen before any write, and any bad token aborts the whole batch (existing behavior).

| Input (case-insensitive) | Canonical | Notes |
|---|---|---|
| `a`, `accept` | `a` | accept as the item's current type |
| `act` | `a` + retype to act | accept and force type=act |
| `act:DATE` | `a` + retype act + deadline | see Task 2 |
| `watch` | `a` + retype watch | NOT `w`; `w` stays defer-only |
| `delegate:NAME` | `a` + retype delegate + owner=NAME | see Task 2 |
| `d`, `done`, `complete` | `d` | |
| `x`, `drop`, `dismiss` | `x` | |
| `w<YYYY-MM-DD>` | defer | unchanged |
| `defer:<when>`, `w:<when>` | defer | `<when>` via date parser below |
| `p`, `pin` / `u`, `unpin` / `k`, `keep` | as-is | |

Bare `w` or `watch:` with junk: exit with a message that says `w` means defer and `watch` means accept-as-watch. This removes the trap.

**Date parser** (`parse_when(s, today)`, shared by `defer:` and `act:`): accepts `YYYY-MM-DD`, `today`, `tomorrow`, weekday names (`mon`..`sunday`; the next occurrence strictly after today), `+Nd`, `+Nw`, `nextweek` (next Monday). Anything else: exit with the accepted forms. Reject dates in the past for `defer:`.

**Tokenizing:** current code splits on whitespace and commas, then `split(":", 1)`. Change to split the id off the first colon only, then split the remainder on the first colon again (`delegate:colin` -> action `delegate`, arg `colin`). Names with spaces are not supported; document that.

## Task 2: brief_state helpers

`accept_candidate(item_id, item_type)` already clears `source_analyzed` and sets type. Add:

- `set_deadline(item_id, iso_date) -> bool`: sets `deadline`.
- `set_owner(item_id, owner) -> bool`: sets `owner`.

Both follow the `_load()` / mutate / `_save()` pattern used by `set_loe`. For `delegate:NAME`, preserve the original owner in `notes` (e.g. "delegated from Brad, triage <date>") so nothing is lost; this uses the existing `notes` field, so no schema change. If `deadline` or `owner` semantics are not already in the schema doc, add one line each. `act:DATE` on an item that already has a deadline overwrites it and records the old value in `notes`.

Valid-type check: `delegate` and `watch` are in `_VALID_TYPES` (confirm before relying on it).

## Task 3: apply() flow

Keep the order: parse all tokens -> resolve all IDs -> validate -> backup -> apply. Apply compound actions in order: accept/retype -> set_owner -> set_deadline. Extend the counts summary to use the canonical code (`a`, `d`, `x`, `w`, `p`, ...) plus `delegated` and `retyped` so the one-line result is accurate.

Edge cases to handle explicitly:
- `act`/`watch`/`delegate` on an item already ACCEPTED (not in inbox): retype only, no error.
- `a` on a non-inbox item: currently a silent no-op via `accept_candidate`; make it a no-op with a printed note rather than a counted action.
- Same ID twice in one batch: reject with a message (currently the last write silently wins).
- Prefix ambiguity: current `resolve` returns None for both "not found" and "ambiguous"; split the error messages.

## Task 4: Docs

- `brief-triage.md` step 3: replace the shorthand line with the full table above in compact form, list both short and long forms, and state `w<date>` = defer, `watch` = accept as watch. Remove the "delegate not available" gap.
- Add the one-line "Honeycomb" and other rules untouched.
- `list` output footer: print a one-line token reminder after the "N shown" line.

## Task 5: Tests

There is no `tests/` directory in the repo today. Add `tests/test_brief_triage.py` (pytest) that points `bs._STATE_PATH` at a temp file with fixture items (one inbox act, one accepted they-owe, one inbox they-owe). Cases:

1. Every alias maps to the right canonical action.
2. `defer:tomorrow`, `defer:wednesday`, `defer:+3d`, `defer:2026-11-20` against a fixed `today`; weekday rule when today is that weekday.
3. `delegate:colin` on an inbox item: type=delegate, owner=colin, source_analyzed cleared, original owner in notes.
4. `act:2026-11-20` sets deadline; on an item with an existing deadline, old value goes to notes.
5. Bad token anywhere in a batch: nothing written, no backup file created.
6. Duplicate ID in a batch rejected; ambiguous prefix gives a distinct error.
7. Regression: the original `id:a|d|x|p|u|k|w2026-11-20` tokens behave exactly as before.

Make `today` injectable (parameter, not `date.today()` inside the parser) so tests are deterministic.

## Out of scope

- Changing `/morning-brief` to use IDs, or merging the two commands.
- UI (`analyzer/ui.py`) changes; the web inbox has its own Accept form (see `plan-inbox-cleanup.md`, Task 1).
- Multi-word delegate names.

## Acceptance

Brad's actual input from 2026-11-12 works unchanged:

`513feb:drop 7e4461:defer:tomorrow 72cf24:drop 2dada4:done 3537a6:defer:wednesday 5207f6:delegate:colin`

Expected: drops/done as written, deferrals resolve to the next day / next Wednesday, and 5207f6 becomes a delegate item owned by colin and leaves the inbox.
