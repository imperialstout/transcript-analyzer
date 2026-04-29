#!/bin/bash
# Wrapper for scheduled runs of the transcript analyzer. Designed to be invoked
# by launchd every N minutes. Stays silent on no-op runs; fires a macOS banner
# when something actually happened (analyses written, or failures).

set -uo pipefail

VENV_PYTHON="$HOME/.venvs/transcript-analyzer/bin/python"
LOG_DIR="$HOME/Library/Logs"
LOG_FILE="$LOG_DIR/transcript-analyzer.log"
LAST_RUN="$LOG_DIR/transcript-analyzer-last.log"

mkdir -p "$LOG_DIR"

if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "[$(date -Iseconds)] ERROR: venv python not found at $VENV_PYTHON" >> "$LOG_FILE"
    exit 1
fi

# Capture this run's output separately so we can parse it; also tee to the
# rolling log for forensic browsing.
{
    echo "----- $(date -Iseconds) -----"
    "$VENV_PYTHON" -u -m analyzer 2>&1
    echo "exit=$?"
} | tee "$LAST_RUN" >> "$LOG_FILE"

# Parse the summary line. Notify only when something actually happened.
SUMMARY=$(grep -E "^All done\." "$LAST_RUN" | tail -1)
if [[ -n "$SUMMARY" ]]; then
    SUCCEEDED=$(echo "$SUMMARY" | sed -nE 's/.*([0-9]+) succeeded.*/\1/p')
    FAILED=$(echo "$SUMMARY" | sed -nE 's/.*([0-9]+) failed.*/\1/p')
    SUCCEEDED=${SUCCEEDED:-0}
    FAILED=${FAILED:-0}
    if [[ "$SUCCEEDED" -gt 0 || "$FAILED" -gt 0 ]]; then
        TITLE="Transcript Analyzer"
        if [[ "$FAILED" -gt 0 ]]; then
            TITLE="Transcript Analyzer — $FAILED failed"
        fi
        # osascript may silently fail if notification permission isn't granted;
        # don't make that a fatal error.
        osascript -e "display notification \"$SUMMARY\" with title \"$TITLE\"" || true
    fi
else
    # No summary line means the script crashed before completion. Always notify.
    osascript -e "display notification \"Run did not complete cleanly. Check ~/Library/Logs/transcript-analyzer.log\" with title \"Transcript Analyzer — error\"" || true
fi
