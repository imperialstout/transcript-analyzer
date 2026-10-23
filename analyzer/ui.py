"""Minimal web dashboard for transcript-analyzer.

Usage:
    python -m analyzer ui              # default port 7070
    python -m analyzer ui --port 7071

Routes:
    GET  /          → status page (analyzed files + costs)
    POST /synthesize  → run daily or weekly synthesis
    GET  /settings   → settings form
    POST /settings   → save settings to ~/.config/transcript-analyzer/.env
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

_ENV_PATH = Path("~/.config/transcript-analyzer/.env").expanduser()
_LOG_PATH = Path("~/Library/Logs/transcript-analyzer.log").expanduser()
_PROGRAM_OVERRIDES_PATH = Path("~/.cache/transcript-analyzer/program-overrides.json").expanduser()


def _load_program_overrides() -> dict:
    try:
        if _PROGRAM_OVERRIDES_PATH.exists():
            return json.loads(_PROGRAM_OVERRIDES_PATH.read_text())
    except Exception:
        pass
    return {}


def _save_program_override(output_filename: str, program_key: str) -> None:
    overrides = _load_program_overrides()
    if program_key:
        overrides[output_filename] = program_key
    else:
        overrides.pop(output_filename, None)
    _PROGRAM_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PROGRAM_OVERRIDES_PATH.write_text(json.dumps(overrides, indent=2))

# Env vars the settings page exposes.  Tuple of (key, label, kind, description).
# kind: "text" | "url" | "toggle"
_SETTINGS_FIELDS = [
    ("SHARED_FOLDER_URL", "Shared meeting files URL", "url",
     "Link to the shared Drive / SharePoint folder where you upload shareable docs."),
    ("ROLODEX_PATH", "People rolodex path", "text",
     "Absolute path to 04_people_rolodex.md (leave blank to use Drive default)."),
    ("VOCABULARY_PATH", "Term glossary path", "text",
     "Absolute path to 05_vocabulary.md (leave blank to use Drive default)."),
    ("SHAREABLE_PASS", "Enable shareable redaction pass", "toggle",
     "Generate a redacted [SHAREABLE] sibling for every analysis."),
    ("PLAUD_ENABLED", "Enable Plaud sync", "toggle",
     "Automatically pull recent Plaud recordings into the inbox on each Run Analysis. Requires `plaud login` once."),
    ("PLAUD_DAYS", "Plaud pull window (days)", "text",
     "How many days back to look for new Plaud recordings (default: 1). Increase for catch-up after time away."),
    ("PLAUD_BIN", "Plaud CLI path", "text",
     "Path to the plaud binary (default: plaud). Set if it's not on PATH, e.g. /usr/local/bin/plaud."),
    ("BACKEND", "Execution backend", "text",
     "claude-cli (default, uses your Code seat) or api (uses ANTHROPIC_API_KEY)."),
    ("MODEL_OVERRIDE", "Model override", "text",
     "Force a specific model for all analysis runs (e.g. claude-opus-4-7). Leave blank for per-category defaults."),
]

# Background job state (simple — only one synthesis can run at a time).
_job_lock = threading.Lock()
_job: dict | None = None  # {"mode": str, "output": str, "done": bool, "returncode": int|None, "new_count": int}

# argv for the /restart self-re-exec, set by main(). We can't trust the live
# sys.argv: by the time a route fires, __main__.py has already stripped the "ui"
# subcommand and sys.argv[0] points at __main__.py — re-execing that would launch
# an analysis run, not the UI. main() reconstructs the real `-m analyzer ui` form.
_REEXEC_ARGV: list[str] = []

# Ambient warning state — set by the most recent _render() call so the
# indicator survives across pages without re-querying on every request.
_last_render_warnings: list[str] = []


def _load_env_file() -> dict[str, str]:
    vals: dict[str, str] = {}
    if not _ENV_PATH.exists():
        return vals
    for raw in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            vals[k.strip()] = v.strip()
    return vals


def _save_env_key(key: str, value: str) -> None:
    """Write / update a single key in the env file, preserving comments."""
    _ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = _ENV_PATH.read_text(encoding="utf-8").splitlines(keepends=True) if _ENV_PATH.exists() else []
    new_line = f"{key}={value}\n"
    found = False
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped.startswith(f"{key}=") or (
            stripped.startswith("#") and stripped.lstrip("#").strip().startswith(f"{key}=")
        ):
            lines[i] = new_line
            found = True
            break
    if not found:
        lines.append(new_line)
    _ENV_PATH.write_text("".join(lines), encoding="utf-8")


def _run_job(cmd: list[str], mode: str) -> None:
    """Run a subprocess for a UI-triggered job, streaming output two ways.

    Updates the in-memory ``_job["output"]`` line-by-line so the dashboard's
    poller shows live progress, and tees the same lines into the rolling log
    (``transcript-analyzer.log``) in the same ``----- <iso> ----- … exit=N``
    frame ``bin/analyze.sh`` writes, so UI runs are forensically visible
    alongside scheduled runs. ``python -u`` keeps the child's stdout unbuffered
    so progress arrives as it happens rather than all at once on exit.
    """
    global _job
    from datetime import datetime as _dt
    label = _MODE_LABELS.get(mode, mode)

    # Snapshot manifest size before the run so we can report how many new files landed.
    _manifest_size_before = 0
    if mode == "analysis":
        try:
            from .manifest import load as _load_manifest
            _manifest_size_before = len(_load_manifest())
        except Exception:
            pass

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )
    with _job_lock:
        if _job is not None:
            _job["proc"] = proc
    buf: list[str] = []
    log = None
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        log = _LOG_PATH.open("a", encoding="utf-8")
        log.write(f"----- {_dt.now().isoformat(timespec='seconds')} ----- (UI: {label})\n")
        log.flush()
    except Exception:
        log = None  # logging is best-effort; never block the job on it
    for line in proc.stdout:
        buf.append(line)
        with _job_lock:
            _job["output"] = "".join(buf)
        if log is not None:
            try:
                log.write(line)
                log.flush()
            except Exception:
                log = None
    proc.wait()
    if log is not None:
        try:
            log.write(f"exit={proc.returncode}\n")
            log.close()
        except Exception:
            pass
    new_count = 0
    if mode == "analysis" and proc.returncode == 0 and _manifest_size_before > 0:
        try:
            from .manifest import load as _load_manifest
            new_count = max(0, len(_load_manifest()) - _manifest_size_before)
        except Exception:
            pass

    with _job_lock:
        _job["done"] = True
        _job["returncode"] = proc.returncode
        _job["new_count"] = new_count


_SYNTHESIS_TAGS = ("[DAILY PULSE]", "[SLACK DELTA]", "[WEEKLY SUMMARY]")

_MODE_LABELS = {
    "daily": "Daily Pulse synthesis",
    "weekly": "Weekly Delta synthesis",
    "career": "Career Trajectory review",
    "catch-up": "catch-up (one pulse per backlog day)",
    "timecard": "Weekly Timecard compilation",
    "program-pulse": "Program Pulse sweep",
    "analysis": "transcript analysis",
}


def _analyzed_files() -> list[dict]:
    from .config import CONFIG
    from .manifest import load as load_manifest
    from .program_pulse import classify_program
    manifest = load_manifest()
    overrides = _load_program_overrides()
    rows = []
    for src, entry in sorted(manifest.items(), key=lambda x: x[1].get("analyzed_at", ""), reverse=True):
        if src.startswith("plaud:"):
            continue
        output = entry.get("output_filename", "")
        # Compute program: override wins, else classify from the Analyzed/ file
        program = overrides.get(output, "")
        if not program and output:
            try:
                fpath = CONFIG.analyzed_path / output
                if not fpath.exists():
                    # rglob() treats [...] as a character class — walk manually
                    import os as _os
                    archive_root = CONFIG.analyzed_path / "_Archive"
                    for _dir, _, _files in _os.walk(archive_root):
                        if output in _files:
                            fpath = Path(_dir) / output
                            break
                if fpath.exists():
                    program = classify_program(fpath)
                    if program == "_admin":
                        program = ""
            except Exception:
                program = ""
        rows.append({
            "source": src,
            "output": output,
            "shareable": entry.get("shareable_filename", ""),
            "category": entry.get("category") or entry.get("prompt_key", ""),
            "model": entry.get("model", ""),
            "cost": entry.get("cost_usd", 0.0),
            "analyzed_at": entry.get("analyzed_at", ""),
            "duration": entry.get("duration_seconds", 0),
            "mode": entry.get("mode", "transcript"),
            "program": program,
            "program_overridden": output in overrides,
        })

    # Synthesis outputs are now recorded in the manifest (new runs). Fall back to
    # scanning Analyzed/ for older files not yet in the manifest.
    manifest_keys = set(manifest.keys())
    try:
        import os
        from datetime import datetime as _dt
        analyzed_path = CONFIG.analyzed_path
        for fname in sorted(os.listdir(analyzed_path), reverse=True):
            if not any(tag in fname for tag in _SYNTHESIS_TAGS):
                continue
            if fname in manifest_keys:
                continue  # already covered by the manifest loop above
            fpath = analyzed_path / fname
            if fpath.is_dir():
                continue
            mtime = fpath.stat().st_mtime
            analyzed_at = _dt.fromtimestamp(mtime).strftime("%Y-%m-%dT%H:%M:%S")
            if "[DAILY PULSE]" in fname:
                category = "pulse"
            elif "[SLACK DELTA]" in fname:
                category = "delta"
            else:
                category = "synthesis"
            rows.append({
                "source": fname,
                "output": fname,
                "shareable": "",
                "category": category,
                "model": "",
                "cost": 0.0,
                "analyzed_at": analyzed_at,
                "duration": 0,
                "mode": "synthesis",
            })
    except Exception:
        pass

    rows.sort(key=lambda r: r.get("analyzed_at", ""), reverse=True)
    return rows


def _total_cost(rows: list[dict]) -> float:
    return round(sum(r["cost"] for r in rows), 4)


def _last_analyzed_at(rows: list[dict]) -> tuple[str, str]:
    """Return (formatted timestamp, staleness) for the most recent analyzed_at.

    staleness is '' | 'warn' (>8h) | 'danger' (>24h).
    """
    from datetime import datetime, timezone
    timestamps = [r["analyzed_at"] for r in rows if r.get("analyzed_at")]
    if not timestamps:
        return "", ""
    ts = max(timestamps)
    try:
        dt = datetime.fromisoformat(ts)
        formatted = dt.strftime("%b %-d, %Y %H:%M")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        if age_hours > 24:
            staleness = "danger"
        elif age_hours > 8:
            staleness = "warn"
        else:
            staleness = ""
        return formatted, staleness
    except Exception:
        return ts[:16], ""


def _analyzed_path_url() -> str:
    from .config import CONFIG
    return CONFIG.analyzed_path.as_uri()


def _shared_folder_url() -> str:
    return os.environ.get("SHARED_FOLDER_URL", "") or _load_env_file().get("SHARED_FOLDER_URL", "")


_PAGE_TITLES = {
    "brief-state": "Brief State — Transcript Analyzer",
    "home": "Files — Transcript Analyzer",
    "prep": "Meeting Prep — Transcript Analyzer",
    "slack-paste": "Slack Paste — Transcript Analyzer",
    "run": "Run — Transcript Analyzer",
    "settings": "Settings — Transcript Analyzer",
}

_HTML = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{{ page_title }}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@salesforce-ux/design-system-2@2.0.0/dist/css/bundled/slds2.cosmos.css">
<script>
  (function() {
    var mode = localStorage.getItem('slds-ui-color-mode');
    // Default is now light; only apply dark if explicitly stored
    if (mode === 'dark') document.documentElement.classList.add('slds-color-scheme_dark');
  })();
</script>
<style>
  /* ── App-level reset on top of SLDS ── */
  * { box-sizing: border-box; }
  body { margin: 0; }

  /* ── Scrollbar ── */
  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-track { background: var(--slds-g-color-surface-1, #f3f3f3); }
  ::-webkit-scrollbar-thumb { background: var(--slds-g-color-border-2, #c9c7c5); border-radius: 3px; }
  ::-webkit-scrollbar-thumb:hover { background: var(--slds-g-color-border-3, #aeacab); }

  /* ── Global chrome ── */
  header.ta-header {
    background: var(--slds-g-color-surface-1, #fff);
    border-bottom: 4px solid var(--slds-g-color-brand-2, #0176d3);
    padding: 10px 24px; display: flex; align-items: center; gap: 16px;
    position: sticky; top: 0; z-index: 10;
  }
  header.ta-header h1 {
    font-size: var(--slds-g-font-scale-2, 0.875rem);
    font-weight: 700;
    color: var(--slds-g-color-on-surface-2, #181818);
    white-space: nowrap;
  }
  nav.ta-nav { display: flex; gap: 2px; }
  nav.ta-nav a {
    color: var(--slds-g-color-on-surface-4, #706e6b);
    text-decoration: none;
    font-size: var(--slds-g-font-scale-2, 0.875rem);
    padding: 6px 10px;
    border-radius: var(--slds-g-radius-border-2, 0.25rem);
    transition: color .15s, background .15s;
  }
  nav.ta-nav a:hover, nav.ta-nav a.active {
    color: var(--slds-g-color-on-surface-2, #181818);
    background: var(--slds-g-color-surface-2, #f3f3f3);
  }
  nav.ta-nav a:focus-visible { outline: 2px solid var(--slds-g-color-brand-2, #0176d3); outline-offset: 2px; }

  .ta-content { padding: 24px; max-width: 1400px; }

  /* ── Buttons (ta-* prefix, layered on slds-button) ── */
  .ta-btn {
    display: inline-flex; align-items: center; gap: 6px;
    border-radius: var(--slds-g-radius-border-2, 0.25rem);
    padding: 8px 16px;
    font-size: var(--slds-g-font-scale-2, 0.875rem);
    font-weight: 600; cursor: pointer; text-decoration: none;
    border: none; transition: opacity .15s; white-space: nowrap;
    background: var(--slds-g-color-brand-2, #0176d3); color: #fff;
  }
  .ta-btn:hover { opacity: .85; }
  .ta-btn-secondary {
    background: var(--slds-g-color-surface-1, #fff);
    color: var(--slds-g-color-on-surface-2, #181818);
    border: 1px solid var(--slds-g-color-border-2, #c9c7c5);
  }
  .ta-btn-secondary:hover { background: var(--slds-g-color-surface-2, #f3f3f3); opacity: 1; }
  .ta-btn-destructive {
    background: var(--slds-g-color-error-2, #c23934);
    color: #fff;
    border: none;
  }
  .ta-btn-destructive:hover { background: #a61c17; opacity: 1; }
  .ta-btn-sm { padding: 3px 8px; font-size: 11px; border-radius: var(--slds-g-radius-border-1, 0.125rem); }
  .ta-btn:focus-visible, .ta-btn-secondary:focus-visible, .ta-btn-destructive:focus-visible {
    outline: 2px solid var(--slds-g-color-brand-2, #0176d3); outline-offset: 2px;
  }

  /* Keep native <button> elements styled consistently */
  button:not([class]) {
    display: inline-flex; align-items: center; gap: 6px;
    border-radius: var(--slds-g-radius-border-2, 0.25rem);
    padding: 8px 16px; font-size: var(--slds-g-font-scale-2, 0.875rem);
    font-weight: 600; cursor: pointer; border: none; white-space: nowrap;
    background: var(--slds-g-color-brand-2, #0176d3); color: #fff; transition: opacity .15s;
  }
  button:not([class]):hover { opacity: .85; }

  /* ── Date input ── */
  input[type=date] {
    background: var(--slds-g-color-surface-1, #fff);
    border: 1px solid var(--slds-g-color-border-2, #c9c7c5);
    border-radius: var(--slds-g-radius-border-1, 0.125rem);
    color: var(--slds-g-color-on-surface-2, #181818);
    font-size: 11px; padding: 3px 6px;
  }
  input[type=date]:focus { outline: none; border-color: var(--slds-g-color-brand-2, #0176d3); }

  /* ── Flash messages ── */
  .ta-flash {
    padding: 10px 16px; border-radius: var(--slds-g-radius-border-2, 0.25rem);
    margin-bottom: 16px; font-size: var(--slds-g-font-scale-2, 0.875rem);
  }
  .ta-flash-ok  { background: #eaf5ec; color: var(--slds-g-color-success-2, #2e844a); border: 1px solid #c2e0c6; }
  .ta-flash-err { background: #fef1f1; color: var(--slds-g-color-error-2, #c23934); border: 1px solid #fac9c9; }

  /* ── Stats strip ── */
  .ta-stats { display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }
  .ta-stat {
    background: var(--slds-g-color-surface-1, #fff);
    border: 1px solid var(--slds-g-color-border-1, #e5e5e5);
    border-radius: var(--slds-g-radius-border-3, 0.5rem);
    padding: 14px 20px; min-width: 140px;
  }
  .ta-stat .val { font-size: 22px; font-weight: 700; color: var(--slds-g-color-on-surface-2, #181818); }
  .ta-stat .lbl {
    font-size: 11px; color: var(--slds-g-color-on-surface-4, #706e6b);
    text-transform: uppercase; letter-spacing: .05em; margin-top: 2px;
  }

  /* ── Card ── */
  .ta-card {
    background: var(--slds-g-color-surface-1, #fff);
    border: 1px solid var(--slds-g-color-border-1, #e5e5e5);
    border-radius: var(--slds-g-radius-border-3, 0.5rem);
    padding: 20px; margin-bottom: 20px;
  }
  .ta-card h2 {
    font-size: 11px; font-weight: 700; text-transform: uppercase;
    letter-spacing: .06em; color: var(--slds-g-color-on-surface-4, #706e6b); margin-bottom: 14px;
  }
  .ta-card-inbox {
    background: #fffdf0;
    border-color: var(--slds-g-color-warning-2, #dd7a01);
  }
  .ta-card-inbox h2 { color: var(--slds-g-color-warning-2, #dd7a01); }

  /* ── Action tiers ── */
  .ta-action-tier { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .ta-action-tier + .ta-action-tier {
    margin-top: 10px; padding-top: 10px;
    border-top: 1px solid var(--slds-g-color-border-1, #e5e5e5);
  }
  .ta-action-tier-label {
    font-size: 11px; font-weight: 700; text-transform: uppercase;
    letter-spacing: .07em; color: var(--slds-g-color-on-surface-4, #706e6b); margin-right: 4px;
  }
  .ta-action-group { display: flex; align-items: center; gap: 6px; }
  .ta-action-date-hint { font-size: 11px; color: var(--slds-g-color-on-surface-4, #706e6b); }

  /* ── Table ── */
  table { width: 100%; border-collapse: collapse; font-size: var(--slds-g-font-scale-2, 0.875rem); }
  th {
    text-align: left; color: var(--slds-g-color-on-surface-4, #706e6b);
    font-size: 11px; text-transform: uppercase; letter-spacing: .05em;
    padding: 6px 10px; border-bottom: 1px solid var(--slds-g-color-border-2, #c9c7c5);
  }
  td { padding: 7px 10px; border-bottom: 1px solid var(--slds-g-color-border-1, #e5e5e5); vertical-align: top; }
  .td-source { max-width: 380px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  tr:last-child td { border-bottom: none; }

  /* ── Badges (ta-badge-* namespace to avoid overriding .slds-badge) ── */
  .ta-badge {
    display: inline-block; padding: 2px 7px;
    border-radius: var(--slds-g-radius-border-2, 0.25rem);
    font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .04em;
  }
  .ta-badge-exec      { background: #fef1f1; color: var(--slds-g-color-error-2, #c23934); }
  .ta-badge-solution  { background: #eaf1fb; color: var(--slds-g-color-brand-2, #0176d3); }
  .ta-badge-daily     { background: #eaf5ec; color: var(--slds-g-color-success-2, #2e844a); }
  .ta-badge-standup   { background: #fff3e0; color: var(--slds-g-color-warning-2, #dd7a01); }
  .ta-badge-notes     { background: #f5eafb; color: #6b3fa0; }
  .ta-badge-pulse     { background: #eaf5ec; color: var(--slds-g-color-success-2, #2e844a); }
  .ta-badge-delta     { background: #eaf5f5; color: #0d7377; }
  .ta-badge-synthesis { background: #f5eafb; color: #6b3fa0; }
  .ta-badge-act       { background: #fef1f1; color: var(--slds-g-color-error-2, #c23934); }
  .ta-badge-watch     { background: #fff3e0; color: var(--slds-g-color-warning-2, #dd7a01); }
  .ta-badge-delegate  { background: #eaf1fb; color: var(--slds-g-color-brand-2, #0176d3); }
  .ta-badge-they-owe  { background: #f5eafb; color: #6b3fa0; }
  .ta-badge-insight   { background: #eaf5f5; color: #0d7377; }
  .ta-badge-pending   { background: #eaf5ec; color: var(--slds-g-color-success-2, #2e844a); }
  .ta-badge-complete  { background: #eaf5ec; color: var(--slds-g-color-success-2, #2e844a); }
  .ta-badge-deferred  { background: #fff8e1; color: #8a6000; }
  .ta-badge-expired   { background: #fef1f1; color: var(--slds-g-color-error-2, #c23934); }
  .ta-badge-dropped   { background: var(--slds-g-color-surface-2, #f3f3f3); color: var(--slds-g-color-on-surface-4, #706e6b); }
  .ta-badge-overdue   { background: #fef1f1; color: var(--slds-g-color-error-2, #c23934); border: 1px solid #fac9c9; font-weight: 700; }

  /* ── Job panel ── */
  #job-status {
    background: var(--slds-g-color-surface-2, #f3f3f3);
    border: 1px solid var(--slds-g-color-border-1, #e5e5e5);
    border-radius: var(--slds-g-radius-border-3, 0.5rem);
    padding: 16px; margin-top: 16px; display: none;
  }
  #job-status h3 {
    font-size: 11px; text-transform: uppercase; letter-spacing: .06em;
    color: var(--slds-g-color-on-surface-4, #706e6b); margin-bottom: 10px;
  }
  #job-output {
    font: 12px/1.6 "SF Mono", "Menlo", monospace;
    color: var(--slds-g-color-on-surface-2, #181818);
    white-space: pre-wrap; max-height: 320px; overflow-y: auto; overflow-wrap: break-word;
  }
  .ta-spinner {
    display: inline-block; width: 12px; height: 12px;
    border: 2px solid var(--slds-g-color-border-2, #c9c7c5);
    border-top-color: var(--slds-g-color-brand-2, #0176d3);
    border-radius: 50%; animation: ta-spin .7s linear infinite;
  }
  @keyframes ta-spin { to { transform: rotate(360deg); } }

  /* ── Form fields ── */
  .ta-field { margin-bottom: 18px; }
  .ta-field label {
    display: block; font-size: 12px; font-weight: 600;
    color: var(--slds-g-color-on-surface-4, #706e6b);
    text-transform: uppercase; letter-spacing: .05em; margin-bottom: 5px;
  }
  .ta-field input[type=text], .ta-field input[type=url] {
    background: var(--slds-g-color-surface-1, #fff);
    border: 1px solid var(--slds-g-color-border-2, #c9c7c5);
    border-radius: var(--slds-g-radius-border-2, 0.25rem);
    color: var(--slds-g-color-on-surface-2, #181818);
    font-size: var(--slds-g-font-scale-2, 0.875rem); padding: 7px 10px; width: 100%;
  }
  .ta-field input[type=text]:focus, .ta-field input[type=url]:focus {
    outline: none; border-color: var(--slds-g-color-brand-2, #0176d3);
  }
  .ta-field .desc { font-size: 12px; color: var(--slds-g-color-on-surface-4, #706e6b); margin-top: 4px; }
  .ta-toggle-row { display: flex; align-items: center; gap: 10px; }
  .ta-toggle { position: relative; width: 36px; height: 20px; }
  .ta-toggle input { opacity: 0; width: 0; height: 0; position: absolute; }
  .ta-toggle input:focus-visible + .ta-slider {
    outline: 2px solid var(--slds-g-color-brand-2, #0176d3); outline-offset: 2px;
  }
  .ta-slider {
    position: absolute; cursor: pointer; inset: 0;
    background: var(--slds-g-color-border-2, #c9c7c5);
    border-radius: 20px; transition: .2s;
  }
  .ta-slider:before {
    content: ""; position: absolute; height: 14px; width: 14px;
    left: 3px; bottom: 3px; background: #fff; border-radius: 50%; transition: .2s;
  }
  input:checked + .ta-slider { background: var(--slds-g-color-brand-2, #0176d3); }
  input:checked + .ta-slider:before { transform: translateX(16px); }

  /* ── Quick links ── */
  .ta-quick-link {
    display: inline-flex; align-items: center; gap: 6px;
    color: var(--slds-g-color-brand-2, #0176d3);
    text-decoration: none; font-size: 12px; padding: 5px 0;
  }
  .ta-quick-link:hover { text-decoration: underline; }
  .ta-quick-link:focus-visible { outline: 2px solid var(--slds-g-color-brand-2, #0176d3); outline-offset: 2px; }
  hr.ta-sep { border: none; border-top: 1px solid var(--slds-g-color-border-1, #e5e5e5); margin: 24px 0; }
  .ta-section-title {
    font-size: 11px; font-weight: 700; text-transform: uppercase;
    letter-spacing: .07em; color: var(--slds-g-color-on-surface-4, #706e6b); margin-bottom: 14px;
  }
  .ta-muted { color: var(--slds-g-color-on-surface-4, #706e6b); }

  /* ── Slack paste ── */
  textarea.ta-paste-area {
    width: 100%; height: 360px;
    background: var(--slds-g-color-surface-1, #fff);
    border: 1px solid var(--slds-g-color-border-2, #c9c7c5);
    border-radius: var(--slds-g-radius-border-2, 0.25rem);
    color: var(--slds-g-color-on-surface-2, #181818);
    font: 13px/1.5 "SF Mono", "Menlo", monospace;
    padding: 10px; resize: vertical;
  }
  textarea.ta-paste-area:focus { outline: none; border-color: var(--slds-g-color-brand-2, #0176d3); }

  /* ── Inbox urgency headers ── */
  .ta-inbox-urgent-header {
    font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em;
    color: var(--slds-g-color-error-2, #c23934); padding: 6px 0 8px;
    border-bottom: 1px solid #fac9c9; margin-bottom: 4px;
  }
  .ta-inbox-normal-header {
    font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em;
    color: var(--slds-g-color-warning-2, #dd7a01); padding: 10px 0 8px;
    border-bottom: 1px solid #ffe0a0; margin-bottom: 4px;
  }
  .ta-bs-item-urgent {
    background: #fef7f7;
  }

  /* ── Brief State items ── */
  .ta-bs-item {
    display: flex; align-items: flex-start; gap: 12px; padding: 13px 0;
    border-top: 1px solid var(--slds-g-color-border-1, #e5e5e5);
  }
  .ta-bs-item:first-of-type { border-top: none; }
  .ta-bs-body { flex: 1; min-width: 0; }
  /* Type badge sits on its own line above the text, not inline */
  .ta-bs-type-row { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
  .ta-bs-text {
    font-size: var(--slds-g-font-scale-2, 0.875rem); line-height: 1.5;
    overflow-wrap: break-word; word-break: break-word;
    /* Clamp to 3 lines; full text on hover via title attr */
    display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden;
  }
  .ta-bs-text.expanded { display: block; -webkit-line-clamp: unset; }
  .ta-bs-text-toggle {
    font-size: 11px; color: var(--slds-g-color-brand-2, #0176d3);
    background: none; border: none; padding: 0; cursor: pointer; margin-top: 2px;
  }
  .ta-bs-meta { font-size: 11px; color: var(--slds-g-color-on-surface-4, #706e6b); margin-top: 4px; }
  .ta-bs-meta-source {
    max-width: 480px; overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; display: inline-block; vertical-align: bottom;
  }
  .ta-bs-notes {
    font-size: 12px; color: var(--slds-g-color-on-surface-4, #706e6b); font-style: italic; margin-top: 3px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 600px;
  }
  .ta-bs-actions { display: flex; gap: 4px; flex-shrink: 0; align-items: center; flex-wrap: nowrap; }
  .ta-bs-defer-sep {
    width: 1px; height: 16px;
    background: var(--slds-g-color-border-1, #e5e5e5); margin: 0 1px; flex-shrink: 0;
  }
  .ta-bs-empty { color: var(--slds-g-color-on-surface-4, #706e6b); font-size: var(--slds-g-font-scale-2, 0.875rem); padding: 12px 0; }
  .ta-bs-item-overdue {
    background: rgba(194,57,52,0.05); margin: 0 -20px; padding-left: 20px; padding-right: 20px;
  }
  .ta-bs-item-due-soon {
    background: rgba(221,122,1,0.05); margin: 0 -20px; padding-left: 20px; padding-right: 20px;
  }

  /* ── Program group headers ── */
  .ta-bs-program-header {
    font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .08em;
    color: var(--slds-g-color-on-surface-4, #706e6b); padding: 10px 0 4px; margin-top: 6px;
    border-top: 1px solid var(--slds-g-color-border-1, #e5e5e5);
  }
  .ta-bs-program-header:first-child { border-top: none; margin-top: 0; }
  .ta-bs-program-header-toggle { cursor: pointer; list-style: none; display: flex; align-items: center; gap: 6px; user-select: none; }
  .ta-bs-program-header-toggle::-webkit-details-marker { display: none; }
  details.ta-bs-group-section { margin-top: 6px; }
  details.ta-bs-group-section > summary {
    border-top: 1px solid var(--slds-g-color-border-1, #e5e5e5); padding-top: 10px; margin-top: 0;
  }
  details.ta-bs-group-section:first-child > summary { border-top: none; padding-top: 4px; }
  details.ta-bs-group-section[open] summary .ta-bs-section-chevron { transform: rotate(90deg); }
  details.ta-bs-group-section .ta-bs-section-chevron { font-size: 10px; transition: transform 0.15s; }
  details.ta-bs-program-group { margin-top: 6px; }
  details.ta-bs-program-group > summary { padding-bottom: 2px; }
  details.ta-bs-program-group:first-child > summary { border-top: none; margin-top: 0; }
  details.ta-bs-program-group:not([open]) .ta-bs-program-chevron { transform: rotate(-90deg); }
  .ta-bs-program-chevron { font-size: 10px; display: inline-block; transition: transform 0.15s; }
  details.ta-bs-section summary {
    cursor: pointer; list-style: none; display: flex; align-items: center; gap: 8px;
    padding: 10px 0; color: var(--slds-g-color-on-surface-4, #706e6b);
    font-size: var(--slds-g-font-scale-2, 0.875rem); user-select: none;
  }
  details.ta-bs-section summary::-webkit-details-marker { display: none; }
  details.ta-bs-section summary .ta-bs-section-chevron { font-size: 10px; transition: transform 0.15s; }
  details.ta-bs-section[open] summary .ta-bs-section-chevron { transform: rotate(90deg); }
  details.ta-bs-section summary:focus-visible { outline: 2px solid var(--slds-g-color-brand-2, #0176d3); outline-offset: 2px; }

  /* ── Drag-to-reorder (program order list in Settings) ── */
  .ta-drag-handle {
    cursor: grab; color: var(--slds-g-color-border-2, #c9c7c5);
    font-size: 14px; padding: 0 6px 0 0; flex-shrink: 0; line-height: 1; user-select: none;
  }
  .ta-drag-handle:active { cursor: grabbing; }
  .ta-inline-program-form { margin-top: 4px; }

  /* ── Nav badge ── */
  .ta-nav-badge {
    background: var(--slds-g-color-brand-2, #0176d3); color: #fff;
    border-radius: 10px; padding: 0 6px; font-size: 12px; font-weight: 700;
    line-height: 18px; display: inline-block; vertical-align: middle; margin-left: 4px;
  }

  /* ── Inline code token ── */
  .ta-code-inline {
    color: var(--slds-g-color-on-surface-2, #181818);
    font-family: "SF Mono", "Menlo", monospace; font-size: 11px;
  }

  /* ── Render-warnings chip ── */
  .ta-render-warn-chip {
    display: inline-flex; align-items: center; gap: 5px;
    background: #fff3e0; color: var(--slds-g-color-warning-2, #dd7a01);
    border: 1px solid #f5c26b;
    border-radius: var(--slds-g-radius-border-2, 0.25rem); padding: 3px 10px;
    font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em;
    white-space: nowrap; cursor: pointer; position: relative;
  }
  .ta-render-warn-chip:focus { outline: 2px solid var(--slds-g-color-warning-2, #dd7a01); outline-offset: 2px; }
  .ta-render-warn-popover {
    display: none; position: absolute; top: calc(100% + 6px); right: 0;
    background: var(--slds-g-color-surface-1, #fff);
    border: 1px solid var(--slds-g-color-border-1, #e5e5e5);
    border-radius: var(--slds-g-radius-border-3, 0.5rem); padding: 10px 14px;
    min-width: 220px; z-index: 100;
    box-shadow: var(--slds-g-shadow-2, 0 4px 16px rgba(0,0,0,.12));
  }
  .ta-render-warn-chip.open .ta-render-warn-popover { display: block; }
  .ta-render-warn-popover ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
  .ta-render-warn-popover li {
    font-size: 12px; color: var(--slds-g-color-on-surface-2, #181818);
    font-weight: 400; text-transform: none; letter-spacing: 0; line-height: 1.4;
  }

  /* ── Brief state overdue banner ── */
  .ta-bs-brief-banner {
    background: #fef1f1;
    border: 1px solid #fac9c9;
    border-left: 4px solid var(--slds-g-color-error-2, #c23934);
    border-radius: var(--slds-g-radius-border-3, 0.5rem);
    padding: 14px 20px; margin-bottom: 20px; display: flex; align-items: flex-start; gap: 12px;
  }
  .ta-bs-brief-banner-icon { flex-shrink: 0; padding-top: 2px; line-height: 0; }
  .ta-bs-brief-banner-icon svg { display: block; }
  .ta-bs-brief-banner-body { flex: 1; }
  .ta-bs-brief-banner-title {
    font-size: var(--slds-g-font-scale-2, 0.875rem); font-weight: 700;
    color: var(--slds-g-color-error-2, #c23934);
    margin-bottom: 6px;
  }
  .ta-bs-brief-banner-items { list-style: none; display: flex; flex-direction: column; gap: 4px; }
  .ta-bs-brief-banner-items li {
    font-size: var(--slds-g-font-scale-2, 0.875rem); line-height: 1.5;
    overflow-wrap: break-word; word-break: break-word;
  }

  /* ── Defer toggle ── */
  .ta-defer-wrap {
    display: none; flex-wrap: wrap; gap: 6px; align-items: center;
    margin-top: 6px; padding-top: 6px;
    border-top: 1px dashed var(--slds-g-color-border-1, #e5e5e5);
  }
  .ta-defer-wrap.open { display: flex; }
  .ta-btn-defer-toggle {
    background: none; border: 1px solid var(--slds-g-color-border-2, #c9c7c5);
    color: var(--slds-g-color-on-surface-4, #706e6b);
    border-radius: var(--slds-g-radius-border-1, 0.125rem); padding: 2px 8px;
    font-size: 11px; font-weight: 600; cursor: pointer;
  }
  .ta-btn-defer-toggle:hover {
    color: var(--slds-g-color-on-surface-2, #181818);
    border-color: var(--slds-g-color-border-3, #aeacab);
  }

  /* ── Screen-reader only ── */
  .ta-sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
               overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }

  /* ── Header quick-run strip ── */
  .ta-header-quick-run {
    display: flex; gap: 6px; align-items: center; margin-left: 8px;
    padding-left: 12px;
    border-left: 1px solid var(--slds-g-color-border-1, #e5e5e5);
  }

  /* ── Synthesis button tiers ── */
  .ta-btn-daily-pulse { background: var(--slds-g-color-brand-2, #0176d3); color: #fff; border: none; }
  .ta-btn-daily-pulse:hover { opacity: .85; }
  .ta-btn-periodic {
    background: var(--slds-g-color-surface-1, #fff);
    color: var(--slds-g-color-on-surface-2, #181818);
    border: 1px solid var(--slds-g-color-border-2, #c9c7c5);
  }
  .ta-btn-periodic:hover { background: var(--slds-g-color-surface-2, #f3f3f3); opacity: 1; }
  .ta-btn-rare {
    background: var(--slds-g-color-surface-1, #fff);
    color: var(--slds-g-color-on-surface-4, #706e6b);
    border: 1px solid var(--slds-g-color-border-2, #c9c7c5);
  }
  .ta-btn-rare:hover { color: var(--slds-g-color-on-surface-2, #181818); background: var(--slds-g-color-surface-2, #f3f3f3); opacity: 1; }

  /* ── Operations drawer ── */
  details.ta-ops-drawer > summary {
    cursor: pointer; list-style: none; display: inline-flex; align-items: center;
    gap: 6px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .07em;
    color: var(--slds-g-color-on-surface-4, #706e6b); padding: 2px 0 14px; user-select: none;
  }
  details.ta-ops-drawer > summary::-webkit-details-marker { display: none; }
  details.ta-ops-drawer > summary .ta-ops-chevron { font-size: 10px; transition: transform 0.15s; }
  details.ta-ops-drawer[open] > summary .ta-ops-chevron { transform: rotate(90deg); }

  /* ── Pulse freshness ── */
  .ta-pulse-freshness { font-size: 11px; white-space: nowrap; }
  .ta-pulse-green  { color: var(--slds-g-color-success-2, #2e844a); }
  .ta-pulse-orange { color: var(--slds-g-color-warning-2, #dd7a01); }
  .ta-pulse-red    { color: var(--slds-g-color-error-2, #c23934); }
  .ta-pulse-gray   { color: var(--slds-g-color-on-surface-4, #706e6b); }

  /* ── Weekly Delta nudge ── */
  .ta-weekly-nudge { font-size: 11px; white-space: nowrap; }
  .ta-weekly-nudge-orange { color: var(--slds-g-color-warning-2, #dd7a01); }
  .ta-weekly-nudge-muted  { color: var(--slds-g-color-on-surface-4, #706e6b); }

  /* ── Career full-reread active hint ── */
  .ta-career-full-active { border-color: var(--slds-g-color-warning-2, #dd7a01); }

  /* ── Meeting Prep ── */
  .ta-prep-controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
  .ta-prep-controls select, .ta-prep-controls input[type=text] {
    flex: 1; min-width: 180px;
    background: var(--slds-g-color-surface-1, #fff);
    border: 1px solid var(--slds-g-color-border-2, #c9c7c5);
    border-radius: var(--slds-g-radius-border-2, 0.25rem);
    color: var(--slds-g-color-on-surface-2, #181818);
    font-size: var(--slds-g-font-scale-2, 0.875rem); padding: 7px 10px;
  }
  .ta-prep-controls select:focus, .ta-prep-controls input[type=text]:focus {
    outline: none; border-color: var(--slds-g-color-brand-2, #0176d3);
  }
  .ta-prep-bullet {
    padding: 8px 0; border-bottom: 1px solid var(--slds-g-color-border-1, #e5e5e5);
    font-size: var(--slds-g-font-scale-2, 0.875rem); line-height: 1.5;
  }
  .ta-prep-bullet:last-child { border-bottom: none; }

  /* ── Source drawer ── */
  #source-drawer {
    position: fixed; top: 0; right: 0; width: 540px; max-width: 95vw; height: 100vh;
    background: var(--slds-g-color-surface-1, #fff);
    border-left: 1px solid var(--slds-g-color-border-1, #e5e5e5);
    z-index: 100; display: flex; flex-direction: column;
    transform: translateX(100%); transition: transform .2s ease;
    box-shadow: var(--slds-g-shadow-2, -4px 0 16px rgba(0,0,0,.1));
  }
  #source-drawer.open { transform: translateX(0); }
  #source-drawer-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 14px 20px;
    border-bottom: 1px solid var(--slds-g-color-border-1, #e5e5e5); flex-shrink: 0;
  }
  #source-drawer-title {
    font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em;
    color: var(--slds-g-color-on-surface-4, #706e6b);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  #source-drawer-body { flex: 1; overflow-y: auto; padding: 16px 20px; }
  #source-drawer-content {
    font: 12px/1.6 "SF Mono", "Menlo", monospace;
    color: var(--slds-g-color-on-surface-2, #181818);
    white-space: pre-wrap; overflow-wrap: break-word;
  }

  /* ── Keyboard focus ring for act items (act items are <tr>, so target td) ── */
  .ta-bs-item-focused td:first-child {
    border-left: 3px solid var(--slds-g-color-brand-2, #0176d3);
  }
  .ta-bs-item-focused td {
    background: rgba(1,118,211,0.05);
  }
  .ta-kb-hint {
    font-size: 11px; color: var(--slds-g-color-on-surface-4, #706e6b);
    padding: 8px 0 2px; border-top: 1px solid var(--slds-g-color-border-1, #e5e5e5); margin-top: 8px;
  }

  /* ── Theme toggle button in header ── */
  /* ── Dark-mode overrides for hardcoded light hex values ── */
  .slds-color-scheme_dark .ta-card-inbox          { background: #2a2100; border-color: #7a5200; }
  .slds-color-scheme_dark .ta-card-inbox h2       { color: #f5a623; }
  .slds-color-scheme_dark .ta-inbox-urgent-header { color: #f87171; border-bottom-color: #5a2e2e; }
  .slds-color-scheme_dark .ta-inbox-normal-header { color: #f5a623; border-bottom-color: #5a3a00; }
  .slds-color-scheme_dark .ta-bs-item-urgent      { background: rgba(200,50,50,0.12); }

  .slds-color-scheme_dark .ta-bs-brief-banner {
    background: #2a1a1a; border-color: #5a2e2e;
    border-left-color: #f87171;
  }
  .slds-color-scheme_dark .ta-bs-brief-banner-title { color: #f87171; }
  .slds-color-scheme_dark .ta-bs-brief-banner-icon svg circle,
  .slds-color-scheme_dark .ta-bs-brief-banner-icon svg line { stroke: #f87171; }
  .slds-color-scheme_dark .ta-bs-brief-banner-icon svg circle:last-child { fill: #f87171; stroke: none; }
  .slds-color-scheme_dark .ta-bs-brief-banner-items li { color: var(--slds-g-color-on-surface-2, #e5e5e5); }

  /* Badges — all tint-on-dark equivalents */
  .slds-color-scheme_dark .ta-badge-exec      { background: #3a1a1a; color: #f87171; }
  .slds-color-scheme_dark .ta-badge-solution  { background: #0e2a45; color: #60b0f4; }
  .slds-color-scheme_dark .ta-badge-daily     { background: #0e2e1a; color: #5acf8a; }
  .slds-color-scheme_dark .ta-badge-standup   { background: #2e1e00; color: #f5a623; }
  .slds-color-scheme_dark .ta-badge-notes     { background: #2a1a3a; color: #c08ef7; }
  .slds-color-scheme_dark .ta-badge-pulse     { background: #0e2e1a; color: #5acf8a; }
  .slds-color-scheme_dark .ta-badge-delta     { background: #0e2a2a; color: #3ecfcf; }
  .slds-color-scheme_dark .ta-badge-synthesis { background: #2a1a3a; color: #c08ef7; }
  .slds-color-scheme_dark .ta-badge-act       { background: #3a1a1a; color: #f87171; }
  .slds-color-scheme_dark .ta-badge-watch     { background: #2e1e00; color: #f5a623; }
  .slds-color-scheme_dark .ta-badge-delegate  { background: #0e2a45; color: #60b0f4; }
  .slds-color-scheme_dark .ta-badge-they-owe  { background: #2a1a3a; color: #c08ef7; }
  .slds-color-scheme_dark .ta-badge-insight   { background: #0e2a2a; color: #3ecfcf; }
  .slds-color-scheme_dark .ta-badge-pending   { background: #0e2e1a; color: #5acf8a; }
  .slds-color-scheme_dark .ta-badge-complete  { background: #0e2e1a; color: #5acf8a; }
  .slds-color-scheme_dark .ta-badge-deferred  { background: #2a2000; color: #cfaf3e; }
  .slds-color-scheme_dark .ta-badge-expired   { background: #3a1a1a; color: #f87171; }
  .slds-color-scheme_dark .ta-badge-dropped   { background: #2a2a2a; color: #888; }
  .slds-color-scheme_dark .ta-badge-overdue   { background: #3a1a1a; color: #f87171; border-color: #5a2e2e; }

  /* Flash messages */
  .slds-color-scheme_dark .ta-flash-ok  { background: #0e2e1a; color: #5acf8a; border-color: #1a5a2a; }
  .slds-color-scheme_dark .ta-flash-err { background: #2a1a1a; color: #f87171; border-color: #5a2e2e; }

  /* Toggle thumb stays white */
  .slds-color-scheme_dark .ta-slider:before { background: #e5e5e5; }

  /* Focus/Today card border when overdue */
  .slds-color-scheme_dark .ta-card[style*="fac9c9"] { border-color: #5a2e2e !important; }

  /* Job running banner */
  .ta-flash-info { background: #eaf1fb; color: var(--slds-g-color-brand-2, #0176d3); border: 1px solid #b8d4f5; }
  .slds-color-scheme_dark .ta-flash-info { background: #0e2a45; color: #60b0f4; border-color: #1a4a70; }

  /* ── Brief State record-page tabs ── */
  .ta-bs-tabs {
    display: flex; gap: 0; border-bottom: 1px solid var(--slds-g-color-border-2, #c9c7c5);
    margin-bottom: 20px; background: var(--slds-g-color-surface-1, #fff);
    position: sticky; top: 53px; z-index: 9;
    padding: 0 0 0 0;
  }
  .ta-bs-tab {
    padding: 12px 20px; font-size: var(--slds-g-font-scale-2, 0.875rem); font-weight: 400;
    color: var(--slds-g-color-on-surface-4, #706e6b);
    border: none; background: none; cursor: pointer;
    border-bottom: 3px solid transparent; margin-bottom: -1px;
    white-space: nowrap; display: flex; align-items: center; gap: 6px;
    transition: color .1s;
  }
  .ta-bs-tab:hover { color: var(--slds-g-color-on-surface-2, #181818); }
  .ta-bs-tab.active {
    color: var(--slds-g-color-brand-2, #0176d3);
    border-bottom-color: var(--slds-g-color-brand-2, #0176d3);
    font-weight: 600;
  }
  .ta-bs-tab-count {
    background: var(--slds-g-color-surface-2, #f3f3f3);
    color: var(--slds-g-color-on-surface-4, #706e6b);
    border-radius: 10px; padding: 1px 7px; font-size: 11px; font-weight: 700;
  }
  .ta-bs-tab.active .ta-bs-tab-count {
    background: #daeaf8; color: var(--slds-g-color-brand-2, #0176d3);
  }
  .ta-bs-tab-count.urgent { background: #fef1f1; color: var(--slds-g-color-error-2, #c23934); }
  .ta-bs-panel { display: none; }
  .ta-bs-panel.active { display: block; }

  /* ── Related-list table inside Brief State ── */
  .ta-rl { width: 100%; border-collapse: collapse; }
  .ta-rl thead tr { background: var(--slds-g-color-surface-2, #f3f3f3); }
  .ta-rl th {
    text-align: left; padding: 6px 12px;
    font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em;
    color: var(--slds-g-color-on-surface-4, #706e6b);
    border-bottom: 1px solid var(--slds-g-color-border-2, #c9c7c5);
    white-space: nowrap;
  }
  .ta-rl td {
    padding: 10px 12px; vertical-align: top;
    border-bottom: 1px solid var(--slds-g-color-border-1, #e5e5e5);
    font-size: var(--slds-g-font-scale-2, 0.875rem); line-height: 1.4;
  }
  .ta-rl tr:last-child td { border-bottom: none; }
  .ta-rl tbody tr:hover { background: rgba(1,118,211,0.03); }
  .ta-rl-overdue td { background: rgba(194,57,52,0.04); }
  .ta-rl-due-soon td { background: rgba(221,122,1,0.04); }
  .ta-rl-item-text { max-width: 520px; overflow-wrap: break-word; word-break: break-word; }
  .ta-rl-item-text .ta-bs-text-toggle { font-size: 11px; color: var(--slds-g-color-brand-2, #0176d3); background: none; border: none; padding: 0; cursor: pointer; margin-left: 4px; }
  .ta-rl-actions { white-space: nowrap; display: flex; gap: 4px; align-items: center; justify-content: flex-end; }
  .ta-rl-meta { font-size: 11px; color: var(--slds-g-color-on-surface-4, #706e6b); margin-top: 2px; }
  .ta-rl-section-header {
    background: var(--slds-g-color-surface-1, #fff);
    padding: 10px 12px 6px;
    font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em;
    color: var(--slds-g-color-on-surface-4, #706e6b);
    border-bottom: 1px solid var(--slds-g-color-border-1, #e5e5e5);
  }
  .ta-rl-view-all {
    display: block; text-align: center; padding: 10px;
    font-size: 12px; color: var(--slds-g-color-brand-2, #0176d3);
    border-top: 1px solid var(--slds-g-color-border-1, #e5e5e5);
    cursor: pointer; background: none; border-left: none; border-right: none; border-bottom: none;
    width: 100%; text-decoration: none;
  }
  .ta-rl-view-all:hover { background: var(--slds-g-color-surface-2, #f3f3f3); text-decoration: none; }
  .ta-rl-empty {
    padding: 20px 12px; text-align: center;
    color: var(--slds-g-color-on-surface-4, #706e6b);
    font-size: var(--slds-g-font-scale-2, 0.875rem);
  }
  .ta-rl-inbox-row td { background: #fffdf0; }
  .ta-rl-inline-accept { display: flex; gap: 4px; align-items: center; flex-wrap: wrap; }

  /* Dark mode for tabs + related-list */
  .slds-color-scheme_dark .ta-bs-tabs { background: var(--slds-g-color-surface-1); border-bottom-color: var(--slds-g-color-border-2); }
  .slds-color-scheme_dark .ta-rl thead tr { background: var(--slds-g-color-surface-2); }
  .slds-color-scheme_dark .ta-rl-inbox-row td { background: #2a2100; }
  .slds-color-scheme_dark .ta-rl tbody tr:hover { background: rgba(100,160,255,0.04); }
  .slds-color-scheme_dark .ta-bs-tab-count.urgent { background: #3a1a1a; color: #f87171; }
</style>
</head>
<body>
<header class="ta-header">
  <h1>Transcript Analyzer</h1>
  <nav class="ta-nav">
    <a href="/brief-state" class="{{ 'active' if page == 'brief-state' else '' }}">Brief State{% if pending_act_count %}<span class="ta-nav-badge" aria-label="{{ pending_act_count }} pending">{{ pending_act_count }}</span>{% endif %}</a>
    <a href="/files" class="{{ 'active' if page == 'home' else '' }}">Analyzed Files</a>
    <a href="/prep" class="{{ 'active' if page == 'prep' else '' }}">Meeting Prep</a>
    <a href="/slack-paste" class="{{ 'active' if page == 'slack-paste' else '' }}">Slack Paste</a>
    <a href="/run" class="{{ 'active' if page == 'run' else '' }}">Run</a>
    <a href="/settings" class="{{ 'active' if page == 'settings' else '' }}">Settings</a>
  </nav>
  {% if page == 'brief-state' %}
  <div class="ta-header-quick-run">
    <form method="post" action="/run-analysis" onsubmit="startPoll('Running transcript analysis…')">
      <button type="submit" class="ta-btn ta-btn-sm" title="Pick up new transcripts from the inbox">⚡ Run Analysis</button>
    </form>
    <form method="post" action="/synthesize" onsubmit="startPoll('Running Daily Pulse synthesis…')" style="display:flex;gap:5px;align-items:center" id="daily-pulse-quick-form">
      <input type="hidden" name="mode" value="daily">
      <button type="submit" class="ta-btn ta-btn-daily-pulse ta-btn-sm" title="Synthesize today's analyses into a Daily Pulse">▶ Daily Pulse</button>
    </form>
    {% if last_pulse %}
      {% if last_pulse.analyses_since > 0 %}
      <span class="ta-pulse-freshness ta-pulse-{{ last_pulse.status }}">
        {{ last_pulse.analyses_since }} new — <a href="#" onclick="document.getElementById('daily-pulse-quick-form').submit();return false;" style="color:inherit;text-decoration:underline">run now?</a>
      </span>
      {% else %}
      <span class="ta-pulse-freshness ta-pulse-{{ last_pulse.status }}">Last pulse: {{ last_pulse.label }}</span>
      {% endif %}
    {% endif %}
  </div>
  {% endif %}
  <div style="flex:1"></div>
  <div style="display:flex;gap:8px;align-items:center">
    {% if render_warnings %}
    <span class="ta-render-warn-chip" tabindex="0" role="button"
          onclick="this.classList.toggle('open')"
          onkeydown="if(event.key==='Enter'||event.key===' ')this.classList.toggle('open')"
          onblur="setTimeout(()=>this.classList.remove('open'),150)">
      ⚠ {{ render_warnings | length }} warning{{ 's' if render_warnings | length != 1 else '' }}
      <span class="ta-render-warn-popover" onclick="event.stopPropagation()">
        <ul>{% for w in render_warnings %}<li>{{ w }}</li>{% endfor %}</ul>
      </span>
    </span>
    {% endif %}
    {% if shared_url %}
    <a class="ta-btn{% if render_warnings %} ta-btn-secondary{% endif %}" href="{{ shared_url }}" target="_blank" rel="noopener">
      ↗ Shared Meeting Files
    </a>
    {% endif %}
  </div>
</header>
<div class="ta-content">
{{ body | safe }}
</div>
<script>
// Brief State tab switching
function bsTab(name) {
  document.querySelectorAll('.ta-bs-tab').forEach(function(t) {
    var active = t.id === 'bs-tab-' + name;
    t.classList.toggle('active', active);
    t.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  document.querySelectorAll('.ta-bs-panel').forEach(function(p) {
    p.classList.toggle('active', p.id === 'bs-panel-' + name);
  });
  try { sessionStorage.setItem('bs-active-tab', name); } catch(_) {}
}
// Restore last-used tab on load
(function() {
  var saved = sessionStorage.getItem('bs-active-tab');
  if (saved && document.getElementById('bs-tab-' + saved)) bsTab(saved);
})();

// Expand/collapse clamped text (inbox uses itext-, acts use atext-)
function toggleText(id) {
  var el = document.getElementById('itext-' + id);
  var btn = el && el.nextElementSibling;
  if (!el) return;
  var expanded = el.classList.toggle('expanded');
  if (btn) btn.textContent = expanded ? 'less' : 'more';
}
function toggleText2(id) {
  var el = document.getElementById('atext-' + id);
  var btn = el && el.nextElementSibling;
  if (!el) return;
  var expanded = el.classList.toggle('expanded');
  if (btn) btn.textContent = expanded ? 'less' : 'more';
}

// Theme toggle (light / dark via slds-color-scheme_dark on <html>)
function toggleTheme() {
  const dark = document.documentElement.classList.toggle('slds-color-scheme_dark');
  localStorage.setItem('slds-ui-color-mode', dark ? 'dark' : 'light');
  var cb = document.getElementById('theme-toggle-input');
  if (cb) cb.checked = dark;
}
// Sync settings toggle to current mode on page load
(function() {
  var cb = document.getElementById('theme-toggle-input');
  if (cb) cb.checked = document.documentElement.classList.contains('slds-color-scheme_dark');
})();

// Quick-defer: daysOffset=0 → next Monday; >0 → N days from today
function deferQuick(itemId, daysOffset) {
  const d = new Date();
  if (daysOffset === 0) {
    const dow = d.getDay(); // 0=Sun,1=Mon…
    const daysToMon = dow === 0 ? 1 : (8 - dow) % 7 || 7;
    d.setDate(d.getDate() + daysToMon);
  } else {
    d.setDate(d.getDate() + daysOffset);
  }
  const iso = d.toISOString().slice(0, 10);
  const form = document.createElement('form');
  form.method = 'post';
  form.action = '/brief-state/defer/' + itemId;
  const inp = document.createElement('input');
  inp.type = 'hidden'; inp.name = 'target'; inp.value = iso;
  form.appendChild(inp);
  document.body.appendChild(form);
  form.submit();
}

// Job polling (synthesis + analysis)
let _pollTimer = null;
function startPoll(label) {
  if (_pollTimer) return;
  const indicator = document.getElementById('job-indicator');
  if (indicator && label) indicator.innerHTML = '<span class="ta-spinner"></span> ' + label;
  const jobStatus = document.getElementById('job-status');
  const jobDone = document.getElementById('job-done');
  const jobOutput = document.getElementById('job-output');
  if (jobStatus) jobStatus.style.display = 'block';
  if (jobDone) jobDone.style.display = 'none';
  if (indicator) indicator.style.display = 'inline';
  _pollTimer = setInterval(async () => {
    let d;
    try {
      const r = await fetch('/job-status');
      d = await r.json();
    } catch (_) { return; } // network blip — retry next tick
    if (jobOutput) { jobOutput.textContent = d.output || ''; jobOutput.scrollTop = jobOutput.scrollHeight; }
    if (d.done) {
      clearInterval(_pollTimer); _pollTimer = null;
      if (indicator) indicator.style.display = 'none';
      const banner = document.getElementById('job-running-banner');
      if (banner) banner.style.display = 'none';
      if (d.returncode && d.returncode !== 0) {
        // Job failed (non-zero exit). Keep the panel up with the captured output
        // so the error is visible — do NOT reload it away. (returncode 2 is a
        // soft warning: synthesis written but archive skipped — still surface it.)
        if (jobDone) { jobDone.style.display = 'inline'; jobDone.style.color = 'var(--slds-g-color-error-2,#c23934)'; jobDone.textContent = 'Failed (exit ' + d.returncode + ') — output below, not refreshing.'; }
        document.title = '⚠ Job failed — Transcript Analyzer';
      } else {
        if (jobDone) jobDone.style.display = 'inline';
        document.title = '✓ Done — Transcript Analyzer';
        // Reload page so new files appear; pass a flash if analysis found new files
        let reloadUrl = location.pathname;
        if (d.new_count && d.new_count > 0) {
          const noun = d.new_count === 1 ? 'file' : 'files';
          reloadUrl += '?flash=' + encodeURIComponent(d.new_count + ' new ' + noun + ' processed') + '&ft=ok';
        }
        setTimeout(() => location.href = reloadUrl, 1500);
      }
    }
  }, 1200);
}
// Keyboard triage for act items (Brief State only)
(function() {
  var _focusedIdx = -1;
  function _actItems() { return Array.from(document.querySelectorAll('[data-act-id]')); }
  function _setFocus(idx) {
    var items = _actItems();
    if (!items.length) return;
    items.forEach(function(el) { el.classList.remove('ta-bs-item-focused'); });
    _focusedIdx = Math.max(0, Math.min(idx, items.length - 1));
    items[_focusedIdx].classList.add('ta-bs-item-focused');
    items[_focusedIdx].scrollIntoView({block:'nearest'});
  }
  function _submitAction(el, selector) {
    var btn = el.querySelector(selector);
    if (btn) btn.closest('form') ? btn.closest('form').submit() : btn.click();
  }
  document.addEventListener('keydown', function(e) {
    if (['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)) return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    var items = _actItems();
    if (!items.length) return;
    if (_focusedIdx < 0) _focusedIdx = 0;
    var el = items[_focusedIdx];
    if (e.key === 'ArrowDown' || e.key === 'j') { e.preventDefault(); _setFocus(_focusedIdx + 1); return; }
    if (e.key === 'ArrowUp'   || e.key === 'i') { e.preventDefault(); _setFocus(_focusedIdx - 1); return; }
    if (e.key === 'k') { e.preventDefault(); _submitAction(el, '[data-act-action="done"]'); return; }
    if (e.key === 'd') {
      e.preventDefault();
      var dropBtn = el.querySelector('[data-act-action="drop"]');
      if (dropBtn) {
        var frm = dropBtn.closest('form');
        if (frm && confirm('Drop this item? It will be marked dropped.')) frm.submit();
      }
      return;
    }
    if (e.key === '1') { e.preventDefault(); var id = el.dataset.actId; if(id) deferQuick(id,1); return; }
    if (e.key === '3') { e.preventDefault(); var id = el.dataset.actId; if(id) deferQuick(id,3); return; }
    if (e.key === 'm') { e.preventDefault(); var id = el.dataset.actId; if(id) deferQuick(id,0); return; }
  });
  // Auto-focus first item on page load unless URL hash targets a specific section
  if (document.querySelector('[data-act-id]') && !window.location.hash) {
    setTimeout(function(){_setFocus(0);}, 50);
  }
})();

// Source drawer
async function openSourceDrawer(itemId, sourcePulse) {
  const drawer = document.getElementById('source-drawer');
  const title = document.getElementById('source-drawer-title');
  const content = document.getElementById('source-drawer-content');
  title.textContent = sourcePulse || 'Source';
  content.textContent = 'Loading…';
  drawer.classList.add('open');
  try {
    const resp = await fetch('/source/' + encodeURIComponent(itemId));
    const data = await resp.json();
    content.textContent = data.text || '(empty)';
  } catch(e) {
    content.textContent = 'Failed to load: ' + e;
  }
}
function closeSourceDrawer() {
  document.getElementById('source-drawer').classList.remove('open');
}
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') closeSourceDrawer();
});

// Meeting Prep
async function runMeetingPrep() {
  const program = document.getElementById('prep-program').value;
  if (!program) { alert('Select a program first'); return; }
  const context = document.getElementById('prep-context').value;
  const btn = document.getElementById('prep-submit');
  btn.disabled = true; btn.textContent = 'Prepping…';
  try {
    const resp = await fetch('/meeting-prep', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({program, context})
    });
    const data = await resp.json();
    const out = document.getElementById('prep-output');
    if (data.bullets && data.bullets.length) {
      out.innerHTML = data.bullets.map(b => '<div class="ta-prep-bullet">' + b.replace(/[*][*]([^*]+)[*][*]/g, '<strong>$1</strong>') + '</div>').join('');
    } else {
      out.innerHTML = '<div class="ta-prep-bullet ta-muted">No bullets returned.</div>';
    }
    out.style.display = 'block';
  } catch(e) {
    const out = document.getElementById('prep-output');
    out.innerHTML = '<div class="ta-prep-bullet" style="color:var(--slds-g-color-error-2,#c23934)">Request failed: ' + e + '</div>';
    out.style.display = 'block';
  }
  btn.disabled = false; btn.textContent = 'Prep';
}

// Refresh signals (program pulse sweep)
function refreshSignals() {
  const btn = document.getElementById('prep-refresh-btn');
  const prepBtn = document.getElementById('prep-submit');
  btn.disabled = true; btn.textContent = 'Refreshing…';
  if (prepBtn) prepBtn.disabled = true;
  const form = document.createElement('form');
  form.method = 'post';
  form.action = '/synthesize';
  const modeInput = document.createElement('input');
  modeInput.type = 'hidden'; modeInput.name = 'mode'; modeInput.value = 'program-pulse';
  form.appendChild(modeInput);
  document.body.appendChild(form);
  startPoll('Running Program Pulse sweep…');
  form.submit();
}

// Career full re-read guard
function careerSubmit(form, startPollFn) {
  const full = form.querySelector('#career-full');
  if (full && full.checked) {
    if (!confirm('Full re-read will process all analyzed files through Opus. This may take 10+ minutes and cost $5–15. Proceed?')) return false;
  }
  startPollFn('Running Career Trajectory review…');
  return true;
}

// Prevent double-submission on all brief-state action forms.
// Synthesis forms use startPoll() which reloads the page, so they self-reset.
document.addEventListener('submit', function(e) {
  const form = e.target;
  // Don't lock synthesis/analysis forms — startPoll shows status independently.
  if (form.action && (
      form.action.includes('/synthesize') ||
      form.action.includes('/run-analysis')
  )) return;
  form.querySelectorAll('button[type=submit]').forEach(function(btn) {
    btn.disabled = true;
    btn.style.opacity = '0.5';
  });
});

// Auto-resume polling if server reports a job in-flight when page loads.
// Also fires on bfcache restore (pageshow persisted=true) so the banner
// doesn't stay stuck after back-navigation to a page with a running job.
(function() {
  function _maybeResume() {
    {% if job_running %}
    if (!_pollTimer) startPoll({{ job_label | tojson }} + '…');
    {% endif %}
  }
  _maybeResume();
  window.addEventListener('pageshow', function(e) {
    if (e.persisted) _maybeResume();
  });
})();

// ── Persist <details> open/closed state across page reloads ──
(function() {
  if (typeof sessionStorage === 'undefined') return;
  var KEY = 'bs-details-state';
  function stateKey(el) {
    // Prefer explicit data-persist-key for stable identity across reloads
    if (el.dataset && el.dataset.persistKey) return KEY + ':' + el.dataset.persistKey;
    var card = el.closest('[id]');
    var cardId = card ? card.id : 'root';
    var all = Array.from(document.querySelectorAll('details'));
    return KEY + ':' + cardId + ':' + all.indexOf(el);
  }
  // Restore on load
  document.querySelectorAll('details').forEach(function(el) {
    var k = stateKey(el);
    var saved = sessionStorage.getItem(k);
    if (saved === 'open') el.open = true;
    else if (saved === 'closed') el.open = false;
  });
  // Save on toggle
  document.addEventListener('toggle', function(e) {
    var el = e.target;
    if (el.tagName !== 'DETAILS') return;
    sessionStorage.setItem(stateKey(el), el.open ? 'open' : 'closed');
  }, true);
})();

// ── Program order drag-to-reorder ──
(function() {
  var list = document.getElementById('prog-order-list');
  if (!list) return;
  var dragItem = null;
  list.addEventListener('dragstart', function(e) {
    dragItem = e.target.closest('.prog-order-item');
    if (dragItem) { dragItem.style.opacity = '0.4'; e.dataTransfer.effectAllowed = 'move'; }
  });
  list.addEventListener('dragend', function() {
    if (dragItem) { dragItem.style.opacity = ''; dragItem = null; }
    list.querySelectorAll('.prog-order-item').forEach(function(el) { el.style.outline = ''; });
  });
  list.addEventListener('dragover', function(e) {
    e.preventDefault();
    var target = e.target.closest('.prog-order-item');
    if (target && target !== dragItem) target.style.outline = '2px dashed var(--slds-g-color-brand-2, #0176d3)';
  });
  list.addEventListener('dragleave', function(e) {
    var target = e.target.closest('.prog-order-item');
    if (target) target.style.outline = '';
  });
  list.addEventListener('drop', function(e) {
    e.preventDefault();
    var target = e.target.closest('.prog-order-item');
    if (!target || target === dragItem || !dragItem) return;
    target.style.outline = '';
    var items = Array.from(list.querySelectorAll('.prog-order-item'));
    var di = items.indexOf(dragItem), ti = items.indexOf(target);
    if (di < ti) list.insertBefore(dragItem, target.nextSibling);
    else list.insertBefore(dragItem, target);
    var order = Array.from(list.querySelectorAll('.prog-order-item')).map(function(el) { return el.dataset.key; });
    fetch('/brief-state/prog-order', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({order: order})
    });
  });
})();

// Always start ops-drawer details closed — browser restore can re-open these
document.querySelectorAll('details.ta-ops-drawer').forEach(function(d) {
  d.removeAttribute('open');
});
</script>
</body>
</html>
"""

_HOME_BODY = """\
{% if flash %}<div class="ta-flash ta-flash-{{ flash_type }}">{{ flash }}</div>{% endif %}
{% if job_running %}
<div id="job-running-banner" class="ta-flash ta-flash-info" style="display:flex;align-items:center;gap:8px">
  <span class="ta-spinner"></span>
  <strong>Running {{ job_label }}…</strong> This takes a few minutes. The page will refresh automatically when done.
</div>
{% endif %}

<div class="ta-stats">
  <div class="ta-stat"><div class="val">{{ rows|selectattr("mode","ne","synthesis")|list|length }}</div><div class="lbl">Total analyzed</div></div>
  <div class="ta-stat"><div class="val">{{ rows|selectattr("mode","eq","transcript")|list|length }}</div><div class="lbl">Transcripts</div></div>
  <div class="ta-stat"><div class="val">{{ rows|selectattr("mode","eq","notes")|list|length }}</div><div class="lbl">Notes filed</div></div>
  <div class="ta-stat"><div class="val">{{ rows|selectattr("mode","eq","synthesis")|list|length }}</div><div class="lbl">Syntheses</div></div>
  <div class="ta-stat"><div class="val">${{ "%.2f"|format(total_cost) }}</div><div class="lbl">Total cost</div></div>
  {% if last_analyzed %}
  <div class="ta-stat"><div class="val" style="font-size:13px{% if last_analyzed_stale == 'danger' %};color:var(--slds-g-color-error-2,#c23934){% elif last_analyzed_stale == 'warn' %};color:var(--slds-g-color-warning-2,#dd7a01){% endif %}">{{ last_analyzed }}</div><div class="lbl">Last analyzed</div></div>
  {% endif %}
</div>

<div class="ta-card">
  <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:10px">
    <h2 style="margin:0">Analyzed files — {{ rows|length }} total</h2>
    <div style="display:flex;align-items:center;gap:8px">
      <input id="files-search" type="search" placeholder="Filter…" oninput="filesFilter()"
             style="padding:3px 8px;font-size:12px;border:1px solid var(--slds-g-color-border-1,#e5e5e5);border-radius:4px;width:160px">
      <button class="ta-btn ta-btn-secondary ta-btn-sm" id="bulk-classify-btn" onclick="bulkClassify()" title="Auto-classify all unassigned files using the keyword classifier">
        Auto-classify unassigned
      </button>
      <span id="bulk-classify-status" style="font-size:11px;color:var(--slds-g-color-on-surface-4,#706e6b)"></span>
    </div>
  </div>
  {% if rows %}
  <table id="files-table">
    <thead>
      <tr>
        <th>Date</th><th>Source</th><th>Category</th><th>Project</th><th>Model</th><th>Cost</th><th>Shareable</th>
      </tr>
    </thead>
    <tbody id="files-tbody">
    {% for r in rows %}
    <tr data-search="{{ r.analyzed_at[:10] }} {{ r.output }} {{ r.category }} {{ r.program }}">
      <td class="ta-muted" style="white-space:nowrap">{{ r.analyzed_at[:10] }}</td>
      <td class="td-source">
        {% if r.mode == "synthesis" %}
          <a class="ta-quick-link" href="/open-file?path={{ analyzed_path }}/{{ r.output }}" title="{{ r.source }}">{{ r.output }}</a>
        {% else %}
          <span title="{{ r.source }}">{{ r.output or r.source }}</span>
        {% endif %}
      </td>
      <td>
        {% if r.mode == "notes" %}
          <span class="ta-badge ta-badge-notes">notes</span>
        {% elif r.category %}
          <span class="ta-badge ta-badge-{{ r.category|lower }}">{{ r.category }}</span>
        {% endif %}
      </td>
      <td style="white-space:nowrap">
        {% if r.mode not in ("synthesis",) and r.output %}
        <form method="post" action="/files/set-program" style="display:inline-flex;gap:4px;align-items:center">
          <input type="hidden" name="output" value="{{ r.output }}">
          <select name="program" class="ta-btn ta-btn-secondary ta-btn-sm"
                  style="padding:2px 4px;font-size:11px;max-width:140px{% if r.program_overridden %};font-weight:600{% endif %}"
                  onchange="this.form.submit()" title="{% if r.program_overridden %}Manually overridden{% else %}Auto-classified{% endif %}">
            <option value="">— project —</option>
            {% for p in programs %}<option value="{{ p.key }}" {% if r.program == p.key %}selected{% endif %}>{{ p.label }}</option>{% endfor %}
          </select>
          {% if r.program_overridden %}<span title="Manually overridden" style="font-size:10px;color:var(--slds-g-color-brand-2,#0176d3)">✎</span>{% endif %}
        </form>
        {% else %}
        <span class="ta-muted">—</span>
        {% endif %}
      </td>
      <td class="ta-muted">{{ r.model or "—" }}</td>
      <td class="ta-muted">${{ "%.3f"|format(r.cost) }}</td>
      <td>{% if r.shareable %}<span style="color:var(--slds-g-color-success-2,#2e844a)">✓</span>{% else %}<span class="ta-muted">—</span>{% endif %}</td>
    </tr>
    {% endfor %}
    </tbody>
  </table>
  <div id="files-pagination" style="display:flex;align-items:center;gap:10px;margin-top:12px;padding-top:10px;border-top:1px solid var(--slds-g-color-border-1,#e5e5e5);font-size:12px;color:var(--slds-g-color-on-surface-4,#706e6b)">
    <button class="ta-btn ta-btn-secondary ta-btn-sm" id="files-prev" onclick="filesPage(-1)">← Prev</button>
    <span id="files-page-info"></span>
    <button class="ta-btn ta-btn-secondary ta-btn-sm" id="files-next" onclick="filesPage(1)">Next →</button>
    <span style="margin-left:auto">
      Show
      <select id="files-per-page" onchange="filesSetPerPage()" style="padding:2px 4px;font-size:11px;border:1px solid var(--slds-g-color-border-1,#e5e5e5);border-radius:4px">
        <option value="25">25</option>
        <option value="50">50</option>
        <option value="100">100</option>
        <option value="0">All</option>
      </select>
      per page
    </span>
  </div>
  {% else %}
  <div style="padding:24px 0;display:flex;flex-direction:column;align-items:center;gap:8px;color:var(--slds-g-color-on-surface-4,#706e6b)">
    <span style="font-size:28px;opacity:0.4">⏳</span>
    <span style="font-size:13px">No analyzed files yet</span>
    <span style="font-size:12px;max-width:340px;text-align:center;opacity:0.7">Drop transcripts into the Call Transcripts inbox and hit <strong style="color:var(--slds-g-color-on-surface-2,#181818)">Run Analysis</strong> on Brief State.</span>
  </div>
  {% endif %}
</div>
<script>
(function() {
  var _rows, _filtered, _page = 0, _perPage = 25;

  function init() {
    _rows = Array.from(document.querySelectorAll('#files-tbody tr'));
    _filtered = _rows;
    render();
  }

  function render() {
    var total = _filtered.length;
    var perPage = _perPage === 0 ? total : _perPage;
    var totalPages = perPage === 0 ? 1 : Math.max(1, Math.ceil(total / perPage));
    if (_page >= totalPages) _page = totalPages - 1;
    if (_page < 0) _page = 0;
    var start = _page * perPage;
    var end = perPage === 0 ? total : Math.min(start + perPage, total);

    _rows.forEach(function(r) { r.style.display = 'none'; });
    _filtered.slice(start, end).forEach(function(r) { r.style.display = ''; });

    var info = document.getElementById('files-page-info');
    if (info) info.textContent = total === 0 ? 'No results' : (start + 1) + '–' + end + ' of ' + total;
    var prev = document.getElementById('files-prev');
    var next = document.getElementById('files-next');
    if (prev) prev.disabled = _page === 0;
    if (next) next.disabled = _page >= totalPages - 1;
    var pg = document.getElementById('files-pagination');
    if (pg) pg.style.display = total <= (_perPage === 0 ? total + 1 : _perPage) && _perPage !== 0 && total <= _perPage ? 'none' : 'flex';
  }

  window.filesPage = function(delta) { _page += delta; render(); };
  window.filesSetPerPage = function() {
    _perPage = parseInt(document.getElementById('files-per-page').value, 10);
    _page = 0; render();
  };
  window.filesFilter = function() {
    var q = (document.getElementById('files-search').value || '').toLowerCase();
    _filtered = q ? _rows.filter(function(r) { return (r.dataset.search || '').toLowerCase().includes(q); }) : _rows;
    _page = 0; render();
  };

  window.bulkClassify = function() {
    var btn = document.getElementById('bulk-classify-btn');
    var status = document.getElementById('bulk-classify-status');
    btn.disabled = true;
    status.textContent = 'Running…';
    fetch('/files/bulk-classify', {method: 'POST'})
      .then(function(r) { return r.json(); })
      .then(function(d) {
        status.textContent = d.message || 'Done';
        btn.disabled = false;
        if (d.count > 0) setTimeout(function() { window.location.reload(); }, 800);
      })
      .catch(function() { status.textContent = 'Error'; btn.disabled = false; });
  };

  document.addEventListener('DOMContentLoaded', init);
})();
</script>
"""

_SETTINGS_BODY = """\
{% if flash %}<div class="ta-flash ta-flash-{{ flash_type }}">{{ flash }}</div>{% endif %}

<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;align-items:start">

<form method="post" action="/settings">
<div class="ta-card" style="height:100%">
  <h2>Configuration</h2>
  <p style="font-size:12px;color:var(--slds-g-color-on-surface-4,#706e6b);margin-bottom:20px;line-height:1.6">
    Saved to <code class="ta-code-inline">~/.config/transcript-analyzer/.env</code>.
    Restart server for changes to take effect.
  </p>

  {% for key, label, kind, desc in fields %}
  <div class="ta-field">
    <label>{{ label }}</label>
    {% if kind == "toggle" %}
    <div class="ta-toggle-row">
      <label class="ta-toggle">
        <input type="checkbox" name="{{ key }}" {% if vals.get(key,'').lower() in ('1','true','yes','on') %}checked{% endif %}>
        <span class="ta-slider"></span>
      </label>
      <span style="font-size:12px;color:var(--slds-g-color-on-surface-4,#706e6b);margin:0">{{ desc }}</span>
    </div>
    {% else %}
    <input type="{{ kind }}" name="{{ key }}" value="{{ vals.get(key,'') }}" placeholder="{{ desc[:60] }}">
    <div class="ta-field .desc">{{ desc }}</div>
    {% endif %}
  </div>
  {% endfor %}

  <div style="padding-top:4px">
    <button type="submit" class="ta-btn">Save settings</button>
  </div>
</div>
</form>

<div style="display:flex;flex-direction:column;gap:20px">

<div class="ta-card">
  <h2>Diagnostics</h2>
  <div style="margin-bottom:16px">
    <a class="ta-quick-link" href="/open-log" style="font-size:13px;font-weight:600">Open run log</a>
    <div style="font-size:11px;color:var(--slds-g-color-on-surface-4,#706e6b);font-family:'SF Mono',monospace;margin-top:3px">{{ log_path }}</div>
  </div>
  <div style="padding-top:14px;border-top:1px solid var(--slds-g-color-border-1,#e5e5e5)">
    <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:var(--slds-g-color-on-surface-4,#706e6b);margin-bottom:10px">Server</div>
    <form method="post" action="/restart" style="display:inline"
          onsubmit="return confirm('Restart the dashboard server? This reloads code and .env changes. Any in-progress job will block the restart.');">
      <button type="submit" class="ta-btn ta-btn-secondary">↻ Restart server</button>
    </form>
    <div style="font-size:12px;color:var(--slds-g-color-on-surface-4,#706e6b);margin-top:8px">Re-execs in place to pick up code or <code class="ta-code-inline">.env</code> changes. Analysis runs fresh each time — this only refreshes the long-lived UI process.</div>
  </div>
</div>

<div class="ta-card">
  <h2>Display</h2>
  <div class="ta-field">
    <label>Color mode</label>
    <div class="ta-toggle-row">
      <label class="ta-toggle">
        <input type="checkbox" id="theme-toggle-input" onchange="toggleTheme()">
        <span class="ta-slider"></span>
      </label>
      <span style="font-size:12px;color:var(--slds-g-color-on-surface-4,#706e6b);margin:0">Dark mode</span>
    </div>
  </div>
</div>

<div class="ta-card">
  <h2>Content files</h2>
  <p style="font-size:12px;color:var(--slds-g-color-on-surface-4,#706e6b);margin-bottom:16px">Drive files — opens in your default editor.</p>
  {% for label, path in drive_files %}
  <div style="margin-bottom:10px;padding-bottom:10px;border-bottom:1px solid var(--slds-g-color-border-1,#e5e5e5)">
    <a class="ta-quick-link" href="/open-file?path={{ path }}" style="display:flex;flex-direction:column;gap:2px">
      <span style="font-weight:600;font-size:13px">{{ label }}</span>
      <span class="ta-muted" style="font-size:11px;font-family:'SF Mono',monospace">{{ path }}</span>
    </a>
  </div>
  {% endfor %}
  <div style="margin-top:-2px;padding-top:0;border-top:none"></div>
</div>

<div class="ta-card">
  <h2>Project order</h2>
  <p style="font-size:12px;color:var(--slds-g-color-on-surface-4,#706e6b);margin-bottom:12px;line-height:1.5">
    Drag to reorder. Controls grouping order across Brief State sections. Takes effect immediately.
  </p>
  <div id="prog-order-list" style="display:flex;flex-direction:column;gap:4px">
    {% for key in prog_order %}
    <div class="prog-order-item" data-key="{{ key }}" draggable="true"
         style="display:flex;align-items:center;gap:8px;padding:6px 10px;background:var(--slds-g-color-surface-2,#f3f3f3);border:1px solid var(--slds-g-color-border-1,#e5e5e5);border-radius:var(--slds-g-radius-border-2,0.25rem);cursor:grab;font-size:12px;user-select:none">
      <span style="color:var(--slds-g-color-border-2,#c9c7c5);font-size:14px">⠿</span>
      <span>{{ key }}</span>
    </div>
    {% endfor %}
  </div>
  <p style="margin-top:10px;font-size:11px;color:var(--slds-g-color-on-surface-4,#706e6b)">New projects not in this list appear at the bottom.</p>
</div>


<div class="ta-card">
  <h2>Re-push to STATUS.md</h2>
  <p style="font-size:12px;color:var(--slds-g-color-on-surface-4,#706e6b);margin-bottom:14px;line-height:1.6">
    Backfill rewrites a project's STATUS.md signal history from scratch using all files classified to it (including any overrides above). Use after correcting misclassified files.
  </p>
  {% for p in programs %}
  <form method="post" action="/files/backfill-program" style="display:inline-block;margin:0 4px 6px 0"
        onsubmit="return confirm('Backfill {{ p.label }}? This will re-write its STATUS.md Open signals section.');">
    <input type="hidden" name="program" value="{{ p.key }}">
    <button type="submit" class="ta-btn ta-btn-secondary ta-btn-sm">↺ {{ p.label }}</button>
  </form>
  {% endfor %}
</div>

</div><!-- right column -->
</div><!-- grid -->
"""


_RUN_BODY = """\
{% if flash %}<div class="ta-flash ta-flash-{{ flash_type }}">{{ flash }}</div>{% endif %}
{% if job_running %}
<div id="job-running-banner" class="ta-flash ta-flash-info" style="display:flex;align-items:center;gap:8px">
  <span class="ta-spinner"></span>
  <strong>Running {{ job_label }}…</strong> The page will refresh when done.
</div>
{% endif %}

<div class="ta-card">
  <div class="ta-action-tier">
    <span class="ta-action-tier-label">Daily</span>
    <div class="ta-action-group">
      <form method="post" action="/run-analysis" onsubmit="startPoll('Running transcript analysis…')">
        <button type="submit" class="ta-btn">⚡ Run Analysis</button>
      </form>
    </div>
    <div class="ta-action-group">
      <form method="post" action="/synthesize" onsubmit="startPoll('Running Daily Pulse synthesis…')" style="display:flex;gap:6px;align-items:center">
        <input type="hidden" name="mode" value="daily">
        <button type="submit" class="ta-btn ta-btn-daily-pulse">▶ Daily Pulse</button>
        <label class="ta-sr-only" for="daily-date">Back-date (optional)</label>
        <input type="date" id="daily-date" name="date" title="Leave blank for today; pick a past date to back-date">
      </form>
    </div>
  </div>
  <div class="ta-action-tier">
    <span class="ta-action-tier-label">Periodic</span>
    <div class="ta-action-group">
      <form method="post" action="/synthesize" onsubmit="startPoll('Running Weekly Delta synthesis…')" style="display:flex;gap:6px;align-items:center">
        <input type="hidden" name="mode" value="weekly">
        <button type="submit" class="ta-btn ta-btn-periodic">▶ {% if last_weekly and last_weekly.bdays and last_weekly.bdays > 8 %}Weekly Delta ({{ last_weekly.bdays }}d catch-up){% else %}Weekly Delta{% endif %}</button>
        <label class="ta-sr-only" for="weekly-date">Week anchor date (optional)</label>
        <input type="date" id="weekly-date" name="date" title="Leave blank for this week">
        {% if last_weekly %}
          {% if last_weekly.label and last_weekly.bdays and last_weekly.bdays > 5 %}
          <span class="ta-weekly-nudge ta-weekly-nudge-orange" title="Last weekly was {{ last_weekly.label }}">Last: {{ last_weekly.label }}</span>
          {% elif not last_weekly.label %}
          <span class="ta-weekly-nudge ta-weekly-nudge-muted">Not yet run</span>
          {% endif %}
        {% endif %}
      </form>
    </div>
    <div class="ta-action-group">
      <form method="post" action="/synthesize" onsubmit="startPoll('Running catch-up — one pulse per backlog day…')" style="display:flex;gap:6px;align-items:center">
        <input type="hidden" name="mode" value="catch-up">
        <button type="submit" class="ta-btn ta-btn-periodic">▶ Catch Up</button>
        <label class="ta-sr-only" for="catchup-since">Since date (optional)</label>
        <input type="date" id="catchup-since" name="since" title="Start from this date (leave blank for full backlog)">
      </form>
    </div>
  </div>
  <div class="ta-action-tier">
    <span class="ta-action-tier-label">Rare</span>
    <div class="ta-action-group">
      <form method="post" action="/synthesize" id="career-form"
            onsubmit="return careerSubmit(this, startPoll)" style="display:flex;gap:6px;align-items:center">
        <input type="hidden" name="mode" value="career">
        <button type="submit" id="career-btn" class="ta-btn ta-btn-rare">▶ Career</button>
        <label style="font-size:11px;color:var(--slds-g-color-on-surface-4,#706e6b);display:inline-flex;align-items:center;gap:4px"
               title="Re-read ALL analyses to re-baseline. Off = incremental.">
          <input type="checkbox" name="full" value="1" id="career-full"
                 onchange="document.getElementById('career-btn').classList.toggle('ta-career-full-active',this.checked)"> full
        </label>
      </form>
    </div>
    <div class="ta-action-group">
      <form method="post" action="/synthesize" onsubmit="startPoll('Compiling Weekly Timecard…')" style="display:flex;gap:6px;align-items:center">
        <input type="hidden" name="mode" value="timecard">
        <button type="submit" class="ta-btn ta-btn-rare">▶ Timecard</button>
        <label class="ta-sr-only" for="timecard-date">Week date (optional)</label>
        <input type="date" id="timecard-date" name="date" title="Leave blank for current week">
      </form>
    </div>
  </div>
</div>

<div id="job-status" {% if not job_running %}style="display:none"{% endif %}>
  <h3>
    <span id="job-indicator"><span class="ta-spinner"></span> Running {{ job_label or "job" }}…</span>
    <span id="job-done" style="display:none">Done — refreshing…</span>
  </h3>
  <div id="job-output" role="log" aria-live="polite" aria-label="Job output">{{ job_output }}</div>
</div>
"""

_BRIEF_STATE_BODY = """\
{% if flash %}<div class="ta-flash ta-flash-{{ flash_type }}">{{ flash }}</div>{% endif %}
{% if job_running %}
<div id="job-running-banner" class="ta-flash ta-flash-info" style="display:flex;align-items:center;gap:8px">
  <span class="ta-spinner"></span>
  <strong>Running {{ job_label }}…</strong> The page will refresh when done.
</div>
{% endif %}

{# ── OVERDUE BANNER ── #}
{% set overdue_acts = act_items | selectattr('overdue') | list %}
{% if overdue_acts %}
<div class="ta-bs-brief-banner" role="region" aria-label="Overdue obligations">
  <div class="ta-bs-brief-banner-icon">
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <circle cx="8" cy="8" r="7.5" stroke="#c23934"/>
      <line x1="8" y1="4.5" x2="8" y2="9" stroke="#c23934" stroke-width="1.5" stroke-linecap="round"/>
      <circle cx="8" cy="11.5" r="0.75" fill="#c23934"/>
    </svg>
  </div>
  <div class="ta-bs-brief-banner-body">
    <div class="ta-bs-brief-banner-title">{{ overdue_acts|length }} overdue act{{ 's' if overdue_acts|length != 1 else '' }} — action required</div>
    <ul class="ta-bs-brief-banner-items">
      {% for item in overdue_acts %}
      <li>{{ item.text }}{% if item.days_overdue %} <span class="ta-badge ta-badge-overdue" style="font-size:10px">{{ item.days_overdue }}d overdue</span>{% endif %}</li>
      {% endfor %}
    </ul>
  </div>
</div>
{% endif %}

{# ── COUNT HELPERS ── #}
{% set waiting_count = delegate_items|length + they_owe_items|length %}
{% set inbox_count = candidate_items|length %}
{% set urgent_count = candidate_items | selectattr('urgency', 'equalto', 'high') | list | length %}

{# ── MACRO: inline project reassign select (auto-submits on change) ── #}
{% macro project_select(item, return_to) %}
<form method="post" action="/brief-state/assign-program/{{ item.id }}" style="display:inline">
  <input type="hidden" name="return_to" value="{{ return_to }}">
  <select name="program" class="ta-btn ta-btn-secondary ta-btn-sm" style="padding:2px 4px;font-size:11px;max-width:130px"
          onchange="this.form.submit()" title="Reassign project">
    <option value="">— project —</option>
    {% for p in programs %}<option value="{{ p.key }}" {% if item.program == p.key %}selected{% endif %}>{{ p.label }}</option>{% endfor %}
  </select>
</form>
{% endmacro %}

{# ── TAB BAR ── #}
<div class="ta-bs-tabs" role="tablist">
  <button class="ta-bs-tab active" role="tab" aria-selected="true" aria-controls="bs-panel-act" id="bs-tab-act" onclick="bsTab('act')">
    Act
    {% if act_items %}
    <span class="ta-bs-tab-count{% if overdue_acts %} urgent{% endif %}">{{ act_items|length }}</span>
    {% endif %}
  </button>
  <button class="ta-bs-tab" role="tab" aria-selected="false" aria-controls="bs-panel-watch" id="bs-tab-watch" onclick="bsTab('watch')">
    Watch
    {% if watch_items %}<span class="ta-bs-tab-count">{{ watch_items|length }}</span>{% endif %}
  </button>
  <button class="ta-bs-tab" role="tab" aria-selected="false" aria-controls="bs-panel-waiting" id="bs-tab-waiting" onclick="bsTab('waiting')">
    Waiting
    {% if waiting_count %}<span class="ta-bs-tab-count">{{ waiting_count }}</span>{% endif %}
  </button>
  {% if inbox_count %}
  <button class="ta-bs-tab" role="tab" aria-selected="false" aria-controls="bs-panel-inbox" id="bs-tab-inbox" onclick="bsTab('inbox')">
    Inbox
    <span class="ta-bs-tab-count{% if urgent_count %} urgent{% endif %}">{{ inbox_count }}</span>
  </button>
  {% endif %}
  <button class="ta-bs-tab" role="tab" aria-selected="false" aria-controls="bs-panel-closed" id="bs-tab-closed" onclick="bsTab('closed')">
    Closed
    {% if recent_closed %}<span class="ta-bs-tab-count">{{ recent_closed|length }}</span>{% endif %}
  </button>
</div>

{# ──────────────────────────────────────────── #}
{# ACT TAB                                      #}
{# ──────────────────────────────────────────── #}
<div class="ta-bs-panel active" id="bs-panel-act" role="tabpanel" aria-labelledby="bs-tab-act">

{# Pinned / Today's Focus — always at top of Act tab #}
{% if pinned_acts %}
<div class="ta-card" style="border-color:var(--slds-g-color-brand-2,#0176d3);border-width:2px;margin-bottom:16px">
  <h2 style="display:flex;align-items:center;gap:8px;color:var(--slds-g-color-brand-2,#0176d3)">
    ★ Today's Focus
    <span class="ta-bs-tab-count" style="background:#daeaf8;color:var(--slds-g-color-brand-2,#0176d3)">{{ pinned_acts|length }}</span>
  </h2>
  <table class="ta-rl">
    <thead><tr><th style="width:56%">Item</th><th>Project</th><th>From</th><th>Due</th><th></th></tr></thead>
    <tbody>
    {% for item in pinned_acts %}
    <tr class="{% if item.overdue %}ta-rl-overdue{% elif item.deadline and item.deadline <= today_iso %}ta-rl-due-soon{% endif %}" data-act-id="{{ item.id }}">
      <td class="ta-rl-item-text">
        {% if item.overdue %}<span class="ta-badge ta-badge-overdue" style="margin-right:6px">OVERDUE</span>{% endif %}
        <span id="atext-{{ item.id }}">{{ item.text }}</span>
        {% if item.loe %}<span class="ta-badge" style="background:var(--slds-g-color-surface-2,#f3f3f3);color:var(--slds-g-color-on-surface-4,#706e6b);font-size:10px;margin-left:5px">{{ item.loe }}</span>{% endif %}
        {% if item.source_pulse %}<a href="#" class="ta-quick-link" style="font-size:11px;margin-left:6px" onclick="openSourceDrawer('{{ item.id }}','{{ item.source_pulse }}');return false">↗</a>{% endif %}
      </td>
      <td class="ta-muted">{{ item.program or '—' }}</td>
      {% set _from = item.source_brief or item.source_analyzed or '' %}
      <td class="ta-muted" style="white-space:nowrap">{% if _from %}<a class="ta-quick-link" href="/open-file?path={{ analyzed_path }}/{{ _from }}">{{ _from[:10] }}</a>{% else %}—{% endif %}</td>
      <td class="ta-muted" style="white-space:nowrap">
        {% if item.deadline %}
        <span style="{% if item.overdue %}color:var(--slds-g-color-error-2,#c23934);font-weight:600{% elif item.deadline <= today_iso %}color:var(--slds-g-color-warning-2,#dd7a01){% endif %}">{{ item.deadline }}</span>
        {% else %}—{% endif %}
      </td>
      <td>
        <div class="ta-rl-actions">
          <form method="post" action="/brief-state/close/{{ item.id }}" style="display:inline">
            <input type="hidden" name="status" value="complete">
            <button type="submit" class="ta-btn ta-btn-secondary ta-btn-sm" data-act-action="done">Done</button>
          </form>
          <button type="button" class="ta-btn ta-btn-secondary ta-btn-sm" onclick="deferQuick('{{ item.id }}',1)" data-act-action="defer1">+1d</button>
          <button type="button" class="ta-btn ta-btn-secondary ta-btn-sm" onclick="deferQuick('{{ item.id }}',3)" data-act-action="defer3">+3d</button>
          <button type="button" class="ta-btn ta-btn-secondary ta-btn-sm" onclick="deferQuick('{{ item.id }}',0)" data-act-action="defermon">Mon</button>
          <details class="ta-ops-drawer" style="display:inline-block;position:relative">
            <summary class="ta-btn ta-btn-secondary ta-btn-sm" style="list-style:none;cursor:pointer;padding:4px 8px">…</summary>
            <div style="position:absolute;background:var(--slds-g-color-surface-1,#fff);border:1px solid var(--slds-g-color-border-1,#e5e5e5);border-radius:6px;padding:10px;margin-top:4px;z-index:20;min-width:220px;right:0;white-space:nowrap">
              <form method="post" action="/brief-state/close/{{ item.id }}" style="margin-bottom:6px" onsubmit="return confirm('Drop this item?');">
                <input type="hidden" name="status" value="dropped">
                <button type="submit" class="ta-btn ta-btn-secondary ta-btn-sm" style="width:100%">Drop</button>
              </form>
              <form method="post" action="/brief-state/defer/{{ item.id }}" style="display:flex;gap:4px;align-items:center;margin-bottom:6px">
                <input type="hidden" name="return_to" value="inbox">
                <input type="date" name="target" required style="width:120px">
                <button type="submit" class="ta-btn ta-btn-secondary ta-btn-sm">Defer to…</button>
              </form>
              <form method="post" action="/brief-state/loe/{{ item.id }}" style="display:flex;gap:4px;align-items:center;margin-bottom:6px">
                <input type="hidden" name="return_to" value="inbox">
                <select name="loe" class="ta-btn ta-btn-secondary ta-btn-sm" style="padding:3px 6px;font-size:11px">
                  <option value="">LoE…</option>
                  <option value="S" {% if item.loe == 'S' %}selected{% endif %}>S (&lt;2h)</option>
                  <option value="M" {% if item.loe == 'M' %}selected{% endif %}>M (half day)</option>
                  <option value="L" {% if item.loe == 'L' %}selected{% endif %}>L (multi-day)</option>
                </select>
                <button type="submit" class="ta-btn ta-btn-secondary ta-btn-sm">Set LoE</button>
              </form>
              <form method="post" action="/brief-state/assign-program/{{ item.id }}" style="display:flex;gap:4px;align-items:center;margin-bottom:6px">
                <input type="hidden" name="return_to" value="inbox">
                <select name="program" class="ta-btn ta-btn-secondary ta-btn-sm" style="padding:3px 6px;font-size:11px">
                  <option value="">Clear project</option>
                  {% for p in programs %}<option value="{{ p.key }}" {% if item.program == p.key %}selected{% endif %}>{{ p.label }}</option>{% endfor %}
                </select>
                <button type="submit" class="ta-btn ta-btn-secondary ta-btn-sm">Assign</button>
              </form>
              <form method="post" action="/brief-state/unpin/{{ item.id }}">
                <input type="hidden" name="return_to" value="inbox">
                <button type="submit" class="ta-btn ta-btn-secondary ta-btn-sm" style="width:100%">☆ Unpin</button>
              </form>
            </div>
          </details>
        </div>
      </td>
    </tr>
    {% endfor %}
    </tbody>
  </table>
</div>
{% endif %}

{# All act items grouped by program #}
<div class="ta-card">
  <h2>Act items — Brad owns</h2>
  {% if act_groups %}
  <table class="ta-rl">
    <thead><tr><th style="width:60%">Item</th><th>From</th><th>Due</th><th></th></tr></thead>
    <tbody>
  {% for group in act_groups %}
  {% if act_groups|length > 1 %}
  <tr><td colspan="4" class="ta-rl-section-header" style="padding:6px 12px">{{ group.label }}</td></tr>
  {% endif %}
    {% for item in group.acts %}
    <tr class="{% if item.overdue %}ta-rl-overdue{% elif item.deadline and item.deadline <= today_iso %}ta-rl-due-soon{% endif %}" data-act-id="{{ item.id }}" tabindex="0">
      <td class="ta-rl-item-text">
        {% if item.overdue %}<span class="ta-badge ta-badge-overdue" style="margin-right:6px">OVERDUE</span>{% endif %}
        <span id="atext-{{ item.id }}" class="ta-bs-text">{{ item.text }}</span>
        {% if item.loe %}<span class="ta-badge" style="background:var(--slds-g-color-surface-2,#f3f3f3);color:var(--slds-g-color-on-surface-4,#706e6b);font-size:10px;margin-left:5px">{{ item.loe }}</span>{% endif %}
        {% if item.source_pulse %}<a href="#" class="ta-quick-link" style="font-size:11px;margin-left:6px" onclick="openSourceDrawer('{{ item.id }}','{{ item.source_pulse }}');return false">↗</a>{% endif %}
        {% if item.notes %}<div class="ta-rl-meta">{{ item.notes }}</div>{% endif %}
      </td>
      {% set _from = item.source_brief or item.source_analyzed or '' %}
      <td class="ta-muted" style="white-space:nowrap">{% if _from %}<a class="ta-quick-link" href="/open-file?path={{ analyzed_path }}/{{ _from }}">{{ _from[:10] }}</a>{% else %}—{% endif %}</td>
      <td class="ta-muted" style="white-space:nowrap">
        {% if item.deadline %}<span style="{% if item.overdue %}color:var(--slds-g-color-error-2,#c23934);font-weight:600{% elif item.deadline <= today_iso %}color:var(--slds-g-color-warning-2,#dd7a01){% endif %}">{{ item.deadline }}</span>{% else %}—{% endif %}
      </td>
      <td>
        <div class="ta-rl-actions">
          <form method="post" action="/brief-state/close/{{ item.id }}" style="display:inline">
            <input type="hidden" name="status" value="complete">
            <button type="submit" class="ta-btn ta-btn-secondary ta-btn-sm" data-act-action="done">Done</button>
          </form>
          <button type="button" class="ta-btn ta-btn-secondary ta-btn-sm" onclick="deferQuick('{{ item.id }}',1)" data-act-action="defer1">+1d</button>
          <button type="button" class="ta-btn ta-btn-secondary ta-btn-sm" onclick="deferQuick('{{ item.id }}',3)" data-act-action="defer3">+3d</button>
          <button type="button" class="ta-btn ta-btn-secondary ta-btn-sm" onclick="deferQuick('{{ item.id }}',0)" data-act-action="defermon">Mon</button>
          <details class="ta-ops-drawer" style="display:inline-block;position:relative">
            <summary class="ta-btn ta-btn-secondary ta-btn-sm" style="list-style:none;cursor:pointer;padding:4px 8px">…</summary>
            <div style="position:absolute;background:var(--slds-g-color-surface-1,#fff);border:1px solid var(--slds-g-color-border-1,#e5e5e5);border-radius:6px;padding:10px;margin-top:4px;z-index:20;min-width:220px;right:0;white-space:nowrap">
              <form method="post" action="/brief-state/close/{{ item.id }}" style="margin-bottom:6px" onsubmit="return confirm('Drop this item?');">
                <input type="hidden" name="status" value="dropped">
                <button type="submit" class="ta-btn ta-btn-secondary ta-btn-sm" style="width:100%">Drop</button>
              </form>
              <form method="post" action="/brief-state/defer/{{ item.id }}" style="display:flex;gap:4px;align-items:center;margin-bottom:6px">
                <input type="hidden" name="return_to" value="inbox">
                <input type="date" name="target" required style="width:120px">
                <button type="submit" class="ta-btn ta-btn-secondary ta-btn-sm">Defer to…</button>
              </form>
              <form method="post" action="/brief-state/loe/{{ item.id }}" style="display:flex;gap:4px;align-items:center;margin-bottom:6px">
                <input type="hidden" name="return_to" value="inbox">
                <select name="loe" class="ta-btn ta-btn-secondary ta-btn-sm" style="padding:3px 6px;font-size:11px">
                  <option value="">LoE…</option>
                  <option value="S" {% if item.loe == 'S' %}selected{% endif %}>S (&lt;2h)</option>
                  <option value="M" {% if item.loe == 'M' %}selected{% endif %}>M (half day)</option>
                  <option value="L" {% if item.loe == 'L' %}selected{% endif %}>L (multi-day)</option>
                </select>
                <button type="submit" class="ta-btn ta-btn-secondary ta-btn-sm">Set LoE</button>
              </form>
              <form method="post" action="/brief-state/assign-program/{{ item.id }}" style="display:flex;gap:4px;align-items:center;margin-bottom:6px">
                <input type="hidden" name="return_to" value="inbox">
                <select name="program" class="ta-btn ta-btn-secondary ta-btn-sm" style="padding:3px 6px;font-size:11px">
                  <option value="">Clear project</option>
                  {% for p in programs %}<option value="{{ p.key }}" {% if item.program == p.key %}selected{% endif %}>{{ p.label }}</option>{% endfor %}
                </select>
                <button type="submit" class="ta-btn ta-btn-secondary ta-btn-sm">Assign</button>
              </form>
              <form method="post" action="/brief-state/pin/{{ item.id }}">
                <input type="hidden" name="return_to" value="inbox">
                <button type="submit" class="ta-btn ta-btn-secondary ta-btn-sm" style="width:100%">★ Pin to Focus</button>
              </form>
            </div>
          </details>
        </div>
      </td>
    </tr>
    {% endfor %}
  {% endfor %}
    </tbody>
  </table>
  {% if act_items|length >= 2 %}
  <div class="ta-kb-hint">[k] done &nbsp; [d] drop &nbsp; [1/3/m] defer &nbsp; [↓/j] next &nbsp; [↑/i] prev</div>
  {% endif %}
  {% else %}
  <div class="ta-rl-empty">
    <span style="color:var(--slds-g-color-success-2,#2e844a)">✓</span> All clear — no pending acts.
  </div>
  {% endif %}
</div>

</div>{# /bs-panel-act #}

{# ──────────────────────────────────────────── #}
{# WATCH TAB                                    #}
{# ──────────────────────────────────────────── #}
<div class="ta-bs-panel" id="bs-panel-watch" role="tabpanel" aria-labelledby="bs-tab-watch">
<div class="ta-card">
  <h2>Watch — monitoring items</h2>
  {% if watch_items %}
  {% for group in watch_groups %}
  <details class="ta-bs-program-group" data-persist-key="watch-group-{{ group.key }}" open>
    <summary class="ta-bs-program-header ta-bs-program-header-toggle">
      <span class="ta-bs-program-chevron">▾</span>
      {{ group.label }}
      <span class="ta-muted" style="font-size:11px;font-weight:400">({{ group.rows|length }})</span>
    </summary>
    <table class="ta-rl" style="margin-top:4px">
      <thead><tr><th style="width:72%">Item</th><th>From</th><th></th></tr></thead>
      <tbody>
      {% for item in group.rows %}
      <tr data-act-id="{{ item.id }}">
        <td class="ta-rl-item-text">
          {{ item.text }}
          {% if item.notes %}<div class="ta-rl-meta">{{ item.notes }}</div>{% endif %}
        </td>
        {% set _from = item.source_brief or item.source_analyzed or '' %}
        <td class="ta-muted" style="white-space:nowrap">{% if _from %}<a class="ta-quick-link" href="/open-file?path={{ analyzed_path }}/{{ _from }}">{{ _from[:10] }}</a>{% else %}—{% endif %}</td>
        <td>
          <div class="ta-rl-actions">
            <form method="post" action="/brief-state/close/{{ item.id }}" style="display:inline">
              <input type="hidden" name="status" value="complete">
              <input type="hidden" name="return_to" value="watch-section">
              <button type="submit" class="ta-btn ta-btn-secondary ta-btn-sm">Done</button>
            </form>
            <form method="post" action="/brief-state/close/{{ item.id }}" style="display:inline" onsubmit="return confirm('Drop this watch item?');">
              <input type="hidden" name="status" value="dropped">
              <input type="hidden" name="return_to" value="watch-section">
              <button type="submit" class="ta-btn ta-btn-secondary ta-btn-sm">Drop</button>
            </form>
          </div>
        </td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
  </details>
  {% endfor %}
  {% else %}
  <div class="ta-rl-empty">No active watch items.</div>
  {% endif %}
</div>
</div>{# /bs-panel-watch #}

{# ──────────────────────────────────────────── #}
{# WAITING TAB (Delegate + They Owe)            #}
{# ──────────────────────────────────────────── #}
<div class="ta-bs-panel" id="bs-panel-waiting" role="tabpanel" aria-labelledby="bs-tab-waiting">

{% if delegate_items %}
<div class="ta-card" style="margin-bottom:16px">
  <h2>Delegated — waiting for confirmation</h2>
  <table class="ta-rl">
    <thead><tr><th style="width:52%">Item</th><th>Owner</th><th>From</th><th>Due</th><th></th></tr></thead>
    <tbody>
    {% for item in delegate_items %}
    <tr data-act-id="{{ item.id }}">
      <td class="ta-rl-item-text">
        {{ item.text }}
        {% if item.resurfaced_count %}<div class="ta-rl-meta">Re-surfaced {{ item.resurfaced_count }}×</div>{% endif %}
        {% if item.notes %}<div class="ta-rl-meta">{{ item.notes }}</div>{% endif %}
      </td>
      <td>
        <form method="post" action="/brief-state/set-owner/{{ item.id }}" style="display:inline">
          <input type="hidden" name="return_to" value="delegate-section">
          <input type="text" name="owner" value="{{ item.owner or '' }}" placeholder="owner…"
                 class="ta-btn ta-btn-secondary ta-btn-sm" style="padding:2px 5px;font-size:11px;width:90px"
                 onblur="this.form.submit()" onkeydown="if(event.key==='Enter'){event.preventDefault();this.form.submit();}">
        </form>
      </td>
      {% set _from = item.source_brief or item.source_analyzed or '' %}
      <td class="ta-muted" style="white-space:nowrap">{% if _from %}<a class="ta-quick-link" href="/open-file?path={{ analyzed_path }}/{{ _from }}">{{ _from[:10] }}</a>{% else %}—{% endif %}</td>
      <td class="ta-muted" style="white-space:nowrap">{{ item.deadline or '—' }}</td>
      <td>
        <div class="ta-rl-actions">
          {{ project_select(item, 'delegate-section') }}
          <form method="post" action="/brief-state/close/{{ item.id }}" style="display:inline">
            <input type="hidden" name="status" value="complete">
            <input type="hidden" name="return_to" value="delegate-section">
            <button type="submit" class="ta-btn ta-btn-secondary ta-btn-sm">Confirmed</button>
          </form>
          <form method="post" action="/brief-state/re-own/{{ item.id }}" style="display:inline">
            <input type="hidden" name="return_to" value="delegate-section">
            <button type="submit" class="ta-btn ta-btn-secondary ta-btn-sm">Re-own</button>
          </form>
        </div>
      </td>
    </tr>
    {% endfor %}
    </tbody>
  </table>
</div>
{% endif %}

{% if they_owe_items %}
<div class="ta-card">
  <h2>They owe you</h2>
  <table class="ta-rl">
    <thead><tr><th style="width:52%">Item</th><th>Who</th><th>From</th><th>Due</th><th></th></tr></thead>
    <tbody>
    {% for item in they_owe_items %}
    <tr class="{% if item.they_overdue %}ta-rl-overdue{% endif %}" data-act-id="{{ item.id }}">
      <td class="ta-rl-item-text">
        {{ item.text }}
        {% if item.notes %}<div class="ta-rl-meta">{{ item.notes }}</div>{% endif %}
      </td>
      <td>
        <form method="post" action="/brief-state/set-owner/{{ item.id }}" style="display:inline">
          <input type="hidden" name="return_to" value="they-owe-section">
          <input type="text" name="owner" value="{{ item.owner or '' }}" placeholder="who…"
                 class="ta-btn ta-btn-secondary ta-btn-sm" style="padding:2px 5px;font-size:11px;width:90px"
                 onblur="this.form.submit()" onkeydown="if(event.key==='Enter'){event.preventDefault();this.form.submit();}">
        </form>
      </td>
      {% set _from = item.source_brief or item.source_analyzed or '' %}
      <td class="ta-muted" style="white-space:nowrap">{% if _from %}<a class="ta-quick-link" href="/open-file?path={{ analyzed_path }}/{{ _from }}">{{ _from[:10] }}</a>{% else %}—{% endif %}</td>
      <td class="ta-muted" style="white-space:nowrap">
        {% if item.deadline %}<span style="{% if item.they_overdue %}color:var(--slds-g-color-error-2,#c23934);font-weight:600{% endif %}">{{ item.deadline }}{% if item.they_overdue %} ({{ item.days_overdue }}d){% endif %}</span>
        {% else %}—{% endif %}
      </td>
      <td>
        <div class="ta-rl-actions">
          {{ project_select(item, 'they-owe-section') }}
          <form method="post" action="/brief-state/close/{{ item.id }}" style="display:inline">
            <input type="hidden" name="status" value="complete">
            <input type="hidden" name="return_to" value="they-owe-section">
            <button type="submit" class="ta-btn ta-btn-secondary ta-btn-sm">Received</button>
          </form>
          <form method="post" action="/brief-state/close/{{ item.id }}" style="display:inline" onsubmit="return confirm('Drop?');">
            <input type="hidden" name="status" value="dropped">
            <input type="hidden" name="return_to" value="they-owe-section">
            <button type="submit" class="ta-btn ta-btn-secondary ta-btn-sm">Drop</button>
          </form>
        </div>
      </td>
    </tr>
    {% endfor %}
    </tbody>
  </table>
</div>
{% endif %}

{% if not delegate_items and not they_owe_items %}
<div class="ta-card"><div class="ta-rl-empty">Nothing delegated or owed.</div></div>
{% endif %}

</div>{# /bs-panel-waiting #}

{# ──────────────────────────────────────────── #}
{# INBOX TAB                                    #}
{# ──────────────────────────────────────────── #}
{% if inbox_count %}
<div class="ta-bs-panel" id="bs-panel-inbox" role="tabpanel" aria-labelledby="bs-tab-inbox">
<div class="ta-card ta-card-inbox">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
    <h2 style="margin-bottom:0">Inbox — {{ candidate_items|length }} pending triage</h2>
    <form method="post" action="/brief-state/dismiss-all-candidates" style="display:inline"
          onsubmit="return confirm('Dismiss all {{ candidate_items|length }} candidate item{{ \'s\' if candidate_items|length != 1 else \'\'}}?');">
      <button type="submit" class="ta-btn ta-btn-secondary ta-btn-sm">Dismiss all</button>
    </form>
  </div>
  {% for group in inbox_groups %}
  {% set group_urgent = group.rows | selectattr('urgency', 'equalto', 'high') | list %}
  {% set group_normal = group.rows | rejectattr('urgency', 'equalto', 'high') | list %}
  <details class="ta-bs-program-group" data-persist-key="inbox-group-{{ group.key }}" open>
    <summary class="ta-bs-program-header ta-bs-program-header-toggle">
      <span class="ta-bs-program-chevron">▾</span>
      {{ group.label }}
      <span class="ta-muted" style="font-size:11px;font-weight:400">({{ group.rows|length }})</span>
    </summary>
    <table class="ta-rl" style="margin-top:4px">
      <thead><tr><th style="width:60%">Signal</th><th>Type</th><th></th></tr></thead>
      <tbody>
      {% for item in group_urgent %}
      <tr class="ta-rl-inbox-row">
        <td class="ta-rl-item-text">
          <span class="ta-badge ta-badge-act" style="margin-right:6px">URGENT</span>
          <span id="itext-{{ item.id }}">{{ item.text }}</span>
          <div class="ta-rl-meta">{{ item.source_analyzed or '—' }}{% if item.deadline %} · Due {{ item.deadline }}{% endif %}</div>
        </td>
        <td>
          <span class="ta-badge ta-badge-{{ item.type }}">{{ item.type }}</span>
          {% if item.owner and item.owner != 'Brad' %}<div class="ta-rl-meta">{{ item.owner }}</div>{% endif %}
        </td>
        <td>
          <div class="ta-rl-actions">
            {{ project_select(item, 'inbox') }}
            <form method="post" action="/brief-state/accept/{{ item.id }}" style="display:flex;gap:4px;align-items:center">
              <select name="type" class="ta-btn ta-btn-secondary ta-btn-sm" style="padding:3px 6px;font-size:11px">
                <option value="act"      {% if item.type == 'act' %}selected{% endif %}>act</option>
                <option value="watch"    {% if item.type == 'watch' %}selected{% endif %}>watch</option>
                <option value="they-owe" {% if item.type == 'they-owe' %}selected{% endif %}>they-owe</option>
                <option value="delegate" {% if item.type == 'delegate' %}selected{% endif %}>delegate</option>
                <option value="insight"  {% if item.type == 'insight' %}selected{% endif %}>insight</option>
              </select>
              <input type="hidden" name="program" value="{{ item.program or '' }}">
              <button type="submit" class="ta-btn ta-btn-secondary ta-btn-sm" style="border-color:var(--slds-g-color-error-2,#c23934);color:var(--slds-g-color-error-2,#c23934)">Accept</button>
            </form>
            <form method="post" action="/brief-state/dismiss-candidate/{{ item.id }}" style="display:inline" onsubmit="return confirm('Dismiss?');">
              <button type="submit" class="ta-btn ta-btn-secondary ta-btn-sm">Dismiss</button>
            </form>
          </div>
        </td>
      </tr>
      {% endfor %}
      {% for item in group_normal %}
      <tr>
        <td class="ta-rl-item-text">
          <span id="itext-{{ item.id }}">{{ item.text }}</span>
          <div class="ta-rl-meta">{{ item.source_analyzed or '—' }}{% if item.deadline %} · Due {{ item.deadline }}{% endif %}</div>
        </td>
        <td>
          <span class="ta-badge ta-badge-{{ item.type }}">{{ item.type }}</span>
          {% if item.owner and item.owner != 'Brad' %}<div class="ta-rl-meta">{{ item.owner }}</div>{% endif %}
        </td>
        <td>
          <div class="ta-rl-actions">
            {{ project_select(item, 'inbox') }}
            <form method="post" action="/brief-state/accept/{{ item.id }}" style="display:flex;gap:4px;align-items:center">
              <select name="type" class="ta-btn ta-btn-secondary ta-btn-sm" style="padding:3px 6px;font-size:11px">
                <option value="act"      {% if item.type == 'act' %}selected{% endif %}>act</option>
                <option value="watch"    {% if item.type == 'watch' %}selected{% endif %}>watch</option>
                <option value="they-owe" {% if item.type == 'they-owe' %}selected{% endif %}>they-owe</option>
                <option value="delegate" {% if item.type == 'delegate' %}selected{% endif %}>delegate</option>
                <option value="insight"  {% if item.type == 'insight' %}selected{% endif %}>insight</option>
              </select>
              <input type="hidden" name="program" value="{{ item.program or '' }}">
              <button type="submit" class="ta-btn ta-btn-secondary ta-btn-sm" style="border-color:var(--slds-g-color-warning-2,#dd7a01);color:var(--slds-g-color-warning-2,#dd7a01)">Accept</button>
            </form>
            <form method="post" action="/brief-state/dismiss-candidate/{{ item.id }}" style="display:inline" onsubmit="return confirm('Dismiss?');">
              <button type="submit" class="ta-btn ta-btn-secondary ta-btn-sm">Dismiss</button>
            </form>
          </div>
        </td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
  </details>
  {% endfor %}
</div>
</div>
{% endif %}
{# /bs-panel-inbox #}

{# ──────────────────────────────────────────── #}
{# CLOSED TAB                                   #}
{# ──────────────────────────────────────────── #}
<div class="ta-bs-panel" id="bs-panel-closed" role="tabpanel" aria-labelledby="bs-tab-closed">
<div class="ta-card">
  <h2>Closed — last 7 days</h2>
  {% if recent_closed %}
  {% for group in closed_groups %}
  <details class="ta-bs-program-group" data-persist-key="closed-group-{{ group.key }}" open>
    <summary class="ta-bs-program-header ta-bs-program-header-toggle">
      <span class="ta-bs-program-chevron">▾</span>
      {{ group.label }}
      <span class="ta-muted" style="font-size:11px;font-weight:400">({{ group.rows|length }})</span>
    </summary>
    <table class="ta-rl" style="margin-top:4px">
      <thead><tr><th style="width:54%">Item</th><th>Type</th><th>Status</th><th>Closed</th><th></th></tr></thead>
      <tbody>
      {% for item in group.rows %}
      <tr>
        <td class="ta-rl-item-text">{{ item.text }}</td>
        <td><span class="ta-badge ta-badge-{{ item.type }}">{{ item.type }}</span></td>
        <td><span class="ta-badge ta-badge-{{ item.status }}">{{ item.status }}</span></td>
        <td class="ta-muted" style="white-space:nowrap">{{ item.closed_at[:10] if item.closed_at else '—' }}</td>
        <td>
          <form method="post" action="/brief-state/reopen/{{ item.id }}" style="display:inline">
            <button type="submit" class="ta-btn ta-btn-secondary ta-btn-sm">Reopen</button>
          </form>
        </td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
  </details>
  {% endfor %}
  {% else %}
  <div class="ta-rl-empty">Nothing closed in the last 7 days.</div>
  {% endif %}
</div>
</div>{# /bs-panel-closed #}

{# ── SOURCE DRAWER ── #}
<div id="source-drawer">
  <div id="source-drawer-header">
    <span id="source-drawer-title">Source</span>
    <button class="ta-btn ta-btn-secondary ta-btn-sm" onclick="closeSourceDrawer()">✕ Close</button>
  </div>
  <div id="source-drawer-body">
    <pre id="source-drawer-content"></pre>
  </div>
</div>
"""

_PREP_BODY = """\
{% if flash %}<div class="ta-flash ta-flash-{{ flash_type }}">{{ flash }}</div>{% endif %}

<div class="ta-card" id="meeting-prep-card">
  <h2>Meeting Prep</h2>
  <p class="ta-muted" style="font-size:12px;margin-bottom:14px">Select a program to generate a pre-meeting brief from its recent signals and open act items.</p>
  <div class="ta-prep-controls">
    <select id="prep-program" name="program">
      <option value="">Select project…</option>
      {% set ns = namespace(cur_group=None) %}
      {% for p in programs %}
        {% if p.group != ns.cur_group %}
          {% if ns.cur_group is not none %}</optgroup>{% endif %}
          {% set ns.cur_group = p.group %}
          {% if p.group == 'projects' %}<optgroup label="── Projects ──">
          {% elif p.group == 'presales' %}<optgroup label="── Presales ──">
          {% else %}<optgroup label="── Watch ──">
          {% endif %}
        {% endif %}
      <option value="{{ p.key }}">{{ p.label }}{% if p.last_updated %} ({{ p.last_updated }}){% endif %}{% if p.stale_warn %} ⚠{% endif %}</option>
      {% endfor %}
      {% if ns.cur_group is not none %}</optgroup>{% endif %}
    </select>
    <input type="text" id="prep-context" placeholder="Meeting topic or agenda (optional)">
    <button class="ta-btn ta-btn-secondary" id="prep-refresh-btn" onclick="refreshSignals()" title="Refresh project signals from analyzed files">↻ Refresh signals</button>
    <button class="ta-btn ta-btn-secondary" id="prep-submit" onclick="runMeetingPrep()">Prep</button>
  </div>
  <div id="prep-output" style="display:none"></div>
</div>
"""

_SLACK_PASTE_BODY = """\
{% if flash %}<div class="ta-flash ta-flash-{{ flash_type }}">{{ flash }}</div>{% endif %}

<div class="ta-card">
  <h2>Paste Slack canvas</h2>
  <p class="ta-muted" style="margin-bottom:16px;font-size:12px">
    Paste your Slack canvas (markdown) below. It will be saved as a <code>.md</code>
    file directly into the <strong>Call Transcripts</strong> inbox so the next
    <em>Run Analysis</em> picks it up.
  </p>

  <form method="post" action="/slack-paste">
    <div class="ta-field" style="margin-bottom:12px">
      <label>Filename (no extension)</label>
      <input type="text" name="filename" value="{{ default_filename }}"
             placeholder="e.g. 2026-10-12 Slack Canvas"
             style="max-width:420px">
      <div class="desc">Saved as <code>{{ inbox_path }}/&lt;filename&gt;.md</code></div>
    </div>

    <div class="ta-field">
      <label>Canvas content</label>
      <textarea class="ta-paste-area" name="content" placeholder="Paste your Slack canvas markdown here…"></textarea>
    </div>

    <div style="margin-top:14px">
      <button type="submit" name="action" value="save">Save to inbox</button>
    </div>
  </form>
</div>
"""


def _last_pulse_info() -> dict:
    """Return freshness info for the most recent [DAILY PULSE] file.

    Returns dict with keys: label, hours, status (green/orange/red/gray),
    and analyses_since (count of [ANALYZED] files newer than the pulse).
    Business-day-aware coloring: green <1 bday, orange 1-2 bdays, red >2 bdays.
    """
    from datetime import datetime as _dt
    try:
        from .config import CONFIG
        analyzed_dir = CONFIG.analyzed_path
        pulse_mtime = None
        for fname in analyzed_dir.iterdir():
            if "[DAILY PULSE]" in fname.name and fname.is_file():
                mt = fname.stat().st_mtime
                if pulse_mtime is None or mt > pulse_mtime:
                    pulse_mtime = mt
        if pulse_mtime is None:
            return {"label": "Never run", "hours": None, "status": "gray", "analyses_since": 0}
        pulse_dt = _dt.fromtimestamp(pulse_mtime)
        label = pulse_dt.strftime("%b %-d, %-I:%M %p")
        hours_ago = (_dt.now() - pulse_dt).total_seconds() / 3600
        bdays = _business_days_since_dt(pulse_dt)
        if bdays == 0:
            status = "green"
        elif bdays <= 2:
            status = "orange"
        else:
            status = "red"
        analyses_since = 0
        for fname in analyzed_dir.iterdir():
            if "[ANALYZED]" in fname.name and fname.is_file():
                if fname.stat().st_mtime > pulse_mtime:
                    analyses_since += 1
        return {"label": label, "hours": round(hours_ago, 1), "status": status, "analyses_since": analyses_since}
    except Exception:
        return {"label": "Unknown", "hours": None, "status": "gray", "analyses_since": 0}


def _last_weekly_info() -> dict:
    """Return freshness info for the most recent [SLACK DELTA] or [WEEKLY SUMMARY] file."""
    from datetime import datetime as _dt
    try:
        from .config import CONFIG
        analyzed_dir = CONFIG.analyzed_path
        weekly_mtime = None
        for fname in analyzed_dir.iterdir():
            if ("[SLACK DELTA]" in fname.name or "[WEEKLY SUMMARY]" in fname.name) and fname.is_file():
                mt = fname.stat().st_mtime
                if weekly_mtime is None or mt > weekly_mtime:
                    weekly_mtime = mt
        if weekly_mtime is None:
            return {"label": None, "days": None, "bdays": None}
        weekly_dt = _dt.fromtimestamp(weekly_mtime)
        label = weekly_dt.strftime("%a %b %-d")
        days = (_dt.now() - weekly_dt).total_seconds() / 86400
        bdays = _business_days_since_dt(weekly_dt)
        return {"label": label, "days": round(days, 1), "bdays": bdays}
    except Exception:
        return {"label": None, "days": None, "bdays": None}


def _business_days_since_dt(dt) -> int:
    """Count business days (Mon-Fri) between dt and now."""
    from datetime import datetime as _dt, date as _date
    today = _date.today()
    start = dt.date() if hasattr(dt, "date") else dt
    if start >= today:
        return 0
    count = 0
    current = start
    from datetime import timedelta as _td
    while current < today:
        current += _td(days=1)
        if current.weekday() < 5:
            count += 1
    return count


def _get_programs() -> list[dict]:
    """Scan SiemensRCAGov for STATUS.md files, return sorted list of {key, label, last_updated}."""
    import re
    gov_root = Path("~/code/SiemensRCAGov").expanduser()
    programs = []
    for status_path in gov_root.rglob("STATUS.md"):
        try:
            text = status_path.read_text(encoding="utf-8")
            fm = {}
            if text.startswith("---"):
                end = text.find("---", 3)
                if end > 0:
                    for line in text[3:end].splitlines():
                        if ":" in line:
                            k, _, v = line.partition(":")
                            fm[k.strip()] = v.strip().strip('"')
            key = fm.get("program", "")
            label = fm.get("label", key)
            last_updated = fm.get("last_updated", "")
            if key:
                stale_warn = False
                if last_updated:
                    try:
                        from datetime import date as _date2
                        lu = _date2.fromisoformat(last_updated)
                        bdays_old = _business_days_since_dt(lu)
                        stale_warn = bdays_old > 3
                    except Exception:
                        pass
                _WATCH_KEYS = {"siemens-general", "presales", "DISW-Billing", "SI-Agentforce", "SI-CLM", "SX-PRD", "XMP"}
                _PRESALES_PREFIX = "presales-"
                if key in _WATCH_KEYS:
                    group = "watch"
                elif key.startswith(_PRESALES_PREFIX):
                    group = "presales"
                else:
                    group = "projects"
                programs.append({
                    "key": key,
                    "label": label,
                    "last_updated": last_updated,
                    "path": str(status_path),
                    "stale_warn": stale_warn,
                    "group": group,
                })
        except Exception:
            continue
    prog_order = _load_prog_order()
    order_index = {key: i for i, key in enumerate(prog_order)}
    programs.sort(key=lambda p: order_index.get(p["key"], len(prog_order)))
    return programs


_PROG_ORDER_PATH = Path("~/.config/transcript-analyzer/prog_order.json").expanduser()
_DEFAULT_PROG_ORDER = [
    "SI-RCA", "DISW-RCA", "DISW-CPQ", "siemens-general",
    "presales-sx-prd", "presales-disw-spm", "presales-siesales-cpqplus",
    "presales-energy", "presales-healthineers", "presales-financial-services",
    "_unassigned",
]


def _merged_prog_order() -> list[str]:
    """Return prog_order pruned to only known programs, with any new ones appended."""
    discovered = {p["key"] for p in _get_programs()}
    saved = [k for k in _load_prog_order() if k in discovered or k == "_unassigned"]
    new = [k for k in discovered if k not in set(saved)]
    return saved + new


def _load_prog_order() -> list[str]:
    try:
        if _PROG_ORDER_PATH.exists():
            return json.loads(_PROG_ORDER_PATH.read_text())
    except Exception:
        pass
    return list(_DEFAULT_PROG_ORDER)


def _save_prog_order(order: list[str]) -> None:
    _PROG_ORDER_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PROG_ORDER_PATH.write_text(json.dumps(order))


def _pending_act_count() -> int:
    try:
        from . import brief_state as _bs
        return len(_bs.pending_acts()) + len(_bs.pending_candidates())
    except Exception:
        return 0


def _last_pipeline_run_hours() -> float | None:
    """Return hours since the last pipeline run, parsed from the log's ----- timestamps.

    Returns None if the log is absent or has no timestamp lines.
    """
    from datetime import datetime, timezone
    import re
    if not _LOG_PATH.exists():
        return None
    try:
        # Read the tail of the log efficiently — timestamps are on ----- lines.
        text = _LOG_PATH.read_text(errors="replace")
        # Match lines like: ----- 2026-10-21T10:59:47-04:00 -----
        matches = re.findall(r"^----- (\S+) -----", text, re.MULTILINE)
        if not matches:
            return None
        last_ts = matches[-1]
        dt = datetime.fromisoformat(last_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    except Exception:
        return None


def _collect_pipeline_warnings() -> list[str]:
    """Return a list of short warning strings about pipeline health.

    Called once per page render; results cached in _last_render_warnings so
    the chip in the header stays consistent across navigations without
    re-running on each subrequest.
    """
    global _last_render_warnings
    warnings: list[str] = []
    try:
        hours = _last_pipeline_run_hours()
        if hours is None:
            # Can't determine last run — fall back to last-analyzed-file age.
            rows = _analyzed_files()
            _, stale = _last_analyzed_at(rows)
            if stale == "danger":
                warnings.append("pipeline stale (>24 h since last analysis)")
            elif stale == "warn":
                warnings.append("pipeline stale (>8 h since last analysis)")
        elif hours > 24:
            warnings.append(f"pipeline stale — last run {hours:.0f} h ago")
        elif hours > 8:
            warnings.append(f"pipeline stale — last run {hours:.0f} h ago")
        # hours <= 8: pipeline is healthy, no warning
    except Exception:
        pass
    _last_render_warnings = warnings
    return warnings


def _render(template: str, **ctx) -> str:
    import json
    from jinja2 import Environment
    env = Environment(autoescape=True)
    env.filters["tojson"] = json.dumps
    # Cap flash from URL params so a crafted link can't break layout.
    if "flash" in ctx and ctx["flash"]:
        ctx["flash"] = str(ctx["flash"])[:200]
    wrapper = env.from_string(_HTML)
    from markupsafe import Markup
    body_tmpl = env.from_string(template)
    body = Markup(body_tmpl.render(**ctx))
    ctx.setdefault("pending_act_count", _pending_act_count())
    ctx.setdefault("render_warnings", _collect_pipeline_warnings())
    ctx.setdefault("page_title", _PAGE_TITLES.get(ctx.get("page", ""), "Transcript Analyzer"))
    return wrapper.render(body=body, shared_url=_shared_folder_url(), **ctx)


def _meeting_prep_brief(program_key: str, context_text: str, programs: list[dict]) -> dict:
    """Generate a meeting prep brief for the given program. Returns {bullets, program_label}."""
    try:
        from . import brief_state as _bs
        from . import claude_cli as _cli

        prog = next((p for p in programs if p["key"] == program_key), None)
        if prog is None:
            return {"bullets": ["Program not found."], "program_label": program_key}

        status_path = Path(prog["path"])
        status_text = status_path.read_text(encoding="utf-8")

        # Extract the last 2 "## Open signals" blocks
        open_signals_section = ""
        blocks = status_text.split("## Open signals")
        if len(blocks) > 1:
            last_two = "## Open signals".join(blocks[-2:]) if len(blocks) >= 3 else blocks[-1]
            open_signals_section = "## Open signals" + last_two
        else:
            open_signals_section = status_text[-3000:]

        # Get pending act items for this program
        by_prog = _bs.pending_by_program()
        prog_acts = by_prog.get(program_key, [])
        acts_text = "\n".join(f"- {i['text']}" for i in prog_acts) if prog_acts else "(none)"

        context_line = f"\nMeeting context: {context_text.strip()}" if context_text.strip() else ""

        prompt = f"""You are preparing Brad Gross (Revenue Cloud CTO) for a meeting about {prog['label']}.{context_line}

## Program status (STATUS.md — recent signals):
{open_signals_section[:2000]}

## Open act items for this program:
{acts_text}

Return exactly 3-5 bullet points. Each bullet: one concrete thing Brad should know or do in this meeting. No preamble, no hedging, no padding. Start each bullet with a **bold keyword**."""

        from .config import CONFIG as _cfg
        model = _cfg.models.get("STANDUP", "claude-sonnet-4-6")
        data = _cli.run_claude_p(prompt, model=model)
        raw_text = _cli.result_text(data)
        lines = [l.strip() for l in raw_text.strip().splitlines() if l.strip().startswith(("-", "•", "*"))]
        if not lines:
            lines = [l.strip() for l in raw_text.strip().splitlines() if l.strip()]
        return {"bullets": lines[:5], "program_label": prog["label"]}
    except Exception as exc:
        return {"bullets": [f"Error generating brief: {exc}"], "program_label": program_key}


def create_app():
    from flask import Flask, redirect, request, url_for
    from .config import CONFIG

    app = Flask(__name__)
    app.secret_key = "ta-ui-local-only"

    @app.get("/")
    def home():
        return redirect(url_for("brief_state_page"))

    @app.get("/files")
    def files_page():
        from .config import CONFIG
        rows = _analyzed_files()
        flash = request.args.get("flash", "")
        flash_type = request.args.get("ft", "ok")
        with _job_lock:
            job_running = bool(_job and not _job["done"])
            job_label = _MODE_LABELS.get(_job["mode"], "job") if _job else ""
            job_output = _job["output"] if _job else ""
            job_done = _job["done"] if _job else True

        _last_ts, _last_stale = _last_analyzed_at(rows)
        return _render(
            _HOME_BODY,
            page="home",
            rows=rows,
            programs=_get_programs(),
            total_cost=_total_cost(rows),
            last_analyzed=_last_ts,
            last_analyzed_stale=_last_stale,
            analyzed_path=str(CONFIG.analyzed_path),
            analyzed_path_url=_analyzed_path_url(),
            flash=flash,
            flash_type=flash_type,
            job_running=job_running,
            job_label=job_label,
            job_output=job_output,
            job_done=job_done,
        )

    @app.post("/files/set-program")
    def files_set_program():
        output = request.form.get("output", "").strip()
        program = request.form.get("program", "").strip()
        if not output:
            return redirect(url_for("files_page"))

        _save_program_override(output, program)

        # Cascade: re-assign any brief-state items sourced from this file
        cascaded = 0
        try:
            from . import brief_state as _bs
            items = _bs._load()
            for item in items:
                if item.get("source_analyzed") == output:
                    item["program"] = program or None
                    cascaded += 1
            if cascaded:
                _bs._save(items)
        except Exception:
            pass

        if cascaded:
            flash_msg = f"Project set to '{program}' — {cascaded} action item(s) updated"
        else:
            flash_msg = f"Project set to '{program}'" if program else "Project assignment cleared"
        return redirect(url_for("files_page", flash=flash_msg, ft="ok"))

    @app.post("/files/bulk-classify")
    def files_bulk_classify():
        import json as _json
        import os as _os
        from .program_pulse import classify_program as _classify
        from .manifest import load as _load_manifest

        manifest = _load_manifest()
        overrides = _load_program_overrides()
        count = 0
        for src, entry in manifest.items():
            if src.startswith("plaud:"):
                continue
            output = entry.get("output_filename", "")
            if not output or output in overrides:
                continue
            fpath = CONFIG.analyzed_path / output
            if not fpath.exists():
                archive_root = CONFIG.analyzed_path / "_Archive"
                for _dir, _, _files in _os.walk(archive_root):
                    if output in _files:
                        fpath = Path(_dir) / output
                        break
            if not fpath.exists():
                continue
            try:
                prog = _classify(fpath)
            except Exception:
                continue
            if prog and prog != "_admin":
                overrides[output] = prog
                count += 1

        _PROGRAM_OVERRIDES_PATH.write_text(_json.dumps(overrides, indent=2))
        return {"count": count, "message": f"Classified {count} file(s)"}

    @app.post("/synthesize")
    def synthesize():
        global _job
        from datetime import datetime as _dt
        mode = request.form.get("mode", "daily")
        if mode not in ("daily", "weekly", "career", "catch-up", "timecard", "program-pulse"):
            return redirect(url_for("run_page", flash="Invalid mode", ft="err"))

        # Optional back-date for daily/weekly/timecard (recover a missed run). Ignored for career.
        date_arg = request.form.get("date", "").strip()
        if date_arg and mode in ("daily", "weekly", "timecard"):
            try:
                _dt.strptime(date_arg, "%Y-%m-%d")
            except ValueError:
                return redirect(url_for("run_page", flash=f"Invalid date {date_arg!r} (need YYYY-MM-DD)", ft="err"))
        else:
            date_arg = ""

        # catch-up only: optional lower bound on which backlog days to synthesize.
        since_arg = request.form.get("since", "").strip()
        if since_arg and mode == "catch-up":
            try:
                _dt.strptime(since_arg, "%Y-%m-%d")
            except ValueError:
                return redirect(url_for("run_page", flash=f"Invalid since-date {since_arg!r} (need YYYY-MM-DD)", ft="err"))
        else:
            since_arg = ""

        # career only: re-baseline by re-reading all analyses (default is incremental).
        full = bool(request.form.get("full")) and mode == "career"

        with _job_lock:
            if _job and not _job["done"]:
                return redirect(url_for("run_page", flash="A synthesis job is already running", ft="err"))
            _job = {"mode": mode, "output": "", "done": False, "returncode": None, "new_count": 0}

        cmd = [sys.executable, "-u", "-m", "analyzer", "synthesize", "--mode", mode]
        if date_arg:
            cmd += ["--date", date_arg]
        if since_arg:
            cmd += ["--since", since_arg]
        if full:
            cmd.append("--full")
        threading.Thread(target=_run_job, args=(cmd, mode), daemon=True).start()
        return redirect(url_for("run_page"))

    @app.post("/run-analysis")
    def run_analysis():
        global _job
        with _job_lock:
            if _job and not _job["done"]:
                return redirect(url_for("run_page", flash="A job is already running", ft="err"))
            _job = {"mode": "analysis", "output": "", "done": False, "returncode": None, "new_count": 0}

        cmd = [sys.executable, "-u", "-m", "analyzer"]
        threading.Thread(target=_run_job, args=(cmd, "analysis"), daemon=True).start()
        return redirect(url_for("run_page"))

    @app.get("/job-status")
    def job_status():
        from flask import jsonify
        with _job_lock:
            if _job is None:
                return jsonify({"done": True, "output": "", "returncode": None})
            return jsonify({
                "done": _job["done"],
                "output": _job["output"],
                "returncode": _job["returncode"],
                "new_count": _job.get("new_count", 0),
                # "proc" intentionally excluded — Popen is not JSON-serializable
            })

    @app.get("/settings")
    def settings():
        vals = _load_env_file()
        drive_files = [
            ("PromptLibrary.md", str(CONFIG.prompt_library_path)),
            ("Program_Context_Brief.md", str(CONFIG.context_brief_path)),
            ("People Rolodex (04_people_rolodex.md)", str(CONFIG.rolodex_path)),
            ("Term Glossary (05_vocabulary.md)", str(CONFIG.vocabulary_path)),
        ]
        return _render(
            _SETTINGS_BODY,
            page="settings",
            fields=_SETTINGS_FIELDS,
            vals=vals,
            drive_files=drive_files,
            log_path=str(_LOG_PATH),
            prog_order=_merged_prog_order(),
            flash=request.args.get("flash", ""),
            flash_type=request.args.get("ft", "ok"),
        )

    @app.post("/settings")
    def save_settings():
        for key, _label, kind, _desc in _SETTINGS_FIELDS:
            if kind == "toggle":
                value = "true" if request.form.get(key) else "false"
            else:
                value = request.form.get(key, "").strip()
            if value:
                _save_env_key(key, value)
            elif kind != "toggle":
                # Blank text field: leave the existing line untouched rather
                # than writing an empty value — accidental clear is destructive.
                pass
        return redirect(url_for("settings", flash="Settings saved.", ft="ok"))

    @app.get("/open-file")
    def open_file():
        path = request.args.get("path", "")
        if path:
            subprocess.run(["open", path], check=False)
        return redirect(url_for("settings", flash=f"Opened {Path(path).name}", ft="ok"))

    @app.get("/open-log")
    def open_log():
        subprocess.run(["open", str(_LOG_PATH)], check=False)
        return redirect(url_for("settings", flash="Opened run log", ft="ok"))

    @app.route("/slack-paste", methods=["GET", "POST"])
    def slack_paste():
        global _job
        from datetime import date as _date

        if request.method == "GET":
            default_filename = _date.today().isoformat() + " Slack Canvas"
            return _render(
                _SLACK_PASTE_BODY,
                page="slack-paste",
                default_filename=default_filename,
                inbox_path=str(CONFIG.call_transcripts_path),
                flash=request.args.get("flash", ""),
                flash_type=request.args.get("ft", "ok"),
            )

        # POST — save the pasted content
        raw_filename = request.form.get("filename", "").strip()
        content = request.form.get("content", "")

        if not raw_filename:
            raw_filename = _date.today().isoformat() + " Slack Canvas"

        # Sanitize: strip path separators, null bytes, cap length, ensure .md extension.
        safe_name = raw_filename.replace("/", "-").replace("\\", "-").replace("\x00", "")
        safe_name = safe_name[:120]  # prevent absurdly long filenames
        if not safe_name.endswith(".md"):
            safe_name += ".md"

        dest = CONFIG.call_transcripts_path / safe_name
        if not content.strip():
            return redirect(url_for(
                "slack_paste",
                flash="Content was empty — nothing saved.",
                ft="err",
            ))

        try:
            dest.write_text(content, encoding="utf-8")
        except Exception as exc:
            return redirect(url_for(
                "slack_paste",
                flash=f"Write failed: {exc}",
                ft="err",
            ))

        return redirect(url_for(
            "slack_paste",
            flash=f"Saved to inbox: {safe_name}",
            ft="ok",
        ))

    @app.post("/files/backfill-program")
    def files_backfill_program():
        global _job
        program = request.form.get("program", "").strip()
        if not program:
            return redirect(url_for("settings", flash="No program specified", ft="err"))
        with _job_lock:
            if _job and not _job["done"]:
                return redirect(url_for("settings", flash="A job is already running", ft="err"))
            _job = {"mode": "program-pulse", "output": "", "done": False, "returncode": None, "new_count": 0}
        cmd = [
            sys.executable, "-u", "-m", "analyzer", "synthesize",
            "--mode", "program-pulse", "--backfill", "--programs", program,
        ]
        threading.Thread(target=_run_job, args=(cmd, "program-pulse"), daemon=True).start()
        return redirect(url_for("run_page", flash=f"Backfill started for {program}", ft="ok"))

    @app.get("/run")
    def run_page():
        with _job_lock:
            job_running = bool(_job and not _job["done"])
            job_label = _MODE_LABELS.get(_job["mode"], "job") if _job else ""
            job_output = _job["output"] if _job else ""
        return _render(
            _RUN_BODY,
            page="run",
            flash=request.args.get("flash", ""),
            flash_type=request.args.get("ft", "ok"),
            job_running=job_running,
            job_label=job_label,
            job_output=job_output,
            last_weekly=_last_weekly_info(),
        )

    @app.get("/brief-state")
    def brief_state_page():
        from . import brief_state as _bs
        from datetime import date as _date
        today = _date.today()

        def _enrich_act(item):
            item = dict(item)
            dl = item.get("deadline")
            if dl:
                d = _date.fromisoformat(dl)
                item["overdue"] = d < today
                item["days_overdue"] = (today - d).days if d < today else 0
            else:
                item["overdue"] = False
                item["days_overdue"] = 0
            return item

        def _enrich_they_owe(item):
            item = dict(item)
            dl = item.get("deadline")
            if dl:
                d = _date.fromisoformat(dl)
                item["they_overdue"] = d < today
                item["days_overdue"] = (today - d).days if d < today else 0
            else:
                item["they_overdue"] = False
                item["days_overdue"] = 0
            return item

        # Auto-dismiss inbox candidates that duplicate already-promoted items
        _bs.dedupe_candidates()

        all_items = _bs.all_items()
        from datetime import timedelta
        week_ago = (today - timedelta(days=7)).isoformat()
        recent_closed = [
            i for i in all_items
            if i.get("status") in ("complete", "dropped", "expired")
            and (i.get("closed_at") or "") >= week_ago
        ]
        recent_closed.sort(key=lambda i: i.get("closed_at", ""), reverse=True)

        with _job_lock:
            job_running = bool(_job and not _job["done"])
            job_label = _MODE_LABELS.get(_job["mode"], "job") if _job else ""
            job_output = _job["output"] if _job else ""

        def _sort_acts(items):
            def _key(i):
                overdue = i.get("overdue", False)
                dl = i.get("deadline") or ""
                pos = i.get("position") if i.get("position") is not None else 9999
                return (0 if overdue else 1, dl or "9999-99-99", pos)
            return sorted(items, key=_key)

        raw_acts = [_enrich_act(i) for i in _bs.pending_acts()]
        pinned = [_enrich_act(i) for i in _bs.pinned_acts()]
        pinned_ids = {i["id"] for i in pinned}
        unpinned_acts = [i for i in _sort_acts(raw_acts) if i["id"] not in pinned_ids]

        # Group unpinned acts by program; null → "_unassigned"
        prog_order = _load_prog_order()
        _prog_labels = {p["key"]: p["label"] for p in _get_programs()}

        def _group_by_program(items, row_key="rows"):
            by_prog: dict[str, list] = {}
            for item in items:
                k = item.get("program") or "_unassigned"
                by_prog.setdefault(k, []).append(item)

            def _label(k):
                if k == "_unassigned":
                    return "Unassigned"
                return _prog_labels.get(k, k)

            groups = [
                {"key": k, "label": _label(k), row_key: by_prog[k]}
                for k in prog_order if k in by_prog
            ]
            for k, grp_items in by_prog.items():
                if k not in prog_order:
                    groups.append({"key": k, "label": _label(k), row_key: grp_items})
            return groups

        act_groups = _group_by_program(unpinned_acts, "acts")
        watch_groups = _group_by_program(_bs.pending_watches(), "rows")
        delegate_items = [i for i in all_items if i.get("type") == "delegate" and i.get("status") == "pending"]
        delegate_groups = _group_by_program(delegate_items, "rows")
        they_owe_list = [_enrich_they_owe(i) for i in _bs.pending_they_owe()]
        they_owe_groups = _group_by_program(they_owe_list, "rows")
        closed_groups = _group_by_program(recent_closed, "rows")
        inbox_groups = _group_by_program(_bs.pending_candidates(), "rows")

        return _render(
            _BRIEF_STATE_BODY,
            page="brief-state",
            today_iso=today.isoformat(),
            candidate_items=_bs.pending_candidates(),
            act_items=_sort_acts(raw_acts),       # kept for overdue banner
            act_groups=act_groups,
            pinned_acts=pinned,
            watch_items=_bs.pending_watches(),
            watch_groups=watch_groups,
            delegate_items=delegate_items,
            delegate_groups=delegate_groups,
            they_owe_items=they_owe_list,
            they_owe_groups=they_owe_groups,
            recent_closed=recent_closed,
            closed_groups=closed_groups,
            inbox_groups=inbox_groups,
            programs=_get_programs(),
            prog_order=prog_order,
            flash=request.args.get("flash", ""),
            flash_type=request.args.get("ft", "ok"),
            job_running=job_running,
            job_label=job_label,
            job_output=job_output,
            last_pulse=_last_pulse_info(),
            last_weekly=_last_weekly_info(),
            analyzed_path=str(CONFIG.analyzed_path),
        )

    @app.post("/brief-state/accept/<item_id>")
    def brief_state_accept(item_id):
        from . import brief_state as _bs
        item_type = request.form.get("type", "act")
        program = request.form.get("program") or None
        try:
            found = _bs.accept_candidate(item_id, item_type)
        except ValueError as e:
            return redirect(url_for("brief_state_page", flash=str(e), ft="err") + "#inbox")
        if found and program:
            _bs.set_program(item_id, program)
        msg = f"Accepted as {item_type}" if found else "Item not found"
        ft = "ok" if found else "err"
        return redirect(url_for("brief_state_page", flash=msg, ft=ft) + "#inbox")

    @app.post("/brief-state/dismiss-all-candidates")
    def brief_state_dismiss_all_candidates():
        from . import brief_state as _bs
        candidates = _bs.pending_candidates()
        for item in candidates:
            _bs.dismiss_candidate(item["id"])
        n = len(candidates)
        msg = f"Dismissed {n} candidate{'s' if n != 1 else ''}" if n else "No candidates to dismiss"
        return redirect(url_for("brief_state_page", flash=msg, ft="ok") + "#inbox")

    @app.post("/brief-state/dismiss-candidate/<item_id>")
    def brief_state_dismiss_candidate(item_id):
        from . import brief_state as _bs
        found = _bs.dismiss_candidate(item_id)
        msg = "Dismissed" if found else "Item not found"
        ft = "ok" if found else "err"
        return redirect(url_for("brief_state_page", flash=msg, ft=ft) + "#inbox")

    @app.post("/brief-state/close/<item_id>")
    def brief_state_close(item_id):
        from . import brief_state as _bs
        status = request.form.get("status", "complete")
        if status not in ("complete", "dropped", "expired"):
            return redirect(url_for("brief_state_page", flash="Invalid status", ft="err"))
        found = _bs.close(item_id, status)
        msg = "Marked " + status if found else "Item not found"
        ft = "ok" if found else "err"
        anchor = request.form.get("return_to", "")
        dest = url_for("brief_state_page", flash=msg, ft=ft)
        if anchor:
            dest += f"#{anchor}"
        return redirect(dest)

    @app.post("/brief-state/reopen/<item_id>")
    def brief_state_reopen(item_id):
        from . import brief_state as _bs
        found = _bs.reopen(item_id)
        msg = "Reopened" if found else "Item not found"
        ft = "ok" if found else "err"
        return redirect(url_for("brief_state_page", flash=msg, ft=ft))

    @app.post("/brief-state/defer/<item_id>")
    def brief_state_defer(item_id):
        from . import brief_state as _bs
        from datetime import datetime as _dt
        target = request.form.get("target", "").strip()
        try:
            _dt.strptime(target, "%Y-%m-%d")
        except ValueError:
            return redirect(url_for("brief_state_page", flash="Invalid defer date", ft="err"))
        found = _bs.defer(item_id, target)
        msg = f"Deferred to {target}" if found else "Item not found"
        ft = "ok" if found else "err"
        anchor = request.form.get("return_to", "")
        dest = url_for("brief_state_page", flash=msg, ft=ft)
        return redirect(dest + f"#{anchor}" if anchor else dest)

    @app.post("/brief-state/pin/<item_id>")
    def brief_state_pin(item_id):
        from . import brief_state as _bs
        found = _bs.pin(item_id)
        msg = "Pinned to Today's Focus" if found else "Item not found"
        ft = "ok" if found else "err"
        anchor = request.form.get("return_to", "")
        dest = url_for("brief_state_page", flash=msg, ft=ft)
        return redirect(dest + f"#{anchor}" if anchor else dest)

    @app.post("/brief-state/unpin/<item_id>")
    def brief_state_unpin(item_id):
        from . import brief_state as _bs
        found = _bs.unpin(item_id)
        msg = "Unpinned" if found else "Item not found"
        ft = "ok" if found else "err"
        anchor = request.form.get("return_to", "")
        dest = url_for("brief_state_page", flash=msg, ft=ft)
        return redirect(dest + f"#{anchor}" if anchor else dest)

    @app.post("/brief-state/loe/<item_id>")
    def brief_state_loe(item_id):
        from . import brief_state as _bs
        loe = request.form.get("loe") or None
        try:
            found = _bs.set_loe(item_id, loe)
        except ValueError as e:
            return redirect(url_for("brief_state_page", flash=str(e), ft="err"))
        msg = f"LoE set to {loe}" if (found and loe) else ("LoE cleared" if found else "Item not found")
        ft = "ok" if found else "err"
        anchor = request.form.get("return_to", "")
        dest = url_for("brief_state_page", flash=msg, ft=ft)
        return redirect(dest + f"#{anchor}" if anchor else dest)

    @app.post("/brief-state/set-owner/<item_id>")
    def brief_state_set_owner(item_id):
        from . import brief_state as _bs
        owner = (request.form.get("owner") or "").strip() or None
        all_items = _bs.all_items()
        target = next((i for i in all_items if i["id"] == item_id), None)
        if target is None:
            return redirect(url_for("brief_state_page", flash="Item not found", ft="err"))
        updated = dict(target)
        updated["owner"] = owner or "Brad"
        _bs.upsert(updated)
        anchor = request.form.get("return_to", "")
        dest = url_for("brief_state_page", flash=f"Owner set to {updated['owner']}", ft="ok")
        return redirect(dest + f"#{anchor}" if anchor else dest)

    @app.post("/brief-state/re-own/<item_id>")
    def brief_state_re_own(item_id):
        from . import brief_state as _bs
        all_items = _bs.all_items()
        target = next((i for i in all_items if i["id"] == item_id), None)
        if target is None:
            return redirect(url_for("brief_state_page", flash="Item not found", ft="err"))
        updated = dict(target)
        updated["type"] = "act"
        updated["owner"] = "Brad"
        updated["notes"] = ((updated.get("notes") or "") + "; re-owned via UI").lstrip("; ")
        _bs.upsert(updated)
        anchor = request.form.get("return_to", "")
        dest = url_for("brief_state_page", flash="Re-owned", ft="ok")
        return redirect(dest + f"#{anchor}" if anchor else dest)

    @app.post("/brief-state/close-group")
    def brief_state_close_group():
        from . import brief_state as _bs
        from flask import jsonify
        ids = request.form.getlist("ids")
        status = request.form.get("status", "complete")
        if status not in ("complete", "dropped"):
            return redirect(url_for("brief_state_page", flash="Invalid status", ft="err"))
        count = 0
        for item_id in ids:
            if _bs.close(item_id, status):
                count += 1
        anchor = request.form.get("anchor", "")
        msg = f"{count} item{'s' if count != 1 else ''} marked {status}"
        dest = url_for("brief_state_page", flash=msg, ft="ok")
        if anchor:
            dest += f"#{anchor}"
        return redirect(dest)

    @app.post("/brief-state/prog-order")
    def brief_state_prog_order():
        from flask import jsonify
        data = request.get_json(silent=True) or {}
        order = data.get("order", [])
        if not isinstance(order, list):
            return jsonify({"ok": False}), 400
        _save_prog_order(order)
        return jsonify({"ok": True})

    @app.post("/brief-state/assign-program/<item_id>")
    def brief_state_assign_program(item_id):
        from . import brief_state as _bs
        program = request.form.get("program") or None
        found = _bs.set_program(item_id, program)
        msg = f"Program set to {program}" if (found and program) else ("Program cleared" if found else "Item not found")
        ft = "ok" if found else "err"
        anchor = request.form.get("return_to", "")
        dest = url_for("brief_state_page", flash=msg, ft=ft)
        return redirect(dest + f"#{anchor}" if anchor else dest)

    @app.post("/brief-state/reorder")
    def brief_state_reorder():
        from . import brief_state as _bs
        from flask import jsonify
        data = request.get_json(silent=True) or {}
        ordered_ids = data.get("ids", [])
        if not isinstance(ordered_ids, list):
            return jsonify({"ok": False, "error": "ids must be a list"}), 400
        _bs.reorder_acts(ordered_ids)
        return jsonify({"ok": True})

    @app.get("/source/<item_id>")
    def source_view(item_id):
        from flask import jsonify
        from . import brief_state as _bs
        all_items = _bs.all_items()
        item = next((i for i in all_items if i["id"] == item_id), None)
        if item is None:
            return jsonify({"text": "Item not found."}), 404
        source_pulse = item.get("source_pulse", "")
        if not source_pulse:
            return jsonify({"text": "(No source file linked to this item.)"}), 200
        try:
            analyzed_dir = CONFIG.analyzed_path
            source_path = analyzed_dir / source_pulse
            if not source_path.exists():
                # Try partial match
                candidates = [f for f in analyzed_dir.iterdir() if source_pulse in f.name]
                if candidates:
                    source_path = candidates[0]
                else:
                    return jsonify({"text": f"Source file not found: {source_pulse}"}), 200
            text = source_path.read_text(encoding="utf-8", errors="replace")
            return jsonify({"text": text[:50000]})
        except Exception as exc:
            return jsonify({"text": f"Error reading source: {exc}"}), 200

    @app.get("/prep")
    def prep_page():
        return _render(
            _PREP_BODY,
            page="prep",
            programs=_get_programs(),
            flash=request.args.get("flash", ""),
            flash_type=request.args.get("ft", "ok"),
        )

    @app.post("/meeting-prep")
    def meeting_prep():
        from flask import jsonify, request as _req
        import json as _json
        data = _req.get_json(force=True, silent=True) or {}
        program_key = str(data.get("program", "")).strip()
        context_text = str(data.get("context", "")).strip()[:500]

        programs = _get_programs()
        valid_keys = {p["key"] for p in programs}
        if program_key not in valid_keys:
            return jsonify({"bullets": ["Invalid program key."], "program_label": ""}), 400

        result = _meeting_prep_brief(program_key, context_text, programs)
        return jsonify(result)

    @app.post("/restart")
    def restart():
        # If a job is running, terminate it before re-execing so the subprocess
        # doesn't run orphaned (billing Claude quota with no UI visibility).
        with _job_lock:
            if _job and not _job.get("done"):
                proc = _job.get("proc")
                if proc is not None:
                    try:
                        proc.terminate()
                        proc.wait(timeout=3)
                    except Exception:
                        pass
                else:
                    return redirect(url_for(
                        "settings",
                        flash="A job is running — wait for it to finish before restarting.",
                        ft="err",
                    ))

        # Re-exec this process in place (same interpreter + argv), which reloads
        # all module code and re-reads CONFIG/.env at import. os.execv replaces
        # the process, so the worker job lock above is the only safety needed.
        # Defer slightly so this HTTP response (the redirect + flash) is fully
        # sent before the process is replaced; the browser then reconnects to
        # the fresh server on its next request.
        def _reexec():
            # Spawn a fresh, detached replacement and exit this process. We do
            # NOT os.execv: that inherits the live listening socket FD, so the
            # new image fails to rebind the port ("Address already in use"). A
            # separate process + this one exiting releases the socket cleanly;
            # main() retries the bind to absorb the brief handoff overlap.
            time.sleep(0.5)
            subprocess.Popen(_REEXEC_ARGV, start_new_session=True,
                             cwd=str(Path(__file__).parent.parent))
            os._exit(0)

        threading.Thread(target=_reexec, daemon=True).start()
        return redirect(url_for(
            "settings",
            flash="Restarting server… reload the page in a moment.",
            ft="ok",
        ))

    return app


def main(port: int = 7070) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="python -m analyzer ui")
    parser.add_argument("--port", type=int, default=7070)
    parser.add_argument(
        "--no-open", action="store_true",
        help="Don't open a browser tab (used by the /restart self-relaunch).",
    )
    args = parser.parse_args()

    # Capture the canonical relaunch command for /restart. Rebuild the
    # `-m analyzer ui` form explicitly — sys.argv is unreliable here (see
    # _REEXEC_ARGV). --no-open so a restart doesn't spawn a duplicate browser tab.
    global _REEXEC_ARGV
    _REEXEC_ARGV = [sys.executable, "-m", "analyzer", "ui",
                    "--port", str(args.port), "--no-open"]

    app = create_app()
    url = f"http://localhost:{args.port}"
    print(f"Dashboard running at {url}")
    if not args.no_open:
        subprocess.run(["open", url], check=False)

    # Retry the bind briefly: on a /restart relaunch the old process may still
    # be releasing the port for a fraction of a second. Without this the fresh
    # server would die with "Address already in use" and the UI wouldn't return.
    last_err: OSError | None = None
    for attempt in range(10):
        try:
            app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False)
            return 0
        except OSError as e:
            last_err = e
            time.sleep(0.5)
    print(f"ERROR: could not bind port {args.port} after retries: {last_err}",
          file=sys.stderr)
    return 1
