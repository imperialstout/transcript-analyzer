#!/usr/bin/env python3
"""Session-start hook: inject pending morning brief items into Claude context.

Reads ~/.cache/transcript-analyzer/brief-state.json and prints a short
summary of pending act items and overdue they-owe items to stdout.
Claude Code's PreToolUse/session-start hook captures this output and
prepends it to the conversation context.

Output is suppressed (exit 0, no output) when:
  - brief-state.json doesn't exist yet (Phase 1 not deployed)
  - There are no pending items
  - The most recent brief is older than 3 business days (stale context)

Output is a compact block, not a wall of text. The goal is a 3-second
scan, not a re-triage.
"""

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

_STATE_PATH = Path("~/.cache/transcript-analyzer/brief-state.json").expanduser()
_STALE_DAYS = 3  # suppress if most recent brief is older than this


def _business_days_since(iso_date: str) -> int:
    """Count business days between iso_date and today."""
    try:
        start = date.fromisoformat(iso_date[:10])
    except ValueError:
        return 999
    today = date.today()
    if start > today:
        return 0
    days = 0
    current = start
    while current < today:
        current += timedelta(days=1)
        if current.weekday() < 5:  # Mon–Fri
            days += 1
    return days


def main() -> None:
    if not _STATE_PATH.exists():
        sys.exit(0)

    try:
        items = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        sys.exit(0)

    if not items:
        sys.exit(0)

    # Suppress if most recent brief is stale (vacations, weekends, etc.)
    dates = [i.get("created_at", "")[:10] for i in items if i.get("created_at")]
    if dates:
        most_recent = max(dates)
        if _business_days_since(most_recent) > _STALE_DAYS:
            sys.exit(0)

    pinned_acts = [
        i for i in items
        if i.get("pinned") and i.get("type") == "act" and i.get("status") == "pending"
    ]
    pinned_ids = {i["id"] for i in pinned_acts}
    pending_acts = [
        i for i in items
        if i.get("type") == "act" and i.get("status") == "pending"
        and not i.get("source_analyzed")  # exclude inbox candidates
        and i["id"] not in pinned_ids
    ]
    overdue_they_owe = [
        i for i in items
        if i.get("type") == "they-owe"
        and i.get("status") == "pending"
        and i.get("deadline")
        and date.fromisoformat(i["deadline"]) < date.today()
    ]
    overdue_acts = [
        i for i in pending_acts
        if i.get("deadline")
        and date.fromisoformat(i["deadline"]) < date.today()
    ]

    if not pinned_acts and not pending_acts and not overdue_they_owe:
        sys.exit(0)

    lines = ["---", "**Open from morning brief**"]

    def _fmt(item, show_deadline=True):
        loe = f" [{item['loe']}]" if item.get("loe") else ""
        deadline = f" [due {item['deadline']}]" if (show_deadline and item.get("deadline")) else ""
        note = f" — {item['notes']}" if item.get("notes") else ""
        return f"  • {item['text']}{loe}{deadline}{note}"

    if pinned_acts:
        lines.append(f"\n📌 Pinned ({len(pinned_acts)}):")
        for item in pinned_acts:
            lines.append(_fmt(item))

    if overdue_acts:
        lines.append(f"\n⚠ OVERDUE ({len(overdue_acts)} item{'s' if len(overdue_acts) != 1 else ''}):")
        for item in overdue_acts:
            deadline = item.get("deadline", "")
            loe = f" [{item['loe']}]" if item.get("loe") else ""
            note = f" — {item['notes']}" if item.get("notes") else ""
            lines.append(f"  • [{deadline}] {item['text']}{loe}{note}")

    still_pending = [i for i in pending_acts if i not in overdue_acts]
    if still_pending:
        lines.append(f"\nPending act items ({len(still_pending)}):")
        for item in still_pending:
            lines.append(_fmt(item))

    if overdue_they_owe:
        lines.append(f"\nThey owe you — past deadline ({len(overdue_they_owe)}):")
        for item in overdue_they_owe:
            owner = item.get("owner", "?")
            deadline = item.get("deadline", "?")
            lines.append(f"  • {owner} [{deadline}]: {item['text']}")

    brief_file = items[0].get("source_brief", "") if items else ""
    if brief_file:
        brief_date = brief_file[20:30] if len(brief_file) > 30 else ""
        lines.append(f"\n_Brief from {brief_date}. Run /morning-brief to triage today._")

    lines.append("---")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
