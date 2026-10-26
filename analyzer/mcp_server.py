"""
Local MCP server for Claude Desktop.

Exposes brief-state, program pulse, analyzed files, and synthesis outputs
as MCP tools. Transport: stdio (Claude Desktop spawns this process).

Never use print() — stdio is owned by the MCP framework. Use logging.
"""

import logging
import sys
from datetime import date, timedelta
from pathlib import Path

from mcp.server.mcpserver import MCPServer

# ── Logging to file (never stdout) ──────────────────────────────────────────
_log_path = Path("~/Library/Logs/transcript-analyzer-mcp.log").expanduser()
_log_path.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.FileHandler(_log_path), logging.StreamHandler(sys.stderr)],
)
log = logging.getLogger("ta-mcp")

_GOV_REPO = Path("~/code/SiemensRCAGov").expanduser()

mcp = MCPServer("transcript-analyzer")


# ── Lazy module accessors ────────────────────────────────────────────────────

def _bs():
    from analyzer import brief_state
    return brief_state

def _cfg():
    from analyzer.config import CONFIG
    return CONFIG

def _manifest():
    from analyzer import manifest
    return manifest

def _syn():
    from analyzer import synthesize
    return synthesize

def _pp():
    from analyzer import program_pulse
    return program_pulse


# ── Formatting helpers ───────────────────────────────────────────────────────

def _item_md(item: dict) -> str:
    parts = [f"**[{item['id']}]** {item['text']}"]
    meta = []
    if item.get("type"):
        meta.append(f"type={item['type']}")
    if item.get("status"):
        meta.append(f"status={item['status']}")
    if item.get("deadline"):
        meta.append(f"due={item['deadline']}")
    if item.get("owner") and item["owner"] != "Brad":
        meta.append(f"owner={item['owner']}")
    if item.get("program"):
        meta.append(f"program={item['program']}")
    if item.get("loe"):
        meta.append(f"loe={item['loe']}")
    if item.get("pinned"):
        meta.append("📌 pinned")
    if meta:
        parts.append("  " + " | ".join(meta))
    if item.get("notes"):
        parts.append(f"  > {item['notes']}")
    return "\n".join(parts)


# ── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_pending_acts(program: str = "") -> str:
    """Return all pending act items from brief-state.

    Args:
        program: Optional program key to filter by (e.g. SI-RCA, DISW-RCA, siemens-general).
    """
    bs = _bs()
    items = bs.pending_acts()
    if program:
        items = [i for i in items if i.get("program") == program]
    if not items:
        suffix = f" (program={program})" if program else ""
        return f"No pending act items.{suffix}"
    lines = [f"**{len(items)} pending act(s)**\n"]
    for item in items:
        lines.append(_item_md(item))
    return "\n\n".join(lines)


@mcp.tool()
def get_brief_state_summary() -> str:
    """Return counts of brief-state items grouped by type and status."""
    bs = _bs()
    s = bs.summary()
    lines = ["**Brief-state summary**\n"]
    for type_key, counts in sorted(s.items()):
        if isinstance(counts, dict):
            for status, n in sorted(counts.items()):
                if n:
                    lines.append(f"- {type_key} / {status}: {n}")
        else:
            lines.append(f"- {type_key}: {counts}")
    return "\n".join(lines)


@mcp.tool()
def search_brief_state(
    query: str = "",
    type: str = "",
    status: str = "",
    program: str = "",
) -> str:
    """Full-text search across all brief-state items (text + notes).

    Args:
        query: Substring to search for (case-insensitive).
        type: Filter by item type: act, watch, delegate, they-owe, insight.
        status: Filter by status: pending, complete, deferred, expired, dropped.
        program: Filter by program key (e.g. SI-RCA).
    """
    bs = _bs()
    q = query.lower()
    items = bs.all_items()
    results = []
    for item in items:
        if q and q not in (item.get("text", "") + " " + item.get("notes", "")).lower():
            continue
        if type and item.get("type") != type:
            continue
        if status and item.get("status") != status:
            continue
        if program and item.get("program") != program:
            continue
        results.append(item)
    if not results:
        return "No matching items."
    lines = [f"**{len(results)} result(s)**\n"]
    for item in results:
        lines.append(_item_md(item))
    return "\n\n".join(lines)


@mcp.tool()
def close_brief_state_item(item_id: str, status: str, notes: str = "") -> str:
    """Close a brief-state item by ID.

    Args:
        item_id: 12-char item ID (shown in brackets in get_pending_acts output).
        status: One of: complete, dropped, deferred, expired.
        notes: Optional closing notes.
    """
    bs = _bs()
    if status not in ("complete", "dropped", "deferred", "expired"):
        return f"Invalid status '{status}'. Must be one of: complete, dropped, deferred, expired."
    ok = bs.close(item_id, status, notes)
    if ok:
        return f"Item {item_id} marked {status}."
    return f"Item {item_id} not found or already closed."


@mcp.tool()
def defer_brief_state_item(item_id: str, target_date: str, notes: str = "") -> str:
    """Defer a brief-state item to a future date.

    Args:
        item_id: 12-char item ID.
        target_date: ISO date to defer until, e.g. 2026-10-28.
        notes: Optional notes about why it's being deferred.
    """
    bs = _bs()
    ok = bs.defer(item_id, target_date, notes)
    if ok:
        return f"Item {item_id} deferred to {target_date}."
    return f"Item {item_id} not found."


@mcp.tool()
def list_programs() -> str:
    """List all known program keys and their labels from SiemensRCAGov STATUS.md files."""
    from analyzer.program_pulse import _status_paths
    paths = _status_paths(_GOV_REPO)
    lines = ["**Known programs**\n"]
    for key, path in sorted(paths.items()):
        exists = "✓" if path.exists() else "✗ (no STATUS.md)"
        fm_label = ""
        if path.exists():
            try:
                fm, _ = _pp()._read_status(path)
                label = fm.get("label", "")
                if label:
                    fm_label = f" — {label}"
            except Exception:
                pass
        lines.append(f"- **{key}** {exists}{fm_label}")
    return "\n".join(lines)


@mcp.tool()
def get_program_status(key: str) -> str:
    """Read the STATUS.md for a program key, returning frontmatter + open signals.

    Args:
        key: Program key e.g. SI-RCA, DISW-RCA, siemens-general. Use list_programs to see all.
    """
    from analyzer.program_pulse import _status_paths
    paths = _status_paths(_GOV_REPO)
    path = paths.get(key)
    if not path:
        return f"Unknown program key: {key}. Use list_programs to see valid keys."
    if not path.exists():
        return f"STATUS.md not found for {key} at {path}"
    fm, body = _pp()._read_status(path)
    fm_md = "\n".join(f"**{k}:** {v}" for k, v in fm.items())
    # Only surface Open signals section — skip backfill history blocks
    signals_start = body.find("## Open signals")
    if signals_start >= 0:
        body = body[signals_start:]
    return f"## {key}\n\n{fm_md}\n\n{body}"


@mcp.tool()
def get_recent_analyses(days: int = 7) -> str:
    """List [ANALYZED] files from the Analyzed folder, newest first.

    Args:
        days: How many days back to look (default 7).
    """
    cfg = _cfg()
    analyzed = cfg.analyzed_path
    if not analyzed.exists():
        return f"Analyzed folder not found: {analyzed}"
    cutoff = date.today() - timedelta(days=days)
    syn = _syn()
    files = sorted(analyzed.glob("*[ANALYZED]*"), reverse=True)
    results = []
    for f in files:
        d = syn._meeting_date(f)
        if d and d >= cutoff:
            results.append((f, d))
    if not results:
        return f"No [ANALYZED] files in the last {days} days."
    lines = [f"**{len(results)} analyzed file(s) in last {days} days**\n"]
    for f, d in results:
        lines.append(f"- `{f.name}`  ({d})")
    return "\n".join(lines)


@mcp.tool()
def read_analysis(filename: str) -> str:
    """Read the full content of an analysis file by filename (basename only).

    Works for [ANALYZED], [DAILY PULSE], [SLACK DELTA], [CAREER TRAJECTORY] files.

    Args:
        filename: Basename of the file in the Analyzed folder.
    """
    cfg = _cfg()
    path = cfg.analyzed_path / filename
    if not path.exists():
        return f"File not found: {filename}"
    return path.read_text(encoding="utf-8", errors="replace")


@mcp.tool()
def get_latest_synthesis(mode: str) -> str:
    """Return the content of the most recent synthesis of a given type.

    Args:
        mode: One of: daily, weekly, career.
    """
    tag_map = {
        "daily": "[DAILY PULSE]",
        "weekly": "[SLACK DELTA]",
        "career": "[CAREER TRAJECTORY]",
    }
    tag = tag_map.get(mode)
    if not tag:
        return f"Unknown mode: {mode}. Use daily, weekly, or career."
    cfg = _cfg()
    syn = _syn()
    path = syn._most_recent_synthesis(cfg.analyzed_path, tag)
    if not path:
        return f"No {tag} file found in {cfg.analyzed_path}"
    text = path.read_text(encoding="utf-8", errors="replace")
    return f"**{path.name}**\n\n{text}"


@mcp.tool()
def get_processing_costs(since: str = "") -> str:
    """Return token usage and cost statistics from the processing manifest.

    Args:
        since: Optional ISO date lower bound e.g. 2026-10-01.
    """
    m = _manifest()
    data = m.load()
    rows = list(data.values())
    if since:
        rows = [r for r in rows if (r.get("analyzed_at") or "") >= since]
    total_input = sum(r.get("input_tokens", 0) for r in rows)
    total_output = sum(r.get("output_tokens", 0) for r in rows)
    total_cache_create = sum(r.get("cache_creation_tokens", 0) for r in rows)
    total_cache_read = sum(r.get("cache_read_tokens", 0) for r in rows)
    total_cost = sum(r.get("cost_usd", 0.0) for r in rows)
    n = len(rows)
    label = f"since {since}" if since else "all time"
    return "\n".join([
        f"**Processing costs ({label}, {n} runs)**\n",
        f"- Input tokens: {total_input:,}",
        f"- Output tokens: {total_output:,}",
        f"- Cache creation tokens: {total_cache_create:,}",
        f"- Cache read tokens: {total_cache_read:,}",
        f"- **Total cost: ${total_cost:.4f}**",
    ])


# ── Resources ────────────────────────────────────────────────────────────────

@mcp.resource("brief://pending", mime_type="text/markdown")
def resource_pending() -> str:
    """All pending acts and watches as markdown."""
    bs = _bs()
    acts = bs.pending_acts()
    watches = bs.pending_watches()
    lines = [f"# Pending ({len(acts)} acts, {len(watches)} watches)\n"]
    if acts:
        lines.append("## Act items")
        for item in acts:
            lines.append(_item_md(item))
    if watches:
        lines.append("\n## Watch items")
        for item in watches:
            lines.append(_item_md(item))
    return "\n\n".join(lines)


@mcp.resource("brief://today", mime_type="text/markdown")
def resource_today() -> str:
    """Pinned items and items due today."""
    bs = _bs()
    today = date.today().isoformat()
    pinned = bs.pinned_acts()
    acts = bs.pending_acts()
    due_today = [i for i in acts if i.get("deadline") == today and not i.get("pinned")]
    lines = ["# Today's focus\n"]
    if pinned:
        lines.append("## Pinned")
        for item in pinned:
            lines.append(_item_md(item))
    if due_today:
        lines.append("\n## Due today")
        for item in due_today:
            lines.append(_item_md(item))
    if not pinned and not due_today:
        lines.append("Nothing pinned or due today.")
    return "\n\n".join(lines)


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    log.info("transcript-analyzer MCP server starting (mcp 2.x)")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
