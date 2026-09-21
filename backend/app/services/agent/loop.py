"""
The agent loop (plan section 10, Week 6).

Plain RAG is a straight line: search once, answer once. This is a loop:

    ask the model  ──▶ it writes the answer            ──▶ done
                   └─▶ it asks for tools ──▶ run them ──┘  (up to N times)

Each time round, the model sees everything it has found so far and decides
what to do next. That is the whole difference between "a search box with a
language model on top" and "an assistant": it can look twice.

What keeps it safe and affordable:

- **A hard step limit** (AGENT_MAX_STEPS). Every step is a paid model call, so
  a model that keeps searching forever would keep spending forever. At the
  limit we make one final call with no tools offered, which forces an answer
  from what it already has instead of cutting the user off mid-loop.
- **Tools are the only way out to data**, and each one filters by tenant and by
  the collections this user may read (services/agent/tools.py).
- **Every step is recorded** — which tool, with what arguments, how long it
  took — and streamed to the UI as it happens. A client watching "searched
  'termination clause Unit 7A' — 4 passages" understands what they are paying
  for, and so do we when an answer comes out wrong.
"""

import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.agent.tools import TOOL_SCHEMAS, ToolContext, run_tool
from app.services.generation.llm import (
    LLMProvider,
    StreamEnd,
    TextDelta,
    ToolUse,
    get_llm_provider,
)
from app.services.generation.prompts import history_for_prompt
from app.services.retrieval.base import RetrievedChunk, SearchFilters

log = get_logger(__name__)

# Added to the normal system prompt when tools are available. The rules about
# citing and refusing are unchanged — they come from prompts.py — because the
# agent must behave exactly like plain RAG once it has its passages.
AGENT_INSTRUCTIONS = """

You have tools for finding information. Use them like this:
- Search first. If the passages don't answer the question, search again with \
different words rather than guessing.
- A question about two documents needs information from BOTH before you answer.
- Look up a document's id with list_documents before using a tool that needs one.
- Stop calling tools as soon as you can answer, and never call the same tool \
twice with the same arguments.
- Passages keep their numbers across every tool call: cite them as [n] exactly \
as you received them."""

FINAL_TURN_INSTRUCTION = (
    "You have used all the tool calls available for this question. "
    "Answer now using only the passages you already have, citing them as [n]. "
    "If they are not enough, say so plainly."
)


@dataclass
class AgentStep:
    """One completed tool call — the row the UI shows in the trace."""

    number: int
    tool: str
    arguments: dict
    summary: str
    passages_found: int
    latency_ms: int
    error: bool = False


@dataclass
class AgentDone:
    """The end of the loop: everything the caller needs to save the answer."""

    answer: str
    passages: list[RetrievedChunk]  # in citation order: index 0 is [1]
    steps: list[AgentStep]
    model: str | None
    input_tokens: int
    output_tokens: int
    stop_reason: str | None
    hit_step_limit: bool = False
    retrievals: list = field(default_factory=list)  # for usage/cost accounting


AgentEvent = TextDelta | AgentStep | AgentDone


async def run_agent(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    collection_ids: list[uuid.UUID],
    question: str,
    history: list[dict],
    system: str,
    filters: SearchFilters | None = None,
    model: str | None = None,
    provider: LLMProvider | None = None,
) -> AsyncIterator[AgentEvent]:
    """
    Answer a question with tools, streaming text and steps as they happen.

    Yields TextDelta for every piece of text (forward it to the user), AgentStep
    after each tool call, and exactly one AgentDone at the end.
    """
    settings = get_settings()
    llm = provider or get_llm_provider()
    ctx = ToolContext(
        session=session, tenant_id=tenant_id, collection_ids=collection_ids, filters=filters
    )

    messages: list[dict] = [*history_for_prompt(history), {"role": "user", "content": question}]
    system_prompt = system + AGENT_INSTRUCTIONS

    answer_parts: list[str] = []
    steps: list[AgentStep] = []
    input_tokens = output_tokens = 0
    served_model: str | None = None
    stop_reason: str | None = None
    hit_step_limit = False

    while True:
        # Out of steps: ask once more with no tools on the table. The model can
        # then only do one thing — answer with what it has.
        final_turn = len(steps) >= settings.agent_max_steps
        if final_turn:
            hit_step_limit = True
            messages.append({"role": "user", "content": FINAL_TURN_INSTRUCTION})

        tool_calls: list[ToolUse] = []
        end: StreamEnd | None = None
        events = (
            llm.stream(
                system=system_prompt,
                messages=messages,
                max_tokens=settings.llm_max_tokens,
                model=model,
            )
            if final_turn
            else llm.stream_with_tools(
                system=system_prompt,
                messages=messages,
                tools=TOOL_SCHEMAS,
                max_tokens=settings.llm_max_tokens,
                model=model,
            )
        )
        async for event in events:
            if isinstance(event, TextDelta):
                answer_parts.append(event.text)
                yield event
            elif isinstance(event, ToolUse):
                tool_calls.append(event)
            else:
                end = event

        if end:
            input_tokens += end.input_tokens
            output_tokens += end.output_tokens
            served_model = end.model
            stop_reason = end.stop_reason

        if final_turn or not tool_calls:
            break

        # Replay the model's turn, then answer each tool call in one user turn —
        # the API expects every tool_use block to be followed by its result.
        messages.append({"role": "assistant", "content": end.content if end else []})
        results = []
        for call in tool_calls:
            started = time.perf_counter()
            result = await run_tool(ctx, call.name, call.input)
            step = AgentStep(
                number=len(steps) + 1,
                tool=call.name,
                arguments=call.input,
                summary=result.summary,
                passages_found=result.passages_found,
                latency_ms=round((time.perf_counter() - started) * 1000),
                error=result.error,
            )
            steps.append(step)
            log.info(
                "agent.tool",
                step=step.number,
                tool=step.tool,
                passages=step.passages_found,
                error=step.error,
                latency_ms=step.latency_ms,
            )
            yield step
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": result.content,
                    "is_error": result.error,
                }
            )
        messages.append({"role": "user", "content": results})

    yield AgentDone(
        answer="".join(answer_parts),
        passages=ctx.passages,
        steps=steps,
        model=served_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        stop_reason=stop_reason,
        hit_step_limit=hit_step_limit,
        retrievals=ctx.retrievals,
    )
