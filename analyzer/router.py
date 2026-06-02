"""Route a transcript to one of the consolidated meeting-category prompts.

Replaces the old "every transcript runs A2" behaviour. A cheap (`Haiku`-tier)
`claude -p` call reads the transcript and picks one category; the matching prompt
body is then run by the analysis backend.

Design choice — classification NEVER raises. A misroute should still produce an
internal analysis (under the fallback category), not a hard failure. So any
error, timeout, or unexpected output collapses to `FALLBACK`. This is distinct
from the analysis/redaction passes, which fail closed.

The four categories mirror the user's meeting taxonomy:
  DAILY    — daily Slack/standup-style digests (note: true Slack daily summaries
             flow through notes_intake with no LLM; this only catches daily-style
             *transcripts*).
  STANDUP  — internal team syncs / stand-ups.
  SOLUTION — technical solutioning, architecture, design reviews.
  EXEC     — exec / steerco / leadership alignment (highest stakes).
"""

import sys

from . import claude_cli
from .config import CONFIG

CATEGORIES = ("DAILY", "STANDUP", "SOLUTION", "EXEC")
# Safest general-purpose internal analysis when classification is unavailable or
# ambiguous — a standard internal-sync writeup.
FALLBACK = "STANDUP"

# Routing rarely needs the whole transcript; the opening is almost always enough
# to tell an exec alignment from a stand-up. Cap input to keep the call fast.
_CLASSIFY_CHAR_BUDGET = 8000

_CLASSIFY_SYSTEM = (
    "You are a meeting classifier. Read the transcript and choose the single "
    "best category from this exact set:\n"
    "- DAILY: a daily digest / quick daily status.\n"
    "- STANDUP: an internal team sync or stand-up.\n"
    "- SOLUTION: technical solutioning, architecture, or design review.\n"
    "- EXEC: executive, steering-committee, or leadership alignment.\n\n"
    "Reply with ONLY the category word (DAILY, STANDUP, SOLUTION, or EXEC). "
    "No punctuation, no explanation."
)


def classify(transcript_text: str, *, model: str | None = None) -> str:
    """Return one of CATEGORIES. Never raises — falls back to FALLBACK."""
    model = model or CONFIG.classifier_model
    snippet = transcript_text[:_CLASSIFY_CHAR_BUDGET]
    try:
        data = claude_cli.run_claude_p(
            f"Transcript:\n\n{snippet}",
            model=model,
            system=_CLASSIFY_SYSTEM,
            timeout=120,
        )
        raw = claude_cli.result_text(data)
    except Exception as e:  # never let classification fail a transcript
        print(
            f"  [router] classification failed ({type(e).__name__}: {e}); "
            f"falling back to {FALLBACK}",
            file=sys.stderr,
        )
        return FALLBACK

    return _coerce(raw)


def _coerce(raw: str) -> str:
    """Map a model reply to a known category; FALLBACK on anything unexpected."""
    token = raw.strip().upper()
    if token in CATEGORIES:
        return token
    # Tolerate stray wording like "Category: EXEC." — match the first category
    # word that appears.
    for cat in CATEGORIES:
        if cat in token:
            return cat
    print(
        f"  [router] unrecognized classification {raw.strip()!r}; "
        f"falling back to {FALLBACK}",
        file=sys.stderr,
    )
    return FALLBACK
