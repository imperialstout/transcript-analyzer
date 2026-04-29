import re
from datetime import date, datetime
from pathlib import Path

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_LEADING_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}Z)?\s*-\s*")
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
    stem = _LEADING_TIMESTAMP.sub("", stem)
    stem = _TRAILING_DATE.sub("", stem)
    # Defensive: strip path separators in case a transcript name ever contains
    # one (would otherwise let the assembled output filename escape Analyzed/).
    stem = stem.replace("/", "_").replace("\\", "_")
    return stem.strip(" -")


def build_output_filename(
    transcript_filename: str, run_time: datetime | None = None
) -> tuple[str, date]:
    """`[ISO-timestamp] - [Title] - [Date] [ANALYZED].txt` per the doc convention.

    Returns (output_filename, meeting_date). The meeting_date is also fed to
    `move_to_processed` so the source ends up under _Processed/<YYYY-MM>/.
    """
    run_time = run_time or datetime.now()
    iso = run_time.strftime("%Y-%m-%dT%H-%M-%S")
    title = _original_title(transcript_filename)
    meeting_date = _extract_meeting_date(transcript_filename)
    name = f"{iso} - {title} - {meeting_date.isoformat()} [ANALYZED].txt"
    # Final safety net: ensure no path components survived assembly.
    name = Path(name).name
    return name, meeting_date
