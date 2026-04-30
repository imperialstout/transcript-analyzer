import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).parent.parent

# This repo lives in iCloud Drive (under "Mobile Documents/"), so a .env file
# sitting in the repo would sync the API key across every signed-in Apple
# device and into iCloud backups. Prefer a non-synced location; fall back to
# the repo copy with a warning so users can migrate.
_USER_ENV = Path("~/.config/transcript-analyzer/.env").expanduser()
_REPO_ENV = _REPO_ROOT / ".env"

if _USER_ENV.exists():
    load_dotenv(_USER_ENV)
elif _REPO_ENV.exists():
    load_dotenv(_REPO_ENV)
    print(
        f"WARNING: .env loaded from {_REPO_ENV}\n"
        f"         That path is iCloud-synced. Your API key is being synced.\n"
        f"         Migrate with:\n"
        f"           mkdir -p {_USER_ENV.parent}\n"
        f"           mv {_REPO_ENV!s} {_USER_ENV!s}\n",
        file=sys.stderr,
    )

_DRIVE_BASE = "~/Library/CloudStorage/GoogleDrive-brad@bradgross.org/My Drive/Workcall"


def _path(env_name: str, default: str) -> Path:
    return Path(os.environ.get(env_name, default)).expanduser()


def _model(env_name: str, default: str) -> str:
    return os.environ.get(env_name, default)


@dataclass(frozen=True)
class Config:
    call_transcripts_path: Path
    processed_path: Path
    analyzed_path: Path
    notes_path: Path
    notes_processed_path: Path
    prompt_library_path: Path
    context_brief_path: Path
    manifest_path: Path
    anthropic_api_key: str
    default_prompt_key: str
    effort: str
    models: dict[str, str]
    model_override: str | None


CONFIG = Config(
    call_transcripts_path=_path("CALL_TRANSCRIPTS_PATH", f"{_DRIVE_BASE}/Call Transcripts"),
    processed_path=_path("PROCESSED_PATH", f"{_DRIVE_BASE}/Call Transcripts/_Processed"),
    analyzed_path=_path("ANALYZED_PATH", f"{_DRIVE_BASE}/Analyzed"),
    notes_path=_path("NOTES_PATH", f"{_DRIVE_BASE}/Call Transcripts/notes"),
    notes_processed_path=_path("NOTES_PROCESSED_PATH", f"{_DRIVE_BASE}/Call Transcripts/notes/_Processed"),
    prompt_library_path=_path("PROMPT_LIBRARY_PATH", f"{_DRIVE_BASE}/PromptLibrary.md"),
    context_brief_path=_path("CONTEXT_BRIEF_PATH", f"{_DRIVE_BASE}/Program_Context_Brief.md"),
    manifest_path=_path("MANIFEST_PATH", str(_REPO_ROOT / ".processed.json")),
    anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
    default_prompt_key=os.environ.get("DEFAULT_PROMPT_KEY", "A2"),
    effort=os.environ.get("EFFORT", "high"),
    models={
        "A1": _model("MODEL_A1", "claude-opus-4-7"),
        "A2": _model("MODEL_A2", "claude-sonnet-4-6"),
        "A3": _model("MODEL_A3", "claude-sonnet-4-6"),
        "B1": _model("MODEL_B1", "claude-sonnet-4-6"),
        "B2": _model("MODEL_B2", "claude-opus-4-7"),
        "B3": _model("MODEL_B3", "claude-sonnet-4-6"),
        "B4": _model("MODEL_B4", "claude-opus-4-7"),
    },
    model_override=os.environ.get("MODEL_OVERRIDE") or None,
)


def model_for(prompt_key: str) -> str:
    if CONFIG.model_override:
        return CONFIG.model_override
    return CONFIG.models.get(prompt_key, "claude-sonnet-4-6")


def supports_thinking(model: str) -> bool:
    return any(tag in model for tag in ("opus-4-7", "opus-4-6", "sonnet-4-6"))
