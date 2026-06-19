"""Daily Pulse (D1), Weekly Slack Delta (D2), and Career Trajectory (D3) synthesis.

Reads filed [ANALYZED] outputs from Analyzed/, bundles them with the matching
prompt from dailyAndWeeklyPrompts.md, runs a single claude -p call, writes
the synthesis to Analyzed/, and (for daily/weekly) archives the inputs to
Analyzed/_Archive/YYYY-MM/.

Usage:
    python -m analyzer synthesize --mode daily
    python -m analyzer synthesize --mode weekly
    python -m analyzer synthesize --mode career

Design choices:
- Only [ANALYZED] files are bundled — [SHAREABLE] and prior synthesis outputs
  ([DAILY PULSE], [WEEKLY SUMMARY], [SLACK DELTA], [CAREER TRAJECTORY]) are excluded.
- Archive moves files to Analyzed/_Archive/YYYY-MM/ keyed on the meeting date
  embedded in the filename. Refuses to overwrite existing targets (fail closed).
- career mode does NOT archive: its trajectory is cumulative, so it keeps its
  inputs and chains off the prior [CAREER TRAJECTORY] for continuity. daily/weekly
  use a date window (today / this ISO week); career bundles all current analyses.
- The synthesis file itself is NOT archived — it stays in Analyzed/ as context
  for the next pulse/delta/review.
- dailyAndWeeklyPrompts.md is parsed by the same `### KEY.` + fenced-block
  convention as PromptLibrary.md; keys are D1, D2, D3.
- If the synthesis prompt is missing, the run fails with a clear message.
"""

import argparse
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from . import claude_cli
from . import prompts as prompts_mod
from .config import CONFIG, model_for
from .filesystem import write_text

_DAILY_PROMPTS_PATH = (
    CONFIG.analyzed_path.parent / "dailyAndWeeklyPrompts.md"
)

# Keys in dailyAndWeeklyPrompts.md
_MODE_KEY = {"daily": "D1", "weekly": "D2", "career": "D3"}
_MODE_SUFFIX = {
    "daily": "[DAILY PULSE]",
    "weekly": "[SLACK DELTA]",
    "career": "[CAREER TRAJECTORY]",
}

# Only bundle internal analyses — skip shareable siblings, prior synthesis
# outputs, and the program reference file (it's injected as context, not bundled).
_SKIP_TAGS = (
    "[SHAREABLE]",
    "[DAILY PULSE]",
    "[WEEKLY SUMMARY]",
    "[SLACK DELTA]",
    "[CAREER TRAJECTORY]",
    "[PROGRAM REFERENCE]",
)

_DATE_TOKEN = re.compile(r"\d{4}-\d{2}-\d{2}")

# When the bundled inputs would overflow a single `claude -p` call, fall back to
# map-reduce: split the files into batches that each fit, run a detail-preserving
# digest pass per batch, then run the real synthesis prompt over the digests.
# This keeps the "read all N files" value of the weekly (catching what the daily
# compression dropped) without blowing claude_cli's char ceiling or the model
# window. A busy week (~50 meetings) overflows; a normal day/career run does not,
# so the single-pass path below is unchanged for those.
#
# Single-pass threshold sits under claude_cli._MAX_PROMPT_CHARS (525K) with
# margin for the task prefix; the per-batch budget is smaller still so each
# digest call has ample headroom for the system prefix + its own output.
_SINGLE_PASS_BUDGET_CHARS = 480_000
_BUNDLE_BUDGET_CHARS = 380_000

_DIGEST_TASK = (
    "You are PRE-DIGESTING a batch of filed meeting analyses as one stage of a "
    "larger synthesis — do NOT write the final synthesis. Extract and preserve, "
    "with attribution and specifics, everything a strategic review would need "
    "from THIS batch: decisions made/reversed and who drove them; risks, "
    "blockers, and slippage; commitments and owners; political signals "
    "(alignment, friction, territory, who is gaining or losing ground); missed "
    "opportunities and unaddressed threads; and notable attributed quotes. Keep "
    "names. Prefer completeness over brevity — this digest is the ONLY thing the "
    "final synthesis will see from these files. Output structured notes, not prose."
)

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


def _files_for_mode(mode: str, analyzed_path: Path, week: str = "current", target_date: date | None = None) -> list[Path]:
    """Return the [ANALYZED] files to bundle for this mode.

    daily  → the anchor day's meetings; weekly → the anchor's ISO week (Mon–anchor);
    career → all current analyses (no date window — the trajectory is a
    bigger-picture read over everything still in Analyzed/).

    ``target_date`` back-dates daily/weekly to recover a missed run; when None the
    anchor is today (unchanged behavior).
    """
    anchor = target_date or date.today()
    if mode == "career":
        target_dates = None  # no window
    elif mode == "daily":
        target_dates = {anchor}
    else:
        # ISO week containing the anchor: Monday through anchor; "last" shifts back 7 days
        monday = anchor - timedelta(days=anchor.weekday())
        if week == "last":
            monday = monday - timedelta(weeks=1)
        week_end = monday + timedelta(days=6)
        target_dates = {monday + timedelta(days=i) for i in range(7) if monday + timedelta(days=i) <= min(week_end, anchor)}

    # Determine which archive month folders to also scan
    archive_root = analyzed_path / "_Archive"
    if target_dates is None:
        # career mode: scan all archive subdirs
        extra_dirs = sorted(archive_root.iterdir()) if archive_root.is_dir() else []
        scan_dirs = [analyzed_path] + [d for d in extra_dirs if d.is_dir()]
    else:
        archive_months = {f"{d.year:04d}-{d.month:02d}" for d in target_dates}
        scan_dirs = [analyzed_path] + [
            archive_root / m for m in archive_months
            if (archive_root / m).is_dir()
        ]

    found = []
    for scan_dir in scan_dirs:
        for f in sorted(scan_dir.iterdir()):
            if f.is_dir():
                continue
            name = f.name
            if any(tag in name for tag in _SKIP_TAGS):
                continue
            if "[ANALYZED]" not in name:
                continue
            if target_dates is None:
                found.append(f)
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
        # Already in the archive — was included for bundling but needs no move.
        if archive_root in f.parents:
            continue
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


def _file_block(f: Path) -> str:
    """One file rendered as a bundle section (header + body)."""
    return f"=== ANALYSIS FILE: {f.name} ===\n\n{f.read_text(encoding='utf-8')}"


def _build_bundle(files: list[Path], prior_synthesis: Path | None) -> str:
    parts = []
    if prior_synthesis and prior_synthesis.exists():
        parts.append(
            f"=== MOST RECENT PRIOR SUMMARY ===\n"
            f"(File: {prior_synthesis.name})\n\n"
            f"{prior_synthesis.read_text(encoding='utf-8')}"
        )
    for f in files:
        parts.append(_file_block(f))
    return "\n\n---\n\n".join(parts)


def _batch_by_budget(files: list[Path], budget_chars: int) -> list[list[Path]]:
    """Greedily pack files into batches whose rendered size stays under budget.

    A single file larger than the budget gets its own batch (it still overflows,
    but the digest call will raise a legible error rather than silently dropping
    it — fail closed). Preserves file order so date grouping stays intact.
    """
    batches: list[list[Path]] = []
    current: list[Path] = []
    current_chars = 0
    for f in files:
        size = len(_file_block(f)) + len("\n\n---\n\n")
        if current and current_chars + size > budget_chars:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(f)
        current_chars += size
    if current:
        batches.append(current)
    return batches


def _digest_batches(files: list[Path], system: str, model: str) -> str:
    """Map step: digest files in budget-sized batches, return concatenated digests.

    Each batch runs through the digest task (detail-preserving extraction, not
    final synthesis) so the reduce step sees a compact-but-complete stand-in for
    every file. Raises on any batch failure — fail closed, consistent with run().
    """
    batches = _batch_by_budget(files, _BUNDLE_BUDGET_CHARS)
    print(
        f"  bundle exceeds single-pass budget — map-reduce over "
        f"{len(batches)} batch(es) of {len(files)} file(s)"
    )
    digests = []
    for i, batch in enumerate(batches, 1):
        batch_bundle = "\n\n---\n\n".join(_file_block(f) for f in batch)
        print(f"  digesting batch {i}/{len(batches)} ({len(batch)} file(s))...")
        data = claude_cli.run_claude_p(
            f"{_DIGEST_TASK}\n\nBatch {i} of {len(batches)}:\n\n{batch_bundle}",
            model=model,
            system=system,
        )
        digest = claude_cli.result_text(data)
        if not digest.strip():
            raise claude_cli.ClaudeCliError(
                f"digest batch {i}/{len(batches)} returned empty result"
            )
        digests.append(f"=== BATCH {i} DIGEST ({len(batch)} file(s)) ===\n\n{digest}")
    return "\n\n---\n\n".join(digests)


def _output_filename(mode: str, target_date: date | None = None) -> str:
    # iso prefix uses real wall-clock time (filename uniqueness + correct
    # newest-first sort for continuity chaining), even when back-dating.
    now = datetime.now()
    iso = now.strftime("%Y-%m-%dT%H-%M-%S")
    anchor = (target_date or date.today()).isoformat()
    suffix = _MODE_SUFFIX[mode]
    if mode == "daily":
        return f"{iso} - Daily Pulse - {anchor} {suffix}.md"
    elif mode == "career":
        return f"{iso} - Career Trajectory - {anchor} {suffix}.md"
    else:
        anchor_date = target_date or date.today()
        monday = anchor_date - timedelta(days=anchor_date.weekday())
        return f"{iso} - Slack Delta - Week of {monday.isoformat()} {suffix}.md"


def run(mode: str, week: str = "current", target_date: date | None = None) -> int:
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

    files = _files_for_mode(mode, analyzed_path, week=week, target_date=target_date)
    if not files:
        if target_date and mode in ("daily", "weekly"):
            label = f"{target_date.isoformat()}" + (" (its ISO week)" if mode == "weekly" else "")
        else:
            label = {"daily": "today", "weekly": "this week", "career": "in Analyzed/"}[mode]
        print(f"No [ANALYZED] files found for {label} — nothing to synthesize.")
        return 0

    print(f"Found {len(files)} file(s) for {mode} synthesis:")
    for f in files:
        print(f"  {f.name}")

    # Chain off the most recent synthesis of the same kind for continuity.
    prior_tag = _MODE_SUFFIX[mode]
    prior = _most_recent_synthesis(analyzed_path, prior_tag)
    if prior:
        print(f"Including prior {mode} for context: {prior.name}")

    prompt_body = synthesis_prompts[key]

    context_brief = CONFIG.context_brief_path.read_text(encoding="utf-8") if CONFIG.context_brief_path.exists() else ""
    rolodex = prompts_mod.load_rolodex()
    program_reference = prompts_mod.load_program_reference()

    system_parts = ["You are analyzing filed meeting analyses for an enterprise program. Use the Program Context Brief below for background on the program and its people."]
    if context_brief:
        system_parts.append(f"=== PROGRAM CONTEXT BRIEF ===\n{context_brief}")
    if program_reference:
        system_parts.append(f"=== PROGRAM REFERENCE (pipeline-maintained facts) ===\n{program_reference}")
    if rolodex:
        system_parts.append(f"=== PEOPLE ROLODEX ===\n{rolodex}")
    system_parts.append(f"=== SYNTHESIS INSTRUCTIONS ===\n{prompt_body}")
    system = "\n\n".join(system_parts)

    # Daily/weekly pulses are cheap recaps → Sonnet. Career trajectory is
    # high-value strategic reasoning → the B4 (political/career) model, which is
    # Opus on the personal machine. model_for honors MODEL_OVERRIDE.
    model = model_for("B4") if mode == "career" else CONFIG.models.get("STANDUP", "claude-sonnet-4-6")
    print(f"Running {mode} synthesis with {model}...")

    try:
        task = {"daily": "Generate the Daily Pulse.", "weekly": "Generate the Weekly Slack Delta.", "career": "Generate the Career Trajectory synthesis."}[mode]
        # Map-reduce when the full bundle would overflow a single call: digest the
        # files in budget-sized batches first, then synthesize over the digests.
        # The prior synthesis (continuity) is kept whole in the reduce step.
        bundle = _build_bundle(files, prior)
        if len(bundle) > _SINGLE_PASS_BUDGET_CHARS:
            digests = _digest_batches(files, system, model)
            reduce_parts = []
            if prior and prior.exists():
                reduce_parts.append(
                    f"=== MOST RECENT PRIOR SUMMARY ===\n"
                    f"(File: {prior.name})\n\n{prior.read_text(encoding='utf-8')}"
                )
            reduce_parts.append(
                "=== BATCH DIGESTS (detail-preserving extracts of every analysis "
                "file this period) ===\n\n" + digests
            )
            bundle = "\n\n---\n\n".join(reduce_parts)
            print(f"  reducing {len(digests):,}-char digest set into final {mode} synthesis...")
        data = claude_cli.run_claude_p(f"{task}\n\n{bundle}", model=model, system=system)
        text = claude_cli.result_text(data)
    except Exception as e:
        print(f"ERROR: synthesis failed — {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    if not text.strip():
        print("ERROR: synthesis returned empty result", file=sys.stderr)
        return 1

    out_name = _output_filename(mode, target_date=target_date)
    out_path = analyzed_path / out_name
    write_text(out_path, text)
    print(f"Synthesis written → {out_name}")

    # career keeps its inputs: the trajectory is cumulative and chains off the
    # prior review, so we don't archive (or pay to re-derive) the analyses.
    if mode == "career":
        print(f"All done. {len(files)} analysis file(s) reviewed (kept in place — not archived).")
        return 0

    # Archive input files after successful write (daily/weekly only)
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
        description="Run D1 (daily pulse), D2 (weekly slack delta), or D3 (career trajectory) synthesis.",
    )
    parser.add_argument(
        "--mode",
        choices=["daily", "weekly", "career"],
        required=True,
        help=(
            "daily = D1 pulse over today's analyses; weekly = D2 delta over this "
            "week's; career = D3 position-trajectory review over all current analyses"
        ),
    )
    parser.add_argument(
        "--week",
        choices=["current", "last"],
        default="current",
        help="weekly only: 'current' = Mon–today (default); 'last' = prior full Mon–Sun week",
    )
    parser.add_argument(
        "--date",
        default=None,
        help=(
            "daily/weekly: anchor the window on this YYYY-MM-DD instead of today "
            "(back-date a missed run); ignored for career"
        ),
    )
    args = parser.parse_args()

    target_date = None
    if args.date:
        try:
            target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"ERROR: --date must be YYYY-MM-DD, got {args.date!r}", file=sys.stderr)
            return 1

    return run(args.mode, week=args.week, target_date=target_date)


if __name__ == "__main__":
    sys.exit(main())
