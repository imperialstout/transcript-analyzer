import re

from .config import CONFIG

# Legacy A1–B4 keys plus the consolidated category prompts (DAILY/STANDUP/
# SOLUTION/EXEC) and the REDACT prompt used by the shareable pass. All are
# `### KEY.`-headed sections with a single fenced body.
_HEADING = re.compile(
    r"^### (DAILY|STANDUP|SOLUTION|EXEC|REDACT|[A-B]\d)\.", re.MULTILINE
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
