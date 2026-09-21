"""
Follow-up query rewriting (Week 6).

Two things are being checked here, and the second matters more than the first:

1. When we rewrite — never on the first question, and (by default) only when the
   question looks like it leans on the conversation, because every rewrite is a
   paid model call.
2. That a rewrite can never break an answer. A provider that raises, a model
   that waffles, an empty reply: all of them must quietly fall back to the
   question the user actually typed.
"""

import pytest

from app.core.config import get_settings
from app.services.generation.llm import Completion, FakeLLMProvider, LLMProvider
from app.services.generation.rewrite import (
    MAX_REWRITE_CHARS,
    build_rewrite_prompt,
    looks_context_dependent,
    rewrite_question,
)

HISTORY = [
    {"role": "user", "content": "What is the notice period for terminating the lease for Unit 4B?"},
    {"role": "assistant", "content": "The tenant must give 60 days written notice."},
]


class ScriptedProvider(LLMProvider):
    """Returns whatever we tell it to, and remembers whether it was called."""

    model = "scripted"

    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.calls = 0

    def stream(self, **kwargs):  # pragma: no cover - complete() is what we use
        raise NotImplementedError

    async def complete(self, *, system, messages, max_tokens, model=None) -> Completion:
        self.calls += 1
        self.prompt = messages[-1]["content"]
        if isinstance(self.reply, Exception):
            raise self.reply
        return Completion(
            text=self.reply, model="scripted", input_tokens=40, output_tokens=9, stop_reason=None
        )


# --------------------------------------------------------------- the heuristic


@pytest.mark.parametrize(
    "question",
    [
        "what about the pet bond?",
        "How about Unit 7A?",
        "Does it apply to them as well?",
        "And the bond?",
        "Is that the same for the other one?",
    ],
)
def test_questions_that_lean_on_the_conversation(question):
    assert looks_context_dependent(question)


@pytest.mark.parametrize(
    "question",
    [
        "What is the notice period for terminating the lease for Unit 4B?",
        "Define E-204",  # short, but complete on its own
        "How many days of annual leave do I get after 3 years?",
    ],
)
def test_questions_that_stand_alone(question):
    assert not looks_context_dependent(question)


# ------------------------------------------------------------ when we rewrite


async def test_first_question_is_never_rewritten():
    provider = ScriptedProvider("should not be called")
    result = await rewrite_question("What is the pet bond?", [], provider=provider)

    assert result.reason == "first-turn"
    assert result.query == "What is the pet bond?"
    assert not result.rewritten
    assert provider.calls == 0  # no history means nothing to fold in, so no cost


async def test_self_contained_follow_up_costs_nothing():
    provider = ScriptedProvider("should not be called")
    question = "How many days of annual leave do I get after 3 years?"
    result = await rewrite_question(question, HISTORY, provider=provider)

    assert result.reason == "self-contained"
    assert result.query == question
    assert provider.calls == 0


async def test_rewrite_always_overrides_the_heuristic(monkeypatch):
    monkeypatch.setattr(get_settings(), "rewrite_always", True)
    provider = ScriptedProvider("What is the annual leave entitlement after 3 years at Unit 4B?")
    result = await rewrite_question("How many days of annual leave?", HISTORY, provider=provider)

    assert provider.calls == 1
    assert result.rewritten


async def test_rewriting_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(get_settings(), "query_rewrite", False)
    provider = ScriptedProvider("What is the pet bond for Unit 4B?")
    result = await rewrite_question("what about the pet bond?", HISTORY, provider=provider)

    assert result.reason == "off"
    assert result.query == "what about the pet bond?"
    assert provider.calls == 0


async def test_follow_up_is_rewritten_and_costed():
    provider = ScriptedProvider("What is the pet bond for Unit 4B?")
    result = await rewrite_question("what about the pet bond?", HISTORY, provider=provider)

    assert result.rewritten
    assert result.reason == "rewritten"
    assert result.query == "What is the pet bond for Unit 4B?"
    assert result.original == "what about the pet bond?"
    assert (result.input_tokens, result.output_tokens) == (40, 9)
    assert result.model == "scripted"


async def test_prompt_carries_the_conversation_and_escapes_it():
    provider = ScriptedProvider("What is the pet bond for Unit 4B?")
    history = [{"role": "user", "content": "Is <b>Unit 4B</b> & 7A the same?"}]
    await rewrite_question("what about the pet bond?", history, provider=provider)

    assert '<turn role="user">' in provider.prompt
    assert "&lt;b&gt;Unit 4B&lt;/b&gt; &amp; 7A" in provider.prompt  # no tag injection
    assert "<question>what about the pet bond?</question>" in provider.prompt


# ------------------------------------------------- when the model misbehaves


async def test_provider_failure_falls_back_to_the_original():
    provider = ScriptedProvider(RuntimeError("API down"))
    result = await rewrite_question("what about the pet bond?", HISTORY, provider=provider)

    assert result.reason == "failed"
    assert result.query == "what about the pet bond?"
    assert not result.rewritten


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "   ",
        "I can't help with that.",
        "x" * (MAX_REWRITE_CHARS + 1),  # started explaining instead of rewriting
    ],
)
async def test_unusable_replies_fall_back_to_the_original(reply):
    provider = ScriptedProvider(reply)
    result = await rewrite_question("what about the pet bond?", HISTORY, provider=provider)

    assert result.query == "what about the pet bond?"
    assert not result.rewritten
    assert result.reason == "failed"


async def test_preamble_and_quotes_are_stripped():
    provider = ScriptedProvider('Rewritten question: "What is the pet bond for Unit 4B?"')
    result = await rewrite_question("what about the pet bond?", HISTORY, provider=provider)

    assert result.query == "What is the pet bond for Unit 4B?"


async def test_unchanged_reply_is_not_reported_as_a_rewrite():
    provider = ScriptedProvider("what about the pet bond?")
    result = await rewrite_question("what about the pet bond?", HISTORY, provider=provider)

    assert not result.rewritten
    assert result.query == "what about the pet bond?"


# --------------------------------------------------------- the offline fake


async def test_fake_provider_rewrites_deterministically():
    """The fake has to exercise the path for tests and no-key demos."""
    result = await rewrite_question("what about the pet bond?", HISTORY, provider=FakeLLMProvider())

    assert result.rewritten
    assert "pet bond" in result.query
    assert "Unit 4B" in result.query  # the subject came from the conversation


def test_prompt_shape_is_stable():
    prompt = build_rewrite_prompt("what about the pet bond?", HISTORY)

    assert prompt.count("<turn") == 2
    assert prompt.index("<conversation>") < prompt.index("<question>")
