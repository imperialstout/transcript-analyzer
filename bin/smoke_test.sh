#!/bin/bash
# End-to-end smoke test against the REAL Claude seat — run on the work machine:
#
#   bash bin/smoke_test.sh
#
# Builds a throwaway Workcall layout in a temp dir (NOTHING touches your real
# Call Transcripts / Analyzed / .processed.json / Drive), drops in a sample exec
# transcript + minimal prompts, and runs `python -m analyzer` with
# BACKEND=claude-cli. This is the first real-CLI exercise of routing + analysis +
# redaction, so it catches integration gaps stub tests can't.
#
# Expect: one [ANALYZED].txt + one [SHAREABLE].txt, routed category in the log,
# and a "## Private read" section present in the internal file but GONE from the
# shareable one.

REPO="$(cd "$(dirname "$0")/.." && pwd)"
CLAUDE_BIN="${CLAUDE_BIN:-/opt/homebrew/bin/claude}"
T="$(mktemp -d "${TMPDIR:-/tmp}/ta_smoke.XXXXXX")" 2>/dev/null || T="$(mktemp -d)"
echo "Repo:    $REPO"
echo "claude:  $CLAUDE_BIN"
echo "Sandbox: $T"
mkdir -p "$T/Call Transcripts/notes/_Processed" "$T/Call Transcripts/_Processed" "$T/Analyzed"

# ---- sample transcript (exec-flavored; should route to EXEC) ----------------
cat > "$T/Call Transcripts/2026-06-02 - Leadership Steering Review.txt" <<'TXT'
Leadership Steering Review — Siemens SherpaX — June 2, 2026

Brad Gross: Let's start with the RCA go-live decision. Gunnar, where do we land on the April milestone?
Gunnar Ulle: We're holding the date, but I want to flag the data migration risk — the CML mapping isn't validated yet.
Christoph Hallmann: From the business side, leadership expects the date to hold. Slipping it sends the wrong signal upward.
Brad Gross: Understood, but I'm not going to commit the team to a date we can't hit on quality. I'd rather frame it as a phased go-live.
Imad Sghoul: Agreed. The integration layer needs another two weeks of SIT regardless of how we message it.
Gunnar Ulle: If we phase it, I need exec air cover when the partner pushes back.
Christoph Hallmann: I'll take that to the steering committee. But Brad, I need you to own the technical narrative — last review it felt like we were hedging.
Brad Gross: I'll own it. I'll be the final technical approver on the go-live gate.
Lisa Jehle: One concern — staffing. We're down two engineers and the roadmap assumes full capacity.
Brad Gross: Noted. I'll escalate the staffing gap to leadership this week; it's a real risk to the Q3 commitments.
Christoph Hallmann: Let's be careful how we position staffing. We don't want it read as the program being under-resourced.
Brad Gross: Decisions then: phased go-live, I'm the final technical approver, and I escalate staffing. Eike-Oliver sent regrets, so I'll brief him separately.
TXT

# ---- minimal prompt library (trimmed starters; keep the Private-read heading) -
cat > "$T/PromptLibrary.md" <<'MD'
### DAILY.
```
Produce a fast daily digest: ## TL;DR, ## Updates by workstream, ## Needs your attention,
## Watch items. End with ## Private read — internal only (politics/career signal; stripped from shareable).
```

### STANDUP.
```
Analyze this internal team sync. Sections: ## Summary, ## Progress & status by workstream,
## Commitments & action items (owner — commitment — due), ## Blockers & dependencies, ## Risks.
End with ## Private read — internal only (team dynamics / career signal; stripped from shareable).
```

### SOLUTION.
```
Analyze this solutioning/design session. Sections: ## Summary, ## Problem & context,
## Options considered, ## Decisions & rationale, ## Open technical questions, ## Architecture & integration risks.
End with ## Private read — internal only (design politics / career signal; stripped from shareable).
```

### EXEC.
```
Analyze this executive/steering meeting thoroughly and with attribution. Sections:
## Executive summary, ## Decisions & commitments (owner, timeline, firm vs directional),
## Stakeholder positions (per person; note absences), ## Risks & escalations,
## Narrative & positioning, ## Open threads & next steps.
End with ## Private read — internal only (political dynamics, leadership read on individuals,
and the reader's own positioning/career exposure — be specific; stripped from shareable).
```

### REDACT.
```
Convert this INTERNAL analysis into a SHAREABLE version for other team leads. REMOVE the entire
"## Private read — internal only" section wherever it appears, plus any internal politics framing
or the reader's career/positioning notes. KEEP the frontmatter unchanged and all decisions, risks,
action items, owners, dates, and substantive content. Do not add a preamble or note what was removed;
emit only the cleaned analysis starting with the frontmatter.
```
MD

cat > "$T/Program_Context_Brief.md" <<'MD'
Program Context Brief (smoke-test stub)

Program: SherpaX — enterprise Salesforce Revenue Cloud at Siemens.
Cast: Brad Gross (Revenue Cloud CTO), Gunnar Ulle (delivery lead), Christoph Hallmann (business),
Imad Sghoul (integration), Lisa Jehle (staffing/PMO), Eike-Oliver Steffen (exec sponsor).
Workstreams include SI RCA. This is a stub brief for testing only.
MD

echo
echo "==================== running analyzer (real claude -p) ===================="
cd "$REPO" || exit 1
VENV="$HOME/.venvs/transcript-analyzer"
if [ -x "$VENV/bin/python" ]; then
  PY="$VENV/bin/python"
else
  echo "ERROR: venv not found at $VENV. Create it first:"
  echo "    python3 -m venv \"$VENV\""
  echo "    \"$VENV/bin/pip\" install -r \"$REPO/requirements.txt\""
  echo "  then re-run: bash bin/smoke_test.sh"
  exit 1
fi

env -u ANTHROPIC_API_KEY \
  BACKEND=claude-cli \
  CLAUDE_BIN="$CLAUDE_BIN" \
  CALL_TRANSCRIPTS_PATH="$T/Call Transcripts" \
  PROCESSED_PATH="$T/Call Transcripts/_Processed" \
  ANALYZED_PATH="$T/Analyzed" \
  NOTES_PATH="$T/Call Transcripts/notes" \
  NOTES_PROCESSED_PATH="$T/Call Transcripts/notes/_Processed" \
  PROMPT_LIBRARY_PATH="$T/PromptLibrary.md" \
  CONTEXT_BRIEF_PATH="$T/Program_Context_Brief.md" \
  MANIFEST_PATH="$T/.processed.json" \
  "$PY" -m analyzer
rc=$?

echo
echo "==================== results ===================="
echo "analyzer exit code: $rc"
echo "--- Analyzed/ ---"; ls -1 "$T/Analyzed/" || true
A="$(ls "$T/Analyzed/"*"[ANALYZED].txt" 2>/dev/null | head -1)"
S="$(ls "$T/Analyzed/"*"[SHAREABLE].txt" 2>/dev/null | head -1)"
if [ -n "$A" ]; then
  echo "--- INTERNAL frontmatter + has Private read? ---"
  sed -n '1,8p' "$A"
  grep -q "Private read" "$A" && echo "  [ok] internal contains a Private read section" \
                              || echo "  [!!] internal MISSING Private read section"
fi
if [ -n "$S" ]; then
  echo "--- SHAREABLE: Private read removed? ---"
  grep -q "Private read" "$S" && echo "  [!!] shareable STILL contains Private read — redaction failed" \
                              || echo "  [ok] shareable has NO Private read section"
else
  echo "  (no [SHAREABLE] file — check the log above for the shareable-pass error)"
fi
echo
echo "Sandbox kept for inspection: $T"
echo "Open the two files there to eyeball quality, then:  rm -rf \"$T\""
