#!/bin/bash
# Wrapper for scheduled runs of the transcript analyzer. Designed to be invoked
# by launchd every N minutes. Stays silent on no-op runs; fires a macOS banner
# when something actually happened (analyses written, or failures).

set -uo pipefail

# Derive repo root from the script's own location so this works wherever the
# repo lives, without hardcoded paths.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT" || { echo "ERROR: cannot cd to $REPO_ROOT"; exit 1; }

VENV_PYTHON="$HOME/.venvs/transcript-analyzer/bin/python"
LOG_DIR="$HOME/Library/Logs"
LOG_FILE="$LOG_DIR/transcript-analyzer.log"
LAST_RUN="$LOG_DIR/transcript-analyzer-last.log"

mkdir -p "$LOG_DIR"

# Concurrency guard. Analyses take 3+ min and launchd fires every 30 min, but
# manual `python -m analyzer` runs can overlap with a scheduled tick — when
# they do, both pay Claude for the same transcript and the loser collides on
# the source-file move. `mkdir` is atomic, dependency-free, and portable.
LOCK_DIR="$HOME/Library/Caches/transcript-analyzer.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "[$(date -Iseconds)] another run in progress (lock at $LOCK_DIR); skipping" >> "$LOG_FILE"
    exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

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
        BODY="$SUMMARY"
        if [[ "$FAILED" -gt 0 ]]; then
            TITLE="Transcript Analyzer — $FAILED failed"
            # Surface terminal failure reasons in the banner so Brad doesn't
            # have to open the log to find which file/why. Catches both the
            # notes pipeline (`  [notes] file.gdoc: reason`) and the transcript
            # pipeline (`  [1/1] FAILED: ExceptionType: message`).
            FAIL_DETAILS=$(grep -E "^[[:space:]]*(\[notes\] .+:|\[[0-9]+/[0-9]+\] FAILED:)" "$LAST_RUN" \
                | sed -E -e 's/^[[:space:]]*\[notes\] //' -e 's/^[[:space:]]*\[[0-9]+\/[0-9]+\] //' \
                | head -3)
            if [[ -n "$FAIL_DETAILS" ]]; then
                BODY="$FAIL_DETAILS"
            fi
        fi
        # AppleScript embeds the body inside double quotes — escape backslashes
        # and quotes so filenames with punctuation don't break the call.
        BODY_ESC=$(printf '%s' "$BODY" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
        TITLE_ESC=$(printf '%s' "$TITLE" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
        # osascript may silently fail if notification permission isn't granted;
        # don't make that a fatal error.
        osascript -e "display notification \"$BODY_ESC\" with title \"$TITLE_ESC\"" || true
    fi
else
    # No summary line means the script crashed before completion. Always notify.
    osascript -e "display notification \"Run did not complete cleanly. Check ~/Library/Logs/transcript-analyzer.log\" with title \"Transcript Analyzer — error\"" || true
fi
