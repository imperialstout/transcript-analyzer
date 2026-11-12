#!/usr/bin/env python3
"""Triage pending brief-state items.

  brief-triage.py list [--type act,they-owe] [--inbox | --accepted] [--limit N]
  brief-triage.py apply "<id6>:d <id6>:x <id6>:defer:tomorrow <id6>:delegate:colin ..."

Actions (short | long):
  a | accept          accept inbox candidate as its current type
  act[:DATE]          accept and force type=act, optionally set the deadline
  watch               accept and force type=watch  (NOT `w`; `w` is defer)
  delegate:NAME       accept as delegate, owner=NAME (single word; original owner kept in notes)
  d | done | complete mark complete
  x | drop | dismiss  drop
  w<YYYY-MM-DD> | w:<when> | defer:<when>   defer
  p | pin, u | unpin, k | keep
<when>/DATE: YYYY-MM-DD, today, tomorrow, mon..sun (next occurrence), +Nd, +Nw, nextweek.
INBOX items are unreviewed candidates extracted from meetings; ACCEPTED items are on the live list.
IDs are the first 6+ chars shown by `list`. A timestamped backup is written before any change.
"""
import re
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analyzer import brief_state as bs  # noqa: E402

_ORDER = {"act": 0, "they-owe": 1, "delegate": 2, "watch": 3, "insight": 4}

_WEEKDAYS = {
    "mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "wednesday": 2, "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4, "sat": 5, "saturday": 5, "sun": 6, "sunday": 6,
}
_WHEN_FORMS = "YYYY-MM-DD, today, tomorrow, mon..sun, +Nd, +Nw, nextweek"
_SIMPLE = {
    "a": "a", "accept": "a",
    "d": "d", "done": "d", "complete": "d",
    "x": "x", "drop": "x", "dismiss": "x",
    "p": "p", "pin": "p", "u": "u", "unpin": "u", "k": "k", "keep": "k",
}
_W_HELP = "'w' means defer (w<YYYY-MM-DD>, w:<when>, defer:<when>); to accept an item as a watch item use 'watch'"
_TOKEN_REMINDER = (
    "tokens: <id>:a|act[:DATE]|watch|delegate:NAME|d|x|defer:<when>|w<YYYY-MM-DD>|p|u|k "
    "(w=defer, watch=accept as watch)"
)


def _next_weekday(today, weekday):
    return today + timedelta(days=(weekday - today.weekday()) % 7 or 7)


def parse_when(s, today):
    s = s.strip().lower()
    if s == "today":
        return today
    if s == "tomorrow":
        return today + timedelta(days=1)
    if s == "nextweek":
        return _next_weekday(today, 0)
    if s in _WEEKDAYS:
        return _next_weekday(today, _WEEKDAYS[s])
    m = re.fullmatch(r"\+(\d+)([dw])", s)
    if m:
        return today + timedelta(days=int(m.group(1)) * (7 if m.group(2) == "w" else 1))
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            return date.fromisoformat(s)
        except ValueError:
            pass
    raise ValueError(f"unrecognized date {s!r}; accepted: {_WHEN_FORMS}")


def _future_date(s, today):
    d = parse_when(s, today)
    if d < today:
        raise ValueError(f"defer date {d.isoformat()} is in the past")
    return d.isoformat()


def normalize(action, today=None):
    """Map any accepted action spelling to (code, arg).

    Codes: a, act, watch, delegate, d, x, w, p, u, k. For w the arg is an ISO date;
    for act it is an ISO date or None; for delegate it is the owner name. Raises ValueError.
    """
    today = today or date.today()
    action = action.strip()
    low = action.lower()
    name, sep, arg = action.partition(":")
    name = name.lower()

    if not sep and low in _SIMPLE:
        return _SIMPLE[low], None
    m = re.fullmatch(r"w(\d{4}-\d{2}-\d{2})", low)
    if m:
        try:
            return "w", date.fromisoformat(m.group(1)).isoformat()
        except ValueError:
            raise ValueError(f"invalid date in {action!r}")
    if name in ("w", "defer"):
        if not arg.strip():
            raise ValueError(_W_HELP if name == "w" else f"defer needs a date; accepted: {_WHEN_FORMS}")
        return "w", _future_date(arg, today)
    if name == "watch":
        if sep:
            raise ValueError(f"'watch' takes no argument; {_W_HELP}")
        return "watch", None
    if name == "act":
        return "act", (parse_when(arg, today).isoformat() if arg.strip() else None)
    if name == "delegate":
        if not arg.strip() or " " in arg.strip():
            raise ValueError("delegate needs a single-word name, e.g. delegate:colin")
        return "delegate", arg.strip()
    if low == "w":
        raise ValueError(_W_HELP)
    raise ValueError(f"bad action {action!r}")


def _resolve(prefix, ids):
    m = [x for x in ids if x.startswith(prefix)]
    if not m:
        raise ValueError(f"id {prefix!r} not found; nothing applied")
    if len(m) > 1:
        raise ValueError(f"id {prefix!r} is ambiguous ({len(m)} matches); use more characters; nothing applied")
    return m[0]


def build_plan(tokens, by_id, today):
    plan = []
    seen = {}
    for t in tokens:
        p, sep, a = t.partition(":")
        if not sep or not p or not a:
            raise ValueError(f"bad token {t!r}; expected id:action")
        full = _resolve(p.lower(), by_id)
        if full in seen:
            raise ValueError(f"{p!r} and {seen[full]!r} are the same item; one action per item; nothing applied")
        seen[full] = p
        try:
            code, arg = normalize(a, today)
        except ValueError as e:
            raise ValueError(f"{p}: {e}; nothing applied")
        plan.append((full, code, arg))
    return plan


def run_apply(tokens, today=None):
    """Validate every token, back up, then apply. Returns the result text. Raises ValueError before any write."""
    today = today or date.today()
    if not tokens:
        raise ValueError("no actions given")
    by_id = {i["id"]: i for i in bs.all_items()}
    plan = build_plan(tokens, by_id, today)

    backup = bs._STATE_PATH.with_name(f"brief-state.backup-{datetime.now():%Y%m%d-%H%M%S}.json")
    shutil.copy2(bs._STATE_PATH, backup)
    note = f"triage {today.isoformat()}"
    counts = {}
    msgs = []

    def bump(k):
        counts[k] = counts.get(k, 0) + 1

    for full, code, arg in plan:
        item = by_id[full]
        inbox = bool(item.get("source_analyzed"))
        if code == "d":
            bs.close(full, "complete", note)
        elif code == "x":
            bs.close(full, "dropped", note)
        elif code == "p":
            bs.pin(full)
        elif code == "u":
            bs.unpin(full)
        elif code == "w":
            bs.defer(full, arg, note)
        elif code == "k":
            pass
        elif code == "a":
            if not inbox:
                msgs.append(f"{full[:6]}: already accepted; 'a' ignored")
                continue
            bs.accept_candidate(full, item["type"])
        else:  # act / watch / delegate: accept, or retype if already accepted
            if item["type"] != code:
                bump("retyped")
            bs.accept_candidate(full, code)
            if code == "delegate":
                bs.set_owner(full, arg, f"delegated from {item.get('owner') or '?'}, {note}")
                bump("delegated")
            elif code == "act" and arg:
                old = item.get("deadline")
                bs.set_deadline(full, arg, f"deadline was {old}, changed in {note}" if old else "")
            if not inbox:
                continue
            code = "a"
        bump(code)
    return "\n".join([*msgs, f"applied {len(plan) - len(msgs)} actions {counts}; backup: {backup}"])


def _pending():
    items = [i for i in bs.all_items() if i.get("status") == "pending"]
    today = date.today().isoformat()

    def key(i):
        dl = i.get("deadline")
        overdue = 0 if (dl and dl < today) else 1
        inbox = 1 if i.get("source_analyzed") else 0
        return (inbox, _ORDER.get(i["type"], 9), 0 if i.get("pinned") else 1, overdue, dl or "9999", i["created_at"])

    return sorted(items, key=key)


def cmd_list(args):
    types = None
    limit = None
    if "--type" in args:
        types = set(args[args.index("--type") + 1].split(","))
    if "--limit" in args:
        limit = int(args[args.index("--limit") + 1])
    today = date.today().isoformat()
    cur = None
    n = 0
    for i in _pending():
        inbox = bool(i.get("source_analyzed"))
        if types and i["type"] not in types:
            continue
        if "--inbox" in args and not inbox:
            continue
        if "--accepted" in args and inbox:
            continue
        if cur != (inbox, i["type"]):
            cur = (inbox, i["type"])
            print(f"\n== {'INBOX' if inbox else 'ACCEPTED'} / {i['type'].upper()} ==")
        dl = i.get("deadline")
        flag = "OVERDUE " if dl and dl < today else ""
        dl_s = f"due {dl}" if dl else "no date"
        pin = "PIN " if i.get("pinned") else ""
        age = (date.today() - date.fromisoformat(i["created_at"][:10])).days
        print(f"{i['id'][:6]} | {pin}{flag}{dl_s} | {age}d old | {i['owner']} | {i['text'][:170]}")
        n += 1
        if limit and n >= limit:
            break
    print(f"\n{n} shown")
    print(_TOKEN_REMINDER)


def cmd_apply(args):
    tokens = " ".join(args).replace(",", " ").split()
    try:
        print(run_apply(tokens))
    except ValueError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("list", "apply"):
        sys.exit(__doc__)
    (cmd_list if sys.argv[1] == "list" else cmd_apply)(sys.argv[2:])
