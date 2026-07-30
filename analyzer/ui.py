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


def _last_analyzed_at(rows: list[dict]) -> str:
    """Return the most recent analyzed_at timestamp across all manifest entries."""
    timestamps = [r["analyzed_at"] for r in rows if r.get("analyzed_at")]
    if not timestamps:
        return ""
    ts = max(timestamps)
    # Format as "Jun 6, 2026 14:32" for display
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%b %-d, %Y %H:%M")
    except Exception:
        return ts[:16]


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
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font: 14px/1.5 "SF Pro Text", system-ui, sans-serif; }
  header { background: var(--surface); border-bottom: 1px solid var(--border);
           padding: 12px 24px; display: flex; align-items: center; gap: 16px; }
  header h1 { font-size: 15px; font-weight: 600; }
  nav a { color: var(--muted); text-decoration: none; font-size: 13px; padding: 6px 10px;
          border-radius: 6px; }
  nav a:hover, nav a.active { color: var(--text); background: var(--border); }
  nav { display: flex; gap: 4px; }
  .content { padding: 24px; max-width: 1100px; }
  .stats { display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }
  .stat { background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
          padding: 14px 20px; min-width: 140px; }
  .stat .val { font-size: 22px; font-weight: 700; }
  .stat .lbl { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; margin-top: 2px; }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
          padding: 20px; margin-bottom: 20px; }
  .card h2 { font-size: 13px; font-weight: 600; text-transform: uppercase;
             letter-spacing: .06em; color: var(--muted); margin-bottom: 14px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; color: var(--muted); font-size: 11px; text-transform: uppercase;
       letter-spacing: .05em; padding: 6px 10px; border-bottom: 1px solid var(--border); }
  td { padding: 7px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  .badge { display: inline-block; padding: 2px 7px; border-radius: 4px; font-size: 11px;
           font-weight: 600; text-transform: uppercase; letter-spacing: .04em; }
  .badge-exec   { background: #3a2020; color: var(--red); }
  .badge-solution { background: #1e2c3a; color: var(--accent); }
  .badge-daily  { background: #1e3a2c; color: var(--green); }
  .badge-standup { background: #2a2c1e; color: var(--orange); }
  .badge-notes  { background: #2a1e3a; color: #b08ef7; }
  .badge-pulse  { background: #1e3020; color: #5ecf6e; }
  .badge-delta  { background: #1e2820; color: #3ecf8e; }
  .badge-synthesis { background: #1e2820; color: #3ecf8e; }
  .muted { color: var(--muted); }
  .actions { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 20px; }
  button, .btn { background: var(--accent); color: #fff; border: none; border-radius: 6px;
                  padding: 8px 16px; font-size: 13px; font-weight: 600; cursor: pointer;
                  text-decoration: none; display: inline-flex; align-items: center; gap: 6px; }
  button:hover, .btn:hover { opacity: .85; }
  button.secondary { background: var(--surface); color: var(--text);
                     border: 1px solid var(--border); }
  button.secondary:hover { background: var(--border); }
  button.danger { background: #3a1f1f; color: var(--red); border: 1px solid #5a2e2e; }
  .flash { padding: 10px 16px; border-radius: 6px; margin-bottom: 16px; font-size: 13px; }
  .flash-ok  { background: #1b3628; color: var(--green); }
  .flash-err { background: #3a1f1f; color: var(--red); }
  #job-status { background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
                padding: 16px; margin-top: 16px; display: none; }
  #job-status h3 { font-size: 12px; text-transform: uppercase; letter-spacing: .06em;
                   color: var(--muted); margin-bottom: 10px; }
  #job-output { font: 12px/1.6 "SF Mono", "Menlo", monospace; color: var(--text);
                white-space: pre-wrap; max-height: 320px; overflow-y: auto; }
  .spinner { display: inline-block; width: 12px; height: 12px; border: 2px solid var(--border);
             border-top-color: var(--accent); border-radius: 50%; animation: spin .7s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  /* Settings */
  .field { margin-bottom: 18px; }
  .field label { display: block; font-size: 12px; font-weight: 600; color: var(--muted);
                 text-transform: uppercase; letter-spacing: .05em; margin-bottom: 5px; }
  .field input[type=text], .field input[type=url] {
    background: var(--bg); border: 1px solid var(--border); border-radius: 6px;
    color: var(--text); font-size: 13px; padding: 7px 10px; width: 100%; }
  .field input:focus { outline: none; border-color: var(--accent); }
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
  .quick-link { display: inline-flex; align-items: center; gap: 6px; color: var(--accent);
                text-decoration: none; font-size: 12px; padding: 5px 0; }
  .quick-link:hover { text-decoration: underline; }
  hr.sep { border: none; border-top: 1px solid var(--border); margin: 24px 0; }
  .section-title { font-size: 11px; font-weight: 700; text-transform: uppercase;
                   letter-spacing: .07em; color: var(--muted); margin-bottom: 14px; }
</style>
</head>
<body>
<header>
  <h1>Transcript Analyzer</h1>
  <nav>
    <a href="/" class="{{ 'active' if page == 'home' else '' }}">Analyzed Files</a>
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
<div class="flash" style="background:#1a2540;color:var(--accent);display:flex;align-items:center;gap:8px">
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
  <div class="stat"><div class="val" style="font-size:13px">{{ last_analyzed }}</div><div class="lbl">Last analyzed</div></div>
  {% endif %}
</div>

<div class="actions">
  <form method="post" action="/run-analysis" style="display:inline" onsubmit="startPoll('Running transcript analysis…')">
    <button type="submit" class="secondary">⚡ Run Analysis</button>
  </form>
  <form method="post" action="/synthesize" style="display:inline" onsubmit="startPoll('Running Daily Pulse synthesis…')">
    <input type="hidden" name="mode" value="daily">
    <input type="date" name="date" title="Leave blank for today; pick a past date to back-date a missed run">
    <button type="submit">▶ Daily Pulse</button>
  </form>
  <form method="post" action="/synthesize" style="display:inline" onsubmit="startPoll('Running Weekly Slack Delta…')">
    <input type="hidden" name="mode" value="weekly">
    <input type="date" name="date" title="Leave blank for this week; pick a date in the week to back-date">
    <button type="submit" class="secondary">▶ Weekly Slack Delta</button>
  </form>
  <form method="post" action="/synthesize" style="display:inline" onsubmit="startPoll('Running catch-up — one pulse per backlog day…')">
    <input type="hidden" name="mode" value="catch-up">
    <input type="date" name="since" title="Vacation recovery: emits one Daily Pulse per backlog day. Pick the date you want to start from (e.g. your first day away); leave blank for the whole backlog.">
    <button type="submit" class="secondary" title="One Daily Pulse per backlog day, in order. For catching up after time away.">▶ Catch Up</button>
  </form>
  <form method="post" action="/synthesize" style="display:inline" onsubmit="startPoll('Running Career Trajectory review…')">
    <input type="hidden" name="mode" value="career">
    <label class="muted" style="font-size:12px;display:inline-flex;align-items:center;gap:4px"
           title="Re-read ALL analyses to re-baseline the trajectory. Off = incremental (only files filed since the last review).">
      <input type="checkbox" name="full" value="1"> full re-read
    </label>
    <button type="submit" class="secondary">▶ Career Trajectory</button>
  </form>
</div>

<div id="job-status" {% if not job_running %}style="display:none"{% endif %}>
  <h3>
    <span id="job-indicator"><span class="spinner"></span> Running {{ job_label or "job" }}…</span>
    <span id="job-done" style="display:none">Done — refreshing…</span>
  </h3>
  <div id="job-output">{{ job_output }}</div>
</div>

<div class="card">
  <h2>Analyzed files (most recent first)</h2>
  {% if rows %}
  <table>
    <thead>
      <tr>
        <th>Date</th><th>Source</th><th>Category</th><th>Model</th><th>Cost</th><th>Shareable</th>
      </tr>
    </thead>
    <tbody>
    {% for r in rows %}
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
  {% else %}
  <p class="muted">No analyzed files yet.</p>
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

  <button type="submit">Save settings</button>
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
    <button type="submit">↻ Restart server</button>
  </form>
  <div class="desc">Re-execs the server in place to pick up code or <code>.env</code> changes. The analysis itself already runs fresh each time — this only refreshes the long-lived UI process.</div>
</div>
"""


def _render(template: str, **ctx) -> str:
    from jinja2 import Environment
    env = Environment(autoescape=False)
    wrapper = env.from_string(_HTML)
    body_tmpl = env.from_string(template)
    body = body_tmpl.render(**ctx)
    return wrapper.render(body=body, shared_url=_shared_folder_url(), **ctx)


def create_app():
    from flask import Flask, redirect, request, url_for
    from .config import CONFIG

    app = Flask(__name__)
    app.secret_key = "ta-ui-local-only"

    @app.get("/")
    def home():
        rows = _analyzed_files()
        flash = request.args.get("flash", "")
        flash_type = request.args.get("ft", "ok")
        from .config import CONFIG
        with _job_lock:
            job_running = bool(_job and not _job["done"])
            job_label = _MODE_LABELS.get(_job["mode"], "job") if _job else ""
            job_output = _job["output"] if _job else ""
            job_done = _job["done"] if _job else True
        return _render(
            _HOME_BODY,
            page="home",
            rows=rows,
            total_cost=_total_cost(rows),
            last_analyzed=_last_analyzed_at(rows),
            analyzed_path=str(CONFIG.analyzed_path),
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
        if mode not in ("daily", "weekly", "career", "catch-up"):
            return redirect(url_for("home", flash="Invalid mode", ft="err"))

        # Optional back-date for daily/weekly (recover a missed run). Ignored for career.
        date_arg = request.form.get("date", "").strip()
        if date_arg and mode in ("daily", "weekly"):
            try:
                _dt.strptime(date_arg, "%Y-%m-%d")
            except ValueError:
                return redirect(url_for("home", flash=f"Invalid date {date_arg!r} (need YYYY-MM-DD)", ft="err"))
        else:
            date_arg = ""

        # catch-up only: optional lower bound on which backlog days to synthesize.
        since_arg = request.form.get("since", "").strip()
        if since_arg and mode == "catch-up":
            try:
                _dt.strptime(since_arg, "%Y-%m-%d")
            except ValueError:
                return redirect(url_for("home", flash=f"Invalid since-date {since_arg!r} (need YYYY-MM-DD)", ft="err"))
        else:
            since_arg = ""

        # career only: re-baseline by re-reading all analyses (default is incremental).
        full = bool(request.form.get("full")) and mode == "career"

        with _job_lock:
            if _job and not _job["done"]:
                return redirect(url_for("home", flash="A synthesis job is already running", ft="err"))
            _job = {"mode": mode, "output": "", "done": False, "returncode": None}

        cmd = [sys.executable, "-u", "-m", "analyzer", "synthesize", "--mode", mode]
        if date_arg:
            cmd += ["--date", date_arg]
        if since_arg:
            cmd += ["--since", since_arg]
        if full:
            cmd.append("--full")
        threading.Thread(target=_run_job, args=(cmd, mode), daemon=True).start()
        return redirect(url_for("home"))

    @app.post("/run-analysis")
    def run_analysis():
        global _job
        with _job_lock:
            if _job and not _job["done"]:
                return redirect(url_for("home", flash="A job is already running", ft="err"))
            _job = {"mode": "analysis", "output": "", "done": False, "returncode": None}

        cmd = [sys.executable, "-u", "-m", "analyzer"]
        threading.Thread(target=_run_job, args=(cmd, "analysis"), daemon=True).start()
        return redirect(url_for("home"))

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
