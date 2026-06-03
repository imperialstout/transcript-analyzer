"""Daily Pulse (D1) and Weekly Slack Delta (D2) synthesis.

Reads filed [ANALYZED] outputs from Analyzed/, bundles them with the matching
prompt from dailyAndWeeklyPrompts.md, runs a single claude -p call, writes
the synthesis to Analyzed/, and archives the inputs to Analyzed/_Archive/YYYY-MM/.

Usage:
    python -m analyzer synthesize --mode daily
    python -m analyzer synthesize --mode weekly

Design choices:
- Only [ANALYZED] files are bundled — [SHAREABLE] and prior synthesis outputs
  ([DAILY PULSE], [WEEKLY SUMMARY], [SLACK DELTA]) are excluded.
- Archive moves files to Analyzed/_Archive/YYYY-MM/ keyed on the meeting date
  embedded in the filename. Refuses to overwrite existing targets (fail closed).
- The synthesis file itself is NOT archived — it stays in Analyzed/ as context
  for the next pulse/delta.
- dailyAndWeeklyPrompts.md is parsed by the same `### KEY.` + fenced-block
  convention as PromptLibrary.md; keys are D1 and D2.
- If the synthesis prompt is missing, the run fails with a clear message.
"""

import argparse
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from . import claude_cli
from . import prompts as prompts_mod
from .config import CONFIG
from .filesystem import write_text

_DAILY_PROMPTS_PATH = (
    CONFIG.analyzed_path.parent / "dailyAndWeeklyPrompts.md"
)

# Keys in dailyAndWeeklyPrompts.md
_MODE_KEY = {"daily": "D1", "weekly": "D2"}
_MODE_SUFFIX = {"daily": "[DAILY PULSE]", "weekly": "[SLACK DELTA]"}

# Only bundle internal analyses — skip shareable siblings and prior synthesis outputs.
_SKIP_TAGS = ("[SHAREABLE]", "[DAILY PULSE]", "[WEEKLY SUMMARY]", "[SLACK DELTA]")

_DATE_TOKEN = re.compile(r"\d{4}-\d{2}-\d{2}")

# Heading pattern for D1/D2 keys in dailyAndWeeklyPrompts.md
_HEADING = re.compile(r"^### (D\d)\.", re.MULTILINE)
_FENCED = re.compile(r"^```\s*\n(.*?)\n```", re.DOTALL | re.MULTILINE)


def _load_synthesis_prompts() -> dict[str, str]:
    p = _DAILY_PROMPTS_PATH
    if not p.exists():
        raise FileNotFoundError(f"dailyAndWeeklyPrompts.md not found at {p}")
    text = p.read_text(encoding="utf-8")
    matches = list(_HEADING.finditer(text))
    out: dict[str, str] = {}
    for i, m in enumerate(matches):
        key = m.group(1)
        section_start = m.end()
        section_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        fenced = _FENCED.search(text, section_start, section_end)
        if fenced:
            out[key] = fenced.group(1).strip()
    return out


def _meeting_date_from_filename(name: str) -> date | None:
    m = _DATE_TOKEN.search(name)
    if m:
        try:
            return datetime.strptime(m.group(0), "%Y-%m-%d").date()
        except ValueError:
            pass
    return None


def _files_for_mode(mode: str, analyzed_path: Path) -> list[Path]:
    """Return [ANALYZED] files whose meeting date falls in the target window."""
    today = date.today()
    if mode == "daily":
        target_dates = {today}
    else:
        # Current ISO week: Monday through today
        monday = today - timedelta(days=today.weekday())
        target_dates = {monday + timedelta(days=i) for i in range(today.weekday() + 1)}

    found = []
    for f in sorted(analyzed_path.iterdir()):
        if f.is_dir():
            continue
        name = f.name
        if not any(name.endswith(tag + ".txt") or tag in name for tag in []):
            pass
        # Skip synthesis outputs and shareables
        if any(tag in name for tag in _SKIP_TAGS):
            continue
        if "[ANALYZED]" not in name:
            continue
        d = _meeting_date_from_filename(name)
        if d and d in target_dates:
            found.append(f)
    return found


def _most_recent_synthesis(analyzed_path: Path, tag: str) -> Path | None:
    """Find the most recent synthesis file with the given tag, for context."""
    candidates = [
        f for f in analyzed_path.iterdir()
        if not f.is_dir() and tag in f.name
    ]
    return max(candidates, key=lambda f: f.name, default=None)


def _archive(files: list[Path], analyzed_path: Path) -> None:
    archive_root = analyzed_path / "_Archive"
    for f in files:
        d = _meeting_date_from_filename(f.name)
        folder = f"{d.year:04d}-{d.month:02d}" if d else "unknown"
        target_dir = archive_root / folder
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f.name
        if target.exists():
            raise FileExistsError(
                f"refusing to archive {f.name}: target already exists at {target}"
            )
        f.rename(target)
        print(f"  archived → _Archive/{folder}/{f.name}")


def _build_bundle(files: list[Path], prior_synthesis: Path | None) -> str:
    parts = []
    if prior_synthesis and prior_synthesis.exists():
        parts.append(
            f"=== MOST RECENT PRIOR SUMMARY ===\n"
            f"(File: {prior_synthesis.name})\n\n"
            f"{prior_synthesis.read_text(encoding='utf-8')}"
        )
    for f in files:
        parts.append(
            f"=== ANALYSIS FILE: {f.name} ===\n\n"
            f"{f.read_text(encoding='utf-8')}"
        )
    return "\n\n---\n\n".join(parts)


def _output_filename(mode: str) -> str:
    now = datetime.now()
    iso = now.strftime("%Y-%m-%dT%H-%M-%S")
    today = date.today().isoformat()
    suffix = _MODE_SUFFIX[mode]
    if mode == "daily":
        return f"{iso} - Daily Pulse - {today} {suffix}.md"
    else:
        monday = date.today() - timedelta(days=date.today().weekday())
        return f"{iso} - Slack Delta - Week of {monday.isoformat()} {suffix}.md"


def run(mode: str) -> int:
    analyzed_path = CONFIG.analyzed_path
    if not analyzed_path.exists():
        print(f"ERROR: Analyzed/ path does not exist: {analyzed_path}", file=sys.stderr)
        return 1

    try:
        synthesis_prompts = _load_synthesis_prompts()
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    key = _MODE_KEY[mode]
    if key not in synthesis_prompts:
        print(
            f"ERROR: prompt key {key!r} not found in dailyAndWeeklyPrompts.md — "
            f"add a ### {key}. block with a fenced body.",
            file=sys.stderr,
        )
        return 1

    files = _files_for_mode(mode, analyzed_path)
    if not files:
        label = "today" if mode == "daily" else "this week"
        print(f"No [ANALYZED] files found for {label} — nothing to synthesize.")
        return 0

    print(f"Found {len(files)} file(s) for {mode} synthesis:")
    for f in files:
        print(f"  {f.name}")

    prior_tag = "[DAILY PULSE]" if mode == "daily" else "[SLACK DELTA]"
    prior = _most_recent_synthesis(analyzed_path, prior_tag)
    if prior:
        print(f"Including prior {mode} for context: {prior.name}")

    bundle = _build_bundle(files, prior)
    prompt_body = synthesis_prompts[key]

    context_brief = CONFIG.context_brief_path.read_text(encoding="utf-8") if CONFIG.context_brief_path.exists() else ""
    rolodex = prompts_mod.load_rolodex()

    system_parts = ["You are analyzing filed meeting analyses for an enterprise Salesforce program (SherpaX at Siemens). The reader is Brad — Revenue Cloud CTO."]
    if context_brief:
        system_parts.append(f"=== PROGRAM CONTEXT BRIEF ===\n{context_brief}")
    if rolodex:
        system_parts.append(f"=== PEOPLE ROLODEX ===\n{rolodex}")
    system_parts.append(f"=== SYNTHESIS INSTRUCTIONS ===\n{prompt_body}")
    system = "\n\n".join(system_parts)

    model = CONFIG.models.get("STANDUP", "claude-sonnet-4-6")  # Sonnet — synthesis is cheap
    print(f"Running {mode} synthesis with {model}...")

    try:
        data = claude_cli.run_claude_p(bundle, model=model, system=system)
        text = claude_cli.result_text(data)
    except Exception as e:
        print(f"ERROR: synthesis failed — {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    if not text.strip():
        print("ERROR: synthesis returned empty result", file=sys.stderr)
        return 1

    out_name = _output_filename(mode)
    out_path = analyzed_path / out_name
    write_text(out_path, text)
    print(f"Synthesis written → {out_name}")

    # Archive input files after successful write
    try:
        _archive(files, analyzed_path)
    except Exception as e:
        print(
            f"WARNING: archive step failed ({type(e).__name__}: {e}); "
            f"synthesis file is written but inputs were NOT archived.",
            file=sys.stderr,
        )
        return 2

    print(f"All done. {len(files)} file(s) archived.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m analyzer synthesize",
        description="Run D1 (daily pulse) or D2 (weekly slack delta) synthesis.",
    )
    parser.add_argument(
        "--mode",
        choices=["daily", "weekly"],
        required=True,
        help="daily = D1 pulse over today's analyses; weekly = D2 delta over this week's",
    )
    args = parser.parse_args()
    return run(args.mode)


if __name__ == "__main__":
    sys.exit(main())
