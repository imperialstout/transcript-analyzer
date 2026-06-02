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

import difflib
import json
import re
import subprocess
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

_KEY_VALUE_LINE_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z ]{2,40})\s*:\s*(.+?)\s*$", re.MULTILINE
)


def _find_typo_hints(
    candidates: list[tuple[str, str]], missing: list[str]
) -> list[str]:
    # Catches `Workspace: BuildingX` when the validator wanted `Workstream:`.
    # Brad hit this once and lost two hours of cron cycles before noticing.
    keys = [k for k, _ in candidates]
    keys_lc = [k.lower() for k in keys]
    hints: list[str] = []
    for field in missing:
        match = difflib.get_close_matches(
            field.lower(), keys_lc, n=1, cutoff=0.6
        )
        if not match:
            continue
        idx = keys_lc.index(match[0])
        actual_key, actual_val = candidates[idx]
        if actual_key.lower() == field.lower():
            continue
        hints.append(
            f'did you mean "{field}"? found "{actual_key}: {actual_val}"'
        )
    return hints


def list_pending_notes() -> list[Path]:
    """Return `.txt`, `.gdoc`, and `.md` files at the root of notes_path.

    `.md` notes are Slack/Gemini summary *bodies* exported locally (e.g. from
    the work machine), as opposed to `.gdoc` shortcuts (body fetched via the
    Drive API) and `.txt` notes (YAML frontmatter). All three are dispatched
    by suffix in `process_note`.
    """
    root = CONFIG.notes_path
    if not root.exists():
        return []
    return sorted(
        p for p in root.iterdir()
        if p.is_file() and p.suffix in (".txt", ".gdoc", ".md")
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


def _read_gdoc_id_via_xattr(path: Path) -> str | None:
    """Read doc_id from Drive's extended attribute.

    Google Drive stores the document id in `com.google.drivefs.item-id#S` on
    every `.gdoc` file. This is read via the `getxattr(2)` syscall, which
    uses a different code path than file-content reads — it doesn't trigger
    the File Provider materialization that causes EDEADLK in launchd-spawned
    processes for freshly-synced files.
    """
    try:
        result = subprocess.run(
            ["/usr/bin/xattr", "-p", "com.google.drivefs.item-id#S", str(path)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError) as e:
        print(
            f"  [notes] {path.name}: xattr fallback failed — "
            f"{type(e).__name__}: {e}",
            file=sys.stderr,
        )
        return None
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        print(
            f"  [notes] {path.name}: xattr fallback returned "
            f"{result.returncode} ({stderr or 'no stderr'})",
            file=sys.stderr,
        )
        return None
    doc_id = result.stdout.strip()
    return doc_id or None


def _resolve_doc_id_via_drive(path: Path, drive_service) -> str | None:
    """Look up a Drive doc by its local filename.

    Used when the local `.gdoc` body is unreadable due to Drive's File
    Provider returning EDEADLK to launchd-spawned processes for newly-synced
    files. The filename of the local shortcut matches the Doc title in
    Drive (Drive sync names them after the title), so a name search can
    recover the doc_id without ever touching the local file body.
    """
    title = path.stem.replace("'", r"\'")
    try:
        results = drive_service.files().list(
            q=f"name = '{title}' and mimeType = 'application/vnd.google-apps.document' and trashed = false",
            spaces="drive",
            fields="files(id, name, modifiedTime)",
            pageSize=10,
        ).execute()
    except Exception as e:
        print(
            f"  [notes] {path.name}: Drive name-search failed — "
            f"{type(e).__name__}: {e}",
            file=sys.stderr,
        )
        return None
    files = results.get("files", [])
    if not files:
        print(
            f"  [notes] {path.name}: no Drive doc matches name {path.stem!r}",
            file=sys.stderr,
        )
        return None
    if len(files) > 1:
        # Multiple docs with this exact title — pick the most recent so we
        # at least make progress instead of stalling forever.
        files.sort(key=lambda f: f.get("modifiedTime", ""), reverse=True)
        print(
            f"  [notes] {path.name}: {len(files)} Drive docs match name; "
            f"picking most recent ({files[0].get('modifiedTime')})",
            file=sys.stderr,
        )
    return files[0]["id"]


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


# Gemini meeting notes write "Jun 01, 2026"; Daily Slack summaries write
# "June 1, 2026" (full month, unpadded day). strptime's %d already accepts
# unpadded days, so the only axis that varies is abbreviated vs. full month.
_HEADER_DATE_FORMATS = ("%b %d, %Y", "%B %d, %Y")


def _parse_header_date(line: str) -> date | None:
    for fmt in _HEADER_DATE_FORMATS:
        try:
            return datetime.strptime(line, fmt).date()
        except ValueError:
            continue
    return None


# Some summary bodies have no date on line 1 (the first line is the title
# instead) — but the date is always in the filename, e.g.
# "Daily Slack Summary – June 1, 2026" or the range "Daily Status Summary –
# May 18–19, 2026". Match `Month D[–D], YYYY` anywhere in the stem.
_FILENAME_DATE_RE = re.compile(
    r"(?P<month>[A-Za-z]+)\s+(?P<d1>\d{1,2})"
    r"(?:\s*[–—-]\s*(?P<d2>\d{1,2}))?,\s*(?P<year>\d{4})"
)


def _date_from_filename(stem: str) -> date | None:
    m = _FILENAME_DATE_RE.search(stem)
    if not m:
        return None
    # For a day range ("May 18–19, 2026") file under the later day — that's
    # the most recent activity the summary covers.
    day = m.group("d2") or m.group("d1")
    return _parse_header_date(f"{m.group('month')} {day}, {m.group('year')}")


def parse_gemini_header(text: str) -> tuple[date | None, str | None]:
    """Pull meeting_date (line 1) and title (line 2) from a notes body.

    Line 1 is always the date (`Jun 01, 2026` for Gemini meeting notes,
    `June 1, 2026` for Daily Slack summaries). Line 2 is the meeting title
    for Gemini notes, but Slack summaries have no body title — line 2 is a
    `Workstream:` / `Meeting Type:` metadata line instead. In that case the
    title is returned as None and the caller falls back to the filename stem
    (where Slack summaries actually carry the title).
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None, None
    meeting_date = _parse_header_date(lines[0])
    if meeting_date is None:
        return None, None
    if len(lines) < 2:
        return meeting_date, None
    title = lines[1]
    if _WORKSTREAM_RE.match(title) or _MEETING_TYPE_RE.match(title):
        return meeting_date, None
    return meeting_date, title or None


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
        # Local body read failed (typically EDEADLK from Drive's File Provider
        # on launchd-spawned processes for freshly-synced files). Try the
        # xattr stored by Drive — different syscall path, often unaffected by
        # the deadlock-avoidance error. Drive name-search is the final
        # safety net (works unless Drive renamed the file locally, e.g.,
        # because the title contained a `/`).
        print(
            f"  [notes] {src.name}: local read failed; trying xattr fallback",
            file=sys.stderr,
        )
        doc_id = _read_gdoc_id_via_xattr(src)
        if not doc_id:
            print(
                f"  [notes] {src.name}: xattr failed; trying Drive name-search",
                file=sys.stderr,
            )
            doc_id = _resolve_doc_id_via_drive(src, drive_service)
        if not doc_id:
            return None

    try:
        body = _fetch_gdoc_text(doc_id, drive_service)
    except Exception as e:
        print(
            f"  [notes] {src.name}: Drive fetch failed — {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        return None

    return _file_summary_body(src, body)


# ---- .md path (locally-exported summary body) ---------------------------


def _process_md(src: Path, drive_service=None) -> dict | None:
    """File a Slack/Gemini summary body dropped in as a local `.md` file.

    Same body shape as a `.gdoc`, but the body is already on disk — read it
    with the EDEADLK-tolerant fallback chain (Drive's File Provider can still
    deadlock launchd reads of freshly-synced files) and file it identically.
    """
    try:
        body = fs.read_text(src, drive_service)
    except OSError as e:
        print(f"  [notes] {src.name}: read failed — {e}", file=sys.stderr)
        return None
    return _file_summary_body(src, body)


# ---- shared summary-body filing (.gdoc + .md) ---------------------------


def _file_summary_body(src: Path, body: str) -> dict | None:
    """File a parsed summary body into Analyzed/. Shared by `.gdoc` and `.md`.

    Date comes from the body header when present, else from the filename
    (some bodies lead with the title, not the date). Title comes from the
    body's line 2 when it's a real title, else the filename stem (Slack
    summaries carry the title in the filename). Workstream / Meeting Type
    are required body lines and fail closed if absent.
    """
    meeting_date, title = parse_gemini_header(body)
    if meeting_date is None:
        # No date on body line 1 — recover it from the filename.
        meeting_date = _date_from_filename(src.stem)
    if meeting_date is None:
        print(
            f"  [notes] {src.name}: could not determine meeting date "
            f"(no `Jun 01, 2026` / `June 1, 2026` on line 1 and no date in "
            f"the filename)",
            file=sys.stderr,
        )
        return None
    if not title:
        # No title in the body, so fall back to the filename stem
        # (e.g. "Daily Slack Summary – June 1, 2026").
        title = src.stem

    workstream, meeting_type = _extract_workstream_meeting_type(body)
    missing = [
        name
        for name, val in (("Workstream", workstream), ("Meeting Type", meeting_type))
        if not val
    ]
    if missing:
        parts = [
            f"  [notes] {src.name}: missing required body line(s): "
            f"{', '.join(missing)} (add `Workstream: X` / `Meeting Type: Y` "
            f"anywhere in the doc)"
        ]
        candidates = [
            (m.group(1).strip(), m.group(2).strip())
            for m in _KEY_VALUE_LINE_RE.finditer(body)
        ]
        parts.extend(f"    {h}" for h in _find_typo_hints(candidates, missing))
        print("\n".join(parts), file=sys.stderr)
        return None

    # Pass the derived title through filing so the output filename matches
    # the title from the body/filename, not a Google Doc name that can drift
    # if Brad renames the doc later.
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
        text = fs.read_text(src)
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
        parts = [
            f"  [notes] {src.name}: missing required field(s): {', '.join(missing)}"
        ]
        candidates = [(k, str(v)) for k, v in metadata.items()]
        parts.extend(f"    {h}" for h in _find_typo_hints(candidates, missing))
        print("\n".join(parts), file=sys.stderr)
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
    if src.suffix == ".md":
        return _process_md(src, drive_service)
    return _process_txt(src)
