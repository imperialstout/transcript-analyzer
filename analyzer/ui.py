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
_job: dict | None = None  # {"mode": str, "output": str, "done": bool, "returncode": int|None}

# argv for the /restart self-re-exec, set by main(). We can't trust the live
# sys.argv: by the time a route fires, __main__.py has already stripped the "ui"
# subcommand and sys.argv[0] points at __main__.py — re-execing that would launch
# an analysis run, not the UI. main() reconstructs the real `-m analyzer ui` form.
_REEXEC_ARGV: list[str] = []


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
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )
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
    with _job_lock:
        _job["done"] = True
        _job["returncode"] = proc.returncode


_SYNTHESIS_TAGS = ("[DAILY PULSE]", "[SLACK DELTA]", "[WEEKLY SUMMARY]")

_MODE_LABELS = {
    "daily": "Daily Pulse synthesis",
    "weekly": "Weekly Slack Delta synthesis",
    "career": "Career Trajectory review",
    "catch-up": "catch-up (one pulse per backlog day)",
    "timecard": "Weekly Timecard compilation",
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


_HTML = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Transcript Analyzer</title>
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
  header h1 { font-size: 15px; font-weight: 600; }
  nav { display: flex; gap: 4px; }
  nav a { color: var(--muted); text-decoration: none; font-size: 13px; padding: 6px 10px; border-radius: 6px; transition: color .15s, background .15s; }
  nav a:hover, nav a.active { color: var(--text); background: var(--border); }

  .content { padding: 24px; max-width: 1100px; }

  /* ── Button system: 3 variants + sm modifier ── */
  .btn, button { display: inline-flex; align-items: center; gap: 6px; border-radius: 6px;
                 padding: 8px 16px; font-size: 13px; font-weight: 600; cursor: pointer;
                 text-decoration: none; border: none; transition: opacity .15s; white-space: nowrap; }
  .btn, button { background: var(--accent); color: #fff; }
  .btn:hover, button:hover { opacity: .85; }
  .btn-secondary, button.secondary { background: var(--surface); color: var(--text); border: 1px solid var(--border); }
  .btn-secondary:hover, button.secondary:hover { background: var(--border); opacity: 1; }
  .btn-destructive, button.danger { background: var(--bg-danger); color: var(--red); border: 1px solid var(--border-danger); }
  .btn-destructive:hover, button.danger:hover { background: #4a2828; opacity: 1; }
  .btn-sm { padding: 3px 10px; font-size: 11px; border-radius: 4px; }

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
  .flash-ok  { background: #1b3628; color: var(--green); }
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
  .badge-delta     { background: var(--bg-insight);   color: var(--cyan); }
  .badge-synthesis { background: var(--bg-daily);    color: var(--green); }
  .badge-act       { background: var(--bg-act);      color: var(--red); }
  .badge-watch     { background: var(--bg-watch);    color: var(--orange); }
  .badge-delegate  { background: var(--bg-delegate); color: var(--accent); }
  .badge-they-owe  { background: var(--bg-they-owe); color: var(--purple); }
  .badge-insight   { background: var(--bg-insight);  color: var(--cyan); }
  .badge-pending   { background: var(--bg-daily);    color: var(--green); }
  .badge-complete  { background: var(--bg-complete); color: var(--green); }
  .badge-deferred  { background: var(--bg-deferred); color: var(--gold); }
  .badge-expired   { background: var(--bg-expired);  color: var(--red); }
  .badge-dropped   { background: #1e1e1e;            color: var(--muted); }
  .badge-overdue   { background: var(--bg-overdue);  color: var(--red); font-weight: 700; }

  /* ── Job panel ── */
  #job-status { background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
                padding: 16px; margin-top: 16px; display: none; }
  #job-status h3 { font-size: 12px; text-transform: uppercase; letter-spacing: .06em;
                   color: var(--muted); margin-bottom: 10px; }
  #job-output { font: 12px/1.6 "SF Mono", "Menlo", monospace; color: var(--text);
                white-space: pre-wrap; max-height: 320px; overflow-y: auto; }
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
  .toggle input { opacity: 0; width: 0; height: 0; }
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

  /* ── Triage alerts ── */
  .alert-banner { background: var(--bg-alert); border: 1px solid var(--border-alert); border-radius: 8px;
                  padding: 16px 20px; margin-bottom: 20px; }
  .alert-banner-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
  .alert-banner-title { font-size: 13px; font-weight: 700; color: var(--red); text-transform: uppercase; letter-spacing: .05em; }
  .alert-item { display: flex; align-items: flex-start; gap: 12px; padding: 8px 0;
                border-top: 1px solid var(--bg-alert-item); }
  .alert-item:first-of-type { border-top: none; }
  .alert-text { flex: 1; font-size: 13px; line-height: 1.5; }
  .alert-source { font-size: 11px; color: var(--muted); margin-top: 2px; }

  /* ── Brief state ── */
  .bs-item { display: flex; align-items: flex-start; gap: 12px; padding: 10px 0;
             border-top: 1px solid var(--border); }
  .bs-item:first-of-type { border-top: none; }
  .bs-body { flex: 1; min-width: 0; }
  .bs-text { font-size: 13px; line-height: 1.5; }
  .bs-meta { font-size: 11px; color: var(--muted); margin-top: 3px; }
  .bs-notes { font-size: 12px; color: var(--muted); font-style: italic; margin-top: 3px;
              white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 600px; }
  .bs-actions { display: flex; gap: 6px; flex-shrink: 0; align-items: center; flex-wrap: wrap; }
  .bs-defer-sep { width: 1px; height: 16px; background: var(--border); margin: 0 2px; flex-shrink: 0; }
  .bs-empty { color: var(--muted); font-size: 13px; padding: 12px 0; }
  .bs-item-overdue { background: rgba(247,111,111,0.07); margin: 0 -20px; padding-left: 20px; padding-right: 20px; }
  .bs-item-due-soon { background: rgba(247,162,79,0.06); margin: 0 -20px; padding-left: 20px; padding-right: 20px; }
  details.bs-section summary { cursor: pointer; list-style: none; display: flex; align-items: center; gap: 8px;
                                padding: 10px 0; color: var(--muted); font-size: 13px; user-select: none; }
  details.bs-section summary::-webkit-details-marker { display: none; }
  details.bs-section summary .bs-section-chevron { font-size: 10px; transition: transform 0.15s; }
  details.bs-section[open] summary .bs-section-chevron { transform: rotate(90deg); }

  /* ── Inbox card (candidates) ── */
  .card-inbox { background: var(--bg-inbox); border-color: var(--border-inbox); }
  .card-inbox h2 { color: var(--gold); }
</style>
</head>
<body>
<header>
  <h1>Transcript Analyzer</h1>
  <nav>
    <a href="/brief-state" class="{{ 'active' if page == 'brief-state' else '' }}">Brief State{% if pending_act_count %} <span style="background:var(--red);color:#fff;border-radius:10px;padding:0 6px;font-size:11px;font-weight:700;line-height:18px;display:inline-block;vertical-align:middle;margin-left:4px">{{ pending_act_count }}</span>{% endif %}</a>
    <a href="/files" class="{{ 'active' if page == 'home' else '' }}">Analyzed Files</a>
    <a href="/slack-paste" class="{{ 'active' if page == 'slack-paste' else '' }}">Slack Paste</a>
    <a href="/settings" class="{{ 'active' if page == 'settings' else '' }}">Settings</a>
  </nav>
  {% if shared_url %}
  <a class="btn" style="margin-left:auto" href="{{ shared_url }}" target="_blank" rel="noopener">
    ↗ Shared Meeting Files
  </a>
  {% endif %}
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
    const r = await fetch('/job-status');
    const d = await r.json();
    document.getElementById('job-output').textContent = d.output || '';
    const el = document.getElementById('job-output');
    el.scrollTop = el.scrollHeight;
    if (d.done) {
      clearInterval(_pollTimer); _pollTimer = null;
      if (indicator) indicator.style.display = 'none';
      if (d.returncode && d.returncode !== 0) {
        // Job failed (non-zero exit). Keep the panel up with the captured output
        // so the error is visible — do NOT reload it away. (returncode 2 is a
        // soft warning: synthesis written but archive skipped — still surface it.)
        const done = document.getElementById('job-done');
        done.style.display = 'inline';
        done.style.color = 'var(--red)';
        done.textContent = 'Failed (exit ' + d.returncode + ') — output below, not refreshing.';
      } else {
        document.getElementById('job-done').style.display = 'inline';
        // Reload page so the new synthesis file appears in the table
        setTimeout(() => location.reload(), 1500);
      }
    }
  }, 1200);
}
// Auto-resume polling if server reports a job in-flight when page loads
(function() {
  {% if job_running %}
  startPoll('{{ job_label }}…');
  {% endif %}
})();
</script>
</body>
</html>
"""

_HOME_BODY = """\
{% if flash %}<div class="flash flash-{{ flash_type }}">{{ flash }}</div>{% endif %}
{% if job_running %}
<div class="flash" style="background:var(--bg-job-run);color:var(--accent);display:flex;align-items:center;gap:8px">
  <span class="spinner"></span>
  <strong>Running {{ job_label }}…</strong> This takes a few minutes. The page will refresh automatically when done.
</div>
{% endif %}

{% if triage_alerts %}
<div class="alert-banner">
  <div class="alert-banner-header">
    <span class="alert-banner-title">⚠ {{ triage_alerts|length }} Triage Alert{{ 's' if triage_alerts|length != 1 else '' }} — Action Required</span>
    <form method="post" action="/alerts/dismiss-all" style="display:inline">
      <button type="submit" class="btn-secondary btn-sm">Dismiss all</button>
    </form>
  </div>
  {% for a in triage_alerts %}
  <div class="alert-item">
    <div style="flex:1">
      <div class="alert-text">{{ a.text }}</div>
      <div class="alert-source">{{ a.source }}{% if a.source_date %} · {{ a.source_date }}{% endif %}</div>
    </div>
    <form method="post" action="/alerts/dismiss/{{ a.id }}" style="display:inline">
      <button type="submit" class="btn-destructive btn-sm">Dismiss</button>
    </form>
  </div>
  {% endfor %}
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
      <td>
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

<form method="post" action="/settings">
<div class="card">
  <h2>Configuration</h2>
  <p class="muted" style="margin-bottom:18px;font-size:12px">
    Values are saved to <code style="color:var(--text)">~/.config/transcript-analyzer/.env</code>.
    Restart the analyzer (or the launchd job) for changes to take effect.
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

  <button type="submit" class="btn-secondary">Save settings</button>
</div>
</form>

<hr class="sep">

<div class="card">
  <h2>Edit content files</h2>
  <p class="section-title">Drive files (open in editor)</p>
  {% for label, path in drive_files %}
  <div style="margin-bottom:8px">
    <a class="quick-link" href="/open-file?path={{ path }}">
      ✏ {{ label }}
      <span class="muted" style="font-size:11px">{{ path }}</span>
    </a>
  </div>
  {% endfor %}
</div>

<hr class="sep">

<div class="card">
  <h2>Diagnostics</h2>
  <a class="quick-link" href="/open-log">📋 Open run log</a>
  <span class="muted" style="font-size:11px;margin-left:8px">{{ log_path }}</span>

  <p class="section-title" style="margin-top:18px">Server</p>
  <form method="post" action="/restart" style="display:inline"
        onsubmit="return confirm('Restart the dashboard server? This reloads code and .env changes. Any in-progress job will block the restart.');">
    <button type="submit" class="btn-secondary">↻ Restart server</button>
  </form>
  <div class="desc">Re-execs the server in place to pick up code or <code>.env</code> changes. The analysis itself already runs fresh each time — this only refreshes the long-lived UI process.</div>
</div>
"""


_BRIEF_STATE_BODY = """\
{% if flash %}<div class="flash flash-{{ flash_type }}">{{ flash }}</div>{% endif %}
{% if job_running %}
<div class="flash" style="background:var(--bg-job-run);color:var(--accent);display:flex;align-items:center;gap:8px">
  <span class="spinner"></span>
  <strong>Running {{ job_label }}…</strong> The page will refresh when done.
</div>
{% endif %}

<div class="card">
  <h2>Operations</h2>
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
        <button type="submit" class="btn-secondary">▶ Daily Pulse</button>
        <input type="date" name="date" title="Leave blank for today; pick a past date to back-date">
      </form>
    </div>
  </div>
  <div class="action-tier">
    <span class="action-tier-label">Periodic</span>
    <div class="action-group">
      <form method="post" action="/synthesize" onsubmit="startPoll('Running Slack Delta synthesis…')" style="display:flex;gap:6px;align-items:center">
        <input type="hidden" name="mode" value="weekly">
        <button type="submit" class="btn-secondary">▶ Slack Delta</button>
        <input type="date" name="date" title="Leave blank for this week">
      </form>
    </div>
    <div class="action-group">
      <form method="post" action="/synthesize" onsubmit="startPoll('Running catch-up — one pulse per backlog day…')" style="display:flex;gap:6px;align-items:center">
        <input type="hidden" name="mode" value="catch-up">
        <button type="submit" class="btn-secondary">▶ Catch Up</button>
        <input type="date" name="since" title="Start from this date (leave blank for full backlog)">
      </form>
    </div>
    <div class="action-group">
      <form method="post" action="/synthesize" onsubmit="startPoll('Running Career Trajectory review…')" style="display:flex;gap:6px;align-items:center">
        <input type="hidden" name="mode" value="career">
        <button type="submit" class="btn-secondary">▶ Career</button>
        <label style="font-size:11px;color:var(--muted);display:inline-flex;align-items:center;gap:4px"
               title="Re-read ALL analyses to re-baseline. Off = incremental.">
          <input type="checkbox" name="full" value="1"> full
        </label>
      </form>
    </div>
    <div class="action-group">
      <form method="post" action="/synthesize" onsubmit="startPoll('Compiling Weekly Timecard…')" style="display:flex;gap:6px;align-items:center">
        <input type="hidden" name="mode" value="timecard">
        <button type="submit" class="btn-secondary">▶ Timecard</button>
        <input type="date" name="date" title="Leave blank for current week">
      </form>
    </div>
  </div>
</div>

<div id="job-status" {% if not job_running %}style="display:none"{% endif %}>
  <h3>
    <span id="job-indicator"><span class="spinner"></span> Running {{ job_label or "job" }}…</span>
    <span id="job-done" style="display:none">Done — refreshing…</span>
  </h3>
  <div id="job-output">{{ job_output }}</div>
</div>

{% if candidate_items %}
<div class="card card-inbox">
  <h2>Inbox — signals pending triage ({{ candidate_items|length }})</h2>
  {% for item in candidate_items %}
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
    <div class="bs-actions">
      <form method="post" action="/brief-state/accept/{{ item.id }}">
        <input type="hidden" name="type" value="{{ item.type }}">
        <button type="submit" class="btn-secondary btn-sm" style="border-color:var(--gold);color:var(--gold)">Accept</button>
      </form>
      <form method="post" action="/brief-state/dismiss-candidate/{{ item.id }}">
        <button type="submit" class="btn-destructive btn-sm">Dismiss</button>
      </form>
    </div>
  </div>
  {% endfor %}
</div>
{% endif %}

<div class="card" style="{% if act_items|selectattr('overdue')|list %}border-color:var(--border-alert){% endif %}">
  <h2>Act items — Brad owns</h2>
  {% if act_items %}
  {% for item in act_items %}
  <div class="bs-item{% if item.overdue %} bs-item-overdue{% elif item.deadline and item.deadline <= today_iso %} bs-item-due-soon{% endif %}">
    <div class="bs-body">
      <div class="bs-text">
        {% if item.overdue %}<span class="badge badge-overdue">OVERDUE</span> {% endif %}
        {{ item.text }}
      </div>
      <div class="bs-meta">
        {% if item.deadline %}Due {{ item.deadline }}{% if item.overdue %} ({{ item.days_overdue }}d overdue){% endif %} · {% endif %}
        From {{ item.source_brief[:10] if item.source_brief else '—' }}
      </div>
      {% if item.notes %}<div class="bs-notes">{{ item.notes }}</div>{% endif %}
    </div>
    <div class="bs-actions">
      <form method="post" action="/brief-state/close/{{ item.id }}">
        <input type="hidden" name="status" value="complete">
        <button type="submit" class="btn-secondary btn-sm">Done</button>
      </form>
      <form method="post" action="/brief-state/close/{{ item.id }}">
        <input type="hidden" name="status" value="dropped">
        <button type="submit" class="btn-destructive btn-sm">Drop</button>
      </form>
      <div class="bs-defer-sep"></div>
      <button type="button" class="btn-secondary btn-sm" onclick="deferQuick('{{ item.id }}',1)" title="Defer 1 day">+1d</button>
      <button type="button" class="btn-secondary btn-sm" onclick="deferQuick('{{ item.id }}',3)" title="Defer 3 days">+3d</button>
      <button type="button" class="btn-secondary btn-sm" onclick="deferQuick('{{ item.id }}',0)" title="Defer to Monday">Mon</button>
      <form method="post" action="/brief-state/defer/{{ item.id }}" style="display:flex;gap:4px;align-items:center">
        <input type="date" name="target" required title="Defer to a specific date" style="width:130px">
        <button type="submit" class="btn-secondary btn-sm">Defer</button>
      </form>
    </div>
  </div>
  {% endfor %}
  {% else %}
  <div class="bs-empty" style="display:flex;align-items:center;gap:8px">
    <span style="color:var(--green);font-size:15px">✓</span>
    All clear — no pending acts.
  </div>
  {% endif %}
</div>

{% if watch_items %}
<div class="card">
  <details class="bs-section">
    <summary>
      <span class="bs-section-chevron">▶</span>
      Watch
      <span class="badge badge-watch" style="margin-left:4px">{{ watch_items|length }}</span>
    </summary>
    {% for item in watch_items %}
    <div class="bs-item">
      <div class="bs-body">
        <div class="bs-text">{{ item.text }}</div>
        <div class="bs-meta">From {{ item.source_brief[:10] if item.source_brief else '—' }}</div>
        {% if item.notes %}<div class="bs-notes">{{ item.notes }}</div>{% endif %}
      </div>
      <div class="bs-actions">
        <form method="post" action="/brief-state/close/{{ item.id }}">
          <input type="hidden" name="status" value="complete">
          <button type="submit" class="btn-secondary btn-sm">Done</button>
        </form>
        <form method="post" action="/brief-state/close/{{ item.id }}">
          <input type="hidden" name="status" value="dropped">
          <button type="submit" class="btn-destructive btn-sm">Drop</button>
        </form>
      </div>
    </div>
    {% endfor %}
  </details>
</div>
{% endif %}

{% if delegate_items %}
<div class="card">
  <details class="bs-section">
    <summary>
      <span class="bs-section-chevron">▶</span>
      Delegated
      <span class="badge badge-delegate" style="margin-left:4px">{{ delegate_items|length }}</span>
    </summary>
    {% for item in delegate_items %}
    <div class="bs-item">
      <div class="bs-body">
        <div class="bs-text">{{ item.text }}</div>
        <div class="bs-meta">
          Owner: <strong>{{ item.owner }}</strong>
          {% if item.deadline %} · Due {{ item.deadline }}{% endif %}
          {% if item.resurfaced_count %} · Re-surfaced {{ item.resurfaced_count }}×{% endif %}
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
      </div>
    </div>
    {% endfor %}
  </details>
</div>
{% endif %}

{% if they_owe_items %}
<div class="card">
  <details class="bs-section">
    <summary>
      <span class="bs-section-chevron">▶</span>
      They owe you
      <span class="badge badge-they-owe" style="margin-left:4px">{{ they_owe_items|length }}</span>
    </summary>
    {% for item in they_owe_items %}
    <div class="bs-item">
      <div class="bs-body">
        <div class="bs-text">{{ item.text }}</div>
        <div class="bs-meta">
          <strong>{{ item.owner }}</strong>
          {% if item.deadline %} · Due {{ item.deadline }}{% if item.they_overdue %} <span style="color:var(--red)">({{ item.days_overdue }}d overdue)</span>{% endif %}{% endif %}
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
      </div>
    </div>
    {% endfor %}
  </details>
</div>
{% endif %}

{% if recent_closed %}
<div class="card">
  <h2>Closed (last 7 days)</h2>
  <table>
    <thead>
      <tr><th>Item</th><th>Type</th><th>Status</th><th>Closed</th></tr>
    </thead>
    <tbody>
    {% for item in recent_closed %}
    <tr>
      <td>{{ item.text }}</td>
      <td><span class="badge badge-{{ item.type }}">{{ item.type }}</span></td>
      <td><span class="badge badge-{{ item.status }}">{{ item.status }}</span></td>
      <td class="muted">{{ item.closed_at[:10] if item.closed_at else '—' }}</td>
    </tr>
    {% endfor %}
    </tbody>
  </table>
</div>
{% endif %}
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


def _pending_act_count() -> int:
    try:
        from . import brief_state as _bs
        return len(_bs.pending_acts()) + len(_bs.pending_candidates())
    except Exception:
        return 0


def _render(template: str, **ctx) -> str:
    from jinja2 import Environment
    env = Environment(autoescape=False)
    wrapper = env.from_string(_HTML)
    body_tmpl = env.from_string(template)
    body = body_tmpl.render(**ctx)
    ctx.setdefault("pending_act_count", _pending_act_count())
    return wrapper.render(body=body, shared_url=_shared_folder_url(), **ctx)


def create_app():
    from flask import Flask, redirect, request, url_for
    from .config import CONFIG

    app = Flask(__name__)
    app.secret_key = "ta-ui-local-only"

    @app.get("/")
    def home():
        return redirect(url_for("files_page"))

    @app.get("/files")
    def files_page():
        from . import alerts as _alerts
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
            triage_alerts=_alerts.undismissed(),
        )

    @app.post("/alerts/dismiss/<alert_id>")
    def dismiss_alert(alert_id):
        from . import alerts as _alerts
        _alerts.dismiss(alert_id)
        return redirect(url_for("brief_state_page"))

    @app.post("/alerts/dismiss-all")
    def dismiss_all_alerts():
        from . import alerts as _alerts
        _alerts.dismiss_all()
        return redirect(url_for("brief_state_page", flash="All alerts dismissed", ft="ok"))

    @app.post("/synthesize")
    def synthesize():
        global _job
        from datetime import datetime as _dt
        mode = request.form.get("mode", "daily")
        if mode not in ("daily", "weekly", "career", "catch-up", "timecard"):
            return redirect(url_for("brief_state_page", flash="Invalid mode", ft="err"))

        # Optional back-date for daily/weekly/timecard (recover a missed run). Ignored for career.
        date_arg = request.form.get("date", "").strip()
        if date_arg and mode in ("daily", "weekly", "timecard"):
            try:
                _dt.strptime(date_arg, "%Y-%m-%d")
            except ValueError:
                return redirect(url_for("brief_state_page", flash=f"Invalid date {date_arg!r} (need YYYY-MM-DD)", ft="err"))
        else:
            date_arg = ""

        # catch-up only: optional lower bound on which backlog days to synthesize.
        since_arg = request.form.get("since", "").strip()
        if since_arg and mode == "catch-up":
            try:
                _dt.strptime(since_arg, "%Y-%m-%d")
            except ValueError:
                return redirect(url_for("brief_state_page", flash=f"Invalid since-date {since_arg!r} (need YYYY-MM-DD)", ft="err"))
        else:
            since_arg = ""

        # career only: re-baseline by re-reading all analyses (default is incremental).
        full = bool(request.form.get("full")) and mode == "career"

        with _job_lock:
            if _job and not _job["done"]:
                return redirect(url_for("brief_state_page", flash="A synthesis job is already running", ft="err"))
            _job = {"mode": mode, "output": "", "done": False, "returncode": None}

        cmd = [sys.executable, "-u", "-m", "analyzer", "synthesize", "--mode", mode]
        if date_arg:
            cmd += ["--date", date_arg]
        if since_arg:
            cmd += ["--since", since_arg]
        if full:
            cmd.append("--full")
        threading.Thread(target=_run_job, args=(cmd, mode), daemon=True).start()
        return redirect(url_for("brief_state_page"))

    @app.post("/run-analysis")
    def run_analysis():
        global _job
        with _job_lock:
            if _job and not _job["done"]:
                return redirect(url_for("brief_state_page", flash="A job is already running", ft="err"))
            _job = {"mode": "analysis", "output": "", "done": False, "returncode": None}

        cmd = [sys.executable, "-u", "-m", "analyzer"]
        threading.Thread(target=_run_job, args=(cmd, "analysis"), daemon=True).start()
        return redirect(url_for("brief_state_page"))

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

        # Sanitize: strip path separators, ensure .md extension
        safe_name = raw_filename.replace("/", "-").replace("\\", "-")
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

        all_items = _bs.all_items()
        cutoff = (today.isoformat()[:8] + "01")  # rough 7-day filter via string sort
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
                return (0 if overdue else 1, dl or "9999-99-99")
            return sorted(items, key=_key)

        raw_acts = [_enrich_act(i) for i in _bs.pending_acts()]
        return _render(
            _BRIEF_STATE_BODY,
            page="brief-state",
            today_iso=today.isoformat(),
            candidate_items=_bs.pending_candidates(),
            act_items=_sort_acts(raw_acts),
            watch_items=_bs.pending_watches(),
            delegate_items=[i for i in all_items if i.get("type") == "delegate" and i.get("status") == "pending"],
            they_owe_items=[_enrich_they_owe(i) for i in _bs.pending_they_owe()],
            recent_closed=recent_closed,
            flash=request.args.get("flash", ""),
            flash_type=request.args.get("ft", "ok"),
            job_running=job_running,
            job_label=job_label,
            job_output=job_output,
        )

    @app.post("/brief-state/accept/<item_id>")
    def brief_state_accept(item_id):
        from . import brief_state as _bs
        item_type = request.form.get("type", "act")
        try:
            found = _bs.accept_candidate(item_id, item_type)
        except ValueError as e:
            return redirect(url_for("brief_state_page", flash=str(e), ft="err"))
        msg = f"Accepted as {item_type}" if found else "Item not found"
        ft = "ok" if found else "err"
        return redirect(url_for("brief_state_page", flash=msg, ft=ft))

    @app.post("/brief-state/dismiss-candidate/<item_id>")
    def brief_state_dismiss_candidate(item_id):
        from . import brief_state as _bs
        found = _bs.dismiss_candidate(item_id)
        msg = "Dismissed" if found else "Item not found"
        ft = "ok" if found else "err"
        return redirect(url_for("brief_state_page", flash=msg, ft=ft))

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

    @app.post("/brief-state/re-own/<item_id>")
    def brief_state_re_own(item_id):
        from . import brief_state as _bs
        items = _bs.all_items()
        # Flip type back to act, owner back to Brad
        state_path = Path("~/.cache/transcript-analyzer/brief-state.json").expanduser()
        raw = __import__("json").loads(state_path.read_text())
        found = False
        for item in raw:
            if item["id"] == item_id:
                item["type"] = "act"
                item["owner"] = "Brad"
                item["notes"] = (item.get("notes") or "") + "; re-owned via UI"
                found = True
                break
        if found:
            state_path.write_text(__import__("json").dumps(raw, indent=2))
        msg = "Re-owned" if found else "Item not found"
        ft = "ok" if found else "err"
        return redirect(url_for("brief_state_page", flash=msg, ft=ft))

    @app.post("/restart")
    def restart():
        # Refuse while a job is running — re-exec would orphan the worker thread
        # and the user would lose the in-flight analysis/synthesis output.
        with _job_lock:
            if _job and not _job["done"]:
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
