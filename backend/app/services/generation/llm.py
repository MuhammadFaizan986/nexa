"""
LLM provider abstraction (plan section 9.7).

The rest of the app only knows this interface:

    async for event in llm.stream(system=..., messages=[...]):
        TextDelta(text="The notice ")   # zero or more, as tokens arrive
        ...
        StreamEnd(model=..., input_tokens=..., output_tokens=..., stop_reason=...)

Swapping Claude for OpenAI (or a local model later) is a config change:
LLM_PROVIDER=anthropic | openai | fake. Clients often ask "can it use X
instead?" — the answer becomes "yes, it's one setting".

`messages` use the common chat shape: [{"role": "user"|"assistant", "content": str}].
"""

import html
import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import lru_cache

from app.core.config import get_settings


@dataclass
class TextDelta:
    text: str


@dataclass
class StreamEnd:
    model: str  # the model that actually produced the answer
    input_tokens: int
    output_tokens: int
    stop_reason: str | None  # "end_turn", "max_tokens", "refusal", ...


StreamEvent = TextDelta | StreamEnd


class LLMProvider(ABC):
    model: str

    @abstractmethod
    def stream(
        self, *, system: str, messages: list[dict], max_tokens: int, model: str | None = None
    ) -> AsyncIterator[StreamEvent]:
        """
        Yield TextDelta events as text is generated, then exactly one StreamEnd.
        `model` overrides the configured one (tenants can pick their own).
        """


class AnthropicProvider(LLMProvider):
    """
    Claude via the official `anthropic` SDK (async client, streaming).

    - Thinking: Claude Opus 5 uses adaptive thinking by default (it decides how
      much to reason). Thinking text isn't streamed to the user; they only see
      the answer text, which is why we only forward `text_stream`.
    - `effort` (optional, LLM_EFFORT): trade answer depth against latency/cost.
    - Refusal fallback: Claude's safety classifiers can occasionally decline a
      request. With `fallbacks="default"` the API re-runs a declined request on
      Anthropic's recommended fallback model inside the same call, so the user
      still gets an answer. The final message's `model` tells us who answered.
    - Retries: the SDK retries 408/409/429/5xx and connection errors itself.
    """

    REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(
        self, *, api_key: str, model: str, effort: str | None, refusal_fallback: bool
    ) -> None:
        import anthropic

        self.model = model
        self.effort = effort
        self.refusal_fallback = refusal_fallback
        self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=3, timeout=120.0)

    async def stream(
        self, *, system: str, messages: list[dict], max_tokens: int, model: str | None = None
    ) -> AsyncIterator[StreamEvent]:
        kwargs: dict = {
            "model": model or self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        if self.refusal_fallback:
            kwargs["betas"] = [self.REFUSAL_FALLBACK_BETA]
            kwargs["fallbacks"] = "default"

        async with self._client.beta.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield TextDelta(text)
            final = await stream.get_final_message()

        yield StreamEnd(
            model=final.model,
            input_tokens=final.usage.input_tokens,
            output_tokens=final.usage.output_tokens,
            stop_reason=final.stop_reason,
        )


class OpenAIProvider(LLMProvider):
    """OpenAI Chat Completions with streaming. Set LLM_MODEL to a current OpenAI model id."""

    def __init__(self, *, api_key: str, model: str) -> None:
        from openai import AsyncOpenAI

        self.model = model
        self._client = AsyncOpenAI(api_key=api_key, max_retries=3, timeout=120.0)

    async def stream(
        self, *, system: str, messages: list[dict], max_tokens: int, model: str | None = None
    ) -> AsyncIterator[StreamEvent]:
        response = await self._client.chat.completions.create(
            model=model or self.model,
            messages=[{"role": "system", "content": system}, *messages],
            max_completion_tokens=max_tokens,
            stream=True,
            # Ask for a final chunk containing token usage (off by default when streaming).
            stream_options={"include_usage": True},
        )
        served_model, finish_reason, usage = model or self.model, None, None
        async for chunk in response:
            served_model = chunk.model or served_model
            if chunk.usage is not None:
                usage = chunk.usage
            for choice in chunk.choices:
                if choice.delta.content:
                    yield TextDelta(choice.delta.content)
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
        yield StreamEnd(
            model=served_model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            stop_reason=finish_reason,
        )


class FakeLLMProvider(LLMProvider):
    """
    Offline stand-in for tests and no-key demos. It "answers" by quoting the
    sentence of passage [1] that best matches the question, and cites it —
    enough to exercise streaming, citation parsing and storage end to end
    without any API calls. It does not reason; don't judge answer quality by it.
    """

    model = "fake-llm"
    _PASSAGE = re.compile(r'<passage id="1"[^>]*>\s*(.+?)\s*</passage>', re.DOTALL)
    _QUESTION = re.compile(r"Question: (.+)$", re.DOTALL)

    async def stream(
        self, *, system: str, messages: list[dict], max_tokens: int, model: str | None = None
    ) -> AsyncIterator[StreamEvent]:
        from app.services.generation.citations import best_matching_sentence

        prompt = messages[-1]["content"]
        passage = self._PASSAGE.search(prompt)
        question = self._QUESTION.search(prompt)
        if passage:
            quote = best_matching_sentence(question.group(1) if question else "", passage.group(1))
            answer = f"According to the documents: {html.unescape(quote)} [1]"
        else:
            answer = "I don't have enough information in the available documents to answer that."
        words = answer.split(" ")
        for i, word in enumerate(words):
            yield TextDelta(word if i == 0 else " " + word)
        yield StreamEnd(
            model=self.model,
            input_tokens=len(prompt.split()),
            output_tokens=len(words),
            stop_reason="end_turn",
        )


class LLMConfigError(RuntimeError):
    pass


@lru_cache
def get_llm_provider() -> LLMProvider:
    settings = get_settings()
    if settings.llm_provider == "fake":
        return FakeLLMProvider()
    if settings.llm_provider == "anthropic":
        if settings.anthropic_api_key is None:
            raise LLMConfigError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set")
        return AnthropicProvider(
            api_key=settings.anthropic_api_key.get_secret_value(),
            model=settings.llm_model,
            effort=settings.llm_effort,
            refusal_fallback=settings.anthropic_refusal_fallback,
        )
    if settings.openai_api_key is None:
        raise LLMConfigError("LLM_PROVIDER=openai but OPENAI_API_KEY is not set")
    return OpenAIProvider(
        api_key=settings.openai_api_key.get_secret_value(), model=settings.llm_model
    )
