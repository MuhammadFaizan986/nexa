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
class ToolUse:
    """The model asked for a tool. See services/agent/."""

    id: str  # echoed back with the result so the model can match them up
    name: str
    input: dict


@dataclass
class StreamEnd:
    model: str  # the model that actually produced the answer
    input_tokens: int
    output_tokens: int
    stop_reason: str | None  # "end_turn", "max_tokens", "refusal", "tool_use", ...
    # The assistant's raw content blocks, needed only by the agent loop: the
    # next request must replay this turn verbatim, tool calls included.
    content: list[dict] | None = None


@dataclass
class Completion:
    """The whole answer at once, for short internal calls nobody watches."""

    text: str
    model: str
    input_tokens: int
    output_tokens: int
    stop_reason: str | None


StreamEvent = TextDelta | ToolUse | StreamEnd


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

    def stream_with_tools(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
        model: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """
        Like stream(), but the model may ask to call a tool instead of (or as
        well as) writing text. Yields TextDelta and ToolUse events, then one
        StreamEnd carrying the raw assistant blocks for the next round.

        Not every provider supports this; the agent is opt-in per question, so
        a provider without tools simply can't be used in agent mode.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support tools")

    async def complete(
        self, *, system: str, messages: list[dict], max_tokens: int, model: str | None = None
    ) -> Completion:
        """
        Collect a whole response instead of streaming it.

        Streaming exists so a *person* sees words appear; for a one-line
        internal step like query rewriting there is nobody watching, and the
        caller just wants the string. Building it on top of stream() means every
        provider — including the offline fake — gets this for free, and there is
        only one place per provider where the API is actually called.
        """
        parts: list[str] = []
        end: StreamEnd | None = None
        async for event in self.stream(
            system=system, messages=messages, max_tokens=max_tokens, model=model
        ):
            if isinstance(event, TextDelta):
                parts.append(event.text)
            else:
                end = event
        return Completion(
            text="".join(parts).strip(),
            model=end.model if end else (model or self.model),
            input_tokens=end.input_tokens if end else 0,
            output_tokens=end.output_tokens if end else 0,
            stop_reason=end.stop_reason if end else None,
        )


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

    async def stream_with_tools(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
        model: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """
        One turn of the agent loop.

        The model writes text, asks for tools, or both. Text is streamed as it
        arrives so the user sees thinking-out-loud like "Let me check both
        leases"; the tool calls are only complete once the turn ends, so they
        are read from the final message and yielded after it.

        `stop_reason == "tool_use"` is what tells the loop to run the tools and
        come back for another turn.
        """
        kwargs: dict = {
            "model": model or self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
            "tools": tools,
        }
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}

        async with self._client.beta.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield TextDelta(text)
            final = await stream.get_final_message()

        for block in final.content:
            if block.type == "tool_use":
                yield ToolUse(id=block.id, name=block.name, input=dict(block.input or {}))

        yield StreamEnd(
            model=final.model,
            input_tokens=final.usage.input_tokens,
            output_tokens=final.usage.output_tokens,
            stop_reason=final.stop_reason,
            # Replayed verbatim on the next turn: the API requires the tool_use
            # blocks it sent to come back with their results.
            content=[block.model_dump(exclude_none=True) for block in final.content],
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
    # The query-rewriting prompt (services/generation/rewrite.py).
    _REWRITE_QUESTION = re.compile(r"<question>(.+?)</question>", re.DOTALL)
    _LAST_USER_TURN = re.compile(r'<turn role="user">(.+?)</turn>', re.DOTALL)

    async def stream(
        self, *, system: str, messages: list[dict], max_tokens: int, model: str | None = None
    ) -> AsyncIterator[StreamEvent]:
        from app.services.generation.citations import best_matching_sentence

        prompt = messages[-1]["content"]
        rewrite = self._REWRITE_QUESTION.search(prompt)
        passage = self._PASSAGE.search(prompt)
        question = self._QUESTION.search(prompt)
        if rewrite:
            # Not a real rewrite — a real one needs a model that understands the
            # conversation. This glues the follow-up to the subject of the last
            # thing the user asked, which is enough to prove the wiring: the
            # rewritten text is what reaches retrieval.
            turns = self._LAST_USER_TURN.findall(prompt)
            follow_up = html.unescape(rewrite.group(1).strip()).rstrip("?")
            previous = html.unescape(turns[-1].strip()).rstrip("?") if turns else ""
            answer = f"{follow_up} (in the context of: {previous})?" if previous else follow_up
        elif passage:
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

    async def stream_with_tools(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
        model: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """
        A scripted two-step agent: search once, then answer from what came back.

        Real tool choice needs a real model. What this proves offline is the
        machinery around it — that a tool call is executed, that its result is
        fed back, that the passages it found are numbered, and that the final
        answer's citations resolve against them.
        """
        already_searched = any(
            isinstance(m.get("content"), list)
            and any(block.get("type") == "tool_result" for block in m["content"])
            for m in messages
        )
        if not already_searched:
            question = messages[-1]["content"]
            if isinstance(question, list):  # pragma: no cover - first turn is plain text
                question = " ".join(b.get("text", "") for b in question)
            call = {
                "type": "tool_use",
                "id": "fake-tool-1",
                "name": "search_documents",
                "input": {"query": question},
            }
            yield ToolUse(id=call["id"], name=call["name"], input=call["input"])
            yield StreamEnd(
                model=self.model,
                input_tokens=len(str(messages).split()),
                output_tokens=8,
                stop_reason="tool_use",
                content=[call],
            )
            return

        # The tool results are in the conversation now; answer from passage [1]
        # exactly as the non-agent fake does.
        from app.services.generation.citations import best_matching_sentence

        transcript = str(messages)
        passage = self._PASSAGE.search(transcript)
        if passage:
            quote = best_matching_sentence("", passage.group(1))
            answer = f"According to the documents: {html.unescape(quote)} [1]"
        else:
            answer = "I don't have enough information in the available documents to answer that."
        words = answer.split(" ")
        for i, word in enumerate(words):
            yield TextDelta(word if i == 0 else " " + word)
        yield StreamEnd(
            model=self.model,
            input_tokens=len(transcript.split()),
            output_tokens=len(words),
            stop_reason="end_turn",
            content=[{"type": "text", "text": answer}],
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
