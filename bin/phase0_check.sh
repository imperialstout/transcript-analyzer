#!/bin/bash
# Phase 0 — verify the Claude Code seat works for headless analysis on THIS machine.
# Run this on the WORK machine (the one with the seat) BEFORE switching the
# pipeline to BACKEND=claude-cli. It does not touch the repo or any transcripts.
#
#   ./bin/phase0_check.sh
#
# It checks: (1) claude is installed, (2) `claude -p --output-format json` works
# WITHOUT a personal API key, (3) the JSON exposes a `result`, (4) the tool-
# disabling flag is accepted. The launchd-context check (#5) is printed as a
# manual follow-up because it must run as a real LaunchAgent.

set -u
MODEL="${CLASSIFIER_MODEL:-claude-sonnet-4-6}"
CLAUDE="${CLAUDE_BIN:-claude}"
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; }

echo "== 1. claude binary =="
if command -v "$CLAUDE" >/dev/null 2>&1; then
  pass "found: $(command -v "$CLAUDE")  ($("$CLAUDE" --version 2>/dev/null | head -1))"
  echo "     -> for launchd, set CLAUDE_BIN=$(command -v "$CLAUDE") (absolute) in .env/plist"
else
  fail "claude not on PATH. Install the Claude Code seat or set CLAUDE_BIN."; exit 1
fi

echo "== 2. headless call WITHOUT personal API key =="
OUT="$(env -u ANTHROPIC_API_KEY "$CLAUDE" -p "Reply with the single word: pong" \
        --model "$MODEL" --output-format json 2>/tmp/phase0.err)"
if [ $? -eq 0 ] && [ -n "$OUT" ]; then
  pass "call succeeded on the seat (no ANTHROPIC_API_KEY in env)"
else
  fail "call failed — likely not logged in. Run: $CLAUDE  (then /login), and retry."
  echo "     stderr:"; sed 's/^/       /' /tmp/phase0.err; exit 1
fi

echo "== 3. JSON schema (result / is_error / usage) =="
# Pass the program via -c and the JSON via the OUT env var. No stdin/heredoc/pipe
# (a bare `python3 -` would block waiting on stdin in some shells).
OUT="$OUT" python3 -c '
import json, os, sys
raw = os.environ.get("OUT", "")
try:
    d = json.loads(raw)
except Exception as e:
    print(f"  FAIL not JSON: {e}"); sys.exit(1)
print("  keys present:", sorted(d)[:12])
print("  result text :", repr((d.get("result") or d.get("text") or "")[:40]))
print("  is_error    :", d.get("is_error"))
print("  total_cost  :", d.get("total_cost_usd"))
print("  usage       :", d.get("usage"))
'
echo "     -> the backend reads the result field; if absent, adjust claude_cli.result_text()."

echo "== 4. tool-disabling flag (CLAUDE_EXTRA_ARGS default) =="
if env -u ANTHROPIC_API_KEY "$CLAUDE" -p "ok" --model "$MODEL" \
     --output-format json --allowed-tools "" >/dev/null 2>/tmp/phase0b.err; then
  pass '--allowed-tools "" accepted (default CLAUDE_EXTRA_ARGS works)'
else
  fail '--allowed-tools "" rejected on this CLI version.'
  echo "     Try a different flag and set CLAUDE_EXTRA_ARGS in .env, e.g.:"
  echo '       CLAUDE_EXTRA_ARGS=--disallowedTools "Bash Edit Write Read"'
  echo "     stderr:"; sed 's/^/       /' /tmp/phase0b.err
fi

echo "== 4b. model-ID probe (find exact Haiku/Opus IDs for .env) =="
# The work seat may expose models under slightly different (possibly Bedrock-style)
# IDs. Probe a set of candidates; whichever PASS are safe to put in .env as
# CLASSIFIER_MODEL (cheapest passing) and MODEL_EXEC/MODEL_SOLUTION (an Opus).
# Add your own candidates here if your environment uses dated/ARN-style IDs.
CANDIDATES="${MODEL_CANDIDATES:-claude-sonnet-4-6 claude-haiku-4-5 claude-haiku-4-5-20251001 claude-opus-4-7 claude-opus-4-8}"
for m in $CANDIDATES; do
  if env -u ANTHROPIC_API_KEY "$CLAUDE" -p "ok" --model "$m" \
       --output-format json >/dev/null 2>/tmp/phase0m.err; then
    pass "model resolves: $m"
  else
    err="$(tail -1 /tmp/phase0m.err 2>/dev/null)"
    fail "model rejected:  $m  (${err:-no stderr})"
  fi
done
echo "     -> set CLASSIFIER_MODEL to the cheapest PASS (Haiku if present, else Sonnet),"
echo "        and MODEL_EXEC / MODEL_SOLUTION to a passing Opus, in your .env."

echo "== 5. launchd context (MANUAL) =="
cat <<'EOF'
  Scheduled runs execute under launchd, which has a different security context
  than your shell — the seat auth may or may not be readable there. To confirm
  BEFORE relying on the cron schedule, run the same call from a LaunchAgent:

    1) Save a one-shot agent that writes the JSON to /tmp/phase0_launchd.json:
         launchctl submit -l phase0test -- /bin/bash -lc \
           'env -u ANTHROPIC_API_KEY claude -p "pong" --model claude-sonnet-4-6 \
            --output-format json > /tmp/phase0_launchd.json 2>/tmp/phase0_launchd.err'
    2) Inspect: cat /tmp/phase0_launchd.json   (should be valid JSON with a result)
       If empty / error in /tmp/phase0_launchd.err -> the seat is NOT usable under
       launchd; run the pipeline foreground (e.g. a login-shell cron or manual
       `python -m analyzer`) instead of the LaunchAgent.
    3) launchctl remove phase0test

  This is the #1 risk in the plan — don't skip it.
EOF
echo "Done."
