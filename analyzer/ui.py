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
# The rolling log written by bin/analyze.sh — full run history.  (The older
# *-launchd.log only ever captured launchd's own stdout and went stale once
# analyze.sh started teeing into its own files.)
_LOG_PATH = Path("~/Library/Logs/transcript-analyzer.log").expanduser()

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
    manifest = load_manifest()
    rows = []
    for src, entry in sorted(manifest.items(), key=lambda x: x[1].get("analyzed_at", ""), reverse=True):
        if src.startswith("plaud:"):
            continue
        rows.append({
            "source": src,
            "output": entry.get("output_filename", ""),
            "shareable": entry.get("shareable_filename", ""),
            "category": entry.get("category") or entry.get("prompt_key", ""),
            "model": entry.get("model", ""),
            "cost": entry.get("cost_usd", 0.0),
            "analyzed_at": entry.get("analyzed_at", ""),
            "duration": entry.get("duration_seconds", 0),
            "mode": entry.get("mode", "transcript"),
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
<style>
  :root {
    --bg: #0f1117; --surface: #1a1d27; --border: #2a2d3a;
    --text: #e2e4ec; --muted: #7a7f96; --accent: #4f8ef7;
    --green: #3ecf8e; --orange: #f7a24f; --red: #f76f6f;
    --purple: #b08ef7; --cyan: #3ecfcf; --gold: #cfaf3e;
    /* badge backgrounds — tokenized so no raw hex bleeds into rules */
    --bg-exec:     #3a2020; --bg-solution: #1e2c3a; --bg-daily:   #1e3a2c;
    --bg-standup:  #2a2c1e; --bg-notes:    #2a1e3a; --bg-pulse:   #1e3020;
    --bg-act:      #3a2020; --bg-watch:    #2a2c1e; --bg-delegate:#1e2c3a;
    --bg-they-owe: #2a1e3a; --bg-insight:  #1e2c2c; --bg-overdue: #4a1010;
    --bg-deferred: #2a2010; --bg-complete: #1e3a1e; --bg-expired: #2a1010;
    --bg-alert:    #2a1a1a; --border-alert: #6b2f2f; --bg-alert-item: #3a2020;
    --bg-danger:   #3a1f1f; --border-danger: #5a2e2e;
    --bg-inbox:    #1e1c10; --border-inbox: #3a3010;
    --bg-job-run:  #1a2540;
    /* deep tints — dark variants for hover states and specialty surfaces */
    --bg-exec-deep:     #4a2828;
    --bg-daily-deep:    #1b3628;
    --bg-void-deep:     #1e1e1e;
    --bg-deferred-deep: #5a3e1f;
    --bg-watch-deep:    #1a3030;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font: 14px/1.5 "SF Pro Text", system-ui, sans-serif; }
  /* scrollbar */
  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-track { background: var(--bg); }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
  ::-webkit-scrollbar-thumb:hover { background: var(--muted); }
  /* selection */
  ::selection { background: color-mix(in srgb, var(--accent) 30%, transparent); color: var(--text); }

  header { background: var(--surface); border-bottom: 1px solid var(--border);
           padding: 12px 24px; display: flex; align-items: center; gap: 16px; position: sticky; top: 0; z-index: 10; }
  header h1 { font-size: 14px; font-weight: 600; }
  nav { display: flex; gap: 4px; }
  nav a { color: var(--muted); text-decoration: none; font-size: 13px; padding: 6px 10px; border-radius: 6px; transition: color .15s, background .15s; }
  nav a:hover, nav a.active { color: var(--text); background: var(--border); }

  .content { padding: 24px; max-width: 1400px; }

  /* ── Button system: 3 variants + sm modifier ── */
  .btn, button { display: inline-flex; align-items: center; gap: 6px; border-radius: 6px;
                 padding: 8px 16px; font-size: 13px; font-weight: 600; cursor: pointer;
                 text-decoration: none; border: none; transition: opacity .15s; white-space: nowrap; }
  .btn, button { background: var(--accent); color: #fff; }
  .btn:hover, button:hover { opacity: .85; }
  .btn-secondary, button.secondary { background: var(--surface); color: var(--text); border: 1px solid var(--border); }
  .btn-secondary:hover, button.secondary:hover { background: var(--border); opacity: 1; }
  .btn-destructive, button.danger { background: var(--bg-danger); color: var(--red); border: 1px solid var(--border-danger); }
  .btn-destructive:hover, button.danger:hover { background: var(--bg-exec-deep); opacity: 1; }
  .btn-sm { padding: 3px 8px; font-size: 11px; border-radius: 4px; }

  /* ── Focus rings (keyboard navigation) ── */
  .btn:focus-visible, button:focus-visible,
  nav a:focus-visible,
  .quick-link:focus-visible,
  details.bs-section summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  input[type=date]:focus-visible, input[type=text]:focus-visible,
  input[type=url]:focus-visible, textarea:focus-visible { outline: none; border-color: var(--accent); }

  /* ── Shared form chrome ── */
  input[type=date] { background: var(--bg); border: 1px solid var(--border); border-radius: 4px;
                     color: var(--text); font-size: 11px; padding: 3px 6px; }
  input[type=date]:focus { outline: none; border-color: var(--accent); }

  .flash { padding: 10px 16px; border-radius: 6px; margin-bottom: 16px; font-size: 13px; }
  .flash-ok  { background: var(--bg-daily-deep); color: var(--green); }
  .flash-err { background: var(--bg-danger); color: var(--red); }

  /* ── Stats ── */
  .stats { display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }
  .stat { background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
          padding: 14px 20px; min-width: 140px; }
  .stat .val { font-size: 22px; font-weight: 700; }
  .stat .lbl { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; margin-top: 2px; }

  /* ── Card ── */
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
          padding: 20px; margin-bottom: 20px; }
  .card h2 { font-size: 13px; font-weight: 600; text-transform: uppercase;
             letter-spacing: .06em; color: var(--muted); margin-bottom: 14px; }

  /* ── Action tiers ── */
  .action-tier { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .action-tier + .action-tier { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border); }
  .action-tier-label { font-size: 11px; font-weight: 700; text-transform: uppercase;
                       letter-spacing: .07em; color: var(--muted); margin-right: 4px; }
  .action-group { display: flex; align-items: center; gap: 6px; }
  .action-date-hint { font-size: 11px; color: var(--muted); }

  /* ── Table ── */
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; color: var(--muted); font-size: 11px; text-transform: uppercase;
       letter-spacing: .05em; padding: 6px 10px; border-bottom: 1px solid var(--border); }
  td { padding: 7px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
  .td-source { max-width: 380px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  tr:last-child td { border-bottom: none; }

  /* ── Badges ── */
  .badge { display: inline-block; padding: 2px 7px; border-radius: 4px; font-size: 11px;
           font-weight: 600; text-transform: uppercase; letter-spacing: .04em; }
  .badge-exec      { background: var(--bg-exec);     color: var(--red); }
  .badge-solution  { background: var(--bg-solution); color: var(--accent); }
  .badge-daily     { background: var(--bg-daily);    color: var(--green); }
  .badge-standup   { background: var(--bg-standup);  color: var(--orange); }
  .badge-notes     { background: var(--bg-notes);    color: var(--purple); }
  .badge-pulse     { background: var(--bg-pulse);    color: var(--green); }
  .badge-delta     { background: var(--bg-watch-deep); color: var(--cyan); }
  .badge-synthesis { background: var(--bg-notes);     color: var(--purple); }
  .badge-act       { background: var(--bg-act);      color: var(--red); }
  .badge-watch     { background: var(--bg-watch);    color: var(--orange); }
  .badge-delegate  { background: var(--bg-delegate); color: var(--accent); }
  .badge-they-owe  { background: var(--bg-they-owe); color: var(--purple); }
  .badge-insight   { background: var(--bg-insight);  color: var(--cyan); }
  .badge-pending   { background: var(--bg-daily);    color: var(--green); }
  .badge-complete  { background: var(--bg-complete); color: var(--green); }
  .badge-deferred  { background: var(--bg-deferred); color: var(--gold); }
  .badge-expired   { background: var(--bg-expired);  color: var(--red); }
  .badge-dropped   { background: var(--bg-void-deep); color: var(--muted); }
  .badge-overdue   { background: var(--bg-overdue);  color: var(--red); font-weight: 700; }

  /* ── Job panel ── */
  #job-status { background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
                padding: 16px; margin-top: 16px; display: none; }
  #job-status h3 { font-size: 12px; text-transform: uppercase; letter-spacing: .06em;
                   color: var(--muted); margin-bottom: 10px; }
  #job-output { font: 12px/1.6 "SF Mono", "Menlo", monospace; color: var(--text);
                white-space: pre-wrap; max-height: 320px; overflow-y: auto;
                overflow-wrap: break-word; }
  .spinner { display: inline-block; width: 12px; height: 12px; border: 2px solid var(--border);
             border-top-color: var(--accent); border-radius: 50%; animation: spin .7s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* ── Settings ── */
  .field { margin-bottom: 18px; }
  .field label { display: block; font-size: 12px; font-weight: 600; color: var(--muted);
                 text-transform: uppercase; letter-spacing: .05em; margin-bottom: 5px; }
  .field input[type=text], .field input[type=url] {
    background: var(--bg); border: 1px solid var(--border); border-radius: 6px;
    color: var(--text); font-size: 13px; padding: 7px 10px; width: 100%; }
  .field input[type=text]:focus, .field input[type=url]:focus { outline: none; border-color: var(--accent); }
  .field .desc { font-size: 12px; color: var(--muted); margin-top: 4px; }
  .toggle-row { display: flex; align-items: center; gap: 10px; }
  .toggle { position: relative; width: 36px; height: 20px; }
  .toggle input { opacity: 0; width: 0; height: 0; position: absolute; }
  .toggle input:focus-visible + .slider { outline: 2px solid var(--accent); outline-offset: 2px; }
  .slider { position: absolute; cursor: pointer; inset: 0; background: var(--border);
            border-radius: 20px; transition: .2s; }
  .slider:before { content: ""; position: absolute; height: 14px; width: 14px;
                   left: 3px; bottom: 3px; background: #fff; border-radius: 50%; transition: .2s; }
  input:checked + .slider { background: var(--accent); }
  input:checked + .slider:before { transform: translateX(16px); }

  /* ── Quick links ── */
  .quick-link { display: inline-flex; align-items: center; gap: 6px; color: var(--accent);
                text-decoration: none; font-size: 12px; padding: 5px 0; }
  .quick-link:hover { text-decoration: underline; }
  hr.sep { border: none; border-top: 1px solid var(--border); margin: 24px 0; }
  .section-title { font-size: 11px; font-weight: 700; text-transform: uppercase;
                   letter-spacing: .07em; color: var(--muted); margin-bottom: 14px; }
  .muted { color: var(--muted); }

  /* ── Slack paste ── */
  textarea.paste-area { width: 100%; height: 360px; background: var(--bg); border: 1px solid var(--border);
    border-radius: 6px; color: var(--text); font: 13px/1.5 "SF Mono", "Menlo", monospace;
    padding: 10px; resize: vertical; }
  textarea.paste-area:focus { outline: none; border-color: var(--accent); }

  /* ── Inbox urgency tier ── */
  .inbox-urgent-header { font-size: 11px; font-weight: 700; text-transform: uppercase;
                         letter-spacing: .06em; color: var(--red); padding: 6px 0 8px;
                         border-bottom: 1px solid var(--border-alert); margin-bottom: 4px; }
  .inbox-normal-header { font-size: 11px; font-weight: 700; text-transform: uppercase;
                         letter-spacing: .06em; color: var(--gold); padding: 10px 0 8px;
                         border-bottom: 1px solid var(--border-inbox); margin-bottom: 4px; }
  .bs-item-urgent { background: var(--bg-alert); border-left: 3px solid var(--red); padding-left: 10px; }

  /* ── Brief state ── */
  .bs-item { display: flex; align-items: flex-start; gap: 12px; padding: 10px 0;
             border-top: 1px solid var(--border); }
  .bs-item:first-of-type { border-top: none; }
  .bs-body { flex: 1; min-width: 0; }
  .bs-text { font-size: 13px; line-height: 1.5; overflow-wrap: break-word; word-break: break-word; }
  .bs-meta { font-size: 11px; color: var(--muted); margin-top: 3px; }
  .bs-notes { font-size: 12px; color: var(--muted); font-style: italic; margin-top: 3px;
              white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 600px; }
  .bs-actions { display: flex; gap: 4px; flex-shrink: 0; align-items: center; flex-wrap: nowrap; }
  .bs-defer-sep { width: 1px; height: 16px; background: var(--border); margin: 0 1px; flex-shrink: 0; }
  .bs-empty { color: var(--muted); font-size: 13px; padding: 12px 0; }
  .bs-item-overdue { background: rgba(247,111,111,0.07); margin: 0 -20px; padding-left: 20px; padding-right: 20px; }
  .bs-item-due-soon { background: rgba(247,162,79,0.06); margin: 0 -20px; padding-left: 20px; padding-right: 20px; }
  .bs-program-header { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .08em;
                       color: var(--muted); padding: 10px 0 4px; margin-top: 6px;
                       border-top: 1px solid var(--border); }
  .bs-program-header:first-child { border-top: none; margin-top: 0; }
  .bs-program-header-toggle { cursor: pointer; list-style: none; display: flex; align-items: center; gap: 6px;
                               user-select: none; }
  .bs-program-header-toggle::-webkit-details-marker { display: none; }
  details.bs-group-section { margin-top: 6px; }
  details.bs-group-section > summary { border-top: 1px solid var(--border); padding-top: 10px; margin-top: 0; }
  details.bs-group-section:first-child > summary { border-top: none; padding-top: 4px; }
  details.bs-group-section[open] summary .bs-section-chevron { transform: rotate(90deg); }
  details.bs-group-section .bs-section-chevron { font-size: 10px; transition: transform 0.15s; }
  details.bs-section summary { cursor: pointer; list-style: none; display: flex; align-items: center; gap: 8px;
                                padding: 10px 0; color: var(--muted); font-size: 13px; user-select: none; }
  details.bs-section summary::-webkit-details-marker { display: none; }
  details.bs-section summary .bs-section-chevron { font-size: 10px; transition: transform 0.15s; }
  details.bs-section[open] summary .bs-section-chevron { transform: rotate(90deg); }
  /* ── Drag-to-reorder ── */
  .drag-handle { cursor: grab; color: var(--border); font-size: 14px; padding: 0 6px 0 0;
                 flex-shrink: 0; line-height: 1; user-select: none; }
  .drag-handle:active { cursor: grabbing; }
  .bs-item.drag-over { outline: 2px dashed var(--accent); outline-offset: -2px; }
  .bs-item.dragging { opacity: 0.4; }
  .inline-program-form { margin-top: 4px; }

  /* ── Inbox card (candidates) ── */
  .card-inbox { background: var(--bg-inbox); border-color: var(--border-inbox); }
  .card-inbox h2 { color: var(--gold); }

  /* ── Nav badge (pending count) ── */
  .nav-badge { background: var(--red); color: #fff; border-radius: 10px;
               padding: 0 6px; font-size: 12px; font-weight: 700; line-height: 18px;
               display: inline-block; vertical-align: middle; margin-left: 4px; }

  /* ── Inline code token ── */
  .code-inline { color: var(--text); font-family: "SF Mono", "Menlo", monospace; font-size: 11px; }

  /* ── Render-warnings ambient chip in header ── */
  .render-warn-chip { display: inline-flex; align-items: center; gap: 5px;
                      background: var(--bg-danger); color: var(--orange); border: 1px solid var(--bg-deferred-deep);
                      border-radius: 6px; padding: 3px 10px; font-size: 11px; font-weight: 700;
                      text-transform: uppercase; letter-spacing: .05em; white-space: nowrap; }

  /* ── Brief-state briefing banner (overdue-aware) ── */
  .bs-brief-banner { background: var(--bg-alert); border: 1px solid var(--border-alert);
                     border-radius: 8px; padding: 14px 20px; margin-bottom: 20px;
                     display: flex; align-items: flex-start; gap: 12px; }
  .bs-brief-banner-icon { font-size: 16px; flex-shrink: 0; padding-top: 1px; }
  .bs-brief-banner-body { flex: 1; }
  .bs-brief-banner-title { font-size: 13px; font-weight: 700; color: var(--red);
                           text-transform: uppercase; letter-spacing: .05em; margin-bottom: 6px; }
  .bs-brief-banner-items { list-style: none; display: flex; flex-direction: column; gap: 4px; }
  .bs-brief-banner-items li { font-size: 13px; line-height: 1.5;
                               overflow-wrap: break-word; word-break: break-word; }

  /* ── Defer toggle ── */
  .defer-wrap { display: none; flex-wrap: wrap; gap: 6px; align-items: center;
                margin-top: 6px; padding-top: 6px; border-top: 1px dashed var(--border); }
  .defer-wrap.open { display: flex; }
  .btn-defer-toggle { background: none; border: 1px solid var(--border); color: var(--muted);
                      border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 600;
                      cursor: pointer; }
  .btn-defer-toggle:hover { color: var(--text); border-color: var(--muted); opacity: 1; }

  /* ── Screen-reader only ── */
  .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
             overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }

  /* ── Header quick-run strip (Brief State only) ── */
  .header-quick-run { display: flex; gap: 6px; align-items: center; margin-left: 8px;
                      padding-left: 12px; border-left: 1px solid var(--border); }

  /* ── Synthesis button frequency tiers ── */
  .btn-daily-pulse { background: var(--accent); color: #fff; border: none; }
  .btn-daily-pulse:hover { opacity: .85; }
  .btn-periodic { background: var(--surface); color: var(--text); border: 1px solid var(--border); }
  .btn-periodic:hover { background: var(--border); opacity: 1; }
  .btn-rare { background: var(--surface); color: var(--muted); border: 1px solid var(--border); }
  .btn-rare:hover { color: var(--text); background: var(--border); opacity: 1; }

  /* ── Operations drawer (collapsible) ── */
  details.ops-drawer > summary { cursor: pointer; list-style: none; display: inline-flex; align-items: center;
    gap: 6px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .07em;
    color: var(--muted); padding: 2px 0 14px; user-select: none; }
  details.ops-drawer > summary::-webkit-details-marker { display: none; }
  details.ops-drawer > summary .ops-chevron { font-size: 10px; transition: transform 0.15s; }
  details.ops-drawer[open] > summary .ops-chevron { transform: rotate(90deg); }

  /* ── Pulse freshness chip ── */
  .pulse-freshness { font-size: 11px; white-space: nowrap; }
  .pulse-green  { color: var(--green); }
  .pulse-orange { color: var(--orange); }
  .pulse-red    { color: var(--red); }
  .pulse-gray   { color: var(--muted); }

  /* ── Weekly Delta staleness nudge ── */
  .weekly-nudge { font-size: 11px; white-space: nowrap; }
  .weekly-nudge-orange { color: var(--orange); }
  .weekly-nudge-muted  { color: var(--muted); }

  /* ── Career full-active border hint ── */
  .career-full-active { border-color: var(--orange) !important; }

  /* ── Meeting Prep card ── */
  .prep-controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
  .prep-controls select, .prep-controls input[type=text] {
    flex: 1; min-width: 180px; background: var(--bg); border: 1px solid var(--border);
    border-radius: 6px; color: var(--text); font-size: 13px; padding: 7px 10px; }
  .prep-controls select:focus, .prep-controls input[type=text]:focus { outline: none; border-color: var(--accent); }
  .prep-bullet { padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px; line-height: 1.5; }
  .prep-bullet:last-child { border-bottom: none; }

  /* ── Source drawer ── */
  #source-drawer { position: fixed; top: 0; right: 0; width: 540px; max-width: 95vw; height: 100vh;
    background: var(--surface); border-left: 1px solid var(--border); z-index: 100;
    display: flex; flex-direction: column; transform: translateX(100%); transition: transform .2s ease; }
  #source-drawer.open { transform: translateX(0); }
  #source-drawer-header { display: flex; align-items: center; justify-content: space-between;
    padding: 14px 20px; border-bottom: 1px solid var(--border); flex-shrink: 0; }
  #source-drawer-title { font-size: 12px; font-weight: 700; text-transform: uppercase;
    letter-spacing: .06em; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #source-drawer-body { flex: 1; overflow-y: auto; padding: 16px 20px; }
  #source-drawer-content { font: 12px/1.6 "SF Mono", "Menlo", monospace; color: var(--text);
    white-space: pre-wrap; overflow-wrap: break-word; }

  /* ── Keyboard focus act item ── */
  .bs-item-focused { border-left: 2px solid var(--accent); padding-left: 18px; margin-left: -20px;
    padding-right: 20px; margin-right: -20px; background: rgba(79,142,247,.06); }
  .kb-hint { font-size: 11px; color: var(--muted); padding: 8px 0 2px; border-top: 1px solid var(--border); margin-top: 8px; }
</style>
</head>
<body>
<header>
  <h1>Transcript Analyzer</h1>
  <nav>
    <a href="/brief-state" class="{{ 'active' if page == 'brief-state' else '' }}">Brief State{% if pending_act_count %}<span class="nav-badge" aria-label="{{ pending_act_count }} pending">{{ pending_act_count }}</span>{% endif %}</a>
    <a href="/files" class="{{ 'active' if page == 'home' else '' }}">Analyzed Files</a>
    <a href="/prep" class="{{ 'active' if page == 'prep' else '' }}">Meeting Prep</a>
    <a href="/slack-paste" class="{{ 'active' if page == 'slack-paste' else '' }}">Slack Paste</a>
    <a href="/run" class="{{ 'active' if page == 'run' else '' }}">Run</a>
    <a href="/settings" class="{{ 'active' if page == 'settings' else '' }}">Settings</a>
  </nav>
  {% if page == 'brief-state' %}
  <div class="header-quick-run">
    <form method="post" action="/run-analysis" onsubmit="startPoll('Running transcript analysis…')">
      <button type="submit" class="btn-sm" title="Pick up new transcripts from the inbox">⚡ Run Analysis</button>
    </form>
    <form method="post" action="/synthesize" onsubmit="startPoll('Running Daily Pulse synthesis…')" style="display:flex;gap:5px;align-items:center" id="daily-pulse-quick-form">
      <input type="hidden" name="mode" value="daily">
      <button type="submit" class="btn-daily-pulse btn-sm" title="Synthesize today's analyses into a Daily Pulse">▶ Daily Pulse</button>
    </form>
    {% if last_pulse %}
      {% if last_pulse.analyses_since > 0 %}
      <span class="pulse-freshness pulse-{{ last_pulse.status }}">
        {{ last_pulse.analyses_since }} new — <a href="#" onclick="document.getElementById('daily-pulse-quick-form').submit();return false;" style="color:inherit;text-decoration:underline">run now?</a>
      </span>
      {% else %}
      <span class="pulse-freshness pulse-{{ last_pulse.status }}">Last pulse: {{ last_pulse.label }}</span>
      {% endif %}
    {% endif %}
  </div>
  {% endif %}
  <div class="header-spacer" style="flex:1"></div>
  <div class="header-right" style="display:flex;gap:8px;align-items:center">
    {% if render_warnings %}
    <span class="render-warn-chip" title="{{ render_warnings | join('; ') }}">⚠ {{ render_warnings | length }} warning{{ 's' if render_warnings | length != 1 else '' }}</span>
    {% endif %}
    {% if shared_url %}
    <a class="btn{% if render_warnings %} btn-secondary{% endif %}" href="{{ shared_url }}" target="_blank" rel="noopener">
      ↗ Shared Meeting Files
    </a>
    {% endif %}
  </div>
</header>
<div class="content">
{{ body | safe }}
</div>
<script>
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
  if (indicator && label) indicator.innerHTML = '<span class="spinner"></span> ' + label;
  document.getElementById('job-status').style.display = 'block';
  document.getElementById('job-done').style.display = 'none';
  if (indicator) indicator.style.display = 'inline';
  _pollTimer = setInterval(async () => {
    let d;
    try {
      const r = await fetch('/job-status');
      d = await r.json();
    } catch (_) { return; } // network blip — retry next tick
    document.getElementById('job-output').textContent = d.output || '';
    const el = document.getElementById('job-output');
    el.scrollTop = el.scrollHeight;
    if (d.done) {
      clearInterval(_pollTimer); _pollTimer = null;
      if (indicator) indicator.style.display = 'none';
      const banner = document.getElementById('job-running-banner');
      if (banner) banner.style.display = 'none';
      if (d.returncode && d.returncode !== 0) {
        // Job failed (non-zero exit). Keep the panel up with the captured output
        // so the error is visible — do NOT reload it away. (returncode 2 is a
        // soft warning: synthesis written but archive skipped — still surface it.)
        const done = document.getElementById('job-done');
        done.style.display = 'inline';
        done.style.color = 'var(--red)';
        done.textContent = 'Failed (exit ' + d.returncode + ') — output below, not refreshing.';
        document.title = '⚠ Job failed — Transcript Analyzer';
      } else {
        document.getElementById('job-done').style.display = 'inline';
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
    items.forEach(function(el) { el.classList.remove('bs-item-focused'); });
    _focusedIdx = Math.max(0, Math.min(idx, items.length - 1));
    items[_focusedIdx].classList.add('bs-item-focused');
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
  // Auto-focus first item on page load unless URL hash targets the inbox
  if (document.querySelector('[data-act-id]') && window.location.hash !== '#inbox') {
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
      out.innerHTML = data.bullets.map(b => '<div class="prep-bullet">' + b.replace(/[*][*]([^*]+)[*][*]/g, '<strong>$1</strong>') + '</div>').join('');
    } else {
      out.innerHTML = '<div class="prep-bullet muted">No bullets returned.</div>';
    }
    out.style.display = 'block';
  } catch(e) {
    const out = document.getElementById('prep-output');
    out.innerHTML = '<div class="prep-bullet" style="color:var(--red)">Request failed: ' + e + '</div>';
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

// Auto-resume polling if server reports a job in-flight when page loads
(function() {
  {% if job_running %}
  startPoll({{ job_label | tojson }} + '…');
  {% endif %}
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
    if (target && target !== dragItem) target.style.outline = '2px dashed var(--accent)';
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

// ── Drag-to-reorder act items within a group ──
(function() {
  var dragged = null;

  function initDragGroup(container) {
    container.addEventListener('dragstart', function(e) {
      var item = e.target.closest('.bs-item[data-act-id]');
      if (!item || !item.parentElement.classList.contains('bs-group-items') &&
          item.parentElement.id !== 'focus-group') return;
      dragged = item;
      item.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
    });
    container.addEventListener('dragend', function(e) {
      if (dragged) { dragged.classList.remove('dragging'); dragged = null; }
      container.querySelectorAll('.drag-over').forEach(function(el) { el.classList.remove('drag-over'); });
    });
    container.addEventListener('dragover', function(e) {
      e.preventDefault();
      var target = e.target.closest('.bs-item[data-act-id]');
      if (!target || target === dragged) return;
      target.classList.add('drag-over');
    });
    container.addEventListener('dragleave', function(e) {
      var target = e.target.closest('.bs-item[data-act-id]');
      if (target) target.classList.remove('drag-over');
    });
    container.addEventListener('drop', function(e) {
      e.preventDefault();
      var target = e.target.closest('.bs-item[data-act-id]');
      if (!target || target === dragged || !dragged) return;
      target.classList.remove('drag-over');
      var parent = target.parentElement;
      var items = Array.from(parent.querySelectorAll(':scope > .bs-item[data-act-id]'));
      var draggedIdx = items.indexOf(dragged);
      var targetIdx = items.indexOf(target);
      if (draggedIdx < targetIdx) {
        parent.insertBefore(dragged, target.nextSibling);
      } else {
        parent.insertBefore(dragged, target);
      }
      // Persist new order
      var ids = Array.from(parent.querySelectorAll(':scope > .bs-item[data-act-id]')).map(function(el) { return el.dataset.actId; });
      fetch('/brief-state/reorder', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ids: ids})
      });
    });
  }

  document.querySelectorAll('.bs-group-items').forEach(function(grp) {
    // Make items draggable
    grp.querySelectorAll('.bs-item[data-act-id]').forEach(function(item) {
      item.setAttribute('draggable', 'true');
    });
    initDragGroup(grp);
  });
})();
</script>
</body>
</html>
"""

_HOME_BODY = """\
{% if flash %}<div class="flash flash-{{ flash_type }}">{{ flash }}</div>{% endif %}
{% if job_running %}
<div id="job-running-banner" class="flash" style="background:var(--bg-job-run);color:var(--accent);display:flex;align-items:center;gap:8px">
  <span class="spinner"></span>
  <strong>Running {{ job_label }}…</strong> This takes a few minutes. The page will refresh automatically when done.
</div>
{% endif %}

<div class="stats">
  <div class="stat"><div class="val">{{ rows|selectattr("mode","ne","synthesis")|list|length }}</div><div class="lbl">Total analyzed</div></div>
  <div class="stat"><div class="val">{{ rows|selectattr("mode","eq","transcript")|list|length }}</div><div class="lbl">Transcripts</div></div>
  <div class="stat"><div class="val">{{ rows|selectattr("mode","eq","notes")|list|length }}</div><div class="lbl">Notes filed</div></div>
  <div class="stat"><div class="val">{{ rows|selectattr("mode","eq","synthesis")|list|length }}</div><div class="lbl">Syntheses</div></div>
  <div class="stat"><div class="val">${{ "%.2f"|format(total_cost) }}</div><div class="lbl">Total cost</div></div>
  {% if last_analyzed %}
  <div class="stat"><div class="val" style="font-size:13px{% if last_analyzed_stale == 'danger' %};color:var(--red){% elif last_analyzed_stale == 'warn' %};color:var(--orange){% endif %}">{{ last_analyzed }}</div><div class="lbl">Last analyzed</div></div>
  {% endif %}
</div>

<div class="card">
  <h2>Analyzed files — most recent 25 of {{ rows|length }}</h2>
  {% if rows %}
  <table>
    <thead>
      <tr>
        <th>Date</th><th>Source</th><th>Category</th><th>Model</th><th>Cost</th><th>Shareable</th>
      </tr>
    </thead>
    <tbody>
    {% for r in rows[:25] %}
    <tr>
      <td class="muted" style="white-space:nowrap">{{ r.analyzed_at[:10] }}</td>
      <td class="td-source">
        {% if r.mode == "synthesis" %}
          <a class="quick-link" href="/open-file?path={{ analyzed_path }}/{{ r.output }}" title="{{ r.source }}">{{ r.output }}</a>
        {% else %}
          <span title="{{ r.source }}">{{ r.output or r.source }}</span>
        {% endif %}
      </td>
      <td>
        {% if r.mode == "notes" %}
          <span class="badge badge-notes">notes</span>
        {% elif r.category %}
          <span class="badge badge-{{ r.category|lower }}">{{ r.category }}</span>
        {% endif %}
      </td>
      <td class="muted">{{ r.model or "—" }}</td>
      <td class="muted">${{ "%.3f"|format(r.cost) }}</td>
      <td>{% if r.shareable %}<span style="color:var(--green)">✓</span>{% else %}<span class="muted">—</span>{% endif %}</td>
    </tr>
    {% endfor %}
    </tbody>
  </table>
  {% if rows|length > 25 %}
  <div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--border);font-size:12px;color:var(--muted)">
    Showing 25 of {{ rows|length }} files — open <a class="quick-link" style="font-size:12px" href="{{ analyzed_path_url }}">Analyzed/</a> in Finder for the full history.
  </div>
  {% endif %}
  {% else %}
  <div style="padding:24px 0;display:flex;flex-direction:column;align-items:center;gap:8px;color:var(--muted)">
    <span style="font-size:28px;opacity:0.4">⏳</span>
    <span style="font-size:13px">No analyzed files yet</span>
    <span style="font-size:12px;max-width:340px;text-align:center;opacity:0.7">Drop transcripts into the Call Transcripts inbox and hit <strong style="color:var(--text)">Run Analysis</strong> on Brief State.</span>
  </div>
  {% endif %}
</div>
"""

_SETTINGS_BODY = """\
{% if flash %}<div class="flash flash-{{ flash_type }}">{{ flash }}</div>{% endif %}

<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;align-items:start">

<form method="post" action="/settings">
<div class="card" style="height:100%">
  <h2>Configuration</h2>
  <p style="font-size:12px;color:var(--muted);margin-bottom:20px;line-height:1.6">
    Saved to <code class="code-inline">~/.config/transcript-analyzer/.env</code>.
    Restart server for changes to take effect.
  </p>

  {% for key, label, kind, desc in fields %}
  <div class="field">
    <label>{{ label }}</label>
    {% if kind == "toggle" %}
    <div class="toggle-row">
      <label class="toggle">
        <input type="checkbox" name="{{ key }}" {% if vals.get(key,'').lower() in ('1','true','yes','on') %}checked{% endif %}>
        <span class="slider"></span>
      </label>
      <span class="desc" style="margin:0">{{ desc }}</span>
    </div>
    {% else %}
    <input type="{{ kind }}" name="{{ key }}" value="{{ vals.get(key,'') }}" placeholder="{{ desc[:60] }}">
    <div class="desc">{{ desc }}</div>
    {% endif %}
  </div>
  {% endfor %}

  <div style="padding-top:4px">
    <button type="submit">Save settings</button>
  </div>
</div>
</form>

<div style="display:flex;flex-direction:column;gap:20px">

<div class="card">
  <h2>Content files</h2>
  <p style="font-size:12px;color:var(--muted);margin-bottom:16px">Drive files — opens in your default editor.</p>
  {% for label, path in drive_files %}
  <div style="margin-bottom:10px;padding-bottom:10px;border-bottom:1px solid var(--border)">
    <a class="quick-link" href="/open-file?path={{ path }}" style="display:flex;flex-direction:column;gap:2px">
      <span style="font-weight:600;font-size:13px">{{ label }}</span>
      <span class="muted" style="font-size:11px;font-family:'SF Mono',monospace">{{ path }}</span>
    </a>
  </div>
  {% endfor %}
  <div style="margin-top:-2px;padding-top:0;border-top:none"></div>
</div>

<div class="card">
  <h2>Diagnostics</h2>
  <div style="margin-bottom:16px">
    <a class="quick-link" href="/open-log" style="font-size:13px;font-weight:600">Open run log</a>
    <div style="font-size:11px;color:var(--muted);font-family:'SF Mono',monospace;margin-top:3px">{{ log_path }}</div>
  </div>
  <div style="padding-top:14px;border-top:1px solid var(--border)">
    <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin-bottom:10px">Server</div>
    <form method="post" action="/restart" style="display:inline"
          onsubmit="return confirm('Restart the dashboard server? This reloads code and .env changes. Any in-progress job will block the restart.');">
      <button type="submit" class="btn-secondary">↻ Restart server</button>
    </form>
    <div class="desc" style="margin-top:8px">Re-execs in place to pick up code or <code class="code-inline">.env</code> changes. Analysis runs fresh each time — this only refreshes the long-lived UI process.</div>
  </div>
</div>

</div><!-- right column -->
</div><!-- grid -->
"""


_RUN_BODY = """\
{% if flash %}<div class="flash flash-{{ flash_type }}">{{ flash }}</div>{% endif %}
{% if job_running %}
<div id="job-running-banner" class="flash" style="background:var(--bg-job-run);color:var(--accent);display:flex;align-items:center;gap:8px">
  <span class="spinner"></span>
  <strong>Running {{ job_label }}…</strong> The page will refresh when done.
</div>
{% endif %}

<div class="card">
  <div class="action-tier">
    <span class="action-tier-label">Daily</span>
    <div class="action-group">
      <form method="post" action="/run-analysis" onsubmit="startPoll('Running transcript analysis…')">
        <button type="submit">⚡ Run Analysis</button>
      </form>
    </div>
    <div class="action-group">
      <form method="post" action="/synthesize" onsubmit="startPoll('Running Daily Pulse synthesis…')" style="display:flex;gap:6px;align-items:center">
        <input type="hidden" name="mode" value="daily">
        <button type="submit" class="btn-daily-pulse">▶ Daily Pulse</button>
        <label class="sr-only" for="daily-date">Back-date (optional)</label>
        <input type="date" id="daily-date" name="date" title="Leave blank for today; pick a past date to back-date">
      </form>
    </div>
  </div>
  <div class="action-tier">
    <span class="action-tier-label">Periodic</span>
    <div class="action-group">
      <form method="post" action="/synthesize" onsubmit="startPoll('Running Weekly Delta synthesis…')" style="display:flex;gap:6px;align-items:center">
        <input type="hidden" name="mode" value="weekly">
        <button type="submit" class="btn-periodic">▶ {% if last_weekly and last_weekly.bdays and last_weekly.bdays > 8 %}Weekly Delta ({{ last_weekly.bdays }}d catch-up){% else %}Weekly Delta{% endif %}</button>
        <label class="sr-only" for="weekly-date">Week anchor date (optional)</label>
        <input type="date" id="weekly-date" name="date" title="Leave blank for this week">
        {% if last_weekly %}
          {% if last_weekly.label and last_weekly.bdays and last_weekly.bdays > 5 %}
          <span class="weekly-nudge weekly-nudge-orange" title="Last weekly was {{ last_weekly.label }}">Last: {{ last_weekly.label }}</span>
          {% elif not last_weekly.label %}
          <span class="weekly-nudge weekly-nudge-muted">Not yet run</span>
          {% endif %}
        {% endif %}
      </form>
    </div>
    <div class="action-group">
      <form method="post" action="/synthesize" onsubmit="startPoll('Running catch-up — one pulse per backlog day…')" style="display:flex;gap:6px;align-items:center">
        <input type="hidden" name="mode" value="catch-up">
        <button type="submit" class="btn-periodic">▶ Catch Up</button>
        <label class="sr-only" for="catchup-since">Since date (optional)</label>
        <input type="date" id="catchup-since" name="since" title="Start from this date (leave blank for full backlog)">
      </form>
    </div>
  </div>
  <div class="action-tier">
    <span class="action-tier-label">Rare</span>
    <div class="action-group">
      <form method="post" action="/synthesize" id="career-form"
            onsubmit="return careerSubmit(this, startPoll)" style="display:flex;gap:6px;align-items:center">
        <input type="hidden" name="mode" value="career">
        <button type="submit" id="career-btn" class="btn-rare">▶ Career</button>
        <label style="font-size:11px;color:var(--muted);display:inline-flex;align-items:center;gap:4px"
               title="Re-read ALL analyses to re-baseline. Off = incremental.">
          <input type="checkbox" name="full" value="1" id="career-full"
                 onchange="document.getElementById('career-btn').classList.toggle('career-full-active',this.checked)"> full
        </label>
      </form>
    </div>
    <div class="action-group">
      <form method="post" action="/synthesize" onsubmit="startPoll('Compiling Weekly Timecard…')" style="display:flex;gap:6px;align-items:center">
        <input type="hidden" name="mode" value="timecard">
        <button type="submit" class="btn-rare">▶ Timecard</button>
        <label class="sr-only" for="timecard-date">Week date (optional)</label>
        <input type="date" id="timecard-date" name="date" title="Leave blank for current week">
      </form>
    </div>
  </div>
</div>

<div id="job-status" {% if not job_running %}style="display:none"{% endif %}>
  <h3>
    <span id="job-indicator"><span class="spinner"></span> Running {{ job_label or "job" }}…</span>
    <span id="job-done" style="display:none">Done — refreshing…</span>
  </h3>
  <div id="job-output" role="log" aria-live="polite" aria-label="Job output">{{ job_output }}</div>
</div>
"""

_BRIEF_STATE_BODY = """\
{% if flash %}<div class="flash flash-{{ flash_type }}">{{ flash }}</div>{% endif %}
{% if job_running %}
<div id="job-running-banner" class="flash" style="background:var(--bg-job-run);color:var(--accent);display:flex;align-items:center;gap:8px">
  <span class="spinner"></span>
  <strong>Running {{ job_label }}…</strong> The page will refresh when done.
</div>
{% endif %}

{# ── P0 BRIEF BANNER: overdue acts surfaced before anything else ── #}
{% set overdue_acts = act_items | selectattr('overdue') | list %}
{% if overdue_acts %}
<div class="bs-brief-banner" role="region" aria-label="Overdue obligations">
  <div class="bs-brief-banner-icon">🔴</div>
  <div class="bs-brief-banner-body">
    <div class="bs-brief-banner-title">{{ overdue_acts|length }} overdue act{{ 's' if overdue_acts|length != 1 else '' }} — action required</div>
    <ul class="bs-brief-banner-items">
      {% for item in overdue_acts %}
      <li>{{ item.text }}{% if item.days_overdue %} <span class="badge badge-overdue" style="font-size:10px">{{ item.days_overdue }}d overdue</span>{% endif %}</li>
      {% endfor %}
    </ul>
  </div>
</div>
{% endif %}

{# ── MACRO: render one act item row (used in both Focus and grouped sections) ── #}
{% macro act_row(item, show_pin=true) %}
<div class="bs-item{% if item.overdue %} bs-item-overdue{% elif item.deadline and item.deadline <= today_iso %} bs-item-due-soon{% endif %}" data-act-id="{{ item.id }}" tabindex="0">
  <span class="drag-handle" title="Drag to reorder" aria-hidden="true">⠿</span>
  <div class="bs-body">
    <div class="bs-text">
      {% if item.overdue %}<span class="badge badge-overdue">OVERDUE</span> {% endif %}
      {% if item.loe %}<span class="badge" style="background:var(--bg-alt);color:var(--fg-muted);font-size:10px;letter-spacing:.04em">{{ item.loe }}</span> {% endif %}
      {{ item.text }}
      {% if item.source_pulse %}<a href="#" class="quick-link" style="font-size:11px;margin-left:6px" onclick="openSourceDrawer('{{ item.id }}','{{ item.source_pulse }}');return false">↗ source</a>{% endif %}
    </div>
    <div class="bs-meta">
      {% if item.deadline %}Due {{ item.deadline }}{% if item.overdue %} ({{ item.days_overdue }}d overdue){% endif %} · {% endif %}
      From {{ item.source_brief[:10] if item.source_brief else '—' }}
      {% if item.program %} · <span style="opacity:.65">{{ item.program }}</span>{% endif %}
    </div>
    {% if not item.program %}
    <form method="post" action="/brief-state/assign-program/{{ item.id }}" class="inline-program-form">
      <select name="program" class="btn-secondary btn-sm" style="padding:2px 5px;font-size:11px;max-width:160px" onchange="this.form.submit()">
        <option value="">assign program…</option>
        {% for p in programs %}
        <option value="{{ p.key }}">{{ p.label }}</option>
        {% endfor %}
      </select>
    </form>
    {% endif %}
    {% if item.notes %}<div class="bs-notes">{{ item.notes }}</div>{% endif %}
  </div>
  <div class="bs-actions" style="flex-direction:column;align-items:flex-end;gap:4px">
    <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
      <form method="post" action="/brief-state/close/{{ item.id }}">
        <input type="hidden" name="status" value="complete">
        <button type="submit" class="btn-secondary btn-sm" data-act-action="done">Done</button>
      </form>
      <form method="post" action="/brief-state/close/{{ item.id }}"
            onsubmit="return confirm('Drop this item? It will be marked dropped.');">
        <input type="hidden" name="status" value="dropped">
        <button type="submit" class="btn-destructive btn-sm" data-act-action="drop">Drop</button>
      </form>
      <span class="bs-defer-sep"></span>
      <button type="button" class="btn-secondary btn-sm" style="padding:4px 8px" aria-label="Defer 1 day" onclick="deferQuick('{{ item.id }}',1)" data-act-action="defer1">+1d</button>
      <button type="button" class="btn-secondary btn-sm" style="padding:4px 8px" aria-label="Defer 3 days" onclick="deferQuick('{{ item.id }}',3)" data-act-action="defer3">+3d</button>
      <button type="button" class="btn-secondary btn-sm" style="padding:4px 8px" aria-label="Defer to Monday" onclick="deferQuick('{{ item.id }}',0)" data-act-action="defermon">Mon</button>
      <details style="display:inline-block;position:relative">
        <summary class="btn-secondary btn-sm" style="list-style:none;cursor:pointer;padding:4px 8px">…</summary>
        <div style="position:absolute;background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:10px;margin-top:4px;z-index:20;min-width:220px;right:0;white-space:nowrap">
          <form method="post" action="/brief-state/defer/{{ item.id }}" style="display:flex;gap:4px;align-items:center;margin-bottom:8px">
            <label class="sr-only" for="defer-date-{{ item.id }}">Defer to date</label>
            <input type="date" id="defer-date-{{ item.id }}" name="target" required style="width:120px">
            <button type="submit" class="btn-secondary btn-sm">Defer</button>
          </form>
          <form method="post" action="/brief-state/loe/{{ item.id }}" style="display:flex;gap:4px;align-items:center;margin-bottom:8px">
            <select name="loe" class="btn-secondary btn-sm" style="padding:3px 6px;font-size:11px">
              <option value="">LoE…</option>
              <option value="S" {% if item.loe == 'S' %}selected{% endif %}>S — small (&lt;2h)</option>
              <option value="M" {% if item.loe == 'M' %}selected{% endif %}>M — medium (half day)</option>
              <option value="L" {% if item.loe == 'L' %}selected{% endif %}>L — large (multi-day)</option>
            </select>
            <button type="submit" class="btn-secondary btn-sm">Set</button>
          </form>
          <form method="post" action="/brief-state/assign-program/{{ item.id }}" style="display:flex;gap:4px;align-items:center;margin-bottom:8px">
            <select name="program" class="btn-secondary btn-sm" style="padding:3px 6px;font-size:11px">
              <option value="">Clear program</option>
              {% for p in programs %}
              <option value="{{ p.key }}" {% if item.program == p.key %}selected{% endif %}>{{ p.label }}</option>
              {% endfor %}
            </select>
            <button type="submit" class="btn-secondary btn-sm">Assign</button>
          </form>
          {% if show_pin %}
          {% if item.pinned %}
          <form method="post" action="/brief-state/unpin/{{ item.id }}">
            <button type="submit" class="btn-secondary btn-sm" style="width:100%">☆ Unpin from Focus</button>
          </form>
          {% else %}
          <form method="post" action="/brief-state/pin/{{ item.id }}">
            <button type="submit" class="btn-secondary btn-sm" style="width:100%">★ Pin to Today's Focus</button>
          </form>
          {% endif %}
          {% endif %}
        </div>
      </details>
    </div>
  </div>
</div>
{% endmacro %}

{# ── TODAY'S FOCUS ── #}
{% if pinned_acts %}
<div class="card" style="border-color:var(--accent);border-width:2px">
  <h2 style="display:flex;align-items:center;gap:8px">
    <span style="color:var(--accent)">★</span> Today's Focus
    <span class="badge" style="background:var(--accent);color:#000;font-size:10px">{{ pinned_acts|length }}</span>
  </h2>
  {% for item in pinned_acts %}
  {{ act_row(item, show_pin=true) }}
  {% endfor %}
</div>
{% endif %}

{# ── ACT ITEMS — grouped by program, each group collapsible ── #}
<div class="card" style="{% if overdue_acts %}border-color:var(--border-alert){% endif %}">
  <h2>Act items — Brad owns</h2>
  {% if act_groups %}
  {% for group in act_groups %}
  {% if act_groups|length > 1 %}
  <details class="bs-group-section" open>
    <summary class="bs-program-header bs-program-header-toggle">
      <span class="bs-section-chevron">▶</span>
      {{ group.label }}
      <span class="badge" style="margin-left:4px;font-size:10px;background:var(--bg-alt);color:var(--fg-muted)">{{ group.acts|length }}</span>
    </summary>
    <div class="bs-group-items" data-group-key="{{ group.key }}">
    {% for item in group.acts %}
    {{ act_row(item, show_pin=true) }}
    {% endfor %}
    </div>
  </details>
  {% else %}
  <div class="bs-group-items" data-group-key="{{ group.key }}">
  {% for item in group.acts %}
  {{ act_row(item, show_pin=true) }}
  {% endfor %}
  </div>
  {% endif %}
  {% endfor %}
  {% if act_items|length >= 2 %}
  <div class="kb-hint">[k] done &nbsp; [d] drop &nbsp; [1/3/m] defer &nbsp; [↓/j] next &nbsp; [↑/i] prev</div>
  {% endif %}
  {% else %}
  <div class="bs-empty" style="display:flex;align-items:center;gap:8px">
    <span style="color:var(--green);font-size:15px">✓</span>
    All clear — no pending acts.
  </div>
  {% endif %}
</div>

{# ── INBOX (candidates) ── #}
{% if candidate_items %}
{% set urgent_items = candidate_items | selectattr('urgency', 'equalto', 'high') | list %}
{% set normal_items = candidate_items | rejectattr('urgency', 'equalto', 'high') | list %}
<div class="card card-inbox" id="inbox">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
    <h2 style="margin-bottom:0">Inbox — signals pending triage ({{ candidate_items|length }})</h2>
    <form method="post" action="/brief-state/dismiss-all-candidates" style="display:inline"
          onsubmit="return confirm('Dismiss all {{ candidate_items|length }} candidate item{{ \'s\' if candidate_items|length != 1 else \'\'}}? They will be marked dismissed.');">
      <button type="submit" class="btn-secondary btn-sm">Dismiss all</button>
    </form>
  </div>
  {% if urgent_items %}
  <div class="inbox-urgent-header">⚠ {{ urgent_items|length }} urgent — action required</div>
  {% for item in urgent_items %}
  <div class="bs-item bs-item-urgent">
    <div class="bs-body">
      <div class="bs-text">
        <span class="badge badge-act" style="background:var(--bg-alert);color:var(--red)">URGENT</span>
        {{ item.text }}
      </div>
      <div class="bs-meta">
        From {{ item.source_analyzed or '—' }}
        {% if item.deadline %} · Due {{ item.deadline }}{% endif %}
        {% if item.program %} · {{ item.program }}{% endif %}
      </div>
    </div>
    <div class="bs-actions" style="flex-direction:column;align-items:flex-end;gap:4px">
      <form method="post" action="/brief-state/accept/{{ item.id }}" style="display:flex;gap:4px;align-items:center">
        <input type="hidden" name="type" value="act">
        <select name="program" class="btn-secondary btn-sm" style="padding:3px 6px;font-size:11px;max-width:130px">
          <option value="{{ item.program or '' }}">{{ item.program or 'program…' }}</option>
          {% for p in programs %}{% if p.key != item.program %}<option value="{{ p.key }}">{{ p.label }}</option>{% endif %}{% endfor %}
        </select>
        <button type="submit" class="btn-secondary btn-sm" style="border-color:var(--red);color:var(--red)">Accept</button>
      </form>
      <form method="post" action="/brief-state/dismiss-candidate/{{ item.id }}"
            onsubmit="return confirm('Dismiss this urgent item?');">
        <button type="submit" class="btn-secondary btn-sm">Dismiss</button>
      </form>
    </div>
  </div>
  {% endfor %}
  {% endif %}
  {% if normal_items %}
  {% if urgent_items %}<div class="inbox-normal-header">Signals</div>{% endif %}
  {% for item in normal_items %}
  <div class="bs-item">
    <div class="bs-body">
      <div class="bs-text">
        <span class="badge badge-{{ item.type }}">{{ item.type }}</span>
        {% if item.owner and item.owner != "Brad" %}<span class="muted" style="font-size:11px"> {{ item.owner }} owes</span>{% endif %}
        {{ item.text }}
      </div>
      <div class="bs-meta">
        From {{ item.source_analyzed or '—' }}
        {% if item.deadline %} · Due {{ item.deadline }}{% endif %}
        {% if item.program %} · {{ item.program }}{% endif %}
      </div>
    </div>
    <div class="bs-actions" style="flex-direction:column;align-items:flex-end;gap:4px">
      <form method="post" action="/brief-state/accept/{{ item.id }}" style="display:flex;gap:4px;align-items:center;flex-wrap:wrap">
        <select name="type" class="btn-secondary btn-sm" style="padding:3px 6px;font-size:11px">
          <option value="act"      {% if item.type == 'act' %}selected{% endif %}>act</option>
          <option value="watch"    {% if item.type == 'watch' %}selected{% endif %}>watch</option>
          <option value="they-owe" {% if item.type == 'they-owe' %}selected{% endif %}>they-owe</option>
          <option value="delegate" {% if item.type == 'delegate' %}selected{% endif %}>delegate</option>
          <option value="insight"  {% if item.type == 'insight' %}selected{% endif %}>insight</option>
        </select>
        <select name="program" class="btn-secondary btn-sm" style="padding:3px 6px;font-size:11px;max-width:130px">
          <option value="{{ item.program or '' }}">{{ item.program or 'program…' }}</option>
          {% for p in programs %}{% if p.key != item.program %}<option value="{{ p.key }}">{{ p.label }}</option>{% endif %}{% endfor %}
        </select>
        <button type="submit" class="btn-secondary btn-sm" style="border-color:var(--gold);color:var(--gold)">Accept</button>
      </form>
      <form method="post" action="/brief-state/dismiss-candidate/{{ item.id }}"
            onsubmit="return confirm('Dismiss this candidate item?');">
        <button type="submit" class="btn-secondary btn-sm">Dismiss</button>
      </form>
    </div>
  </div>
  {% endfor %}
  {% endif %}
</div>
{% endif %}

{# ── MACRO: render a grouped collapsible section with per-group mass-close ── #}
{% macro grouped_section(section_id, title, badge_class, groups, item_key, mass_close_label, mass_close_status) %}
{% set total = namespace(n=0) %}
{% for g in groups %}{% set total.n = total.n + g[item_key]|length %}{% endfor %}
{% if total.n > 0 %}
<div class="card" id="{{ section_id }}">
  <details class="bs-section">
    <summary>
      <span class="bs-section-chevron">▶</span>
      {{ title }}
      <span class="badge {{ badge_class }}" style="margin-left:4px">{{ total.n }}</span>
    </summary>
    {% for group in groups %}
    <details class="bs-group-section" open>
      <summary class="bs-program-header bs-program-header-toggle">
        <span class="bs-section-chevron">▶</span>
        {{ group.label }}
        <span class="badge" style="margin-left:4px;font-size:10px;background:var(--bg-alt);color:var(--fg-muted)">{{ group[item_key]|length }}</span>
        <form method="post" action="/brief-state/close-group" style="margin-left:auto;display:inline-flex;gap:4px" onclick="event.stopPropagation()">
          {% for item in group[item_key] %}<input type="hidden" name="ids" value="{{ item.id }}">{% endfor %}
          <input type="hidden" name="status" value="{{ mass_close_status }}">
          <input type="hidden" name="anchor" value="{{ section_id }}">
          <button type="submit" class="btn-secondary btn-sm" style="font-size:10px;padding:2px 6px"
                  onclick="return confirm('Mark all {{ group[item_key]|length }} {{ group.label }} items as {{ mass_close_status }}?')">
            ✓ All {{ mass_close_label }}
          </button>
        </form>
      </summary>
      {{ caller(group[item_key]) }}
    </details>
    {% endfor %}
  </details>
</div>
{% endif %}
{% endmacro %}

{# ── WATCH ── #}
{% if watch_groups %}
<div class="card" id="watch-section">
  <details class="bs-section">
    <summary>
      <span class="bs-section-chevron">▶</span>
      Watch
      <span class="badge badge-watch" style="margin-left:4px">{{ watch_items|length }}</span>
    </summary>
    {% for group in watch_groups %}
    <details class="bs-group-section" open>
      <summary class="bs-program-header bs-program-header-toggle">
        <span class="bs-section-chevron">▶</span>
        {{ group.label }}
        <span class="badge" style="margin-left:4px;font-size:10px;background:var(--bg-alt);color:var(--fg-muted)">{{ group.rows|length }}</span>
        <form method="post" action="/brief-state/close-group" style="margin-left:auto;display:inline-flex;gap:4px" onclick="event.stopPropagation()">
          {% for item in group.rows %}<input type="hidden" name="ids" value="{{ item.id }}">{% endfor %}
          <input type="hidden" name="status" value="complete">
          <input type="hidden" name="anchor" value="watch-section">
          <button type="submit" class="btn-secondary btn-sm" style="font-size:10px;padding:2px 6px"
                  onclick="return confirm('Mark all {{ group.rows|length }} {{ group.label }} watch items as done?')">✓ All done</button>
        </form>
      </summary>
      {% for item in group.rows %}
      <div class="bs-item" data-act-id="{{ item.id }}">
        <div class="bs-body">
          <div class="bs-text">{{ item.text }}</div>
          <div class="bs-meta">
            From {{ item.source_brief[:10] if item.source_brief else '—' }}
            {% if item.program %} · <span style="opacity:.65">{{ item.program }}</span>{% endif %}
          </div>
          {% if item.notes %}<div class="bs-notes">{{ item.notes }}</div>{% endif %}
        </div>
        <div class="bs-actions">
          <form method="post" action="/brief-state/close/{{ item.id }}">
            <input type="hidden" name="status" value="complete">
            <button type="submit" class="btn-secondary btn-sm">Done</button>
          </form>
          <form method="post" action="/brief-state/close/{{ item.id }}"
                onsubmit="return confirm('Drop this watch item?');">
            <input type="hidden" name="status" value="dropped">
            <button type="submit" class="btn-destructive btn-sm">Drop</button>
          </form>
          <details style="display:inline-block;position:relative">
            <summary class="btn-secondary btn-sm" style="list-style:none;cursor:pointer;padding:4px 8px">…</summary>
            <div style="position:absolute;background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:10px;margin-top:4px;z-index:20;min-width:220px;right:0;white-space:nowrap">
              <form method="post" action="/brief-state/assign-program/{{ item.id }}" style="display:flex;gap:4px;align-items:center">
                <select name="program" class="btn-secondary btn-sm" style="padding:3px 6px;font-size:11px">
                  <option value="">Clear program</option>
                  {% for p in programs %}<option value="{{ p.key }}" {% if item.program == p.key %}selected{% endif %}>{{ p.label }}</option>{% endfor %}
                </select>
                <button type="submit" class="btn-secondary btn-sm">Assign</button>
              </form>
            </div>
          </details>
        </div>
      </div>
      {% endfor %}
    </details>
    {% endfor %}
  </details>
</div>
{% endif %}

{# ── DELEGATED ── #}
{% if delegate_items %}
<div class="card" id="delegate-section">
  <details class="bs-section">
    <summary>
      <span class="bs-section-chevron">▶</span>
      Delegated
      <span class="badge badge-delegate" style="margin-left:4px">{{ delegate_items|length }}</span>
    </summary>
    {% for group in delegate_groups %}
    <details class="bs-group-section" open>
      <summary class="bs-program-header bs-program-header-toggle">
        <span class="bs-section-chevron">▶</span>
        {{ group.label }}
        <span class="badge" style="margin-left:4px;font-size:10px;background:var(--bg-alt);color:var(--fg-muted)">{{ group.rows|length }}</span>
        <form method="post" action="/brief-state/close-group" style="margin-left:auto;display:inline-flex;gap:4px" onclick="event.stopPropagation()">
          {% for item in group.rows %}<input type="hidden" name="ids" value="{{ item.id }}">{% endfor %}
          <input type="hidden" name="status" value="complete">
          <input type="hidden" name="anchor" value="delegate-section">
          <button type="submit" class="btn-secondary btn-sm" style="font-size:10px;padding:2px 6px"
                  onclick="return confirm('Confirm all {{ group.rows|length }} {{ group.label }} delegated items done?')">✓ All confirmed</button>
        </form>
      </summary>
      {% for item in group.rows %}
      <div class="bs-item" data-act-id="{{ item.id }}">
        <div class="bs-body">
          <div class="bs-text">{{ item.text }}</div>
          <div class="bs-meta">
            Owner: <strong>{{ item.owner }}</strong>
            {% if item.deadline %} · Due {{ item.deadline }}{% endif %}
            {% if item.resurfaced_count %} · Re-surfaced {{ item.resurfaced_count }}×{% endif %}
            {% if item.program %} · <span style="opacity:.65">{{ item.program }}</span>{% endif %}
          </div>
          {% if item.notes %}<div class="bs-notes">{{ item.notes }}</div>{% endif %}
        </div>
        <div class="bs-actions">
          <form method="post" action="/brief-state/close/{{ item.id }}">
            <input type="hidden" name="status" value="complete">
            <button type="submit" class="btn-secondary btn-sm">Confirmed done</button>
          </form>
          <form method="post" action="/brief-state/re-own/{{ item.id }}">
            <button type="submit" class="btn-destructive btn-sm">Re-own</button>
          </form>
          <details style="display:inline-block;position:relative">
            <summary class="btn-secondary btn-sm" style="list-style:none;cursor:pointer;padding:4px 8px">…</summary>
            <div style="position:absolute;background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:10px;margin-top:4px;z-index:20;min-width:220px;right:0;white-space:nowrap">
              <form method="post" action="/brief-state/assign-program/{{ item.id }}" style="display:flex;gap:4px;align-items:center">
                <select name="program" class="btn-secondary btn-sm" style="padding:3px 6px;font-size:11px">
                  <option value="">Clear program</option>
                  {% for p in programs %}<option value="{{ p.key }}" {% if item.program == p.key %}selected{% endif %}>{{ p.label }}</option>{% endfor %}
                </select>
                <button type="submit" class="btn-secondary btn-sm">Assign</button>
              </form>
            </div>
          </details>
        </div>
      </div>
      {% endfor %}
    </details>
    {% endfor %}
  </details>
</div>
{% endif %}

{# ── THEY OWE YOU ── #}
{% if they_owe_items %}
<div class="card" id="they-owe-section">
  <details class="bs-section">
    <summary>
      <span class="bs-section-chevron">▶</span>
      They owe you
      <span class="badge badge-they-owe" style="margin-left:4px">{{ they_owe_items|length }}</span>
    </summary>
    {% for group in they_owe_groups %}
    <details class="bs-group-section" open>
      <summary class="bs-program-header bs-program-header-toggle">
        <span class="bs-section-chevron">▶</span>
        {{ group.label }}
        <span class="badge" style="margin-left:4px;font-size:10px;background:var(--bg-alt);color:var(--fg-muted)">{{ group.rows|length }}</span>
        <form method="post" action="/brief-state/close-group" style="margin-left:auto;display:inline-flex;gap:4px" onclick="event.stopPropagation()">
          {% for item in group.rows %}<input type="hidden" name="ids" value="{{ item.id }}">{% endfor %}
          <input type="hidden" name="status" value="complete">
          <input type="hidden" name="anchor" value="they-owe-section">
          <button type="submit" class="btn-secondary btn-sm" style="font-size:10px;padding:2px 6px"
                  onclick="return confirm('Mark all {{ group.rows|length }} {{ group.label }} items as received?')">✓ All received</button>
        </form>
      </summary>
      {% for item in group.rows %}
      <div class="bs-item" data-act-id="{{ item.id }}">
        <div class="bs-body">
          <div class="bs-text">{{ item.text }}</div>
          <div class="bs-meta">
            <strong>{{ item.owner }}</strong>
            {% if item.deadline %} · Due {{ item.deadline }}{% if item.they_overdue %} <span style="color:var(--red)">({{ item.days_overdue }}d overdue)</span>{% endif %}{% endif %}
            {% if item.program %} · <span style="opacity:.65">{{ item.program }}</span>{% endif %}
          </div>
          {% if item.notes %}<div class="bs-notes">{{ item.notes }}</div>{% endif %}
        </div>
        <div class="bs-actions">
          <form method="post" action="/brief-state/close/{{ item.id }}">
            <input type="hidden" name="status" value="complete">
            <button type="submit" class="btn-secondary btn-sm">Received</button>
          </form>
          <form method="post" action="/brief-state/close/{{ item.id }}">
            <input type="hidden" name="status" value="dropped">
            <button type="submit" class="btn-destructive btn-sm">Drop</button>
          </form>
          <details style="display:inline-block;position:relative">
            <summary class="btn-secondary btn-sm" style="list-style:none;cursor:pointer;padding:4px 8px">…</summary>
            <div style="position:absolute;background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:10px;margin-top:4px;z-index:20;min-width:220px;right:0;white-space:nowrap">
              <form method="post" action="/brief-state/assign-program/{{ item.id }}" style="display:flex;gap:4px;align-items:center">
                <select name="program" class="btn-secondary btn-sm" style="padding:3px 6px;font-size:11px">
                  <option value="">Clear program</option>
                  {% for p in programs %}<option value="{{ p.key }}" {% if item.program == p.key %}selected{% endif %}>{{ p.label }}</option>{% endfor %}
                </select>
                <button type="submit" class="btn-secondary btn-sm">Assign</button>
              </form>
            </div>
          </details>
        </div>
      </div>
      {% endfor %}
    </details>
    {% endfor %}
  </details>
</div>
{% endif %}

{# ── CLOSED ── #}
{% if recent_closed %}
<div class="card" id="closed-section">
  <details class="bs-section">
    <summary>
      <span class="bs-section-chevron">▶</span>
      Closed (last 7 days)
      <span class="badge badge-complete" style="margin-left:4px">{{ recent_closed|length }}</span>
    </summary>
    {% for group in closed_groups %}
    <details class="bs-group-section" open>
      <summary class="bs-program-header bs-program-header-toggle">
        <span class="bs-section-chevron">▶</span>
        {{ group.label }}
        <span class="badge" style="margin-left:4px;font-size:10px;background:var(--bg-alt);color:var(--fg-muted)">{{ group.rows|length }}</span>
      </summary>
      <table style="margin-top:4px">
        <thead>
          <tr><th>Item</th><th>Type</th><th>Status</th><th>Closed</th><th></th></tr>
        </thead>
        <tbody>
        {% for item in group.rows %}
        <tr>
          <td>{{ item.text }}</td>
          <td><span class="badge badge-{{ item.type }}">{{ item.type }}</span></td>
          <td><span class="badge badge-{{ item.status }}">{{ item.status }}</span></td>
          <td class="muted">{{ item.closed_at[:10] if item.closed_at else '—' }}</td>
          <td>
            <form method="post" action="/brief-state/reopen/{{ item.id }}" style="display:inline">
              <button type="submit" class="btn-secondary btn-sm">Reopen</button>
            </form>
          </td>
        </tr>
        {% endfor %}
        </tbody>
      </table>
    </details>
    {% endfor %}
  </details>
</div>
{% endif %}

{# ── PROGRAM ORDER ── #}
<div class="card" style="margin-top:8px">
  <details class="bs-section">
    <summary>
      <span class="bs-section-chevron">▶</span>
      Program order
      <span style="font-size:11px;color:var(--muted);margin-left:6px">drag to reorder</span>
    </summary>
    <div id="prog-order-list" style="margin-top:8px;display:flex;flex-direction:column;gap:4px">
      {% for key in prog_order %}
      <div class="prog-order-item" data-key="{{ key }}" draggable="true"
           style="display:flex;align-items:center;gap:8px;padding:6px 10px;background:var(--bg-alt);border:1px solid var(--border);border-radius:6px;cursor:grab;font-size:12px;user-select:none">
        <span style="color:var(--muted);font-size:14px">⠿</span>
        <span>{{ key }}</span>
      </div>
      {% endfor %}
    </div>
    <div style="margin-top:8px;font-size:11px;color:var(--muted)">Reorder takes effect on next page load. New programs not in this list appear at the bottom.</div>
  </details>
</div>

{# ── SOURCE DRAWER ── #}
<div id="source-drawer">
  <div id="source-drawer-header">
    <span id="source-drawer-title">Source</span>
    <button class="btn-secondary btn-sm" onclick="closeSourceDrawer()">✕ Close</button>
  </div>
  <div id="source-drawer-body">
    <pre id="source-drawer-content"></pre>
  </div>
</div>
"""

_PREP_BODY = """\
{% if flash %}<div class="flash flash-{{ flash_type }}">{{ flash }}</div>{% endif %}

<div class="card" id="meeting-prep-card">
  <h2>Meeting Prep</h2>
  <p class="muted" style="font-size:12px;margin-bottom:14px">Select a program to generate a pre-meeting brief from its recent signals and open act items.</p>
  <div class="prep-controls">
    <select id="prep-program" name="program">
      <option value="">Select program…</option>
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
    <button class="btn-secondary" id="prep-refresh-btn" onclick="refreshSignals()" title="Refresh program signals from analyzed files">↻ Refresh signals</button>
    <button class="btn-secondary" id="prep-submit" onclick="runMeetingPrep()">Prep</button>
  </div>
  <div id="prep-output" style="display:none"></div>
</div>
"""

_SLACK_PASTE_BODY = """\
{% if flash %}<div class="flash flash-{{ flash_type }}">{{ flash }}</div>{% endif %}

<div class="card">
  <h2>Paste Slack canvas</h2>
  <p class="muted" style="margin-bottom:16px;font-size:12px">
    Paste your Slack canvas (markdown) below. It will be saved as a <code>.md</code>
    file directly into the <strong>Call Transcripts</strong> inbox so the next
    <em>Run Analysis</em> picks it up.
  </p>

  <form method="post" action="/slack-paste">
    <div class="field" style="margin-bottom:12px">
      <label>Filename (no extension)</label>
      <input type="text" name="filename" value="{{ default_filename }}"
             placeholder="e.g. 2026-10-12 Slack Canvas"
             style="max-width:420px">
      <div class="desc">Saved as <code>{{ inbox_path }}/&lt;filename&gt;.md</code></div>
    </div>

    <div class="field">
      <label>Canvas content</label>
      <textarea class="paste-area" name="content" placeholder="Paste your Slack canvas markdown here…"></textarea>
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
                _WATCH_KEYS = {"siemens-general", "presales"}
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
    _GROUP_ORDER = {"projects": 0, "presales": 1, "watch": 2}
    programs.sort(key=lambda p: (_GROUP_ORDER.get(p["group"], 9), p["label"]))
    return programs


_PROG_ORDER_PATH = Path("~/.config/transcript-analyzer/prog_order.json").expanduser()
_DEFAULT_PROG_ORDER = [
    "SI-RCA", "DISW-RCA", "DISW-CPQ", "siemens-general",
    "presales-sx-prd", "presales-energy", "presales-healthineers",
    "presales-financial-services", "_unassigned",
]


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


def _collect_pipeline_warnings() -> list[str]:
    """Return a list of short warning strings about pipeline health.

    Called once per page render; results cached in _last_render_warnings so
    the chip in the header stays consistent across navigations without
    re-running on each subrequest.
    """
    global _last_render_warnings
    warnings: list[str] = []
    try:
        # Surface staleness if last analysis is > 8 h old.
        rows = _analyzed_files()
        _, stale = _last_analyzed_at(rows)
        if stale == "danger":
            warnings.append("pipeline stale (>24 h)")
        elif stale == "warn":
            warnings.append("pipeline stale (>8 h)")
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

        def _group_by_program(items, row_key="rows"):
            by_prog: dict[str, list] = {}
            for item in items:
                k = item.get("program") or "_unassigned"
                by_prog.setdefault(k, []).append(item)
            groups = [
                {"key": k, "label": k if k != "_unassigned" else "Unassigned", row_key: by_prog[k]}
                for k in prog_order if k in by_prog
            ]
            for k, grp_items in by_prog.items():
                if k not in prog_order:
                    groups.append({"key": k, "label": k, row_key: grp_items})
            return groups

        act_groups = _group_by_program(unpinned_acts, "acts")
        watch_groups = _group_by_program(_bs.pending_watches(), "rows")
        delegate_items = [i for i in all_items if i.get("type") == "delegate" and i.get("status") == "pending"]
        delegate_groups = _group_by_program(delegate_items, "rows")
        they_owe_list = [_enrich_they_owe(i) for i in _bs.pending_they_owe()]
        they_owe_groups = _group_by_program(they_owe_list, "rows")
        closed_groups = _group_by_program(recent_closed, "rows")

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
            programs=_get_programs(),
            prog_order=prog_order,
            flash=request.args.get("flash", ""),
            flash_type=request.args.get("ft", "ok"),
            job_running=job_running,
            job_label=job_label,
            job_output=job_output,
            last_pulse=_last_pulse_info(),
            last_weekly=_last_weekly_info(),
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
        return redirect(url_for("brief_state_page", flash=msg, ft=ft))

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
        return redirect(url_for("brief_state_page", flash=msg, ft=ft))

    @app.post("/brief-state/pin/<item_id>")
    def brief_state_pin(item_id):
        from . import brief_state as _bs
        found = _bs.pin(item_id)
        msg = "Pinned to Today's Focus" if found else "Item not found"
        ft = "ok" if found else "err"
        return redirect(url_for("brief_state_page", flash=msg, ft=ft))

    @app.post("/brief-state/unpin/<item_id>")
    def brief_state_unpin(item_id):
        from . import brief_state as _bs
        found = _bs.unpin(item_id)
        msg = "Unpinned" if found else "Item not found"
        ft = "ok" if found else "err"
        return redirect(url_for("brief_state_page", flash=msg, ft=ft))

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
        return redirect(url_for("brief_state_page", flash=msg, ft=ft))

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
        return redirect(url_for("brief_state_page", flash="Re-owned", ft="ok"))

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
        return redirect(url_for("brief_state_page", flash=msg, ft=ft))

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
