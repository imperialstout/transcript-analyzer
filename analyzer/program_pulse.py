"""Program Pulse — keeps governance STATUS.md files current.

For each STATUS.md under ~/code/SiemensRCAGov, finds Analyzed/ files newer
than the `last_analyzed_file` watermark that belong to the program, calls
Claude for a 3–5 bullet summary of signals, then appends a dated block under
"## Open signals" and updates the YAML frontmatter.

Usage (via synthesize dispatch):
    python -m analyzer synthesize --mode program-pulse
    python -m analyzer synthesize --mode program-pulse --dry-run

Classification keyword map (first match wins):
    presales-energy           workstream/title: Energy, SE, Siemens Energy
    presales-healthineers     workstream/title: Healthineers, SHS
    presales-financial-services  workstream/title: Financial Services, SFS, Siemens Financial
    DISW-RCA                  workstream: DI-SW RCA, DISW RCA, RCA PoC, DISW RCA
                              participants: Chandan, Sanjay
                              title: POC, RCA PoC
    DISW-CPQ                  workstream starts with DI-SW/DISW OR
                              title: DISW, Digital Industries, Neeraj, Tequila, Kortney
    siemens-general           workstream: Siemens All, cross-stream, cross-division, executive
                              title/participants: Hana, Eike, Tara, Gunnar
    SI-RCA                    everything else Siemens (catch-all)
    _admin                    workstream: internal, internal-salesforce OR
                              title: Tune-In, Practice Team — NOT written to any STATUS.md
"""

import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from . import claude_cli
from .config import CONFIG

# ---------------------------------------------------------------------------
# Program classification — keyword sets
# ---------------------------------------------------------------------------

_PRESALES_ENERGY_KW = re.compile(
    r"\benergy\b|siemens\s+energy|\bse\b", re.IGNORECASE
)
_PRESALES_HEALTHINEERS_KW = re.compile(
    r"healthineers|\bshs\b", re.IGNORECASE
)
_PRESALES_FS_KW = re.compile(
    r"financial\s+services|\bsfs\b|siemens\s+financial", re.IGNORECASE
)

_DISW_RCA_WORKSTREAMS = {"di-sw rca poc", "disw rca poc", "di-sw rca", "rca poc", "disw rca"}
_DISW_RCA_PARTICIPANTS = {"chandan", "sanjay"}
_DISW_RCA_TITLE_KW = re.compile(r"\bpoc\b|rca\s+poc|di-sw\s+rca", re.IGNORECASE)

_DISW_CPQ_WS_PREFIX = re.compile(r"^di-sw|^disw", re.IGNORECASE)
_DISW_CPQ_TITLE_KW = re.compile(
    r"\b(disw|digital\s+industries|neeraj|tequila|kortney)\b", re.IGNORECASE
)

_SIEMENS_GENERAL_WS = {"siemens all", "cross-stream", "cross-division", "executive"}
_SIEMENS_GENERAL_KW = re.compile(r"\b(hana|eike|tara|gunnar)\b", re.IGNORECASE)

_ADMIN_WS = {"internal", "internal-salesforce"}
_ADMIN_TITLE_KW = re.compile(
    r"tune-in|practice\s+team|revcloud\s+community|introduction\s+to|survey\b|jodi\b",
    re.IGNORECASE,
)

# Programs that write to STATUS.md (in match priority order)
PROGRAMS = [
    "presales-energy",
    "presales-healthineers",
    "presales-financial-services",
    "DISW-RCA",
    "DISW-CPQ",
    "siemens-general",
    "SI-RCA",
]
_ADMIN_PROGRAM = "_admin"

# ---------------------------------------------------------------------------
# Frontmatter parsing for Analyzed/ files
# ---------------------------------------------------------------------------

_FM_WORKSTREAM = re.compile(r"^workstream:\s*(.+)$", re.MULTILINE)
_FM_MEETING_TYPE = re.compile(r"^meeting_type:\s*(.+)$", re.MULTILINE)
_FM_PARTICIPANTS = re.compile(r"^participants:\s*\[([^\]]*)\]", re.MULTILINE)
_TITLE_STRIP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\s*-\s*|"
    r"\s*\[(?:ANALYZED|SHAREABLE|[A-Z ]+)\]\s*$|"
    r"\s*-\s*\d{4}-\d{2}-\d{2}\s*$"
)


def _parse_analyzed_meta(path: Path) -> dict:
    try:
        head = path.read_text(encoding="utf-8")[:3000]
    except OSError:
        head = ""

    ws_m = _FM_WORKSTREAM.search(head)
    workstream = ws_m.group(1).strip() if ws_m else ""

    pt_m = _FM_PARTICIPANTS.search(head)
    participants: list[str] = []
    if pt_m:
        raw = pt_m.group(1)
        participants = [p.strip().strip("'\"") for p in raw.split(",") if p.strip()]

    name = path.stem
    title = _TITLE_STRIP.sub("", name).strip()

    return {"workstream": workstream, "participants": participants, "title": title}


def classify_program(path: Path) -> str:
    """Return the program key for an Analyzed/ file (first match wins)."""
    meta = _parse_analyzed_meta(path)
    ws = meta["workstream"]
    ws_lower = ws.lower().strip()
    title = meta["title"]
    participants_lower = {p.lower() for p in meta["participants"]}

    # Admin — not written to any STATUS.md
    if ws_lower in _ADMIN_WS or _ADMIN_TITLE_KW.search(title):
        return _ADMIN_PROGRAM

    # Presales signals (check before DISW/SI so Energy/Healthineers/FS meetings
    # don't fall into the broader DISW or SI-RCA buckets)
    if _PRESALES_ENERGY_KW.search(ws) or _PRESALES_ENERGY_KW.search(title):
        return "presales-energy"
    if _PRESALES_HEALTHINEERS_KW.search(ws) or _PRESALES_HEALTHINEERS_KW.search(title):
        return "presales-healthineers"
    if _PRESALES_FS_KW.search(ws) or _PRESALES_FS_KW.search(title):
        return "presales-financial-services"

    # DISW-RCA — RCA PoC signals
    if any(kw in ws_lower for kw in _DISW_RCA_WORKSTREAMS):
        return "DISW-RCA"
    if _DISW_RCA_PARTICIPANTS & participants_lower:
        return "DISW-RCA"
    if _DISW_RCA_TITLE_KW.search(title):
        return "DISW-RCA"

    # DISW-CPQ — broader DI-SW workstream
    if _DISW_CPQ_WS_PREFIX.match(ws_lower):
        return "DISW-CPQ"
    if _DISW_CPQ_TITLE_KW.search(title):
        return "DISW-CPQ"

    # siemens-general — cross-division exec signals
    if ws_lower in _SIEMENS_GENERAL_WS:
        return "siemens-general"
    if _SIEMENS_GENERAL_KW.search(title) or any(
        _SIEMENS_GENERAL_KW.search(p) for p in meta["participants"]
    ):
        return "siemens-general"

    # SI-RCA — catch-all for Siemens files
    return "SI-RCA"


# ---------------------------------------------------------------------------
# STATUS.md reading / writing
# ---------------------------------------------------------------------------

_YAML_FENCE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_FRONTMATTER_KEY = re.compile(r"^({key}):\s*(.*)$", re.MULTILINE)


def _read_status(path: Path) -> tuple[dict, str]:
    """Parse STATUS.md → (frontmatter_dict, body_text).

    Returns raw frontmatter values as strings (not typed). Body is everything
    after the closing --- fence.
    """
    text = path.read_text(encoding="utf-8")
    m = _YAML_FENCE.match(text)
    if not m:
        return {}, text

    fm_text = m.group(1)
    body = text[m.end():]
    fm: dict[str, str] = {}
    for line in fm_text.splitlines():
        kv = re.match(r'^(\w[\w_-]*):\s*(.*)$', line)
        if kv:
            k, v = kv.group(1), kv.group(2).strip().strip('"')
            fm[k] = v
    return fm, body


def _write_status(path: Path, fm: dict, body: str) -> None:
    """Write updated frontmatter + body back to STATUS.md."""
    fm_lines = []
    for k, v in fm.items():
        if v is None or v == "null":
            fm_lines.append(f"{k}: null")
        else:
            # Quote values that contain special YAML characters
            needs_quote = any(c in str(v) for c in ('"', "'", ":", "#", "{", "["))
            if needs_quote:
                escaped = str(v).replace('"', '\\"')
                fm_lines.append(f'{k}: "{escaped}"')
            else:
                fm_lines.append(f"{k}: {v}")
    fm_block = "\n".join(fm_lines)
    path.write_text(f"---\n{fm_block}\n---\n{body}", encoding="utf-8")


def _append_open_signals(body: str, iso_date: str, n_files: int, bullets: str) -> str:
    """Insert a dated block under '## Open signals' in the body.

    The block is prepended (newest-first) right after the section header line.
    """
    header = "## Open signals"
    if header not in body:
        body = body + f"\n{header}\n\n*(none yet — sweep will populate)*\n"

    # Remove placeholder if present
    body = body.replace("*(none yet — sweep will populate)*\n", "")

    block = f"\n### {iso_date} — {n_files} new file(s)\n{bullets.strip()}\n"

    # Insert immediately after the ## Open signals line
    idx = body.find(header)
    insert_at = body.find("\n", idx) + 1  # position after the header line's newline
    body = body[:insert_at] + block + body[insert_at:]
    return body


# ---------------------------------------------------------------------------
# Lexicographic watermark helpers
# ---------------------------------------------------------------------------

def _leading_iso(name: str) -> str:
    """Extract the leading ISO timestamp prefix for lexicographic comparison."""
    m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})", name)
    return m.group(1) if m else ""


def _files_newer_than(analyzed_path: Path, watermark: Optional[str]) -> list[Path]:
    """Return [ANALYZED] files whose basename sorts after the watermark basename."""
    candidates = []
    for p in analyzed_path.iterdir():
        if not p.is_file():
            continue
        name = p.name
        if "[ANALYZED]" not in name:
            continue
        if "[SHAREABLE]" in name:
            continue
        candidates.append(p)

    # Also scan _Archive subdirectories
    archive_root = analyzed_path / "_Archive"
    if archive_root.is_dir():
        for month_dir in sorted(archive_root.iterdir()):
            if not month_dir.is_dir():
                continue
            for p in month_dir.iterdir():
                if p.is_file() and "[ANALYZED]" in p.name and "[SHAREABLE]" not in p.name:
                    candidates.append(p)

    if watermark:
        # Compare by leading ISO timestamp so lexicographic order == chronological
        wm_ts = _leading_iso(watermark)
        candidates = [p for p in candidates if _leading_iso(p.name) > wm_ts]

    candidates.sort(key=lambda p: _leading_iso(p.name))
    return candidates


# ---------------------------------------------------------------------------
# Claude call for signal summary
# ---------------------------------------------------------------------------

_PULSE_SYSTEM_TEMPLATE = (
    "You are a program intelligence assistant. Given meeting analyses for the "
    "{label} program, produce 3–5 bullet points summarizing:\n"
    "- Decisions made or ratified\n"
    "- Risks or blockers surfaced\n"
    "- Commitments given (who owes what by when)\n"
    "- Open questions requiring Brad's attention\n"
    "Be specific: name people, decisions, and dates. Avoid vague phrases.\n"
    "Output ONLY the bullet list. No preamble, no labels."
)

_MAX_CHARS_PER_FILE = 4000


def _call_claude(label: str, context_brief: str, files: list[Path], model: str) -> str:
    system = _PULSE_SYSTEM_TEMPLATE.format(label=label)
    if context_brief:
        system = f"{system}\n\n=== PROGRAM CONTEXT ===\n{context_brief}"

    parts = []
    for f in files:
        try:
            snippet = f.read_text(encoding="utf-8")[:_MAX_CHARS_PER_FILE]
        except OSError:
            continue
        parts.append(f"=== {f.name} ===\n{snippet}")

    bundle = "\n\n---\n\n".join(parts)
    prompt = f"Summarize signals from these {len(files)} meeting analysis file(s):\n\n{bundle}"

    data = claude_cli.run_claude_p(prompt, model=model, system=system)
    return claude_cli.result_text(data).strip()


# ---------------------------------------------------------------------------
# STATUS.md path map
# ---------------------------------------------------------------------------

def _status_paths(gov_repo: Path) -> dict[str, Path]:
    """Map program key → STATUS.md path within the governance repo."""
    return {
        "SI-RCA": gov_repo / "SI-RCA" / "STATUS.md",
        "DISW-RCA": gov_repo / "DISW-RCA" / "STATUS.md",
        "DISW-CPQ": gov_repo / "DISW-CPQ" / "STATUS.md",
        "siemens-general": gov_repo / "siemens-general" / "STATUS.md",
        "presales": gov_repo / "presales" / "STATUS.md",
        "presales-energy": gov_repo / "presales" / "Energy" / "STATUS.md",
        "presales-healthineers": gov_repo / "presales" / "Healthineers" / "STATUS.md",
        "presales-financial-services": gov_repo / "presales" / "Financial-Services" / "STATUS.md",
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_program_pulse(
    gov_repo_path: Optional[Path] = None,
    dry_run: bool = False,
) -> int:
    """Sweep Analyzed/ files into governance STATUS.md files.

    gov_repo_path defaults to ~/code/SiemensRCAGov.
    dry_run=True prints what would be written without touching any STATUS.md.
    Returns 0 on success, 1 on error.
    """
    gov_repo = (gov_repo_path or Path("~/code/SiemensRCAGov")).expanduser()
    if not gov_repo.is_dir():
        print(f"ERROR: governance repo not found: {gov_repo}", file=sys.stderr)
        return 1

    analyzed_path = CONFIG.analyzed_path
    if not analyzed_path.exists():
        print(f"ERROR: Analyzed/ path does not exist: {analyzed_path}", file=sys.stderr)
        return 1

    model = CONFIG.models.get("STANDUP", "claude-sonnet-4-6")
    if CONFIG.model_override:
        model = CONFIG.model_override

    context_brief = ""
    if CONFIG.context_brief_path.exists():
        context_brief = CONFIG.context_brief_path.read_text(encoding="utf-8")

    status_map = _status_paths(gov_repo)

    # Verify all STATUS.md files exist
    for prog, sp in status_map.items():
        if not sp.exists():
            print(f"WARNING: STATUS.md not found for {prog}: {sp}", file=sys.stderr)

    # Read watermarks from each STATUS.md
    watermarks: dict[str, Optional[str]] = {}
    labels: dict[str, str] = {}
    for prog, sp in status_map.items():
        if sp.exists():
            fm, _ = _read_status(sp)
            wm = fm.get("last_analyzed_file", "") or None
            watermarks[prog] = wm if wm and wm != "null" else None
            labels[prog] = fm.get("label", prog)
        else:
            watermarks[prog] = None
            labels[prog] = prog

    # Find all candidate files (newer than per-program watermark)
    # We scan once globally and bucket by program
    all_candidates = _files_newer_than(analyzed_path, watermark=None)
    print(f"Total [ANALYZED] files in Analyzed/ (including _Archive): {len(all_candidates)}")

    # Bucket by program
    buckets: dict[str, list[Path]] = {prog: [] for prog in PROGRAMS}
    for f in all_candidates:
        prog = classify_program(f)
        if prog == _ADMIN_PROGRAM:
            continue
        if prog in buckets:
            buckets[prog].append(f)

    today_iso = date.today().isoformat()
    errors = 0

    for prog in PROGRAMS:
        sp = status_map.get(prog)
        if sp is None or not sp.exists():
            continue

        wm = watermarks.get(prog)
        # Filter to files newer than this program's watermark
        all_prog_files = buckets[prog]
        if wm:
            wm_ts = _leading_iso(wm)
            new_files = [f for f in all_prog_files if _leading_iso(f.name) > wm_ts]
        else:
            new_files = all_prog_files

        if not new_files:
            print(f"[{prog}] no new files since watermark ({wm or 'none'}) — skipping")
            continue

        label = labels.get(prog, prog)
        print(f"[{prog}] {len(new_files)} new file(s) since {wm or 'start'}:")
        for f in new_files:
            print(f"  {f.name}")

        if dry_run:
            print(f"  [DRY RUN] would append a dated block to {sp}")
            continue

        # Call Claude for signal summary
        try:
            bullets = _call_claude(label, context_brief, new_files, model)
        except Exception as e:
            print(f"  ERROR: Claude call failed for {prog}: {e}", file=sys.stderr)
            errors += 1
            continue

        if not bullets.strip():
            print(f"  WARNING: empty Claude response for {prog} — skipping STATUS.md update", file=sys.stderr)
            continue

        # Update STATUS.md
        fm, body = _read_status(sp)
        body = _append_open_signals(body, today_iso, len(new_files), bullets)
        fm["last_updated"] = today_iso
        # Watermark = basename of the newest file processed
        newest_basename = new_files[-1].name
        fm["last_analyzed_file"] = newest_basename
        _write_status(sp, fm, body)
        print(f"  Updated {sp.relative_to(gov_repo)} (watermark → {newest_basename})")

    if dry_run:
        print("\n[DRY RUN] no STATUS.md files were modified.")
    return 1 if errors else 0
