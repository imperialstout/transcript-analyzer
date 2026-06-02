from dataclasses import dataclass

import anthropic

from .config import CONFIG, supports_thinking
from .manifest import Usage

_FRAMING = """\
You are analyzing meeting transcripts for an enterprise Salesforce program (SherpaX at Siemens).

The user is Brad Gross, Revenue Cloud CTO. Posture: attributed, specific, non-neutralized. Don't sanitize. Use the Program Context Brief below to ground who's who, who reports to whom, and what's politically loaded. When a People Rolodex follows the brief, use it too — it lists named individuals and the name variants they show up as. When a Term Glossary follows, treat it as the canonical spellings of names, acronyms, and product terms. Transcripts come from Plaud, Microsoft Teams, Gemini, and Slack auto-transcription, all of which mangle proper nouns — silently normalize a mangled term to its canonical form from the rolodex/glossary when the match is clear, and flag it when it isn't.
"""


@dataclass
class AnalysisResult:
    text: str
    usage: Usage


def system_prompt_text(
    context_brief: str,
    prompt_body: str,
    frontmatter_instruction: str,
    rolodex: str = "",
    vocabulary: str = "",
) -> str:
    """The stable system prefix: framing + brief + (rolodex) + (glossary)
    + frontmatter spec + prompt body.

    Shared by both backends so the `claude -p` CLI path composes the exact same
    prefix as the API path — output quality stays identical regardless of which
    backend runs. The API path additionally wraps this in a cache_control block
    (see `_build_system`); the CLI path passes it as a plain system prompt and
    lets Claude Code handle caching internally.

    Rolodex and glossary are optional: when empty their sections are omitted, so
    the prefix (and thus the cache key) is unchanged for runs without them.
    """
    sections = [
        _FRAMING.strip(),
        "=== PROGRAM CONTEXT BRIEF ===",
        context_brief.strip(),
    ]
    if rolodex.strip():
        sections += ["=== PEOPLE ROLODEX ===", rolodex.strip()]
    if vocabulary.strip():
        sections += ["=== TERM GLOSSARY (canonical spellings) ===", vocabulary.strip()]
    sections += [
        frontmatter_instruction.strip(),
        "=== PROMPT TO EXECUTE ===",
        prompt_body.strip(),
    ]
    return "\n\n".join(sections)


def _build_system(
    context_brief: str,
    prompt_body: str,
    frontmatter_instruction: str,
    rolodex: str = "",
    vocabulary: str = "",
) -> list[dict]:
    """System as a list of text blocks. The last block carries cache_control,
    which caches the entire stable prefix (framing + brief + frontmatter spec
    + prompt body) across every call in the batch. The transcript itself goes
    in the user message and stays uncached (varies per call)."""
    cached_prefix = system_prompt_text(
        context_brief, prompt_body, frontmatter_instruction, rolodex, vocabulary
    )
    return [
        {
            "type": "text",
            "text": cached_prefix,
            # 1-hour TTL: each file analysis takes 3+ minutes, so the default
            # 5-minute TTL expires mid-batch and forces redundant cache writes.
            # 1h write is 2× input rate (paid once); subsequent reads at 0.1×.
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }
    ]


def analyze(
    *,
    transcript_text: str,
    prompt_body: str,
    context_brief: str,
    frontmatter_instruction: str,
    model: str,
    rolodex: str = "",
    vocabulary: str = "",
) -> AnalysisResult:
    client = anthropic.Anthropic(api_key=CONFIG.anthropic_api_key)
    system = _build_system(
        context_brief, prompt_body, frontmatter_instruction, rolodex, vocabulary
    )
    messages = [{"role": "user", "content": f"Transcript:\n\n{transcript_text}"}]

    kwargs: dict = {
        "model": model,
        "max_tokens": 64000,
        "system": system,
        "messages": messages,
    }
    if supports_thinking(model):
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["output_config"] = {"effort": CONFIG.effort}

    with client.messages.stream(**kwargs) as stream:
        message = stream.get_final_message()

    text = "".join(b.text for b in message.content if b.type == "text")
    usage = Usage(
        input_tokens=message.usage.input_tokens,
        output_tokens=message.usage.output_tokens,
        cache_creation_input_tokens=getattr(
            message.usage, "cache_creation_input_tokens", 0
        )
        or 0,
        cache_read_input_tokens=getattr(message.usage, "cache_read_input_tokens", 0)
        or 0,
    )
    return AnalysisResult(text=text, usage=usage)
