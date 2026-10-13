"""Triage alert extraction and persistence.

After each transcript is analyzed, this module scans the output for items that
require Brad's direct action and writes them to a JSON store. The UI reads the
store to show an alert banner; items can be dismissed individually.

Storage: ~/.cache/transcript-analyzer/alerts.json
Each alert: {id, text, source, source_date, extracted_at, dismissed}

Design:
- Extraction is regex-based (fast, no LLM cost). It pulls structured sections
  that the prompts already emit: "## Needs your attention" (DAILY),
  "## Commitments & action items" (STANDUP), and filters for items that are
  Brad-directed using a keyword list.
- Best-effort: failure here does NOT affect transcript recording.
- IDs are deterministic (hash of source+text) so re-runs don't duplicate.
"""

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

_CACHE_PATH = Path("~/.cache/transcript-analyzer/alerts.json").expanduser()

# Section headings in analyzed output that may contain Brad-directed items.
_SECTION_PATTERNS = [
    r"^##\s+Needs your attention\s*\n(.*?)(?=^##|\Z)",
    r"^##\s+Commitments\s*&\s*action items\s*\n(.*?)(?=^##|\Z)",
    r"^##\s+Suggested dispositions\s*\n(.*?)(?=^##|\Z)",
    r"^##\s+Decisions?\s*&\s*commitments?\s*\n(.*?)(?=^##|\Z)",
]

# Keywords that indicate the item is directed at Brad specifically.
# Match is case-insensitive and applies to the bullet line itself.
_BRAD_KEYWORDS = [
    r"\bbrad\b",
    r"\byou\b",          # "you need to", "you owe"
    r"\byour\b",         # "your decision", "your response"
    r"\bASAP\b",
    r"\bimmediate\b",
    r"\burgent\b",
    r"\bblocked\b",
    r"\bwaiting on\b",
    r"\bowes?\b",
    r"\bneeds? your\b",
    r"\brequires? your\b",
    r"\bConvert to action\b",
    r"\bact now\b",
]

_BRAD_RE = re.compile("|".join(_BRAD_KEYWORDS), re.IGNORECASE)


def _extract_sections(text: str) -> list[str]:
    """Return all bullet lines from triage-relevant sections of an analysis."""
    bullets: list[str] = []
    for pattern in _SECTION_PATTERNS:
        for m in re.finditer(pattern, text, re.MULTILINE | re.DOTALL):
            block = m.group(1)
            for line in block.splitlines():
                stripped = line.strip()
                # Keep non-empty lines that start with a list marker or are
                # indented continuations of a previous bullet.
                if stripped and (stripped.startswith(("-", "*", "•", "–")) or
                                  (len(line) > 0 and line[0] == " " and stripped)):
                    bullets.append(stripped.lstrip("-*•– ").strip())
    return bullets


def _alert_id(source: str, text: str) -> str:
    return hashlib.sha1(f"{source}|{text}".encode()).hexdigest()[:12]


def _load() -> list[dict]:
    if not _CACHE_PATH.exists():
        return []
    try:
        return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(alerts: list[dict]) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(json.dumps(alerts, indent=2), encoding="utf-8")


def extract_from_file(analysis_text: str, source_filename: str) -> int:
    """Extract Brad-directed items from one analysis file and append to store.

    Returns the count of new alerts added (0 if none, or on any error).
    """
    try:
        bullets = _extract_sections(analysis_text)
        brad_items = [b for b in bullets if _BRAD_RE.search(b)]
        if not brad_items:
            return 0

        # Pull meeting date from filename (first YYYY-MM-DD in the name).
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", source_filename)
        source_date = date_match.group(1) if date_match else ""
        # Strip leading timestamp + trailing [ANALYZED] for a readable label.
        label = re.sub(r"^\d{4}-\d{2}-\d{2}T[\d-]+ - ", "", source_filename)
        label = re.sub(r"\s*\[ANALYZED\]\.txt$", "", label)

        existing = _load()
        existing_ids = {a["id"] for a in existing}
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        added = 0
        for text in brad_items:
            aid = _alert_id(source_filename, text)
            if aid in existing_ids:
                continue
            existing.append({
                "id": aid,
                "text": text,
                "source": label,
                "source_date": source_date,
                "extracted_at": now,
                "dismissed": False,
            })
            added += 1

        if added:
            _save(existing)
        return added
    except Exception:
        return 0


def undismissed() -> list[dict]:
    """Return all alerts that have not been dismissed, newest first."""
    alerts = _load()
    active = [a for a in alerts if not a.get("dismissed")]
    active.sort(key=lambda a: a.get("extracted_at", ""), reverse=True)
    return active


def dismiss(alert_id: str) -> bool:
    """Mark one alert as dismissed. Returns True if found."""
    alerts = _load()
    for a in alerts:
        if a["id"] == alert_id:
            a["dismissed"] = True
            _save(alerts)
            return True
    return False


def dismiss_all() -> int:
    """Dismiss all active alerts. Returns count dismissed."""
    alerts = _load()
    count = 0
    for a in alerts:
        if not a.get("dismissed"):
            a["dismissed"] = True
            count += 1
    if count:
        _save(alerts)
    return count
