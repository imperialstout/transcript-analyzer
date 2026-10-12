#!/bin/bash
# Fires a macOS notification at 7:30am reminding Brad to run /morning-brief.
# Counts action items in the latest Daily Pulse so the banner is specific.
# Invoked by com.bradgross.morning-brief.plist via launchd.

ANALYZED_DIR="$HOME/Library/CloudStorage/GoogleDrive-brad.gross@salesforce.com/My Drive/Workcall/Analyzed"

# Find the most recent [DAILY PULSE] file
LATEST_PULSE=$(ls -1t "$ANALYZED_DIR"/*DAILY\ PULSE*.md 2>/dev/null | head -1)

ITEM_COUNT=""
PULSE_DATE=""

if [[ -n "$LATEST_PULSE" ]]; then
    # Count lines that look like triage items: "Convert to action:" entries
    # or bullet items under "Suggested dispositions"
    ACTION_COUNT=$(grep -c "Convert to action:" "$LATEST_PULSE" 2>/dev/null || true)
    ACTION_COUNT="${ACTION_COUNT//[^0-9]/}"   # strip whitespace/newlines
    ACTION_COUNT="${ACTION_COUNT:-0}"

    # Pull the date from the filename e.g. "Daily Pulse - 2026-10-12"
    PULSE_DATE=$(basename "$LATEST_PULSE" | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' | tail -1)

    if [[ "$ACTION_COUNT" -gt 0 ]]; then
        ITEM_COUNT=" — $ACTION_COUNT action item$([ "$ACTION_COUNT" -gt 1 ] && echo 's' || echo '') from $PULSE_DATE"
    elif [[ -n "$PULSE_DATE" ]]; then
        ITEM_COUNT=" — pulse from $PULSE_DATE"
    fi
fi

TITLE="Morning Brief"
BODY="Run /morning-brief to triage today's program items${ITEM_COUNT}."

# Escape for AppleScript embedding
BODY_ESC=$(printf '%s' "$BODY" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
TITLE_ESC=$(printf '%s' "$TITLE" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')

osascript -e "display notification \"$BODY_ESC\" with title \"$TITLE_ESC\" sound name \"Basso\"" || true
