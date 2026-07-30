"""Plaud CLI intake — pull recent recordings into the transcript inbox.

Calls the `plaud` CLI (https://docs.plaud.ai/plaud-mcp-cli/cli) to list
recordings from the past N days and download any that haven't been processed
yet, dropping them as .txt files into Call Transcripts/ for the existing
transcript pipeline to pick up in the same run.

Dedup key in the manifest: `plaud:<id>` — stable across re-runs, regardless
of how the output filename gets assembled.

Enable via PLAUD_ENABLED=true in ~/.config/transcript-analyzer/.env.
"""

import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

from . import manifest as manifest_mod
from .config import CONFIG


def _run(args: list[str], plaud_bin: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [plaud_bin] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _parse_recent(output: str) -> list[dict]:
    """Parse `plaud recent` / `plaud today` stdout into a list of recording dicts.

    Plaud CLI outputs clean stdout. We try JSON first (in case a future version
    adds --json), then fall back to line-oriented text parsing of the current
    human-readable format, which looks like:

        1. Meeting Title (45:23)
           ID: abc123def456
           Date: 2026-07-29 10:04

    Returns a list of dicts with at least: id, title, recording_date (date).
    Any recording that can't be parsed is skipped with a warning.
    """
    output = output.strip()
    if not output:
        return []

    # JSON fast path
    if output.startswith("[") or output.startswith("{"):
        try:
            data = json.loads(output)
            if isinstance(data, dict):
                data = data.get("recordings", data.get("files", data.get("items", [])))
            return [_normalize_json_recording(r) for r in data if isinstance(r, dict)]
        except (json.JSONDecodeError, KeyError):
            pass

    return _parse_text_output(output)


def _normalize_json_recording(r: dict) -> dict:
    """Normalize a JSON recording object to our internal shape."""
    rec_id = str(r.get("id") or r.get("fileId") or r.get("file_id") or "")
    title = str(r.get("title") or r.get("name") or r.get("fileName") or "Recording")
    raw_date = r.get("date") or r.get("created_at") or r.get("start_time") or r.get("createdAt") or ""
    recording_date = _parse_date(str(raw_date)) or date.today()
    return {"id": rec_id, "title": title, "recording_date": recording_date}


# Table row format (actual `plaud recent` output):
#   ece0d8da...  07-29 Some Title  2026-07-29  24m37s
# Fields are separated by 2+ spaces. ID is a 32-char hex string.
_TABLE_ROW_RE = re.compile(
    r"^\s*([0-9a-f]{32})\s{2,}(.+?)\s{2,}(\d{4}-\d{2}-\d{2})\s{2,}(\S+)"
)

# Minimum recording length to attempt transcript download (accidental taps, etc.)
_MIN_DURATION_SECONDS = 30


def _parse_duration_seconds(dur: str) -> int:
    """Parse a duration string like '1h23m', '24m37s', '5s' into total seconds."""
    total = 0
    for m in re.finditer(r"(\d+)([hms])", dur):
        val, unit = int(m.group(1)), m.group(2)
        total += val * {"h": 3600, "m": 60, "s": 1}[unit]
    return total


def _parse_text_output(output: str) -> list[dict]:
    """Parse the human-readable table output of `plaud recent`.

    Actual format (space-separated columns, 2+ spaces between fields):
        <32-char-hex-id>  <title>  <YYYY-MM-DD>  <duration>
    """
    recordings = []
    for line in output.splitlines():
        m = _TABLE_ROW_RE.match(line)
        if not m:
            continue
        rec_id, title, raw_date, duration_str = m.group(1), m.group(2).strip(), m.group(3), m.group(4)
        recording_date = _parse_date(raw_date) or date.today()
        duration_secs = _parse_duration_seconds(duration_str)
        recordings.append({"id": rec_id, "title": title, "recording_date": recording_date, "duration_secs": duration_secs})

    if not recordings:
        print(
            "[plaud] WARNING: could not parse `plaud recent` output — "
            "no recordings extracted. Raw output:\n" + output[:800],
            file=sys.stderr,
        )

    return recordings


def _parse_date(s: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            from datetime import datetime
            return datetime.strptime(s[:len(fmt) + 2].strip(), fmt).date()
        except ValueError:
            continue
    return None


def _safe_title(title: str) -> str:
    """Sanitize a Plaud recording title for use in a filename."""
    # Strip characters illegal on macOS/Windows filesystems
    title = re.sub(r'[/\\:*?"<>|]', "_", title)
    # Collapse runs of whitespace/underscores
    title = re.sub(r"[\s_]+", " ", title).strip(" _-")
    return title or "Recording"


def _inbox_filename(recording: dict) -> str:
    """Build the .txt filename that gets dropped into Call Transcripts/.

    Format matches what Plaud/Zap historically produces so filing.py's
    LEADING_PREFIX stripping and date extraction work without modification:
        2026-07-29 - <Title> - 2026-07-29.txt
    The plaud:<id> manifest key is the real dedup handle — the filename just
    needs to be unique and parseable.
    """
    d = recording["recording_date"].isoformat()
    title = _safe_title(recording["title"])
    # Keep title under 100 chars so the full filename stays well under 255
    if len(title) > 100:
        title = title[:100].rstrip(" _-")
    return f"{d} - {title} - {d}.txt"


def sync(days: int = 1, plaud_bin: str = "plaud") -> int:
    """Pull unprocessed recordings from the past `days` days into the inbox.

    Returns the number of transcripts downloaded (0 if nothing new or on error).
    Errors are logged to stderr; this function never raises — a Plaud failure
    must not abort the rest of the analysis pipeline.
    """
    import shutil
    if shutil.which(plaud_bin) is None:
        print(
            f"[plaud] WARNING: `{plaud_bin}` not found on PATH — skipping Plaud sync. "
            f"Install with: npm install -g @plaud-ai/cli",
            file=sys.stderr,
        )
        return 0

    try:
        result = _run(["recent", "--days", str(days)], plaud_bin, timeout=30)
    except subprocess.TimeoutExpired:
        print("[plaud] WARNING: `plaud recent` timed out — skipping sync.", file=sys.stderr)
        return 0
    except Exception as e:
        print(f"[plaud] WARNING: could not run `plaud recent` — {e}", file=sys.stderr)
        return 0

    if result.returncode == 2:
        print(
            "[plaud] Not authenticated. Run `plaud login` once to authorize, "
            "then re-run the analyzer.",
            file=sys.stderr,
        )
        return 0
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        print(
            f"[plaud] WARNING: `plaud recent` exited {result.returncode}"
            + (f" — {stderr}" if stderr else ""),
            file=sys.stderr,
        )
        return 0

    recordings = _parse_recent(result.stdout)
    if not recordings:
        print(f"[plaud] No recordings found in the past {days} day(s).")
        return 0

    existing = manifest_mod.load()
    inbox = CONFIG.call_transcripts_path
    inbox.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    for rec in recordings:
        rec_id = rec.get("id", "")
        if not rec_id:
            print(f"[plaud] Skipping recording with no ID: {rec.get('title')}", file=sys.stderr)
            continue

        manifest_key = f"plaud:{rec_id}"
        if manifest_mod.is_recorded(manifest_key, existing):
            continue

        duration_secs = rec.get("duration_secs", 0)
        if duration_secs < _MIN_DURATION_SECONDS:
            print(
                f"[plaud] Skipping {rec_id} ({rec['title']!r}) — "
                f"too short ({duration_secs}s < {_MIN_DURATION_SECONDS}s)."
            )
            manifest_mod.record_plaud_sync(manifest_key, source_filename="")
            existing = manifest_mod.load()
            continue

        filename = _inbox_filename(rec)
        dest = inbox / filename

        # Don't overwrite a file already sitting in the inbox (e.g. a previous
        # partial download that wasn't yet analyzed).
        if dest.exists():
            print(f"[plaud] {filename} already in inbox — skipping download.")
            continue

        print(f"[plaud] Downloading: {rec['title']} ({rec_id})")
        try:
            dl = _run(["transcript", rec_id, "-o", str(dest)], plaud_bin, timeout=120)
        except subprocess.TimeoutExpired:
            print(f"[plaud] WARNING: download timed out for {rec_id}", file=sys.stderr)
            dest.unlink(missing_ok=True)
            continue
        except Exception as e:
            print(f"[plaud] WARNING: download failed for {rec_id} — {e}", file=sys.stderr)
            dest.unlink(missing_ok=True)
            continue

        if dl.returncode != 0:
            stderr = (dl.stderr or "").strip()
            print(
                f"[plaud] WARNING: transcript download failed for {rec_id} "
                + (f"— {stderr}" if stderr else f"(exit {dl.returncode})"),
                file=sys.stderr,
            )
            dest.unlink(missing_ok=True)
            continue

        if not dest.exists() or dest.stat().st_size == 0:
            print(
                f"[plaud] Skipping {rec_id} ({rec['title']!r}) — "
                f"no transcript available (exit 0, empty output)."
            )
            dest.unlink(missing_ok=True)
            # Mark processed so we don't retry on every run.
            manifest_mod.record_plaud_sync(manifest_key, source_filename="")
            existing = manifest_mod.load()
            continue

        # Record in manifest so re-runs skip this recording even after the
        # transcript file moves to _Processed/. The transcript pipeline will
        # record its own entry keyed by filename once analysis completes.
        manifest_mod.record_plaud_sync(manifest_key, source_filename=filename)
        print(f"[plaud] → {filename}")
        downloaded += 1

    if downloaded:
        print(f"[plaud] Downloaded {downloaded} new transcript(s) into inbox.")
    else:
        print(f"[plaud] All {len(recordings)} recording(s) already processed.")

    return downloaded
