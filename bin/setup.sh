#!/bin/bash
# One-time setup for a new user of transcript-analyzer.
#
#   bash bin/setup.sh
#
# Checks the prerequisites you need (Homebrew, python3, the Claude Code CLI +
# your work seat), then automates the safe local steps: builds the Python
# environment, installs dependencies, scaffolds your .env, and verifies your
# seat works. It does NOT install system software for you or touch your work
# GitHub — anything that needs your judgment, it tells you the exact command.

REPO="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$HOME/.venvs/transcript-analyzer"
ENVDIR="$HOME/.config/transcript-analyzer"
ok()   { printf '  \033[32m[ok]\033[0m   %s\n' "$1"; }
todo() { printf '  \033[33m[todo]\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m[fix]\033[0m  %s\n' "$1"; }

echo "=== 1. Prerequisites (install these yourself if flagged) ==="

if command -v brew >/dev/null 2>&1; then
  ok "Homebrew installed"
else
  bad "Homebrew missing. Install it (one line; it asks for your Mac password):"
  echo '         /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  echo "       Then re-run: bash bin/setup.sh"
  exit 1
fi

if command -v python3 >/dev/null 2>&1; then
  ok "python3 installed ($(python3 -V 2>&1))"
else
  bad "python3 missing. Install it:  brew install python   — then re-run this script."
  exit 1
fi

CLAUDE="${CLAUDE_BIN:-claude}"
if command -v "$CLAUDE" >/dev/null 2>&1; then
  CLAUDE_PATH="$(command -v "$CLAUDE")"
  ok "Claude Code installed ($("$CLAUDE" --version 2>/dev/null | head -1)) at $CLAUDE_PATH"
else
  bad "Claude Code (the 'claude' command) not found."
  echo "       Install it and sign in with your WORK Claude seat:"
  echo "         install:  see https://docs.claude.com/claude-code"
  echo "         sign in:  run 'claude' once, then /login with your work account"
  echo "       Then re-run: bash bin/setup.sh"
  exit 1
fi

echo "=== 2. Python environment (automated) ==="
if [ -x "$VENV/bin/python" ]; then
  ok "environment exists at $VENV"
else
  if python3 -m venv "$VENV"; then ok "created environment at $VENV"
  else bad "could not create the Python environment"; exit 1; fi
fi
if "$VENV/bin/python" -m pip install -q -r "$REPO/requirements.txt"; then
  ok "dependencies installed"
else
  bad "dependency install failed. On a locked-down laptop this is usually a proxy/cert wall."
  echo "       Ask IT for the internal pip index, then:"
  echo "         \"$VENV/bin/python\" -m pip install --index-url <internal-url> -r \"$REPO/requirements.txt\""
  exit 1
fi

echo "=== 3. Config file (automated scaffold) ==="
mkdir -p "$ENVDIR"
if [ -f "$ENVDIR/.env" ]; then
  ok ".env already exists at $ENVDIR/.env (left untouched)"
else
  cp "$REPO/.env.example" "$ENVDIR/.env"
  # pin the detected claude path so the scheduled/launchd path is correct
  sed -i '' "s|^# CLAUDE_BIN=.*|CLAUDE_BIN=$CLAUDE_PATH|" "$ENVDIR/.env" 2>/dev/null \
    || printf '\nCLAUDE_BIN=%s\n' "$CLAUDE_PATH" >> "$ENVDIR/.env"
  ok "created $ENVDIR/.env (BACKEND=claude-cli, CLAUDE_BIN=$CLAUDE_PATH)"
  todo "edit $ENVDIR/.env and set DRIVE_BASE to your Workcall folder in Google Drive"
fi

echo "=== 4. Google Drive folder structure ==="

# Auto-detect DRIVE_BASE from .env, falling back to scanning CloudStorage.
DRIVE_BASE=""
DRIVE_BASE=$(grep -E '^DRIVE_BASE=' "$ENVDIR/.env" 2>/dev/null \
  | sed 's/^DRIVE_BASE=//' | tr -d '"'"'" | sed "s|~|$HOME|g" | xargs 2>/dev/null || true)

if [ -z "$DRIVE_BASE" ] || echo "$DRIVE_BASE" | grep -qE 'CHANGE_ME|example'; then
  DRIVE_BASE=""
  # Scan for Google Drive mounts
  MOUNTS=()
  if [ -d "$HOME/Library/CloudStorage" ]; then
    while IFS= read -r -d '' dir; do
      case "$(basename "$dir")" in GoogleDrive-*) MOUNTS+=("$dir/My Drive/Workcall");; esac
    done < <(find "$HOME/Library/CloudStorage" -maxdepth 1 -type d -print0 2>/dev/null)
  fi

  if [ ${#MOUNTS[@]} -eq 1 ]; then
    DRIVE_BASE="${MOUNTS[0]}"
    ok "Auto-detected Drive: $DRIVE_BASE"
  elif [ ${#MOUNTS[@]} -gt 1 ]; then
    echo "  Multiple Google Drive accounts found. Pick the one with your Workcall folder:"
    for i in "${!MOUNTS[@]}"; do printf "    %d) %s\n" $((i+1)) "${MOUNTS[$i]}"; done
    printf "  Enter number: "; read -r CHOICE
    DRIVE_BASE="${MOUNTS[$((CHOICE-1))]}"
  fi
fi

if [ -z "$DRIVE_BASE" ]; then
  echo "  Could not auto-detect your Drive path."
  printf "  Paste the full path to your Workcall folder: "; read -r DRIVE_BASE
  DRIVE_BASE="${DRIVE_BASE/#\~/$HOME}"
fi

ok "Drive root: $DRIVE_BASE"

# Write DRIVE_BASE into .env if not already there.
if ! grep -qE '^DRIVE_BASE=.+' "$ENVDIR/.env" 2>/dev/null || \
     grep -qE '^DRIVE_BASE=.*(CHANGE_ME|example)' "$ENVDIR/.env" 2>/dev/null; then
  if grep -qE '^DRIVE_BASE=' "$ENVDIR/.env" 2>/dev/null; then
    sed -i '' "s|^DRIVE_BASE=.*|DRIVE_BASE=$DRIVE_BASE|" "$ENVDIR/.env"
  else
    printf '\nDRIVE_BASE=%s\n' "$DRIVE_BASE" >> "$ENVDIR/.env"
  fi
  ok "Wrote DRIVE_BASE to $ENVDIR/.env"
fi

# Create the folder structure inside Drive.
for folder in \
  "$DRIVE_BASE" \
  "$DRIVE_BASE/Call Transcripts" \
  "$DRIVE_BASE/Call Transcripts/notes" \
  "$DRIVE_BASE/Call Transcripts/notes/_Processed" \
  "$DRIVE_BASE/Call Transcripts/docs" \
  "$DRIVE_BASE/Call Transcripts/docs/_Processed" \
  "$DRIVE_BASE/Analyzed" \
  "$DRIVE_BASE/Analyzed/_Archive"
do
  if [ -d "$folder" ]; then
    ok "exists:  $folder"
  else
    mkdir -p "$folder" && ok "created: $folder"
  fi
done

echo "=== 5. Drive content files (from workcall-templates/) ==="

for tpl in PromptLibrary.md Program_Context_Brief.md 04_people_rolodex.md 05_vocabulary.md; do
  src="$REPO/workcall-templates/$tpl"
  dest="$DRIVE_BASE/$tpl"
  if [ -f "$dest" ]; then
    ok "already present — skipping: $tpl"
  else
    cp "$src" "$dest" && ok "copied → $dest"
  fi
done

echo "=== 6. Seat check (uses your seat, no personal API key) ==="
if env -u ANTHROPIC_API_KEY "$CLAUDE" -p "ok" --model claude-sonnet-4-6 \
     --output-format json >/dev/null 2>/tmp/ta_setup.err; then
  ok "claude -p works headless on your seat"
else
  todo "seat call failed — run 'claude' and /login, then re-run this script"
  echo "       error: $(tail -1 /tmp/ta_setup.err 2>/dev/null)"
fi

echo
echo "=== You're set. Next: ==="
echo "  1) Fill in your program context (most important step):"
echo "       open -e \"$DRIVE_BASE/Program_Context_Brief.md\""
echo "     Add your program name, client, stakeholders, and workstreams."
echo "     This is injected into every analysis run — the more detail you add, the better."
echo ""
echo "  2) Drop a transcript into:  $DRIVE_BASE/Call Transcripts/"
echo "     (plain .txt or .md — the filename doesn't matter)"
echo ""
echo "  3) Launch the dashboard:    source $VENV/bin/activate && python -m analyzer ui"
echo "     Or for a shortcut, add to ~/.zshrc:"
echo "       alias ta=\"source $VENV/bin/activate && python -m analyzer ui\""
echo ""
echo "Tip: always RUN scripts (bash bin/...), never paste their contents into the terminal."
