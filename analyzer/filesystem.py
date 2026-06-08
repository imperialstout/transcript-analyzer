import errno
import io
import re
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

from . import drive_client
from .config import CONFIG
from .filing import LEADING_PREFIX

# Optional imports — graceful degradation so uninstalled packages only break
# the specific format, not the whole pipeline.
try:
    import pypdf
    _PYPDF_AVAILABLE = True
except ImportError:
    _PYPDF_AVAILABLE = False

try:
    import docx as _docx
    _DOCX_AVAILABLE = True
except ImportError:
    _DOCX_AVAILABLE = False

_PROGRAM_REFERENCE_FILENAME = "[PROGRAM REFERENCE].md"
_DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".md", ".txt"}

_DATE_TOKEN = re.compile(r"\d{4}-\d{2}-\d{2}")


def list_unanalyzed_transcripts() -> tuple[list[Path], int]:
    """Return (.txt/.md files at root, count of .gdoc files seen).

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
        if entry.suffix in (".txt", ".md"):
            txts.append(entry)
        elif entry.suffix == ".gdoc":
            gdocs += 1
    return sorted(txts), gdocs


def move_to_processed(
    transcript_path: Path,
    meeting_date: date,
    processed_root: Path | None = None,
) -> Path:
    root = processed_root or CONFIG.processed_path
    target_dir = root / f"{meeting_date.year:04d}-{meeting_date.month:02d}"
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
    stem = LEADING_PREFIX.sub("", stem)
    return _normalize(stem)


def _dates_in(name: str) -> set[str]:
    return set(_DATE_TOKEN.findall(name))


def fuzzy_is_analyzed(transcript_path: Path) -> bool:
    """Backstop for when the manifest is missing or out of sync.

    Primary path: source filename has a 4-digit year date token — requires
    both a date-token overlap AND the core title as a substring. Date overlap
    prevents short common titles ("Sync") from collapsing distinct meetings.

    Fallback path: source filename has NO 4-digit year (e.g. Slack exports
    like "06-02 Title.txt") — does a title-only match with a higher confidence
    bar (needle >= 15 chars) so short generic titles still can't false-positive.

    Both paths search Analyzed/ AND Analyzed/_Archive/ so a file that was
    archived after a prior synthesis run is still detected as already done.
    """
    analyzed_root = CONFIG.analyzed_path
    if not analyzed_root.exists():
        return False
    needle = _core_title(transcript_path.name)
    if len(needle) < 5:
        return False
    transcript_dates = _dates_in(transcript_path.name)
    no_date_source = not transcript_dates

    # Higher confidence bar when source has no date to anchor the match.
    if no_date_source and len(needle) < 15:
        return False

    def _all_analyzed():
        for f in analyzed_root.iterdir():
            if not f.is_dir():
                yield f
        archive = analyzed_root / "_Archive"
        if archive.exists():
            for f in archive.rglob("*"):
                if not f.is_dir():
                    yield f

    for analyzed in _all_analyzed():
        if not no_date_source:
            if not transcript_dates & _dates_in(analyzed.stem):
                continue
        if needle in _normalize(analyzed.stem):
            return True
    return False


def read_text(path: Path, drive_service=None) -> str:
    """Read a local file, with layered fallbacks for the Drive × launchd quirk.

    Drive's File Provider returns EDEADLK to launchd-spawned processes for
    freshly-synced files. We've observed this hit both `open()` and `/bin/cat`
    in the same process — so retries on local reads aren't enough on their own,
    and the Drive API is the only reliable escape hatch.

    Defense layers (each only runs if the previous failed with EDEADLK):
      1. `open()` + retry (3 attempts, 0.5s/1.0s backoff) — absorbs short locks.
      2. `/bin/cat` + retry — uses a different syscall path than `open()`.
      3. Drive API `files.get_media` by name — bypasses the FUSE mount entirely.

    Non-EDEADLK errors propagate immediately (no point retrying a real
    permission denied / not found). Fails closed if all layers exhaust.
    """
    open_err: OSError | None = None
    for attempt in range(3):
        try:
            return path.read_text(encoding="utf-8")
        except OSError as e:
            if e.errno != errno.EDEADLK:
                raise
            open_err = e
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))

    cat_stderr = "<no stderr>"
    for attempt in range(3):
        result = subprocess.run(
            ["/bin/cat", str(path)],
            capture_output=True,
            timeout=30,
        )
        if result.returncode == 0:
            return result.stdout.decode("utf-8")
        cat_stderr = result.stderr.decode("utf-8", errors="replace").strip() or "<no stderr>"
        if attempt < 2:
            time.sleep(0.5 * (attempt + 1))

    if drive_service is not None:
        print(
            f"  read_text: local read failed for {path.name} "
            f"(open: {open_err}; cat: {cat_stderr}); trying Drive API",
            file=sys.stderr,
        )
        try:
            return drive_client.fetch_text_file_by_name(path.name, drive_service)
        except Exception as e:
            raise OSError(
                f"all read fallbacks failed for {path}: "
                f"open EDEADLK ×3; cat {cat_stderr!r} ×3; "
                f"Drive API {type(e).__name__}: {e}"
            ) from e

    raise OSError(
        f"local read failed for {path} (no Drive fallback configured): "
        f"open EDEADLK ×3; cat {cat_stderr!r} ×3"
    )


def write_text(path: Path, content: str) -> None:
    """Atomic write: tmp file then rename, so a crash mid-write never leaves
    a partial analysis at the destination path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def list_pending_documents() -> list[Path]:
    """Return PDF/document files in the docs/ subfolder, sorted by name.

    Only files with supported extensions are returned. Subfolders (including
    _Processed/) are skipped — only the root of docs/ is scanned.
    Returns [] if the docs/ folder doesn't exist yet (not an error — the
    folder is optional and created on first drop).
    """
    docs_root = CONFIG.docs_path
    if not docs_root.exists():
        return []
    files = []
    for entry in docs_root.iterdir():
        if entry.is_dir():
            continue
        if entry.suffix.lower() in _DOCUMENT_EXTENSIONS:
            files.append(entry)
    return sorted(files)


def read_document(path: Path) -> str:
    """Extract text from a document file in docs/.

    Dispatches by extension:
      .pdf   — pypdf text layer extraction
      .docx  — python-docx paragraph extraction
      .md / .txt — plain read_text() (same EDEADLK-safe path as transcripts)

    Raises ImportError if a required package is missing, RuntimeError if the
    file can't be read or yields no text.
    """
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _read_pdf(path)
    if ext == ".docx":
        return _read_docx(path)
    if ext in (".md", ".txt"):
        return read_text(path)
    raise ValueError(f"unsupported document extension: {ext!r}")


def _read_pdf(path: Path) -> str:
    if not _PYPDF_AVAILABLE:
        raise ImportError("pypdf is not installed — run: pip install pypdf>=4.0.0")
    try:
        reader = pypdf.PdfReader(str(path))
    except Exception as e:
        raise RuntimeError(f"could not open PDF {path.name}: {e}") from e
    pages = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if text.strip():
            pages.append(text.strip())
    if not pages:
        raise RuntimeError(
            f"no extractable text in {path.name} — may be a scanned/image-only PDF"
        )
    raw = "\n\n".join(pages)
    # pypdf can yield lone surrogates from malformed PDF encodings; strip them
    # so the text can be passed to subprocess (text=True / UTF-8).
    return raw.encode("utf-8", errors="replace").decode("utf-8")


def _read_docx(path: Path) -> str:
    if not _DOCX_AVAILABLE:
        raise ImportError("python-docx is not installed — run: pip install python-docx>=1.0.0")
    try:
        doc = _docx.Document(str(path))
    except Exception as e:
        raise RuntimeError(f"could not open DOCX {path.name}: {e}") from e
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    if not paragraphs:
        raise RuntimeError(f"no extractable text in {path.name}")
    return "\n\n".join(paragraphs)


def read_program_reference() -> str:
    """Read the current [PROGRAM REFERENCE].md, or '' if it doesn't exist yet."""
    path = CONFIG.analyzed_path / _PROGRAM_REFERENCE_FILENAME
    return path.read_text(encoding="utf-8") if path.exists() else ""


def write_program_reference(content: str) -> None:
    """Atomically overwrite [PROGRAM REFERENCE].md in Analyzed/."""
    path = CONFIG.analyzed_path / _PROGRAM_REFERENCE_FILENAME
    write_text(path, content)
