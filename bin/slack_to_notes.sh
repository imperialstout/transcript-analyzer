#!/bin/bash
# slack_to_notes.sh — prepend date/workstream/type headers to clipboard content
# and write as a .md note into the transcript-analyzer notes inbox.
#
# Usage: copy Slack Canvas content, then run this script (or invoke via Shortcut).
# The resulting file is picked up by notes_intake on the next Run Analysis.

set -euo pipefail

ENV_FILE="$HOME/.config/transcript-analyzer/.env"

# Read a key from the .env file
_env_val() {
    local key="$1"
    grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'
}

# Resolve notes folder: NOTES_PATH wins, else DRIVE_BASE + default subpath,
# else fall back to the work Drive location.
if [ -f "$ENV_FILE" ]; then
    NOTES_PATH=$(_env_val "NOTES_PATH")
    DRIVE_BASE=$(_env_val "DRIVE_BASE")
fi

if [ -n "${NOTES_PATH:-}" ]; then
    NOTES_DIR="${NOTES_PATH/#\~/$HOME}"
elif [ -n "${DRIVE_BASE:-}" ]; then
    NOTES_DIR="${DRIVE_BASE/#\~/$HOME}/Call Transcripts/notes"
else
    NOTES_DIR="$HOME/Library/CloudStorage/GoogleDrive-brad.gross@salesforce.com/My Drive/Workcall/Call Transcripts/notes"
fi

mkdir -p "$NOTES_DIR"

# Format: "July 29, 2026" — matches what parse_gemini_header expects
DATE_HEADER=$(date "+%B %-d, %Y")
# Filename: "Daily Slack Summary – July 29, 2026.md"
FILENAME="Daily Slack Summary – ${DATE_HEADER}.md"
DEST="$NOTES_DIR/$FILENAME"

if [ -f "$DEST" ]; then
    echo "NOTE: $FILENAME already exists — overwriting with fresh clipboard content."
fi

{
    printf "%s\n" "$DATE_HEADER"
    printf "Workstream: Siemens All Projects\n"
    printf "Meeting Type: Daily Slack Summary\n"
    printf "\n"
    pbpaste
} > "$DEST"

echo "Written: $DEST"
