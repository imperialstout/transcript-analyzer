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
from pathlib import Path

_ENV_PATH = Path("~/.config/transcript-analyzer/.env").expanduser()
_LOG_PATH = Path("~/Library/Logs/transcript-analyzer-launchd.log").expanduser()

# Env vars the settings page exposes.  Tuple of (key, label, kind, description).
# kind: "text" | "url" | "toggle"
_SETTINGS_FIELDS = [
    ("SHARED_FOLDER_URL", "Shared meeting files URL", "url",
     "Link to the shared Drive / SharePoint folder where you upload shareable docs."),
    ("ROLODEX_PATH", "People rolodex path", "text",
     "Absolute path to 04_people_rolodex.md (leave blank to use Drive default)."),
    ("VOCABULARY_PATH", "Term glossary path", "text",
     "Absolute path to 05_plaud_vocabulary.md (leave blank to use Drive default)."),
    ("SHAREABLE_PASS", "Enable shareable redaction pass", "toggle",
     "Generate a redacted [SHAREABLE] sibling for every analysis."),
    ("BACKEND", "Execution backend", "text",
     "claude-cli (default, uses your Code seat) or api (uses ANTHROPIC_API_KEY)."),
    ("MODEL_OVERRIDE", "Model override", "text",
     "Force a specific model for all analysis runs (e.g. claude-opus-4-7). Leave blank for per-category defaults."),
]

# Background job state (simple — only one synthesis can run at a time).
_job_lock = threading.Lock()
_job: dict | None = None  # {"mode": str, "output": str, "done": bool, "returncode": int|None}


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


_SYNTHESIS_TAGS = ("[DAILY PULSE]", "[SLACK DELTA]", "[WEEKLY SUMMARY]")


def _analyzed_files() -> list[dict]:
    from .config import CONFIG
    from .manifest import load as load_manifest
    manifest = load_manifest()
    rows = []
    for src, entry in sorted(manifest.items(), key=lambda x: x[1].get("analyzed_at", ""), reverse=True):
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

    # Synthesis outputs ([DAILY PULSE], [SLACK DELTA]) are never in the manifest —
    # scan Analyzed/ directly and append them.
    try:
        import os
        from datetime import datetime as _dt
        analyzed_path = CONFIG.analyzed_path
        for fname in sorted(os.listdir(analyzed_path), reverse=True):
            if not any(tag in fname for tag in _SYNTHESIS_TAGS):
                continue
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
      document.getElementById('job-done').style.display = 'inline';
    }
  }, 1200);
}
</script>
</body>
</html>
"""

_HOME_BODY = """\
{% if flash %}<div class="flash flash-{{ flash_type }}">{{ flash }}</div>{% endif %}

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
    <button type="submit">▶ Daily Pulse</button>
  </form>
  <form method="post" action="/synthesize" style="display:inline" onsubmit="startPoll('Running Weekly Slack Delta…')">
    <input type="hidden" name="mode" value="weekly">
    <button type="submit" class="secondary">▶ Weekly Slack Delta</button>
  </form>
  <form method="post" action="/synthesize" style="display:inline" onsubmit="startPoll('Running Career Trajectory review…')">
    <input type="hidden" name="mode" value="career">
    <button type="submit" class="secondary">▶ Career Trajectory</button>
  </form>
</div>

<div id="job-status">
  <h3>
    <span id="job-indicator"><span class="spinner"></span> Running synthesis…</span>
    <span id="job-done" style="display:none">Done</span>
  </h3>
  <div id="job-output"></div>
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
  <a class="quick-link" href="/open-log">📋 Open launchd log</a>
  <span class="muted" style="font-size:11px;margin-left:8px">{{ log_path }}</span>
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
        return _render(
            _HOME_BODY,
            page="home",
            rows=rows,
            total_cost=_total_cost(rows),
            last_analyzed=_last_analyzed_at(rows),
            analyzed_path=str(CONFIG.analyzed_path),
            flash=flash,
            flash_type=flash_type,
        )

    @app.post("/synthesize")
    def synthesize():
        global _job
        mode = request.form.get("mode", "daily")
        if mode not in ("daily", "weekly", "career"):
            return redirect(url_for("home", flash="Invalid mode", ft="err"))

        with _job_lock:
            if _job and not _job["done"]:
                return redirect(url_for("home", flash="A synthesis job is already running", ft="err"))
            _job = {"mode": mode, "output": "", "done": False, "returncode": None}

        def _run():
            global _job
            python = sys.executable
            proc = subprocess.Popen(
                [python, "-m", "analyzer", "synthesize", "--mode", mode],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(Path(__file__).parent.parent),
            )
            buf = []
            for line in proc.stdout:
                buf.append(line)
                with _job_lock:
                    _job["output"] = "".join(buf)
            proc.wait()
            with _job_lock:
                _job["done"] = True
                _job["returncode"] = proc.returncode

        threading.Thread(target=_run, daemon=True).start()
        return redirect(url_for("home"))

    @app.post("/run-analysis")
    def run_analysis():
        global _job
        with _job_lock:
            if _job and not _job["done"]:
                return redirect(url_for("home", flash="A job is already running", ft="err"))
            _job = {"mode": "analysis", "output": "", "done": False, "returncode": None}

        def _run():
            global _job
            python = sys.executable
            proc = subprocess.Popen(
                [python, "-m", "analyzer"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(Path(__file__).parent.parent),
            )
            buf = []
            for line in proc.stdout:
                buf.append(line)
                with _job_lock:
                    _job["output"] = "".join(buf)
            proc.wait()
            with _job_lock:
                _job["done"] = True
                _job["returncode"] = proc.returncode

        threading.Thread(target=_run, daemon=True).start()
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
            ("Term Glossary (05_plaud_vocabulary.md)", str(CONFIG.vocabulary_path)),
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
        return redirect(url_for("settings", flash="Opened log in Console.app", ft="ok"))

    return app


def main(port: int = 7070) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="python -m analyzer ui")
    parser.add_argument("--port", type=int, default=7070)
    args = parser.parse_args()

    app = create_app()
    url = f"http://localhost:{args.port}"
    print(f"Dashboard running at {url}")
    subprocess.run(["open", url], check=False)
    app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False)
    return 0
