"""Morning brief state store.

Manages ~/.cache/transcript-analyzer/brief-state.json — the machine-global
record of items surfaced in morning triage sessions: act items Brad owns,
watch items to monitor, delegate items, they-owe commitments, and day-specific
insights.

Designed as a parallel to alerts.py — same patterns, richer schema.

Schema per item:
  id             str   SHA-1(source_brief + "|" + text)[:12]
  text           str   one-line label from triage queue
  type           str   act | watch | delegate | they-owe | insight
  owner          str   "Brad" for act/watch; person name for delegate/they-owe
  deadline       str|null  ISO date or null
  source_brief   str   basename of the [MORNING BRIEF] file
  source_pulse   str   basename of the [DAILY PULSE] file
  status         str   pending | complete | deferred | expired | dropped
  created_at     str   ISO datetime UTC
  closed_at      str|null
  deferred_to    str|null  ISO date
  notes          str
  resurfaced_count  int
  program        str|null  program key matching STATUS.md frontmatter (e.g. "SI-RCA",
                            "presales-energy", "siemens-general"); None = unassigned
"""

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_STATE_PATH = Path("~/.cache/transcript-analyzer/brief-state.json").expanduser()

_VALID_TYPES = {"act", "watch", "delegate", "they-owe", "insight"}
_VALID_STATUSES = {"pending", "complete", "deferred", "expired", "dropped", "dismissed"}

# Business-day expiry thresholds by type (calendar days used as approximation)
_EXPIRY_BUSINESS_DAYS = {
    "act": 1,       # act items without deadline expire after 1 business day
    "watch": 5,     # watch items expire if not re-mentioned for 5 business days
    "delegate": 7,  # delegate items prompt re-confirmation after 7 days
    "insight": 1,   # insights are day-specific; expire next business day
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _business_days_since(iso_date_or_dt: str) -> int:
    """Count business days between a date/datetime string and today."""
    try:
        start = date.fromisoformat(iso_date_or_dt[:10])
    except (ValueError, TypeError):
        return 0
    today = date.today()
    if start >= today:
        return 0
    count = 0
    current = start
    while current < today:
        current += timedelta(days=1)
        if current.weekday() < 5:
            count += 1
    return count


def _item_id(source_brief: str, text: str) -> str:
    return hashlib.sha1(f"{source_brief}|{text}".encode()).hexdigest()[:12]


def _load() -> list[dict]:
    if not _STATE_PATH.exists():
        return []
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(items: list[dict]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(items, indent=2), encoding="utf-8")


def upsert(item: dict) -> None:
    """Write or update one item by id. If id is absent, generate it from source_brief + text."""
    items = _load()
    if "id" not in item:
        item = dict(item)
        item["id"] = _item_id(item.get("source_brief", ""), item.get("text", ""))

    existing_ids = {i["id"]: idx for idx, i in enumerate(items)}
    if item["id"] in existing_ids:
        items[existing_ids[item["id"]]].update(item)
    else:
        # Fill in defaults for required fields
        defaults = {
            "type": "act",
            "owner": "Brad",
            "deadline": None,
            "source_brief": "",
            "source_pulse": "",
            "status": "pending",
            "created_at": _now_iso(),
            "closed_at": None,
            "deferred_to": None,
            "notes": "",
            "resurfaced_count": 0,
            "program": None,
        }
        defaults.update(item)
        items.append(defaults)

    _save(items)


def pending_acts() -> list[dict]:
    """Return act items with status=pending, newest first."""
    items = _load()
    result = [i for i in items if i.get("type") == "act" and i.get("status") == "pending"]
    result.sort(key=lambda i: i.get("created_at", ""), reverse=True)
    return result


def pending_they_owe() -> list[dict]:
    """Return they-owe items with status=pending, sorted by deadline ascending (soonest first)."""
    items = _load()
    result = [i for i in items if i.get("type") == "they-owe" and i.get("status") == "pending"]
    result.sort(key=lambda i: (i.get("deadline") or "9999-12-31"))
    return result


def pending_watches() -> list[dict]:
    """Return watch items with status=pending."""
    items = _load()
    return [i for i in items if i.get("type") == "watch" and i.get("status") == "pending"]


def pending_by_program() -> dict[str, list[dict]]:
    """Return pending act items grouped by program key.
    Items with program=None go under key "_unassigned"."""
    items = _load()
    result: dict[str, list[dict]] = {}
    for item in items:
        if item.get("type") != "act" or item.get("status") != "pending":
            continue
        key = item.get("program") or "_unassigned"
        result.setdefault(key, []).append(item)
    return result


def close(item_id: str, status: str, notes: str = "") -> bool:
    """Mark one item complete/deferred/expired/dropped. Returns True if found."""
    if status not in _VALID_STATUSES:
        raise ValueError(f"Invalid status: {status!r}")
    items = _load()
    for item in items:
        if item["id"] == item_id:
            item["status"] = status
            item["closed_at"] = _now_iso()
            if notes:
                existing_notes = item.get("notes", "")
                item["notes"] = f"{existing_notes}; {notes}".lstrip("; ") if existing_notes else notes
            _save(items)
            return True
    return False


def reopen(item_id: str) -> bool:
    """Reopen a closed/dropped/expired item, setting status back to pending."""
    items = _load()
    for item in items:
        if item["id"] == item_id:
            item["status"] = "pending"
            item["closed_at"] = None
            _save(items)
            return True
    return False


def defer(item_id: str, target_date: str, notes: str = "") -> bool:
    """Defer an item to a future date. Returns True if found.

    Sets status=deferred, deferred_to=target_date. expire_stale() will
    resurface it as pending on or after that date.
    """
    items = _load()
    for item in items:
        if item["id"] == item_id:
            item["status"] = "deferred"
            item["deferred_to"] = target_date
            item["closed_at"] = _now_iso()
            if notes:
                existing_notes = item.get("notes", "")
                item["notes"] = f"{existing_notes}; {notes}".lstrip("; ") if existing_notes else notes
            _save(items)
            return True
    return False


def resurface_deferred() -> list[dict]:
    """Flip deferred items whose deferred_to date has arrived back to pending.

    Called alongside expire_stale() at the top of each synthesis run.
    Returns the list of items resurfaced.
    """
    items = _load()
    today = date.today()
    resurfaced: list[dict] = []
    for item in items:
        if item.get("status") == "deferred":
            target = item.get("deferred_to")
            if target and date.fromisoformat(target) <= today:
                item["status"] = "pending"
                item["closed_at"] = None
                item["deferred_to"] = None
                item["resurfaced_count"] = item.get("resurfaced_count", 0) + 1
                resurfaced.append(item)
    if resurfaced:
        _save(items)
    return resurfaced


def expire_stale(business_days: int | None = None) -> list[dict]:
    """Expire items past their deadlines or age thresholds.

    Called at the top of each daily pulse synthesis so the morning brief
    sees fresh expiry state. Returns the list of items that were expired.

    Rules (from brief-state-schema.md):
      act:      deadline passed (or created_at + 1 bday if no deadline) → expired
      watch:    age > 5 business days → expired
      delegate: age > 7 days → resurface (resurfaced_count++) but stay pending
      they-owe: deadline passed → promote to act item with text "Follow up: ..."
      insight:  age > 1 business day → expired
    """
    items = _load()
    today = date.today()
    now = _now_iso()
    changed: list[dict] = []

    new_items: list[dict] = []

    for item in items:
        if item.get("status") != "pending":
            continue

        itype = item.get("type", "act")
        deadline_str = item.get("deadline")
        created_str = item.get("created_at", "")

        if itype == "act":
            if deadline_str:
                deadline = date.fromisoformat(deadline_str)
                if deadline < today:
                    item["status"] = "expired"
                    item["closed_at"] = now
                    changed.append(item)
            else:
                threshold = business_days if business_days is not None else _EXPIRY_BUSINESS_DAYS["act"]
                if _business_days_since(created_str) > threshold:
                    item["status"] = "expired"
                    item["closed_at"] = now
                    changed.append(item)

        elif itype == "watch":
            threshold = business_days if business_days is not None else _EXPIRY_BUSINESS_DAYS["watch"]
            if _business_days_since(created_str) > threshold:
                item["status"] = "expired"
                item["closed_at"] = now
                changed.append(item)

        elif itype == "delegate":
            # Don't expire — just increment resurfaced_count as a signal to the brief
            threshold = _EXPIRY_BUSINESS_DAYS["delegate"]
            if _business_days_since(created_str) > threshold:
                item["resurfaced_count"] = item.get("resurfaced_count", 0) + 1
                changed.append(item)

        elif itype == "they-owe":
            if deadline_str:
                deadline = date.fromisoformat(deadline_str)
                if deadline < today:
                    # Promote to act item for Brad
                    promoted = {
                        "id": _item_id(item.get("source_brief", ""), f"Follow up: {item['text']}"),
                        "text": f"Follow up: {item['text']}",
                        "type": "act",
                        "owner": "Brad",
                        "deadline": today.isoformat(),
                        "source_brief": item.get("source_brief", ""),
                        "source_pulse": item.get("source_pulse", ""),
                        "status": "pending",
                        "created_at": now,
                        "closed_at": None,
                        "deferred_to": None,
                        "notes": f"Auto-promoted from they-owe: {item.get('owner', '?')} missed deadline {deadline_str}",
                        "resurfaced_count": 0,
                    }
                    new_items.append(promoted)
                    item["status"] = "expired"
                    item["closed_at"] = now
                    changed.append(item)

        elif itype == "insight":
            threshold = _EXPIRY_BUSINESS_DAYS["insight"]
            if _business_days_since(created_str) >= threshold:
                item["status"] = "expired"
                item["closed_at"] = now
                changed.append(item)

    if changed or new_items:
        # Avoid duplicating promoted act items
        existing_ids = {i["id"] for i in items}
        for promoted in new_items:
            if promoted["id"] not in existing_ids:
                items.append(promoted)
        _save(items)

    return changed


def pending_candidates() -> list[dict]:
    """Return candidate items (status=pending, source_analyzed set).

    Urgent items (urgency="high") sort before normal candidates.
    Within each tier, newest first. Two stable sorts achieve this cleanly.
    """
    items = _load()
    result = [i for i in items if i.get("status") == "pending" and i.get("source_analyzed")]
    result.sort(key=lambda i: i.get("created_at", ""), reverse=True)   # newest first
    result.sort(key=lambda i: 0 if i.get("urgency") == "high" else 1)  # urgent tier first (stable)
    return result


def accept_candidate(item_id: str, item_type: str) -> bool:
    """Promote a candidate to a real act/watch/they-owe item.

    Clears source_analyzed so it moves out of the inbox and into the
    appropriate section. Returns True if found.
    """
    if item_type not in _VALID_TYPES:
        raise ValueError(f"Invalid type: {item_type!r}")
    items = _load()
    for item in items:
        if item["id"] == item_id:
            item["type"] = item_type
            item["source_analyzed"] = None  # promoted — no longer a candidate
            item.pop("urgency", None)        # urgency tag only meaningful in inbox
            _save(items)
            return True
    return False


def dismiss_candidate(item_id: str) -> bool:
    """Dismiss a candidate without promoting it. Returns True if found."""
    return close(item_id, "dismissed")


def all_items() -> list[dict]:
    """Return all items, newest first."""
    items = _load()
    items.sort(key=lambda i: i.get("created_at", ""), reverse=True)
    return items


def summary() -> dict:
    """Return a count summary by type and status for the UI panel."""
    items = _load()
    counts: dict[str, int] = {}
    for item in items:
        key = f"{item.get('type', '?')}:{item.get('status', '?')}"
        counts[key] = counts.get(key, 0) + 1
    return counts
