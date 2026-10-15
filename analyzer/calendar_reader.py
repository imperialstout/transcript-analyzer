"""Calendar reader for morning brief integration.

Reads today's events via a compiled Swift helper (bin/calendar_fetch) that
calls EventKit directly — no OAuth required. Requires one-time Calendar
permission grant (granted when the binary first runs from a GUI-accessible
terminal or VS Code).

Compile once:
    swiftc bin/calendar_fetch.swift -o bin/calendar_fetch

Returns structured event data filtered for brief relevance:
- Skips noise (all-day blocks, OOO, holds, birthdays, short syncs)
- Flags meetings with known stakeholders or program keywords as prep-worthy
"""

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# People who warrant a prep flag if they appear in title or attendees
STAKEHOLDERS = [
    "koti", "jitu", "gunnar", "tara", "hana", "sanjay", "kirsten",
    "hemanta", "fadoua", "anjali", "imad", "erwan", "chandan", "neeraj",
    "eike", "evangelos", "evangelous", "andrew allan",
]

# Program keywords — meeting is prep-worthy if title matches
PROGRAM_KEYWORDS = [
    "siemens", "sherpax", "rca", "revenue cloud", "di-sw", "disw",
    "si-rca", "cpq", "billing", "pcm", "dro", "design board",
    "spark", "r1.1", "r1.2", "byoc", "stx", "option c",
    "arm weekly", "go/no-go", "go no go", "practice lead",
]

# Title patterns that indicate noise (case-insensitive)
_SKIP_PATTERNS = [
    r"\booo\b", r"\bout of office\b", r"\bhold\b", r"\bblock(?:ed)?\b",
    r"\blunch\b", r"\bbreakfast\b", r"\bcommute\b", r"\bfocus time\b",
    r"\bbirthday\b", r"\bholiday\b", r"\bvacation\b",
    r"\btravel\b", r"\bflights?\b",
    r"\bpto\b", r"[-–]\s*out\b",              # PTO / "Brad - Out" markers
    r"cpq\s+pi\s+\d+",                         # CPQ PI sprint daily placeholders
    r"seap\s+dive|take\s+a\s+seap\b",          # SEAP office hours
    r"agentforce\s+office\s+hours",             # AMERS/regional Agentforce office hours
    r"break\s+room\b",                          # The Break Room social series
    r"kortney.{0,10}tequila|tequila.{0,10}kortney",  # Kortney/Tequila sync (DISW internal, no Brad action)
]

# Calendars to skip entirely
_SKIP_CALENDARS = {
    "birthdays", "siri suggestions", "scheduled reminders",
    "us holidays", "united states holidays", "holidays in united states",
}

# Minimum meeting duration in minutes to include
_MIN_DURATION_MINUTES = 20

_BINARY = Path(__file__).parent.parent / "bin" / "calendar_fetch"


@dataclass
class CalendarEvent:
    title: str
    start: datetime | None
    end: datetime | None
    start_str: str  # HH:MM local time
    end_str: str
    calendar: str
    all_day: bool
    attendees: list[str] = field(default_factory=list)
    location: str = ""
    prep_worthy: bool = False
    prep_reason: str = ""
    program_key: str = ""   # matched program key, e.g. "SI-RCA", "DISW-CPQ"
    prep_bullets: list[str] = field(default_factory=list)  # 2-3 bullets from STATUS.md


def _duration_minutes(ev: CalendarEvent) -> int | None:
    if ev.start and ev.end:
        return int((ev.end - ev.start).total_seconds() / 60)
    return None


def _is_noise(ev: CalendarEvent) -> bool:
    if ev.all_day:
        return True
    if ev.calendar.lower() in _SKIP_CALENDARS:
        return True
    title_lower = ev.title.lower()
    for pat in _SKIP_PATTERNS:
        if re.search(pat, title_lower, re.IGNORECASE):
            return True
    dur = _duration_minutes(ev)
    if dur is not None and dur < _MIN_DURATION_MINUTES:
        return True
    return False


def _flag_prep_worthy(ev: CalendarEvent) -> CalendarEvent:
    """Set prep_worthy + prep_reason if this meeting warrants prep bullets."""
    # Combine title + attendee names/emails for matching
    combined = (ev.title + " " + " ".join(ev.attendees)).lower()

    for name in STAKEHOLDERS:
        if name in combined:
            ev.prep_worthy = True
            ev.prep_reason = f"stakeholder: {name}"
            return ev

    for kw in PROGRAM_KEYWORDS:
        if kw in combined:
            ev.prep_worthy = True
            ev.prep_reason = f"program: {kw}"
            return ev

    return ev


_DISW_RCA_KW = re.compile(r"\bpoc\b|rca\s*poc|di-sw\s*rca|disw\s*rca|arm\s*poc", re.IGNORECASE)
_DISW_CPQ_KW = re.compile(r"\bdisw\b|di-sw|tequila|kortney|neeraj|cpq\s*pi|disw\s*arm|arm\s*poc", re.IGNORECASE)
_SI_KW = re.compile(r"\bsi\b|smart\s*infra|si-rca|sherpa|siemens|design\s*(board|lead)|arm\s*(weekly|status|show)", re.IGNORECASE)


def _classify_event(ev: CalendarEvent) -> str:
    """Map a calendar event to a SiemensRCAGov program key. Returns '' if no match."""
    combined = ev.title + " " + " ".join(ev.attendees)
    combined_lower = combined.lower()

    # Chandan/Sanjay → DISW-RCA PoC
    if "chandan" in combined_lower or "sanjay" in combined_lower:
        if _DISW_RCA_KW.search(combined):
            return "DISW-RCA"

    if _DISW_RCA_KW.search(combined):
        return "DISW-RCA"

    if _DISW_CPQ_KW.search(combined):
        return "DISW-CPQ"

    if _SI_KW.search(combined):
        return "SI-RCA"

    return ""


def _generate_prep_bullets(ev: CalendarEvent) -> list[str]:
    """Generate 2-3 quick prep bullets for a Siemens meeting from STATUS.md + open acts."""
    if not ev.program_key:
        return []
    try:
        from pathlib import Path as _Path
        from . import brief_state as _bs
        from . import claude_cli as _cli
        from .config import CONFIG as _cfg

        gov_root = _Path("~/code/SiemensRCAGov").expanduser()
        # Find the STATUS.md for this program key
        status_path = None
        for p in gov_root.rglob("STATUS.md"):
            try:
                text = p.read_text(encoding="utf-8")
                if text.startswith("---"):
                    end = text.find("---", 3)
                    if end > 0:
                        fm_text = text[3:end]
                        if f'program: {ev.program_key}' in fm_text or f'program: "{ev.program_key}"' in fm_text:
                            status_path = p
                            break
            except Exception:
                continue

        if status_path is None:
            return []

        status_text = status_path.read_text(encoding="utf-8")
        # Last 2 open-signals blocks
        blocks = status_text.split("## Open signals")
        signals_section = "## Open signals" + "## Open signals".join(blocks[-2:]) if len(blocks) >= 3 else ("## Open signals" + blocks[-1] if len(blocks) > 1 else status_text[-2000:])

        by_prog = _bs.pending_by_program()
        acts = by_prog.get(ev.program_key, [])
        acts_text = "\n".join(f"- {i['text']}" for i in acts) if acts else "(none)"

        prompt = f"""You are giving Brad Gross (Revenue Cloud CTO) a 30-second pre-read before his meeting: "{ev.title}"

## Recent program signals:
{signals_section[:1800]}

## Open act items for this program:
{acts_text}

Return exactly 2-3 bullet points. Each bullet: one thing Brad should keep in mind or be ready to address in this specific meeting. Be concrete and specific — no generic advice. Start each bullet with a **bold keyword**. No preamble."""

        model = _cfg.models.get("STANDUP", "claude-sonnet-4-6")
        data = _cli.run_claude_p(prompt, model=model)
        raw = _cli.result_text(data)
        lines = [l.strip() for l in raw.strip().splitlines() if l.strip().startswith(("-", "•", "*"))]
        if not lines:
            lines = [l.strip() for l in raw.strip().splitlines() if l.strip()]
        return lines[:3]
    except Exception:
        return []


def _parse_iso(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def fetch_range(start: date, end: date, with_prep: bool = False) -> list[CalendarEvent]:
    """Fetch events for a date range (inclusive) via the Swift EventKit binary.

    Args:
        start: First day of the range (YYYY-MM-DD).
        end:   Last day of the range (YYYY-MM-DD, inclusive).
    Returns [] on any failure.
    """
    if not _BINARY.exists():
        return []

    try:
        result = subprocess.run(
            [str(_BINARY), "--start", start.isoformat(), "--end", end.isoformat()],
            capture_output=True, text=True, timeout=30
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []

    if result.returncode != 0 or not result.stdout.strip():
        return []

    try:
        raw_events = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []

    events: list[CalendarEvent] = []
    for r in raw_events:
        ev = CalendarEvent(
            title=r.get("title", ""),
            start=_parse_iso(r.get("start_iso", "")),
            end=_parse_iso(r.get("end_iso", "")),
            start_str=r.get("start", ""),
            end_str=r.get("end", ""),
            calendar=r.get("calendar", ""),
            all_day=bool(r.get("all_day", False)),
            attendees=r.get("attendees", []),
            location=r.get("location", ""),
        )
        events.append(ev)

    events = [ev for ev in events if not _is_noise(ev)]
    events = [_flag_prep_worthy(ev) for ev in events]

    for ev in events:
        if ev.prep_worthy:
            ev.program_key = _classify_event(ev)
    if with_prep:
        for ev in events:
            if ev.program_key:
                ev.prep_bullets = _generate_prep_bullets(ev)

    events.sort(key=lambda e: e.start_str)
    return events


def fetch_today(with_prep: bool = False) -> list[CalendarEvent]:
    """Fetch today's events via the Swift EventKit binary. Returns [] on any failure."""
    if not _BINARY.exists():
        return []

    try:
        result = subprocess.run(
            [str(_BINARY)],
            capture_output=True, text=True, timeout=15
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []

    if result.returncode != 0 or not result.stdout.strip():
        return []

    try:
        raw_events = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []

    events: list[CalendarEvent] = []
    for r in raw_events:
        ev = CalendarEvent(
            title=r.get("title", ""),
            start=_parse_iso(r.get("start_iso", "")),
            end=_parse_iso(r.get("end_iso", "")),
            start_str=r.get("start", ""),
            end_str=r.get("end", ""),
            calendar=r.get("calendar", ""),
            all_day=bool(r.get("all_day", False)),
            attendees=r.get("attendees", []),
            location=r.get("location", ""),
        )
        events.append(ev)

    events = [ev for ev in events if not _is_noise(ev)]
    events = [_flag_prep_worthy(ev) for ev in events]

    # Classify Siemens program and optionally generate prep bullets
    for ev in events:
        if ev.prep_worthy:
            ev.program_key = _classify_event(ev)
    if with_prep:
        for ev in events:
            if ev.program_key:
                ev.prep_bullets = _generate_prep_bullets(ev)

    events.sort(key=lambda e: e.start_str)
    return events


def format_for_brief(events: list[CalendarEvent]) -> str:
    """Format events as a compact morning-brief block, with prep bullets for Siemens meetings."""
    if not events:
        return ""

    lines = ["**Today's calendar:**"]
    for ev in events:
        time_str = f"{ev.start_str}–{ev.end_str}" if ev.start_str else ""
        flag = " ⚑" if ev.prep_worthy else ""
        line = f"- {time_str + '  ' if time_str else ''}{ev.title}{flag}"
        if ev.prep_worthy and ev.prep_reason:
            line += f"  *({ev.prep_reason})*"
        lines.append(line)
        # Indent prep bullets under the meeting line
        for bullet in ev.prep_bullets:
            lines.append(f"  {bullet}")

    prep_count = sum(1 for e in events if e.prep_worthy)
    bulleted_count = sum(1 for e in events if e.prep_bullets)
    if prep_count:
        footer = f"\n*{prep_count} meeting{'s' if prep_count > 1 else ''} flagged ⚑"
        if bulleted_count:
            footer += f" — {bulleted_count} with prep bullets"
        footer += " — cross-reference with triage items below.*"
        lines.append(footer)

    return "\n".join(lines)


def calendar_block(with_prep: bool = False) -> str:
    """Top-level convenience: fetch + format. Returns '' if nothing to show."""
    return format_for_brief(fetch_today(with_prep=with_prep))
