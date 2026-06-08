"""Route a transcript to one of a profile's category prompts.

Replaces the old "every transcript runs A2" behaviour. A cheap (`Haiku`-tier)
`claude -p` call reads the transcript and picks one category; the matching prompt
body (the category name IS the PromptLibrary.md key) is then run by the analysis
backend.

Design choice — classification NEVER raises. A misroute should still produce an
internal analysis (under the fallback category), not a hard failure. So any
error, timeout, or unexpected output collapses to `FALLBACK`. This is distinct
from the analysis/redaction passes, which fail closed.

Two routing profiles, selected by `ROUTING_PROFILE` (see config.routing_profile):

  "work" (default) — the operational meeting taxonomy:
    DAILY    — daily Slack/standup-style digests (note: true Slack daily summaries
               flow through notes_intake with no LLM; this only catches daily-style
               *transcripts*).
    STANDUP  — internal team syncs / stand-ups.
    SOLUTION — technical solutioning, architecture, design reviews.
    EXEC     — exec / steerco / leadership alignment (highest stakes).

  "personal" — the personal-account lens (career + political large issues), which
    keeps sensitive content rather than sharing it:
    B4 — political dynamics: power, alliances, positioning, stakeholder agendas.
    A3 — career / people: 1:1s, performance, growth, relationships.
"""

import sys
from dataclasses import dataclass

from . import claude_cli
from .config import CONFIG


@dataclass(frozen=True)
class RoutingProfile:
    # Each category is a literal prompt key in PromptLibrary.md, so classify()'s
    # output is used directly as the prompt key (no label→key indirection).
    categories: tuple[str, ...]
    fallback: str  # used when classification is unavailable or ambiguous
    system: str  # classifier system prompt describing the categories


PROFILES = {
    "work": RoutingProfile(
        categories=("DAILY", "STANDUP", "SOLUTION", "EXEC"),
        fallback="STANDUP",  # safe general-purpose internal-sync writeup
        system=(
            "You are a meeting classifier. Read the transcript and choose the "
            "single best category from this exact set:\n"
            "- DAILY: a daily digest / quick daily status.\n"
            "- STANDUP: an internal team sync or stand-up.\n"
            "- SOLUTION: technical solutioning, architecture, or design review.\n"
            "- EXEC: executive, steering-committee, or leadership alignment.\n\n"
            "Reply with ONLY the category word (DAILY, STANDUP, SOLUTION, or "
            "EXEC). No punctuation, no explanation."
        ),
    ),
    "personal": RoutingProfile(
        categories=("B4", "A3"),
        fallback="B4",  # the differentiated political read is the safer default
        system=(
            "You are a meeting classifier for a senior consultant's PERSONAL "
            "record. Read the transcript and choose the single best lens:\n"
            "- B4: political dynamics — power, alliances, conflict, positioning, "
            "stakeholder agendas, who is gaining or losing influence.\n"
            "- A3: career / people — 1:1s, performance, growth, team morale, "
            "relationships, personal development.\n\n"
            "Choose B4 when the meeting is mostly about organizational politics "
            "or stakeholder maneuvering; choose A3 when it is mostly about "
            "individuals, 1:1s, or career/people matters. Reply with ONLY the "
            "code (B4 or A3). No punctuation, no explanation."
        ),
    ),
}


def active_profile() -> RoutingProfile:
    """The profile selected by config, defaulting to "work" on an unknown value."""
    profile = PROFILES.get(CONFIG.routing_profile)
    if profile is None:
        print(
            f"  [router] unknown ROUTING_PROFILE {CONFIG.routing_profile!r}; "
            f"using 'work'",
            file=sys.stderr,
        )
        return PROFILES["work"]
    return profile


_ACTIVE = active_profile()
# Module-level aliases for the active profile, kept so callers (main.py startup
# validation) can read the live taxonomy without re-resolving the profile.
CATEGORIES = _ACTIVE.categories
FALLBACK = _ACTIVE.fallback

# Routing rarely needs the whole transcript; the opening is almost always enough
# to tell the category. Cap input to keep the call fast.
_CLASSIFY_CHAR_BUDGET = 8000


def classify(transcript_text: str, *, model: str | None = None) -> str:
    """Return one of CATEGORIES. Never raises — falls back to FALLBACK."""
    model = model or CONFIG.classifier_model
    snippet = transcript_text[:_CLASSIFY_CHAR_BUDGET]
    try:
        data = claude_cli.run_claude_p(
            f"Transcript:\n\n{snippet}",
            model=model,
            system=_ACTIVE.system,
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
