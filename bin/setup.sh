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
  todo "edit $ENVDIR/.env and set the *_PATH vars to where YOUR transcripts live"
fi

echo "=== 4. Seat check (uses your seat, no personal API key) ==="
if env -u ANTHROPIC_API_KEY "$CLAUDE" -p "ok" --model claude-sonnet-4-6 \
     --output-format json >/dev/null 2>/tmp/ta_setup.err; then
  ok "claude -p works headless on your seat"
else
  todo "seat call failed — run 'claude' and /login, then re-run this script"
  echo "       error: $(tail -1 /tmp/ta_setup.err 2>/dev/null)"
fi

echo
echo "=== You're set. Next: ==="
echo "  1) Edit your config:        $ENVDIR/.env   (point *_PATH at your transcripts)"
echo "  2) Add YOUR prompts:        copy docs/prompt_starters.md into your Drive PromptLibrary.md"
echo "     and write your Program_Context_Brief.md (your people / your program)."
echo "  3) Dry run (safe sandbox):  bash bin/smoke_test.sh"
echo "  4) Real run:                \"$VENV/bin/python\" -m analyzer"
echo
echo "Tip: always RUN scripts (bash bin/...), never paste their contents into the terminal."
