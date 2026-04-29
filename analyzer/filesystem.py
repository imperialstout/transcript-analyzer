import re
from datetime import date
from pathlib import Path

from .config import CONFIG

_LEADING_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}Z)?\s*-\s*")
_DATE_TOKEN = re.compile(r"\d{4}-\d{2}-\d{2}")


def list_unanalyzed_transcripts() -> tuple[list[Path], int]:
    """Return (.txt files at root, count of .gdoc files seen).

    Subfolders (including _Processed/) are skipped — only the root counts.
    The .gdoc count is surfaced so the caller can warn Brad if the Zap
    update hasn't landed yet.
    """
    root = CONFIG.call_transcripts_path
    if not root.exists():
        raise FileNotFoundError(f"Call Transcripts path does not exist: {root}")
    txts: list[Path] = []
    gdocs = 0
    for entry in root.iterdir():
        if entry.is_dir():
            continue
        if entry.suffix == ".txt":
            txts.append(entry)
        elif entry.suffix == ".gdoc":
            gdocs += 1
    return sorted(txts), gdocs


def move_to_processed(transcript_path: Path, meeting_date: date) -> Path:
    target_dir = CONFIG.processed_path / f"{meeting_date.year:04d}-{meeting_date.month:02d}"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / transcript_path.name
    if target.exists():
        # POSIX rename silently overwrites — refuse rather than destroy data.
        raise FileExistsError(
            f"refusing to move {transcript_path.name}: target already exists at {target}"
        )
    transcript_path.rename(target)
    return target


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


def _core_title(filename: str) -> str:
    stem = Path(filename).stem
    stem = _LEADING_TIMESTAMP.sub("", stem)
    return _normalize(stem)


def _dates_in(name: str) -> set[str]:
    return set(_DATE_TOKEN.findall(name))


def fuzzy_is_analyzed(transcript_path: Path) -> bool:
    """Backstop for when the manifest is missing or out of sync.

    Returns True only when an analyzed filename shares both a date token
    AND the transcript's core title as a substring. Date overlap prevents
    short common titles ("Sync") from collapsing distinct meetings; the
    title-substring requirement still tolerates the inconsistent legacy
    naming in the existing Analyzed/ corpus.
    """
    if not CONFIG.analyzed_path.exists():
        return False
    needle = _core_title(transcript_path.name)
    if len(needle) < 5:
        return False
    transcript_dates = _dates_in(transcript_path.name)
    if not transcript_dates:
        return False
    for analyzed in CONFIG.analyzed_path.iterdir():
        if not transcript_dates & _dates_in(analyzed.stem):
            continue
        if needle in _normalize(analyzed.stem):
            return True
    return False


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    """Atomic write: tmp file then rename, so a crash mid-write never leaves
    a partial analysis at the destination path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
