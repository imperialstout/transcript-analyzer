"""Notes intake — files Gemini-summarized meeting notes into Analyzed/.

Brad gets ~3-6 meetings/week as Gemini-generated summaries instead of full
Plaud transcripts. These come pre-summarized; running them through the LLM
analyzer would only re-compress already-lossy content. Instead we trust the
summary at face value and just file it with the canonical naming convention
so the weekly cross-transcript synthesis (in Claude.ai) treats it like any
other entry in Analyzed/.

Two input formats, both at the root of `notes_path`:

- `.gdoc` (primary). A Google Doc shortcut file written by Drive sync. The
  body lives server-side; we fetch it via the Drive API. This is the path
  Brad uses from his locked-down work machine: he creates a Doc in the
  Drive notes folder, pastes the Gemini summary, types two lines anywhere
  in the body — `Workstream: <name>` and `Meeting Type: <kind>`. The
  meeting date and title come from the Gemini header (line 1 = `MMM DD,
  YYYY`, line 2 = title).

- `.txt` (fallback). Hand-written notes with YAML frontmatter at top.
  Required frontmatter: `meeting_date`, `workstream`, `meeting_type`.
  (`participants` was previously required; it's been dropped because the
  Gemini body lists them and the cross-transcript synthesis reads the
  body, not the frontmatter.)

No LLM call. Pure file plumbing + a Drive `files.export` for `.gdoc`s.
"""

import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

import yaml

from . import filesystem as fs
from . import filing
from . import manifest
from .config import CONFIG

TXT_REQUIRED_FIELDS = ("meeting_date", "workstream", "meeting_type")

_WORKSTREAM_RE = re.compile(
    r"^\s*Workstream\s*:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE
)
_MEETING_TYPE_RE = re.compile(
    r"^\s*Meeting\s*Type\s*:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE
)


def list_pending_notes() -> list[Path]:
    """Return `.txt` and `.gdoc` files at the root of notes_path."""
    root = CONFIG.notes_path
    if not root.exists():
        return []
    return sorted(
        p for p in root.iterdir()
        if p.is_file() and p.suffix in (".txt", ".gdoc")
    )


# ---- .gdoc path ---------------------------------------------------------


def _read_gdoc_id(path: Path) -> str | None:
    """A `.gdoc` is a small JSON shortcut. Pull the Drive document id.

    Logs the specific failure cause so diagnostics survive across the
    Drive-sync race conditions that are common on first launchd run after
    a doc lands. Retries the read a few times with backoff to absorb
    transient EDEADLK / sync-cache contention on the Google Drive FUSE
    filesystem.
    """
    raw: str | None = None
    last_err: OSError | None = None
    for attempt in range(3):
        try:
            raw = path.read_text(encoding="utf-8")
            break
        except OSError as e:
            last_err = e
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
    if raw is None:
        print(
            f"  [notes] {path.name}: read failed after 3 attempts — "
            f"{type(last_err).__name__}: {last_err}",
            file=sys.stderr,
        )
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        preview = raw[:120].replace("\n", "\\n")
        print(
            f"  [notes] {path.name}: .gdoc is not valid JSON ({e}); "
            f"first 120 chars: {preview!r}",
            file=sys.stderr,
        )
        return None
    if not isinstance(data, dict):
        print(
            f"  [notes] {path.name}: .gdoc JSON is not a dict "
            f"(got {type(data).__name__})",
            file=sys.stderr,
        )
        return None
    doc_id = data.get("doc_id")
    if doc_id:
        return doc_id
    rid = data.get("resource_id", "")
    if isinstance(rid, str) and rid.startswith("document:"):
        return rid.removeprefix("document:")
    keys = sorted(data.keys())
    print(
        f"  [notes] {path.name}: no doc_id in .gdoc JSON; keys present: {keys}",
        file=sys.stderr,
    )
    return None


def _fetch_gdoc_text(doc_id: str, drive_service) -> str:
    """Export a Google Doc as plain text via Drive `files.export`.

    Google's text export prepends a UTF-8 BOM (U+FEFF) to the body — strip it
    so downstream parsing of line 1 (`MMM DD, YYYY`) works.
    """
    response = drive_service.files().export(
        fileId=doc_id, mimeType="text/plain"
    ).execute()
    text = response.decode("utf-8") if isinstance(response, bytes) else str(response)
    return text.lstrip("﻿")


def parse_gemini_header(text: str) -> tuple[date | None, str | None]:
    """Pull meeting_date (line 1, `MMM DD, YYYY`) and title (line 2)."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None, None
    try:
        meeting_date = datetime.strptime(lines[0], "%b %d, %Y").date()
    except ValueError:
        return None, None
    return meeting_date, lines[1] or None


def _extract_workstream_meeting_type(
    text: str,
) -> tuple[str | None, str | None]:
    ws = _WORKSTREAM_RE.search(text)
    mt = _MEETING_TYPE_RE.search(text)
    return (
        ws.group(1).strip() if ws else None,
        mt.group(1).strip() if mt else None,
    )


def _process_gdoc(src: Path, drive_service) -> dict | None:
    if drive_service is None:
        print(
            f"  [notes] {src.name}: skipping — Drive service unavailable",
            file=sys.stderr,
        )
        return None

    doc_id = _read_gdoc_id(src)
    if not doc_id:
        # _read_gdoc_id already logged the specific cause.
        return None

    try:
        body = _fetch_gdoc_text(doc_id, drive_service)
    except Exception as e:
        print(
            f"  [notes] {src.name}: Drive fetch failed — {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        return None

    meeting_date, title = parse_gemini_header(body)
    if meeting_date is None or not title:
        print(
            f"  [notes] {src.name}: could not parse Gemini header "
            f"(expected `MMM DD, YYYY` on line 1, title on line 2)",
            file=sys.stderr,
        )
        return None

    workstream, meeting_type = _extract_workstream_meeting_type(body)
    missing = [
        name
        for name, val in (("Workstream", workstream), ("Meeting Type", meeting_type))
        if not val
    ]
    if missing:
        print(
            f"  [notes] {src.name}: missing required body line(s): "
            f"{', '.join(missing)} (add `Workstream: X` / `Meeting Type: Y` "
            f"anywhere in the doc)",
            file=sys.stderr,
        )
        return None

    # Pass the Gemini-derived title through filing so the output filename
    # matches the title from the doc body, not the user-set Google Doc name
    # (which can drift if Brad renames the doc later).
    output_filename, _ = filing.build_output_filename(
        f"{title}.txt", meeting_date_override=meeting_date
    )
    output_path = CONFIG.analyzed_path / output_filename

    output_text = (
        "---\n"
        f"meeting_date: {meeting_date.isoformat()}\n"
        f"title: {title}\n"
        f"workstream: {workstream}\n"
        f"meeting_type: {meeting_type}\n"
        "source: gemini-summary\n"
        "---\n\n"
        f"{body}"
    )

    try:
        fs.write_text(output_path, output_text)
        fs.move_to_processed(
            src, meeting_date, processed_root=CONFIG.notes_processed_path
        )
    except Exception as e:
        print(
            f"  [notes] {src.name}: filing failed — {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        return None

    entry = manifest.record_note(src.name, output_filename=output_filename)
    print(f"  [notes] {src.name} → {output_filename}")
    return entry


# ---- .txt fallback path -------------------------------------------------


def parse_frontmatter(text: str) -> tuple[dict, str] | None:
    """Split a `---`-bounded YAML block from the head of `text`.

    Returns (metadata, full_text_unchanged) on success, None if no
    well-formed frontmatter is present. The original text is preserved
    verbatim — we only need to read the metadata, not strip it from output.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip() != "---":
        return None
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return None
    yaml_block = "".join(lines[1:end_idx])
    try:
        metadata = yaml.safe_load(yaml_block)
    except yaml.YAMLError:
        return None
    if not isinstance(metadata, dict):
        return None
    return metadata, text


def validate_txt(metadata: dict) -> list[str]:
    """Return list of TXT-required fields that are missing or empty."""
    missing: list[str] = []
    for field in TXT_REQUIRED_FIELDS:
        value = metadata.get(field)
        if value is None or value == "" or value == []:
            missing.append(field)
    return missing


def _coerce_meeting_date(value) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def _process_txt(src: Path) -> dict | None:
    try:
        text = src.read_text(encoding="utf-8")
    except OSError as e:
        print(f"  [notes] {src.name}: read failed — {e}", file=sys.stderr)
        return None

    parsed = parse_frontmatter(text)
    if parsed is None:
        print(
            f"  [notes] {src.name}: missing or malformed YAML frontmatter "
            f"(expected `---`-bounded block at top)",
            file=sys.stderr,
        )
        return None
    metadata, full_text = parsed

    missing = validate_txt(metadata)
    if missing:
        print(
            f"  [notes] {src.name}: missing required field(s): {', '.join(missing)}",
            file=sys.stderr,
        )
        return None

    meeting_date = _coerce_meeting_date(metadata["meeting_date"])
    if meeting_date is None:
        print(
            f"  [notes] {src.name}: meeting_date {metadata['meeting_date']!r} "
            f"is not a valid YYYY-MM-DD date",
            file=sys.stderr,
        )
        return None

    output_filename, _ = filing.build_output_filename(
        src.name, meeting_date_override=meeting_date
    )
    output_path = CONFIG.analyzed_path / output_filename

    try:
        fs.write_text(output_path, full_text)
        fs.move_to_processed(
            src, meeting_date, processed_root=CONFIG.notes_processed_path
        )
    except Exception as e:
        print(
            f"  [notes] {src.name}: filing failed — {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        return None

    entry = manifest.record_note(src.name, output_filename=output_filename)
    print(f"  [notes] {src.name} → {output_filename}")
    return entry


# ---- dispatcher --------------------------------------------------------


def process_note(src: Path, drive_service=None) -> dict | None:
    """File a single note. Returns manifest entry on success, None on failure.

    On any failure the source is left in place so Brad can fix and retry.
    Errors are logged to stderr; we never raise.
    """
    if src.suffix == ".gdoc":
        return _process_gdoc(src, drive_service)
    return _process_txt(src)
