"""Per-file signal extraction — writes candidate inbox items to brief-state.

Called at the end of each analysis run for newly analyzed files. Extracts
1–5 action-relevant signals for Brad and writes them to brief-state.json
with status=pending and source_analyzed set (marking them as inbox candidates,
not yet triaged).

Usage:
    from .signal_extract import extract_signals_from_file
    extract_signals_from_file(analyzed_path, source_filename, program_key)
"""

import re
import sys
from pathlib import Path
from typing import Optional

from . import brief_state
from . import claude_cli
from .config import CONFIG

_EXTRACT_SYSTEM = """\
You are extracting action-relevant signals for Brad Gross from a single meeting \
analysis file. Brad is Revenue Cloud CTO and design authority for the Siemens \
SherpaX program.

Classify each signal as one of:
  act      — Brad needs to do something specific (he is the subject)
  watch    — Brad should monitor; no immediate action needed
  they-owe — a specific person owes Brad or the program something

Rules:
- Only extract signals where Brad is the directly relevant party
- Be specific: name the person, the thing, and the deadline if one exists
- Source reliability: these are AI-generated transcription summaries. \
  A remark ≠ a decision. Flag uncertainty with "reportedly" or "per meeting notes" \
  where the source is ambiguous. Numbers are estimates.
- Maximum 5 items. Fewer is better if signal is weak.
- Skip general program updates that don't require Brad's attention.
- Skip anything that reads as already resolved in the same file.

Output format — one item per line, pipe-delimited, exactly this structure:
[type]|[owner: person name if they-owe, else "Brad"]|[signal: one specific sentence]|[deadline: YYYY-MM-DD or "no date"]

Example:
act|Brad|Confirm with Jitu that waterfall API approach has not been shared with Siemens yet|no date
they-owe|Dinesh Kumar Voona|Load-test characterization of waterfall API at large line counts (10K–50K range per Brad's ask)|no date
watch|Brad|DocGen (Javiera) now has undeclared dependency on ICS JSON storage design — if ICS slips, DocGen is blocked|no date
"""

_MAX_FILE_CHARS = 14000  # read more of the file than pulse — extraction benefits from full context


def _parse_extraction(raw: str) -> list[dict]:
    """Parse pipe-delimited extraction output into brief-state item dicts."""
    items = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            continue
        item_type = parts[0].lower().strip("[]")
        owner = parts[1] if len(parts) > 1 else "Brad"
        text = parts[2] if len(parts) > 2 else ""
        deadline_raw = parts[3] if len(parts) > 3 else "no date"

        if item_type not in ("act", "watch", "they-owe"):
            continue
        if not text:
            continue

        deadline = None
        if deadline_raw and deadline_raw.lower() not in ("no date", "none", ""):
            m = re.search(r"\d{4}-\d{2}-\d{2}", deadline_raw)
            if m:
                deadline = m.group(0)

        items.append({
            "type": item_type,
            "owner": owner if item_type == "they-owe" else "Brad",
            "text": text,
            "deadline": deadline,
        })
    return items


def extract_signals_from_file(
    analyzed_file: Path,
    program_key: Optional[str] = None,
    model: Optional[str] = None,
    dry_run: bool = False,
) -> list[dict]:
    """Extract signals from one analyzed file and write candidates to brief-state.

    Returns the list of candidate items written (or that would be written in dry_run).
    """
    if not analyzed_file.exists():
        print(f"  [signal_extract] file not found: {analyzed_file}", file=sys.stderr)
        return []

    try:
        content = analyzed_file.read_text(encoding="utf-8")[:_MAX_FILE_CHARS]
    except OSError as e:
        print(f"  [signal_extract] read error: {e}", file=sys.stderr)
        return []

    if not model:
        model = CONFIG.models.get("STANDUP", "claude-sonnet-4-6")
    if CONFIG.model_override:
        model = CONFIG.model_override

    prompt = f"Extract action-relevant signals from this meeting analysis:\n\n{content}"
    try:
        result = claude_cli.run_claude_p(prompt, system=_EXTRACT_SYSTEM, model=model)
        raw = claude_cli.result_text(result).strip()
    except Exception as e:
        print(f"  [signal_extract] Claude call failed: {e}", file=sys.stderr)
        return []

    candidates = _parse_extraction(raw)
    if not candidates:
        return []

    source_name = analyzed_file.name
    written = []
    for c in candidates:
        item = {
            "type": c["type"],
            "owner": c["owner"],
            "text": c["text"],
            "deadline": c["deadline"],
            "status": "pending",
            "source_analyzed": source_name,
            "source_brief": "",
            "source_pulse": "",
            "notes": "",
            "program": program_key,
        }
        if dry_run:
            print(f"  [DRY RUN] would write candidate: [{c['type']}] {c['text'][:80]}")
        else:
            brief_state.upsert(item)
        written.append(item)

    if not dry_run and written:
        print(f"  [signal_extract] wrote {len(written)} candidate(s) from {source_name}")

    return written
