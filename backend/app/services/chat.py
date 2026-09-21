"""
The question-answering pipeline, streamed to the client as Server-Sent Events.

There are two ways to answer, chosen per question:

    RAG mode (the default) — one search, one answer:

    question
      -> rewrite a follow-up into a standalone query        (latency: rewrite)
      -> retrieve: embed, semantic + keyword search, fuse, rerank
                                   (latency: embed_query, semantic, keyword, rerank, retrieval)
      -> relevance gate: best score too low? -> "I don't know", no LLM call
      -> LLM streams an answer citing [n] passages    (latency: llm_first_token, llm_total)
      -> validate citations, save message + citations + usage

    Agent mode (opt-in, `agent: true`) — the model uses tools until it can
    answer: search again, read a document, compare two of them. Each tool call
    is streamed to the client as a `tool` event. See services/agent/.

Server-Sent Events (SSE) is a simple one-way streaming format over plain HTTP:
the server keeps the response open and writes blocks like

    event: token
    data: {"text": "The notice period"}

followed by a blank line. Browsers read it with EventSource/fetch; curl with -N.
Our events, in order:

    meta   -> {"conversation_id", "user_message_id"}     (immediately)
    token  -> {"text": "..."}                             (many)
    tool   -> {"number", "tool", "summary", ...}          (agent mode only)
    done   -> {"message_id", "citations", "usage", ...}   (once, at the end)
    error  -> {"detail": "..."}                           (instead of done, on failure)

Why this runs with its OWN database session: the HTTP handler returns the
StreamingResponse right away and this generator keeps running afterwards, so it
must not rely on the request-scoped session.
"""

import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import Message, MessageCitation, MessageRole, UsageEventType
from app.db.rls import use_tenant
from app.db.session import SessionLocal
from app.services.agent.loop import AgentDone, AgentStep, run_agent
from app.services.generation.citations import CitationResult, resolve_citations
from app.services.generation.llm import StreamEnd, TextDelta, get_llm_provider
from app.services.generation.prompts import (
    NO_ANSWER,
    build_system_prompt,
    build_user_message,
    history_for_prompt,
)
from app.services.generation.rewrite import RewriteResult, rewrite_question
from app.services.retrieval.base import RetrievedChunk, SearchFilters
from app.services.retrieval.retriever import (
    gate_score,
    passes_relevance_gate,
    record_retrieval_usage,
    retrieve,
)
from app.services.usage import estimate_cost, estimate_rerank_cost, record_usage

log = get_logger(__name__)

MODEL_REFUSAL_TEXT = "I'm not able to help with that request."


@dataclass
class ChatTurn:
    """Everything the stream needs, captured as plain values by the HTTP handler."""

    tenant_id: uuid.UUID
    tenant_name: str
    user_id: uuid.UUID
    conversation_id: uuid.UUID
    user_message_id: uuid.UUID
    question: str
    history: list[dict]
    collection_ids: list[uuid.UUID]
    filters: SearchFilters | None = None
    # Per-tenant settings (Week 5): how the assistant is named, how it should
    # sound, and which model answers.
    assistant_name: str | None = None
    tone: str | None = None
    model: str | None = None
    # Week 6: answer with tools (the model searches, reads and compares by
    # itself) instead of one search + one answer. Costs more, so it's opt-in.
    agent: bool = False


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _ms(since: float) -> int:
    return round((time.perf_counter() - since) * 1000)


async def stream_answer(turn: ChatTurn) -> AsyncIterator[str]:
    settings = get_settings()
    started = time.perf_counter()
    latency: dict[str, int] = {}

    yield sse(
        "meta",
        {"conversation_id": turn.conversation_id, "user_message_id": turn.user_message_id},
    )

    # This generator keeps running after the HTTP handler has returned, so it
    # sets the tenant itself for Row-Level Security (app/db/rls.py).
    use_tenant(turn.tenant_id)
    try:
        async with SessionLocal() as session:
            # 1. Rewrite a follow-up into a question that stands on its own, so
            #    "what about the pet bond?" searches for the pet bond OF UNIT 4B.
            #    Skipped (free) for a first question or a self-contained one, and
            #    it falls back to the original on any problem. See
            #    services/generation/rewrite.py.
            # In agent mode the model writes its own search queries, so there
            # is nothing for a rewrite to fix and no reason to pay for one.
            rewrite = (
                RewriteResult(turn.question, turn.question, False, "agent")
                if turn.agent
                else await rewrite_question(turn.question, turn.history)
            )
            if rewrite.model:
                # Only when the model was really called: a skipped rewrite costs
                # no time, and a zero in the latency chart would imply otherwise.
                latency["rewrite"] = rewrite.latency_ms

            system_prompt = build_system_prompt(turn.tenant_name, turn.assistant_name, turn.tone)
            answer_parts: list[str] = []
            end: StreamEnd | None = None
            retrieval = None  # one search (RAG mode); the agent runs its own
            retrievals: list = []
            steps: list[AgentStep] = []
            agent_result: AgentDone | None = None
            low_relevance = False

            if turn.agent:
                # 2-5. Agent mode: the model decides what to look up, and we
                #      stream both its text and each tool call as it happens.
                #      See services/agent/loop.py.
                t = time.perf_counter()
                async for event in run_agent(
                    session,
                    tenant_id=turn.tenant_id,
                    collection_ids=turn.collection_ids,
                    question=turn.question,
                    history=turn.history,
                    system=system_prompt,
                    filters=turn.filters,
                    model=turn.model,
                ):
                    if isinstance(event, TextDelta):
                        if not answer_parts:
                            latency["llm_first_token"] = _ms(t)
                        answer_parts.append(event.text)
                        yield sse("token", {"text": event.text})
                    elif isinstance(event, AgentStep):
                        steps.append(event)
                        yield sse("tool", asdict(event))
                    else:
                        agent_result = event
                latency["llm_total"] = _ms(t)

                assert agent_result is not None
                passages = agent_result.passages
                retrievals = agent_result.retrievals
                end = StreamEnd(
                    model=agent_result.model or settings.llm_model,
                    input_tokens=agent_result.input_tokens,
                    output_tokens=agent_result.output_tokens,
                    stop_reason=agent_result.stop_reason,
                )
            else:
                # 2-3. Retrieve: embed the question, run semantic + keyword search
                #      (tenant, permission and metadata filters inside the SQL), fuse
                #      them, rerank the best candidates. See services/retrieval/.
                retrieval = await retrieve(
                    session,
                    tenant_id=turn.tenant_id,
                    collection_ids=turn.collection_ids,
                    query=rewrite.query,
                    filters=turn.filters,
                )
                retrievals = [retrieval]
                passages = retrieval.chunks
                latency.update(retrieval.latency_ms)

                # 4. Relevance gate. If even the best passage is barely related, the
                #    honest answer is "I don't know" — and we save the LLM call.
                low_relevance = not passes_relevance_gate(retrieval)

                if low_relevance:
                    answer_parts.append(NO_ANSWER)
                    yield sse("token", {"text": NO_ANSWER})
                else:
                    # 5. Generate, forwarding tokens to the client as they arrive.
                    llm = get_llm_provider()
                    messages = [
                        *history_for_prompt(turn.history),
                        {"role": "user", "content": build_user_message(turn.question, passages)},
                    ]
                    t = time.perf_counter()
                    async for event in llm.stream(
                        system=system_prompt,
                        messages=messages,
                        max_tokens=settings.llm_max_tokens,
                        model=turn.model,
                    ):
                        if isinstance(event, TextDelta):
                            if not answer_parts:
                                latency["llm_first_token"] = _ms(t)
                            answer_parts.append(event.text)
                            yield sse("token", {"text": event.text})
                        else:
                            end = event
                    latency["llm_total"] = _ms(t)

                    if end and end.stop_reason == "refusal" and not "".join(answer_parts).strip():
                        # Every model in the fallback chain declined. Say so plainly.
                        answer_parts.append(MODEL_REFUSAL_TEXT)
                        yield sse("token", {"text": MODEL_REFUSAL_TEXT})

            answer = "".join(answer_parts)

            # 6. Citations: keep only [n] that point at a passage we really sent.
            cited = (
                resolve_citations(answer, passages)
                if not low_relevance
                else CitationResult(citations=[], invalid_numbers=[])
            )
            latency["total"] = _ms(started)

            # 7. Persist the answer, its citations and the usage.
            message = Message(
                conversation_id=turn.conversation_id,
                role=MessageRole.ASSISTANT,
                content=answer,
                model=end.model if end else None,
                input_tokens=end.input_tokens if end else None,
                output_tokens=end.output_tokens if end else None,
                latency_ms=latency,
                meta={
                    "no_answer": low_relevance or NO_ANSWER.lower() in answer.lower(),
                    "low_relevance": low_relevance,
                    "stop_reason": end.stop_reason if end else None,
                    "search_query": rewrite.query if rewrite.rewritten else None,
                    "rewrite_reason": rewrite.reason,
                    "retrieval_mode": retrieval.mode if retrieval else "agent",
                    "reranked": retrieval.reranked if retrieval else None,
                    "gate_score": gate_score(retrieval) if retrieval else None,
                    "retrieved": _retrieval_trace(passages),
                    "invalid_citations": cited.invalid_numbers,
                    # The tool trace: what the assistant did to find the answer.
                    "agent": bool(turn.agent),
                    "tool_steps": [asdict(step) for step in steps],
                    "hit_step_limit": bool(agent_result and agent_result.hit_step_limit),
                },
            )
            session.add(message)
            await session.flush()  # assigns message.id for the citation rows
            for c in cited.citations:
                session.add(
                    MessageCitation(
                        message_id=message.id,
                        citation_number=c.number,
                        chunk_id=c.chunk_id,
                        document_id=c.document_id,
                        page_start=c.page_start,
                        snippet=c.snippet,
                    )
                )
            for result in retrievals:
                record_retrieval_usage(
                    session, result, tenant_id=turn.tenant_id, user_id=turn.user_id
                )
            if rewrite.model and (rewrite.input_tokens or rewrite.output_tokens):
                record_usage(
                    session,
                    tenant_id=turn.tenant_id,
                    user_id=turn.user_id,
                    event_type=UsageEventType.REWRITE,
                    model=rewrite.model,
                    input_tokens=rewrite.input_tokens,
                    output_tokens=rewrite.output_tokens,
                )
            if end:
                record_usage(
                    session,
                    tenant_id=turn.tenant_id,
                    user_id=turn.user_id,
                    event_type=UsageEventType.CHAT,
                    model=end.model,
                    input_tokens=end.input_tokens,
                    output_tokens=end.output_tokens,
                )
            await session.commit()

        log.info(
            "chat.answered",
            conversation_id=str(turn.conversation_id),
            message_id=str(message.id),
            passages=len(passages),
            rewritten=rewrite.rewritten,
            agent=turn.agent,
            tool_calls=len(steps),
            mode=retrieval.mode if retrieval else "agent",
            reranked=retrieval.reranked if retrieval else None,
            gate_score=round(gate_score(retrieval) or 0.0, 4) if retrieval else None,
            low_relevance=low_relevance,
            citations=len(cited.citations),
            invalid_citations=len(cited.invalid_numbers),
            **{f"latency_{k}_ms": v for k, v in latency.items()},
        )
        yield sse(
            "done",
            {
                "message_id": message.id,
                "citations": [asdict(c) for c in cited.citations],
                "invalid_citations": cited.invalid_numbers,
                "no_answer": message.meta["no_answer"],
                "search_query": rewrite.query if rewrite.rewritten else None,
                "tool_steps": [asdict(step) for step in steps],
                "model": message.model,
                "usage": {
                    "input_tokens": message.input_tokens,
                    "output_tokens": message.output_tokens,
                    "cost_usd": _question_cost(retrievals, end, rewrite),
                },
                "latency_ms": latency,
            },
        )
    except Exception as exc:
        # Full traceback goes to our logs; the client gets a generic message so
        # internal details (SQL, provider errors) never leak to users.
        log.exception("chat.failed", error_type=type(exc).__name__)
        yield sse("error", {"detail": "The assistant could not answer. Please try again."})


def _retrieval_trace(passages: list[RetrievedChunk]) -> list[dict]:
    """Every stage's score for each passage: why did it rank where it did?"""

    def rounded(value: float | None) -> float | None:
        return round(value, 4) if value is not None else None

    return [
        {
            "rank": p.rank,
            "chunk_id": str(p.chunk_id),
            "similarity": rounded(p.similarity),
            "keyword": rounded(p.keyword_score),
            "rerank": rounded(p.rerank_score),
        }
        for p in passages
    ]


def _question_cost(retrievals: list, end: StreamEnd | None, rewrite: RewriteResult | None = None):
    """
    What this one answer cost: the model, the rewrite, and every search.

    `retrievals` is a list because an agent answer runs several searches, each
    embedding a query and calling the reranker. Plain RAG passes a list of one.
    """
    parts = []
    if rewrite and rewrite.model and (rewrite.input_tokens or rewrite.output_tokens):
        parts.append(estimate_cost(rewrite.model, rewrite.input_tokens, rewrite.output_tokens))
    if end:
        parts.append(estimate_cost(end.model, end.input_tokens, end.output_tokens))
    for retrieval in retrievals:
        if retrieval.reranked and retrieval.reranker_model:
            parts.append(estimate_rerank_cost(retrieval.reranker_model))
        if retrieval.embedding_model:
            parts.append(estimate_cost(retrieval.embedding_model, retrieval.embedding_tokens))
    known = [p for p in parts if p is not None]
    return sum(known) if known else None
