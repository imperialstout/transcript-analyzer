"""Triage alert extraction — writes urgent candidates into brief_state.

After each transcript is analyzed, scan the output for Brad-directed items
and write them directly into brief-state.json as candidate items with
urgency="high". They surface in the inbox on Brief State as a priority tier.

Design:
- Extraction is regex-based (fast, no LLM cost). Pulls structured sections
  the prompts already emit: "## Needs your attention" (DAILY),
  "## Commitments & action items" (STANDUP), etc. Filters by Brad-directed
  keyword list.
- Best-effort: failure here does NOT affect transcript recording.
- IDs are deterministic (hash of source+text) so re-runs don't duplicate.
"""

import hashlib
import re
from datetime import datetime

from . import brief_state as _bs

_SECTION_PATTERNS = [
    r"^##\s+Needs your attention\s*\n(.*?)(?=^##|\Z)",
    r"^##\s+Commitments\s*&\s*action items\s*\n(.*?)(?=^##|\Z)",
    r"^##\s+Suggested dispositions\s*\n(.*?)(?=^##|\Z)",
    r"^##\s+Decisions?\s*&\s*commitments?\s*\n(.*?)(?=^##|\Z)",
]

_BRAD_KEYWORDS = [
    r"\bbrad\b",
    r"\byou\b",
    r"\byour\b",
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
    bullets: list[str] = []
    for pattern in _SECTION_PATTERNS:
        for m in re.finditer(pattern, text, re.MULTILINE | re.DOTALL):
            block = m.group(1)
            for line in block.splitlines():
                stripped = line.strip()
                if stripped and (stripped.startswith(("-", "*", "•", "–")) or
                                  (len(line) > 0 and line[0] == " " and stripped)):
                    bullets.append(stripped.lstrip("-*•– ").strip())
    return bullets


def _alert_id(source: str, text: str) -> str:
    return hashlib.sha1(f"{source}|{text}".encode()).hexdigest()[:12]


def extract_from_file(analysis_text: str, source_filename: str) -> int:
    """Extract Brad-directed items from one analysis file and write to brief-state as urgent candidates.

    Returns the count of new items added (0 if none, or on any error).
    """
    try:
        bullets = _extract_sections(analysis_text)
        brad_items = [b for b in bullets if _BRAD_RE.search(b)]
        if not brad_items:
            return 0

        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", source_filename)
        source_date = date_match.group(1) if date_match else ""
        label = re.sub(r"^\d{4}-\d{2}-\d{2}T[\d-]+ - ", "", source_filename)
        label = re.sub(r"\s*\[ANALYZED\]\.txt$", "", label)
        if source_date:
            label = f"{label} · {source_date}"

        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        added = 0
        for text in brad_items:
            item_id = _alert_id(source_filename, text)
            # Check for duplicate — brief_state.upsert won't duplicate by id but
            # we want to skip the write entirely for the count.
            existing = _bs._load()
            if any(i["id"] == item_id for i in existing):
                continue
            _bs.upsert({
                "id": item_id,
                "text": text,
                "type": "act",
                "owner": "Brad",
                "deadline": None,
                "source_brief": source_filename,
                "source_pulse": "",
                "source_analyzed": label,
                "status": "pending",
                "created_at": now,
                "closed_at": None,
                "deferred_to": None,
                "notes": "",
                "resurfaced_count": 0,
                "program": None,
                "urgency": "high",
            })
            added += 1

        return added
    except Exception:
        return 0


def undismissed() -> list[dict]:
    """Shim for callers that check the old alert store — returns empty list."""
    return []
