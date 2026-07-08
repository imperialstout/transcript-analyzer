import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import CONFIG

# (input $/M, output $/M) per current Anthropic pricing.
_RATES: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),  # dated id used by the work seat
}


@dataclass
class Usage:
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int


def estimate_cost(model: str, usage: Usage) -> float:
    rates = _RATES.get(model)
    if not rates:
        return 0.0
    in_rate, out_rate = rates
    cost = (
        usage.input_tokens * in_rate
        + usage.cache_creation_input_tokens * in_rate * 1.25
        + usage.cache_read_input_tokens * in_rate * 0.10
        + usage.output_tokens * out_rate
    ) / 1_000_000
    return round(cost, 4)


def load() -> dict[str, dict]:
    if not CONFIG.manifest_path.exists():
        return {}
    return json.loads(CONFIG.manifest_path.read_text(encoding="utf-8"))


def is_recorded(source_filename: str, manifest: dict[str, dict] | None = None) -> bool:
    m = manifest if manifest is not None else load()
    return source_filename in m


def record(
    source_filename: str,
    *,
    output_filename: str,
    prompt_key: str,
    model: str,
    usage: Usage,
    duration_seconds: float,
    category: str | None = None,
    shareable_filename: str | None = None,
) -> dict:
    manifest = load()
    entry = {
        "analyzed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "transcript",
        "output_filename": output_filename,
        # The routed meeting category (DAILY/STANDUP/SOLUTION/EXEC); None for the
        # legacy single-prompt path. `prompt_key` carries the same value now that
        # routing is keyed by category, but `category` is kept explicit for
        # downstream grouping.
        "category": category,
        # The redacted, leads-readable sibling output. None when the shareable
        # pass is disabled or failed (the internal analysis is still recorded).
        "shareable_filename": shareable_filename,
        "prompt_key": prompt_key,
        "model": model,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_creation_tokens": usage.cache_creation_input_tokens,
        "cache_read_tokens": usage.cache_read_input_tokens,
        "cost_usd": estimate_cost(model, usage),
        "duration_seconds": round(duration_seconds, 2),
    }
    manifest[source_filename] = entry
    _write(manifest)
    return entry


def record_synthesis(
    output_filename: str,
    *,
    mode: str,
    model: str,
    usage: Usage,
    duration_seconds: float,
) -> dict:
    """Manifest entry for a synthesis run (daily/weekly/career)."""
    manifest = load()
    entry = {
        "analyzed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "synthesis",
        "output_filename": output_filename,
        "category": mode,
        "model": model,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_creation_tokens": usage.cache_creation_input_tokens,
        "cache_read_tokens": usage.cache_read_input_tokens,
        "cost_usd": estimate_cost(model, usage),
        "duration_seconds": round(duration_seconds, 2),
    }
    manifest[output_filename] = entry
    _write(manifest)
    return entry


def record_note(source_filename: str, *, output_filename: str) -> dict:
    """Manifest entry for a Gemini-summary note filed without an LLM call."""
    manifest = load()
    entry = {
        "analyzed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "notes",
        "output_filename": output_filename,
    }
    manifest[source_filename] = entry
    _write(manifest)
    return entry


def _write(manifest: dict) -> None:
    CONFIG.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.manifest_path.with_suffix(CONFIG.manifest_path.suffix + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(CONFIG.manifest_path)
