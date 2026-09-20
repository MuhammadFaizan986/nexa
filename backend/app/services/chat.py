"""
The question-answering pipeline, streamed to the client as Server-Sent Events.

    question
      -> retrieve: embed, semantic + keyword search, fuse, rerank
                                   (latency: embed_query, semantic, keyword, rerank, retrieval)
      -> relevance gate: best score too low? -> "I don't know", no LLM call
      -> LLM streams an answer citing [n] passages    (latency: llm_first_token, llm_total)
      -> validate citations, save message + citations + usage

Server-Sent Events (SSE) is a simple one-way streaming format over plain HTTP:
the server keeps the response open and writes blocks like

    event: token
    data: {"text": "The notice period"}

followed by a blank line. Browsers read it with EventSource/fetch; curl with -N.
Our events, in order:

    meta   -> {"conversation_id", "user_message_id"}     (immediately)
    token  -> {"text": "..."}                             (many)
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
from app.services.generation.citations import CitationResult, resolve_citations
from app.services.generation.llm import StreamEnd, TextDelta, get_llm_provider
from app.services.generation.prompts import (
    NO_ANSWER,
    build_system_prompt,
    build_user_message,
    history_for_prompt,
)
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
            # 1-2. Retrieve: embed the question, run semantic + keyword search
            #      (tenant, permission and metadata filters inside the SQL), fuse
            #      them, rerank the best candidates. See services/retrieval/.
            retrieval = await retrieve(
                session,
                tenant_id=turn.tenant_id,
                collection_ids=turn.collection_ids,
                query=turn.question,
                filters=turn.filters,
            )
            passages = retrieval.chunks
            latency.update(retrieval.latency_ms)

            # 3. Relevance gate. If even the best passage is barely related, the
            #    honest answer is "I don't know" — and we save the LLM call.
            low_relevance = not passes_relevance_gate(retrieval)

            answer_parts: list[str] = []
            end: StreamEnd | None = None
            if low_relevance:
                answer_parts.append(NO_ANSWER)
                yield sse("token", {"text": NO_ANSWER})
            else:
                # 4. Generate, forwarding tokens to the client as they arrive.
                llm = get_llm_provider()
                messages = [
                    *history_for_prompt(turn.history),
                    {"role": "user", "content": build_user_message(turn.question, passages)},
                ]
                t = time.perf_counter()
                async for event in llm.stream(
                    system=build_system_prompt(turn.tenant_name),
                    messages=messages,
                    max_tokens=settings.llm_max_tokens,
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

            # 5. Citations: keep only [n] that point at a passage we really sent.
            cited = (
                resolve_citations(answer, passages)
                if not low_relevance
                else CitationResult(citations=[], invalid_numbers=[])
            )
            latency["total"] = _ms(started)

            # 6. Persist the answer, its citations and the usage.
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
                    "retrieval_mode": retrieval.mode,
                    "reranked": retrieval.reranked,
                    "gate_score": gate_score(retrieval),
                    "retrieved": _retrieval_trace(passages),
                    "invalid_citations": cited.invalid_numbers,
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
            record_retrieval_usage(
                session, retrieval, tenant_id=turn.tenant_id, user_id=turn.user_id
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
            mode=retrieval.mode,
            reranked=retrieval.reranked,
            gate_score=round(gate_score(retrieval) or 0.0, 4),
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
                "model": message.model,
                "usage": {
                    "input_tokens": message.input_tokens,
                    "output_tokens": message.output_tokens,
                    "cost_usd": _question_cost(retrieval, end),
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


def _question_cost(retrieval, end: StreamEnd | None):
    """LLM + reranker + question-embedding cost for this answer (None if unknown)."""
    parts = []
    if end:
        parts.append(estimate_cost(end.model, end.input_tokens, end.output_tokens))
    if retrieval.reranked and retrieval.reranker_model:
        parts.append(estimate_rerank_cost(retrieval.reranker_model))
    if retrieval.embedding_model:
        parts.append(estimate_cost(retrieval.embedding_model, retrieval.embedding_tokens))
    known = [p for p in parts if p is not None]
    return sum(known) if known else None
