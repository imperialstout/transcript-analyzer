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

import unicodedata
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


_MIME_FILTER = "(mimeType = 'text/plain' or mimeType = 'text/markdown')"


def _drive_escape(value: str) -> str:
    """Escape a string for inclusion in a Drive query literal."""
    return value.replace("\\", "\\\\").replace("'", r"\'")


def _contains_token(filename: str) -> str:
    """A distinctive ASCII prefix safe to use in a `name contains` query.

    Drive's exact `name =` match is byte-sensitive, so it fails when the local
    name differs from Drive's at the byte level (notably NFD vs NFC). A
    `contains` query on the leading ASCII run sidesteps that: we stop at the
    first non-ASCII char (where normalization differences live) so the token is
    identical in both forms, then disambiguate the candidates in Python. Returns
    "" if the prefix is too short to be distinctive.
    """
    token = []
    for ch in filename:
        if ord(ch) > 127 or ch in "'\\":
            break
        token.append(ch)
    token = "".join(token).strip()
    return token if len(token) >= 8 else ""


def _list_drive_files(drive_service: Resource, q: str) -> list[dict]:
    return drive_service.files().list(
        q=f"{q} and {_MIME_FILTER} and trashed = false",
        spaces="drive",
        fields="files(id, name, modifiedTime)",
        pageSize=25,
    ).execute().get("files", [])


def _download_best(drive_service: Resource, filename: str, files: list[dict]) -> str:
    """Pick the best candidate (exact NFC match preferred, else newest) and fetch it."""
    target = unicodedata.normalize("NFC", filename).casefold()
    exact = [f for f in files if unicodedata.normalize("NFC", f["name"]).casefold() == target]
    pool = exact or files
    pool.sort(key=lambda f: f.get("modifiedTime", ""), reverse=True)
    response = drive_service.files().get_media(fileId=pool[0]["id"]).execute()
    return response.decode("utf-8") if isinstance(response, bytes) else str(response)


def fetch_text_file_by_name(filename: str, drive_service: Resource) -> str:
    """Download a `.txt`/`.md` body from Drive, matching by filename.

    Last-resort fallback for the launchd × Drive File Provider EDEADLK bug:
    when both `open()` and `/bin/cat` refuse to read a freshly-synced file,
    this bypasses the local FUSE mount entirely and pulls the body straight
    from Google's HTTP API. Mime filter accepts both text/plain and
    text/markdown.

    Matching is layered because the local cache name and Drive's stored name
    can differ at the byte level — macOS stores filenames NFD-decomposed
    (`Re` + combining accent) while Drive stores them NFC-composed (`é`), so a
    raw `name = '...'` query silently returns nothing:

      1. Exact `name =` match across NFC / NFD / raw forms (precise, cheap).
      2. `name contains '<ASCII prefix>'` narrowed set, disambiguated in Python
         with NFC-normalized comparison (survives accent + other byte skew).
    """
    forms = list(dict.fromkeys([
        unicodedata.normalize("NFC", filename),
        unicodedata.normalize("NFD", filename),
        filename,
    ]))
    for name in forms:
        files = _list_drive_files(drive_service, f"name = '{_drive_escape(name)}'")
        if files:
            return _download_best(drive_service, filename, files)

    token = _contains_token(filename)
    if token:
        files = _list_drive_files(drive_service, f"name contains '{_drive_escape(token)}'")
        if files:
            return _download_best(drive_service, filename, files)

    raise FileNotFoundError(f"no Drive file matches name {filename!r} (text/plain or text/markdown)")


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
