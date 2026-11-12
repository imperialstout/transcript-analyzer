#!/usr/bin/env python3
"""Triage pending brief-state items.

  brief-triage.py list [--type act,they-owe] [--inbox | --accepted] [--limit N]
  brief-triage.py apply "<id6>:d <id6>:x <id6>:w2026-11-20 <id6>:p ..."

Actions: d=done  x=drop  a=accept inbox candidate into the live list  w<YYYY-MM-DD>=defer
         p=pin  u=unpin  k=keep (no-op)
INBOX items are unreviewed candidates extracted from meetings; ACCEPTED items are on the live list.
IDs are the first 6+ chars shown by `list`. A timestamped backup is written before any change.
"""
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analyzer import brief_state as bs  # noqa: E402

_ORDER = {"act": 0, "they-owe": 1, "delegate": 2, "watch": 3, "insight": 4}


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


def cmd_apply(args):
    tokens = " ".join(args).replace(",", " ").split()
    if not tokens:
        sys.exit("no actions given")
    by_prefix = {}
    for i in bs.all_items():
        by_prefix.setdefault(i["id"], i)
    ids = list(by_prefix)

    def resolve(p):
        m = [x for x in ids if x.startswith(p)]
        return m[0] if len(m) == 1 else None

    plan = []
    for t in tokens:
        if ":" not in t:
            sys.exit(f"bad token {t!r}; expected id:action")
        p, a = t.split(":", 1)
        full = resolve(p)
        if not full:
            sys.exit(f"id {p!r} not found or ambiguous; nothing applied")
        if a not in ("d", "x", "a", "p", "u", "k") and not (a.startswith("w") and len(a) == 11):
            sys.exit(f"bad action {a!r} for {p}; nothing applied")
        plan.append((full, a))

    backup = bs._STATE_PATH.with_name(f"brief-state.backup-{datetime.now():%Y%m%d-%H%M%S}.json")
    shutil.copy2(bs._STATE_PATH, backup)
    note = f"triage {date.today().isoformat()}"
    counts = {}
    for full, a in plan:
        if a == "d":
            bs.close(full, "complete", note)
        elif a == "x":
            bs.close(full, "dropped", note)
        elif a == "a":
            bs.accept_candidate(full, by_prefix[full]["type"])
        elif a == "p":
            bs.pin(full)
        elif a == "u":
            bs.unpin(full)
        elif a.startswith("w"):
            bs.defer(full, a[1:], note)
        counts[a[0]] = counts.get(a[0], 0) + 1
    print(f"applied {len(plan)} actions {counts}; backup: {backup}")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("list", "apply"):
        sys.exit(__doc__)
    (cmd_list if sys.argv[1] == "list" else cmd_apply)(sys.argv[2:])
