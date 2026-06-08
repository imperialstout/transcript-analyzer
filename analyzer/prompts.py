import re

from .config import CONFIG

# Legacy A1–B4 keys plus the consolidated category prompts (DAILY/STANDUP/
# SOLUTION/EXEC), the REDACT prompt, and the DOCUMENT prompt for PDF/deck
# intake. All are `### KEY.`-headed sections with a single fenced body.
_HEADING = re.compile(
    r"^### (DAILY|STANDUP|SOLUTION|EXEC|REDACT|DOCUMENT|[A-B]\d)\.", re.MULTILINE
)
_FENCED = re.compile(r"^```\s*\n(.*?)\n```", re.DOTALL | re.MULTILINE)


def load_prompts() -> dict[str, str]:
    """Parse PromptLibrary.md → {key: fenced prompt body}.

    Picks up the legacy A1–A3 / B1–B4 keys, the consolidated category prompts
    (DAILY/STANDUP/SOLUTION/EXEC), and the REDACT prompt. C1/C2 are
    cross-transcript prompts that run in Claude.ai chat, not here.
    """
    text = CONFIG.prompt_library_path.read_text(encoding="utf-8")
    matches = list(_HEADING.finditer(text))
    out: dict[str, str] = {}
    for i, m in enumerate(matches):
        key = m.group(1)
        section_start = m.end()
        section_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        fenced = _FENCED.search(text, section_start, section_end)
        if fenced:
            out[key] = fenced.group(1).strip()
    return out


def load_context_brief() -> str:
    return CONFIG.context_brief_path.read_text(encoding="utf-8")


def load_rolodex() -> str:
    """The people rolodex, or "" if absent. Best-effort: the rolodex
    complements the brief but isn't required, so a missing file (e.g. a lead
    who hasn't built one) just means no rolodex section in the system prefix.
    """
    p = CONFIG.rolodex_path
    return p.read_text(encoding="utf-8") if p.exists() else ""


def load_vocabulary() -> str:
    """The term glossary (the "Plaud vocabulary" file), or "" if absent.

    Best-effort. Originally pasted into Plaud's on-device Custom Vocabulary, but
    transcripts from Gemini/Teams/Slack never get that device-level correction —
    so the canonical spellings are fed to the analyzer instead, to normalize
    mangled names/acronyms/product terms. Passed as-is (markdown headers and all);
    they categorize terms and help the model rather than hurt it.
    """
    p = CONFIG.vocabulary_path
    return p.read_text(encoding="utf-8") if p.exists() else ""


def load_program_reference() -> str:
    """The current [PROGRAM REFERENCE].md, or "" if it hasn't been created yet.

    Best-effort — the file is pipeline-maintained and starts empty. Never raises.
    """
    from . import filesystem as fs  # local import avoids circular dependency
    return fs.read_program_reference()


# Used when PromptLibrary.md does not contain a ### DOCUMENT. block.
# Handles both meeting artifacts (decks shared in a session) and standalone
# reference documents (roadmaps, org charts, governance docs). The model
# determines which it is from the content itself.
_DEFAULT_DOCUMENT_PROMPT = """\
You are analyzing a document from the SherpaX / Siemens Revenue Cloud program.
This may be a presentation shared in a meeting, a strategic plan, a governance
document, or other program artifact. Analyze it as follows:

## Document Summary
One short paragraph: what this document is, when it was issued, and its primary purpose.

## Key Content
Extract the most important information. Focus on:
- Decisions, commitments, or policies that are now in effect
- Milestones, dates, and release targets
- Org structure, role assignments, or ownership changes
- Process or workflow changes
- Risks, dependencies, or open items explicitly called out

## Relevance to Current Work
How does this document affect day-to-day delivery? What should the reader act on
or be aware of this week?

## Reference Updates
List only durable facts that will still be true in 30 days — things that should
update the program's standing knowledge base. Do NOT include status updates,
action items, or meeting-specific observations. Format as concise bullet points.
If there are no durable facts worth extracting, write "None."

## Private read — internal only
Any politically sensitive observations, stakeholder positioning notes, or
internal-only context not safe to share outside the Salesforce delivery team.
"""


def default_document_prompt() -> str:
    return _DEFAULT_DOCUMENT_PROMPT


_FRONTMATTER_INSTRUCTION = """\
=== OUTPUT FORMAT ===

Begin your response with a YAML frontmatter block bounded by `---` lines, then a blank line, then the structured analysis.

Required fields:

- `meeting_date`: ISO date YYYY-MM-DD. Use the date in the source filename if present; otherwise infer from transcript content.
- `participants`: list of full names. If a Plaud-mistranscribed name is unclear, resolve to the most likely full name from the Cast of Characters in the Program Context Brief (e.g. "Ikem" → "Eike-Oliver Steffen").
- `workstream`: one of `DI-SW`, `SI`, `SI RCA`, `SI CPQ+`, `SI BuildingX`, `SI Services`, `SI SolSys`, `XMP`, `SFS`, `RCA-PoC`, `cross-stream`, `internal-salesforce`, `unclassified`.
- `meeting_type`: one of `client-steerco`, `client-working-session`, `internal-sync`, `1-on-1`, `escalation`, `design-review`, `discovery`, `architecture`, `planning`, `retrospective`, `interview`, `other`.

Optional fields, include when applicable:

- `tags`: free-form list. Suggested vocabulary (extend as needed): `escalation`, `devops`, `integration`, `data-cloud`, `agentforce`, `governance`, `staffing`, `deadline-risk`, `pricing`, `cml`, `mdm`, `sit`, `uat`, `roadmap`, `political`, `commitment`, `unresolved`, `decision-deferred`.
- `decisions_count`: integer count of decisions captured (0 is valid).
- `risks_surfaced`: integer count of new or escalated risks.
- `key_stakeholders_absent`: list of named people whose absence is materially relevant.

Example:

---
meeting_date: 2026-04-23
participants: [Brad Gross, Gunnar Ulle, Imad Sghoul]
workstream: SI RCA
meeting_type: escalation
tags: [escalation, governance, deadline-risk]
decisions_count: 3
risks_surfaced: 2
key_stakeholders_absent: [Eike-Oliver Steffen, Lisa Jehle]
---

After the frontmatter, produce the analysis exactly as specified by the prompt below.
"""


def frontmatter_instruction() -> str:
    return _FRONTMATTER_INSTRUCTION
