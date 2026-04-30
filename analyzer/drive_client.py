"""Google Drive API client for fetching `.gdoc` body text.

A `.gdoc` file in the local Drive cache is a tiny JSON shortcut to a
Google Docs URL — the body lives server-side. To read it from the
analyzer, we go through the Drive API.

Two-stage auth:
- `bootstrap_oauth()` runs interactively (browser-based consent) and
  persists a refresh token. This must be done once before any launchd
  run can use the Drive service.
- `get_drive_service()` is launchd-safe: it loads the saved token,
  silently refreshes if expired, and never opens a browser. It raises
  if no token exists, so the caller can degrade gracefully.

Credentials and tokens live at `~/.config/transcript-analyzer/` —
outside the repo (which is in iCloud) and outside Drive sync.
"""

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

_CONFIG_DIR = Path("~/.config/transcript-analyzer").expanduser()
CREDENTIALS_PATH = _CONFIG_DIR / "google-credentials.json"
TOKEN_PATH = _CONFIG_DIR / "google-token.json"


def get_drive_service() -> Resource:
    """Return an authenticated Drive v3 service. Never opens a browser.

    Raises FileNotFoundError if no saved token exists — caller should
    degrade to skipping `.gdoc` notes and log a clear message.
    """
    creds = _load_credentials()
    if creds is None:
        raise FileNotFoundError(
            f"No saved Drive OAuth token at {TOKEN_PATH}. "
            f"Run `python -m analyzer.drive_client` once interactively to authorize."
        )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _load_credentials() -> Credentials | None:
    if not TOKEN_PATH.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save_token(creds)
        return creds
    return None


def _save_token(creds: Credentials) -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")


def bootstrap_oauth() -> None:
    """Interactive: open browser, get user consent, save refresh token.

    Run once on the personal Mac before launchd-driven runs can fetch
    `.gdoc` bodies:

        python -m analyzer.drive_client
    """
    if not CREDENTIALS_PATH.exists():
        raise FileNotFoundError(
            f"OAuth client credentials not found at {CREDENTIALS_PATH}.\n"
            f"  1. Create a Google Cloud project at https://console.cloud.google.com/\n"
            f"  2. Enable the Google Drive API.\n"
            f"  3. Create OAuth 2.0 Client ID (Desktop app type).\n"
            f"  4. Download the JSON and save it as:\n"
            f"     {CREDENTIALS_PATH}"
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    creds = flow.run_local_server(port=0)
    _save_token(creds)
    print(f"OAuth complete. Token saved to {TOKEN_PATH}")


if __name__ == "__main__":
    bootstrap_oauth()
