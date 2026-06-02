import re
from datetime import date, datetime
from pathlib import Path

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

# Plaud / Zap source filenames look like:
#   "2026-04-28T10:04:27Z - 2026-04-28 10:04:27 - RCA Weekly Raid meet.txt"
# (Finder/Drive display the colons as slashes — the on-disk character is `:`.)
# We strip BOTH leading blocks so the title doesn't end up with a redundant
# date+time stuck on the front. The second block is optional because some
# sources only have one prefix.
LEADING_PREFIX = re.compile(
    r"^"
    r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}Z)?"
    r"\s*-\s*"
    r"(?:\d{4}-\d{2}-\d{2}(?:[\sT]\d{2}:\d{2}:\d{2}Z?)?\s*-\s*)?"
)
_TRAILING_DATE = re.compile(r"\s*-?\s*\d{4}-\d{2}-\d{2}\s*$")


def _extract_meeting_date(transcript_filename: str) -> date:
    m = _DATE.search(transcript_filename)
    if m:
        try:
            return datetime.strptime(m.group(0), "%Y-%m-%d").date()
        except ValueError:
            pass
    return date.today()


def _original_title(transcript_filename: str) -> str:
    stem = Path(transcript_filename).stem
    stem = LEADING_PREFIX.sub("", stem)
    stem = _TRAILING_DATE.sub("", stem)
    # Defensive: strip path separators in case a transcript name ever contains
    # one (would otherwise let the assembled output filename escape Analyzed/).
    stem = stem.replace("/", "_").replace("\\", "_")
    return stem.strip(" -")


def build_output_filename(
    transcript_filename: str,
    run_time: datetime | None = None,
    meeting_date_override: date | None = None,
) -> tuple[str, date]:
    """`[ISO-timestamp] - [Title] - [Date] [ANALYZED].txt` per the doc convention.

    Returns (output_filename, meeting_date). The meeting_date is also fed to
    `move_to_processed` so the source ends up under _Processed/<YYYY-MM>/.

    `meeting_date_override` lets callers (notes intake) supply the date from
    an authoritative source like YAML frontmatter, bypassing filename parsing.
    """
    run_time = run_time or datetime.now()
    iso = run_time.strftime("%Y-%m-%dT%H-%M-%S")
    title = _original_title(transcript_filename)
    meeting_date = meeting_date_override or _extract_meeting_date(transcript_filename)
    name = f"{iso} - {title} - {meeting_date.isoformat()} [ANALYZED].txt"
    # Final safety net: ensure no path components survived assembly.
    name = Path(name).name
    return name, meeting_date


def shareable_filename(analyzed_filename: str) -> str:
    """Derive the `[SHAREABLE]` sibling of an `[ANALYZED]` output name.

    Swaps the `[ANALYZED]` tag for `[SHAREABLE]` so the redacted, leads-readable
    version shares the exact timestamp/title/date stem as the internal file and
    sorts adjacent to it in `Analyzed/`. Falls back to a suffix insert if the
    tag isn't present (shouldn't happen, but never returns a colliding name).
    """
    if "[ANALYZED]" in analyzed_filename:
        name = analyzed_filename.replace("[ANALYZED]", "[SHAREABLE]")
    else:
        stem = Path(analyzed_filename).stem
        suffix = Path(analyzed_filename).suffix or ".txt"
        name = f"{stem} [SHAREABLE]{suffix}"
    return Path(name).name
