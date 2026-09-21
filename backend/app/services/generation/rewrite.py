"""
Follow-up query rewriting (plan section 10, Week 6).

The problem this solves:

    You:  What is the notice period for terminating the lease for Unit 4B?
    NEXA: The tenant must give 60 days written notice. [1]
    You:  what about the pet bond?

"what about the pet bond?" is perfectly clear to a person who read the previous
turn, and meaningless to a search engine: there is no "Unit 4B" in it, no
"lease", nothing to match a passage against. Embedding it finds pet bonds in
every lease in the tenant; keyword search finds the words "pet" and "bond".

So before retrieving, we ask a small, cheap model to rewrite the question into
one that stands on its own — "What is the pet bond for Unit 4B?" — and search
with THAT. The user still sees their own words, and the answering model still
gets the real question plus the conversation; only the search query changes.

Three rules this module follows, all of them about not making things worse:

1. **Never rewrite the first question.** There is no context to fold in, so a
   rewrite could only distort it. (It also means a one-off question costs the
   same as before this feature existed.)
2. **Only pay when it's likely to help.** A question that already names its
   subject doesn't need rewriting; see looks_context_dependent(). Set
   REWRITE_ALWAYS=true to rewrite every follow-up instead.
3. **Any doubt, keep the original.** An empty answer, a suspiciously long one, a
   provider error, a timeout — all of them fall back to the user's question.
   A rewrite is an optimisation; it must never be able to break an answer.
"""

import re
import time
from dataclasses import dataclass
from xml.sax.saxutils import escape

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.generation.llm import LLMProvider, get_llm_provider

log = get_logger(__name__)

REWRITE_SYSTEM = """\
You rewrite the user's latest question so that it can be understood on its own, \
without the conversation.

Rules:
- Replace pronouns and references ("it", "that one", "there", "what about X") \
with the actual subject from the conversation.
- Keep the user's wording and intent. Do not answer the question, do not add \
information, do not invent details that are not in the conversation.
- Keep identifiers exactly as written (unit numbers, error codes, clause \
numbers, dates).
- If the question already stands on its own, return it unchanged.
- Return ONLY the rewritten question. No preamble, no explanation, no quotes."""

# Words that suggest the question leans on what was said before. "it", "that",
# "they", "there" refer backwards; "what about" and "and the ..." continue a
# previous thought. This list is deliberately small: a false positive costs one
# cheap call, while a false negative costs a bad answer.
_CONTEXT_WORDS = {
    "it",
    "its",
    "it's",
    "this",
    "that",
    "these",
    "those",
    "they",
    "them",
    "their",
    "there",
    "he",
    "she",
    "his",
    "her",
    "him",
    "one",
    "ones",
    "same",
    "instead",
    "also",
    "too",
    "either",
    "another",
    "else",
    "then",
}
_CONTEXT_PHRASES = ("what about", "how about", "and the", "and what", "what if", "why not")

# A rewritten question should be a question, not an essay. Anything much longer
# than this means the model started explaining instead of rewriting.
MAX_REWRITE_CHARS = 300

_WORD = re.compile(r"[a-z0-9'][a-z0-9'-]*")


@dataclass
class RewriteResult:
    """
    What retrieval should search for, and everything needed to explain why.

    `query` is always usable: it is the original question whenever rewriting was
    skipped or failed, so callers never have to check before using it.
    """

    query: str  # what to search with
    original: str  # what the user typed
    rewritten: bool  # did the query actually change?
    reason: str  # why: "first-turn", "self-contained", "rewritten", "failed", "off"
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


def looks_context_dependent(question: str) -> bool:
    """
    A cheap guess at whether this question needs the conversation to make sense.

    Two signals, either is enough:
    - it opens with a continuation phrase ("what about ...", "and the ...");
    - it contains a backward-referring word ("it", "that one", "there").

    Short questions are NOT treated as dependent on length alone: "Define E-204"
    is short and complete, while "Does the tenant of Unit 4B need to give notice
    if they leave early?" is long and depends on nothing.
    """
    text = question.strip().lower()
    if any(text.startswith(phrase) for phrase in _CONTEXT_PHRASES):
        return True
    if any(phrase in text for phrase in _CONTEXT_PHRASES):
        return True
    return any(word in _CONTEXT_WORDS for word in _WORD.findall(text))


def build_rewrite_prompt(question: str, history: list[dict]) -> str:
    """
    The conversation, then the question, each in its own tag.

    Same reasoning as the answer prompt (prompts.py): tags draw a hard line
    between "text that came from somewhere else" and "what you must act on", so
    a document quoted earlier in the conversation cannot issue instructions here.
    """
    turns = "\n".join(f'<turn role="{t["role"]}">{escape(t["content"])}</turn>' for t in history)
    return (
        f"<conversation>\n{turns}\n</conversation>\n\n"
        f"<question>{escape(question)}</question>\n\n"
        "Rewrite the question so it stands on its own."
    )


def _clean(text: str, original: str) -> str | None:
    """
    Make the model's reply usable, or reject it.

    Models like to be helpful: "Sure! Here's the rewritten question: ..." or a
    quoted string, or two alternatives on separate lines. We take the first
    non-empty line, strip wrapping quotes, and refuse anything that looks like
    the model did something other than rewrite.
    """
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    # Order matters: drop "Rewritten question:" BEFORE stripping quotes, because
    # the preamble usually sits outside them — Rewritten question: "What is ...?"
    cleaned = re.sub(
        r"^(rewritten|standalone|revised)?\s*question\s*:\s*", "", first_line, flags=re.I
    )
    cleaned = cleaned.strip("\"'` ").strip()
    if not cleaned or len(cleaned) > MAX_REWRITE_CHARS:
        return None
    # A refusal, or an answer instead of a question, is not a rewrite.
    if cleaned.lower().startswith(("i can't", "i cannot", "i'm not able", "sorry")):
        return None
    return cleaned if cleaned.lower() != original.strip().lower() else None


async def rewrite_question(
    question: str,
    history: list[dict],
    *,
    provider: LLMProvider | None = None,
) -> RewriteResult:
    """
    Turn a follow-up into a standalone search query. Never raises.

    `history` is the same list the answer prompt uses: the most recent turns,
    oldest first, each {"role": "user"|"assistant", "content": str}.
    """
    settings = get_settings()
    result = RewriteResult(query=question, original=question, rewritten=False, reason="off")

    if not settings.query_rewrite:
        return result
    if not history:
        return RewriteResult(question, question, False, "first-turn")
    if not settings.rewrite_always and not looks_context_dependent(question):
        return RewriteResult(question, question, False, "self-contained")

    started = time.perf_counter()
    try:
        llm = provider or get_llm_provider()
        completion = await llm.complete(
            system=REWRITE_SYSTEM,
            messages=[{"role": "user", "content": build_rewrite_prompt(question, history)}],
            max_tokens=settings.rewrite_max_tokens,
            model=settings.rewrite_model,
        )
    except Exception as exc:
        # The answer matters more than the rewrite: log it and search for what
        # the user actually typed.
        log.warning("rewrite.failed", error_type=type(exc).__name__)
        return RewriteResult(question, question, False, "failed")

    elapsed = round((time.perf_counter() - started) * 1000)
    cleaned = _clean(completion.text, question)
    if cleaned is None:
        return RewriteResult(
            question,
            question,
            False,
            "failed",
            model=completion.model,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            latency_ms=elapsed,
        )

    log.info("rewrite.done", original=question, rewritten=cleaned, latency_ms=elapsed)
    return RewriteResult(
        query=cleaned,
        original=question,
        rewritten=True,
        reason="rewritten",
        model=completion.model,
        input_tokens=completion.input_tokens,
        output_tokens=completion.output_tokens,
        latency_ms=elapsed,
    )
