"""Weekly Timecard Compiler.

Reads filed [ANALYZED] outputs for a target week (Mon–Fri), classifies each
meeting into one of four timecard rows using workstream frontmatter + title
keyword rules, then calls Claude to compress the per-row, per-day work into a
≤255-character summary. Writes a [TIMECARD].md file to Analyzed/.

Usage (via synthesize dispatch):
    python -m analyzer synthesize --mode timecard
    python -m analyzer synthesize --mode timecard --date 2026-08-25  # any date in target week

Rows (matching the Slack Weekly Timecard Compiler skill):
    DISW         — Digital Industries Software: PoC, CPQ legacy, DI-SW work
    DISW POC     — RCA proof-of-concept within DI-SW (Chandan, Sanjay, RCA PoC)
    SI RCA       — SI/RCA governance, design board, architecture, cross-stream
    Z-Admin      — Internal Salesforce, practice, admin, non-project 1:1s

Bucketing priority order (first match wins):
    1. DISW POC  — workstream contains "RCA PoC" / "DI-SW RCA" OR participant
                   names Chandan / Sanjay on a DI-SW workstream file
    2. DISW      — workstream starts with "DI-SW" or contains "DISW" or
                   title keywords: DISW, Digital Industries, BT SW, Neeraj,
                   Tequila, Kortney, landscape, billing
    3. Z-Admin   — workstream in ("internal", "internal-salesforce") OR title
                   keywords: Tune-In, Practice Team, RevCloud Community,
                   introduction to, survey, Jodi
    4. SI RCA    — everything else Siemens (cross-stream, SI*, Siemens All*)
    (Unclassifiable files get a warning and are assigned to SI RCA as fallback)

Duration heuristic:
    meeting_type "standup" or "daily" → 30 min; else 60 min.
    If the title contains "30 min" / "30m" / "half hour" → 30 min.
    Result is advisory only (prepended to the LLM input so it can include
    it naturally in the summary, e.g. "1h — arch review, gap analysis").
"""

import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from . import claude_cli
from . import manifest as manifest_mod
from . import prompts as prompts_mod
from .config import CONFIG, model_for
from .synthesize import (
    _files_for_mode,
    _meeting_date,
    _DATE_TOKEN,
)

# ---------------------------------------------------------------------------
# Row constants — names must match what you paste into the timecard system
# ---------------------------------------------------------------------------
ROW_DISW = "Siemens Digital Industries Software (DISW)"
ROW_DISW_POC = "DISW POC — RCA Proof of Concept"
ROW_SI_RCA = "Competency Center / SI RCA"
ROW_ADMIN = "Z-Administrative Tasks"

_ALL_ROWS = [ROW_DISW, ROW_DISW_POC, ROW_SI_RCA, ROW_ADMIN]

# ---------------------------------------------------------------------------
# Keyword sets for title-based classification
# ---------------------------------------------------------------------------
_DISW_POC_PARTICIPANTS = {"chandan", "sanjay"}
_DISW_POC_WORKSTREAMS = {"di-sw rca poc", "disw rca poc", "di-sw rca", "rca poc", "disw rca"}
_DISW_POC_TITLE_KW = re.compile(r"\bpoc\b|rca\s+poc|di-sw\s+rca", re.IGNORECASE)

_DISW_TITLE_KW = re.compile(
    r"\b(disw|digital\s+industries|bt\s+sw|neeraj|tequila|kortney|landscape|billing|di-sw)\b",
    re.IGNORECASE,
)
_DISW_WORKSTREAM_PREFIX = re.compile(r"^di-sw|^disw", re.IGNORECASE)

_ADMIN_TITLE_KW = re.compile(
    r"tune-in|practice\s+team|revcloud\s+community|introduction\s+to|survey\b|jodi\b",
    re.IGNORECASE,
)
_ADMIN_WORKSTREAMS = {"internal", "internal-salesforce"}

_DURATION_SHORT = re.compile(r"30\s*min|30m\b|half\s+hour", re.IGNORECASE)
_SHORT_MEETING_TYPES = {"standup", "daily", "stand-up", "standup/scrum"}


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------
_FM_PARTICIPANTS = re.compile(r"^participants:\s*\[([^\]]*)\]", re.MULTILINE)
_FM_WORKSTREAM = re.compile(r"^workstream:\s*(.+)$", re.MULTILINE)
_FM_MEETING_TYPE = re.compile(r"^meeting_type:\s*(.+)$", re.MULTILINE)


@dataclass
class _FileMeta:
    path: Path
    meeting_date: Optional[date]
    title: str
    workstream: str
    meeting_type: str
    participants: list[str]
    row: str = ""
    duration_min: int = 60


def _parse_meta(path: Path) -> _FileMeta:
    try:
        head = path.read_text(encoding="utf-8")[:3000]
    except OSError:
        head = ""

    ws_m = _FM_WORKSTREAM.search(head)
    workstream = ws_m.group(1).strip() if ws_m else ""

    mt_m = _FM_MEETING_TYPE.search(head)
    meeting_type = mt_m.group(1).strip() if mt_m else ""

    pt_m = _FM_PARTICIPANTS.search(head)
    participants = []
    if pt_m:
        raw = pt_m.group(1)
        participants = [p.strip().strip("'\"") for p in raw.split(",") if p.strip()]

    # Title: strip leading timestamp + trailing [TAG] from filename
    name = path.stem
    title = re.sub(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\s*-\s*", "", name)
    title = re.sub(r"\s*\[(?:ANALYZED|SHAREABLE|[A-Z ]+)\]\s*$", "", title)
    title = re.sub(r"\s*-\s*\d{4}-\d{2}-\d{2}\s*$", "", title).strip()

    return _FileMeta(
        path=path,
        meeting_date=_meeting_date(path),
        title=title,
        workstream=workstream,
        meeting_type=meeting_type,
        participants=participants,
    )


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _classify(meta: _FileMeta) -> str:
    ws_lower = meta.workstream.lower().strip()
    title_lower = meta.title.lower()
    participants_lower = {p.lower() for p in meta.participants}

    # 1. DISW POC — explicit workstream, title keyword (POC/RCA PoC), or
    #    Chandan/Sanjay as participant (regardless of workstream — these names
    #    are the POC signal even when the workstream is cross-stream or SI RCA)
    if any(kw in ws_lower for kw in _DISW_POC_WORKSTREAMS):
        return ROW_DISW_POC
    if _DISW_POC_PARTICIPANTS & participants_lower:
        return ROW_DISW_POC
    if _DISW_POC_TITLE_KW.search(meta.title):
        return ROW_DISW_POC

    # 2. DISW — workstream prefix or title keyword
    if _DISW_WORKSTREAM_PREFIX.match(ws_lower):
        return ROW_DISW
    if _DISW_TITLE_KW.search(meta.title):
        return ROW_DISW

    # 3. Admin — workstream or title keyword
    if ws_lower in _ADMIN_WORKSTREAMS:
        return ROW_ADMIN
    if _ADMIN_TITLE_KW.search(meta.title):
        return ROW_ADMIN

    # 4. SI RCA — everything else Siemens (cross-stream, SI*, Siemens All*, etc.)
    #    Also catches unclassified / unknown — fail toward SI RCA
    return ROW_SI_RCA


def _duration(meta: _FileMeta) -> int:
    if _DURATION_SHORT.search(meta.title):
        return 30
    if meta.meeting_type.lower() in _SHORT_MEETING_TYPES:
        return 30
    return 60


# ---------------------------------------------------------------------------
# LLM compression call
# ---------------------------------------------------------------------------

_COMPRESS_SYSTEM = (
    "You are a timecard assistant. Given meeting analyses, produce a single "
    "plain-text summary of the work done that day for ONE timecard row. "
    "Requirements:\n"
    "- Hard limit: 255 characters (not words — characters including spaces).\n"
    "- Concrete and specific: name the meeting, decision made, problem "
    "unblocked, or deliverable advanced. Avoid vague phrases like 'discussed issues'.\n"
    "- Comma-separated shorthand is fine: e.g. 'arch review (1h), gap analysis on DRO "
    "pricing — aligned on threshold approach, stakeholder sync'.\n"
    "- Include approximate total hours in parentheses at the start if >1h.\n"
    "- Output ONLY the summary text. No preamble, no labels, no explanation."
)


def _compress(entries: list[_FileMeta], system: str, model: str) -> str:
    """Call Claude to compress meeting summaries to ≤255 chars."""
    parts = []
    for m in entries:
        dur_str = f"{m.duration_min}min"
        snippet = m.path.read_text(encoding="utf-8")[:4000]
        parts.append(
            f"--- {m.title} ({dur_str}) ---\n{snippet}"
        )
    bundle = "\n\n".join(parts)
    prompt = (
        f"Summarize the work below into ≤255 characters for a timecard entry.\n\n"
        f"{bundle}"
    )
    data = claude_cli.run_claude_p(prompt, model=model, system=system)
    result = claude_cli.result_text(data).strip()
    # Hard-truncate as a last-resort safety net (model should comply)
    if len(result) > 255:
        result = result[:252] + "..."
    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

@dataclass
class _DayBucket:
    files: list[_FileMeta] = field(default_factory=list)


def run_timecard(target_date: Optional[date] = None) -> int:
    analyzed_path = CONFIG.analyzed_path
    if not analyzed_path.exists():
        print(f"ERROR: Analyzed/ path does not exist: {analyzed_path}", file=sys.stderr)
        return 1

    anchor = target_date or date.today()
    monday = anchor - timedelta(days=anchor.weekday())
    week_days = [monday + timedelta(days=i) for i in range(5)]  # Mon–Fri
    week_end = monday + timedelta(days=4)

    # Collect files for the whole week (daily mode windows to a single day;
    # we need Mon-Fri so scan weekly window then filter to exact days)
    files = _files_for_mode("weekly", analyzed_path, target_date=week_end)
    # Narrow to Mon–Fri of target week
    week_day_set = set(week_days)
    files = [f for f in files if (_d := _meeting_date(f)) and _d in week_day_set]

    if not files:
        print(
            f"No [ANALYZED] files found for week of {monday.isoformat()} — nothing to compile."
        )
        return 0

    # Parse metadata and classify
    metas = [_parse_meta(f) for f in files]
    for m in metas:
        m.row = _classify(m)
        m.duration_min = _duration(m)

    print(f"Timecard week: {monday.isoformat()} – {week_end.isoformat()}")
    print(f"Found {len(metas)} file(s):")
    for m in metas:
        print(f"  [{m.row}] {m.meeting_date} — {m.title} ({m.duration_min}min)")

    # System context (brief + rolodex, same as other synthesis modes)
    context_brief = ""
    if CONFIG.context_brief_path.exists():
        context_brief = CONFIG.context_brief_path.read_text(encoding="utf-8")
    rolodex = prompts_mod.load_rolodex()
    system_parts = [_COMPRESS_SYSTEM]
    if context_brief:
        system_parts.append(f"=== PROGRAM CONTEXT ===\n{context_brief}")
    if rolodex:
        system_parts.append(f"=== PEOPLE ROLODEX ===\n{rolodex}")
    system = "\n\n".join(system_parts)

    model = CONFIG.models.get("STANDUP", "claude-sonnet-4-6")
    if CONFIG.model_override:
        model = CONFIG.model_override

    # Build per-row per-day buckets, then compress each non-empty cell
    # Structure: grid[row][day] = [_FileMeta, ...]
    grid: dict[str, dict[date, list[_FileMeta]]] = {
        row: {d: [] for d in week_days} for row in _ALL_ROWS
    }
    for m in metas:
        if m.meeting_date in week_day_set:
            grid[m.row][m.meeting_date].append(m)

    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    output_rows: dict[str, dict[date, str]] = {row: {} for row in _ALL_ROWS}
    total_cells = sum(
        1 for row in _ALL_ROWS for d in week_days if grid[row][d]
    )
    processed = 0

    for row in _ALL_ROWS:
        for d in week_days:
            entries = grid[row][d]
            if not entries:
                output_rows[row][d] = "—"
                continue
            processed += 1
            day_label = day_names[week_days.index(d)]
            print(
                f"  Compressing {row} / {day_label} {d.isoformat()} "
                f"({len(entries)} meeting(s), cell {processed}/{total_cells})..."
            )
            try:
                output_rows[row][d] = _compress(entries, system, model)
            except Exception as e:
                print(
                    f"  WARNING: compression failed for {row}/{d} — {e}",
                    file=sys.stderr,
                )
                # Fallback: join titles
                fallback = "; ".join(m.title for m in entries)
                output_rows[row][d] = fallback[:255]

    # --- Build the markdown output ---
    now = datetime.now()
    iso = now.strftime("%Y-%m-%dT%H-%M-%S")
    lines = [
        f"# Weekly Timecard — Week of {monday.isoformat()} – {week_end.isoformat()}",
        f"",
        f"*Generated {now.strftime('%Y-%m-%d %H:%M')} from {len(metas)} analyzed meeting file(s).*",
        f"*Note: hours are estimated (30min = standup/daily; 60min = all others). Adjust as needed.*",
        f"",
    ]

    for row in _ALL_ROWS:
        lines.append(f"## {row}")
        lines.append("")
        for i, d in enumerate(week_days):
            day_label = day_names[i]
            cell = output_rows[row][d]
            char_note = f" *(⚠ {len(cell)}ch)*" if len(cell) > 255 else ""
            lines.append(f"- **{day_label} {d.isoformat()}**: {cell}{char_note}")
        lines.append("")

    # Key accomplishments: pull from non-admin, non-empty cells
    accomplishments = []
    for row in [ROW_DISW, ROW_DISW_POC, ROW_SI_RCA]:
        for d in week_days:
            cell = output_rows[row][d]
            if cell and cell != "—":
                accomplishments.append(f"[{d.isoformat()} / {row.split('(')[0].strip().split(' — ')[0].strip()}] {cell}")

    if accomplishments:
        lines.append("## Key Accomplishments This Week")
        lines.append("")
        for a in accomplishments[:8]:
            lines.append(f"- {a}")
        lines.append("")

    lines.append("---")
    lines.append(f"*Source files: {', '.join(f.path.name for f in metas[:5])}")
    if len(metas) > 5:
        lines.append(f"  … and {len(metas) - 5} more.*")
    else:
        lines[-1] += "*"

    text = "\n".join(lines)
    print("\n" + text)
    return 0
