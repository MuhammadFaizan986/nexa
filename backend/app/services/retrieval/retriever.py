"""
The retrieval pipeline, shared by /search, /chat and the evaluation harness.

    question ─▶ embed ─▶ semantic search (top 40) ───┐
             ├──────────▶ keyword search (top 40) ────┼─▶ RRF fusion ─▶ top 20
             └─(if the question contains identifiers   │      ─▶ reranker ─▶ top k (5)
                like "WO-6612")▶ identifier search ───┘

- mode="semantic" / "keyword" run one search; "hybrid" runs both and fuses them.
- The reranker (if configured) re-scores the top fused candidates.
- Tenant isolation, collection permissions and metadata filters are applied
  inside every search's SQL, so nothing outside them is ever ranked.

Because each stage can be switched on and off here, the eval harness can
compare "semantic vs keyword vs hybrid vs hybrid + reranker" on the exact same
code path that serves real users.
"""

import time
import uuid
from dataclasses import dataclass, replace

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import UsageEventType
from app.services.embeddings import get_embedding_provider
from app.services.retrieval.base import RetrievedChunk, SearchFilters
from app.services.retrieval.hybrid import reciprocal_rank_fusion
from app.services.retrieval.keyword import extract_identifiers, identifier_search, keyword_search
from app.services.retrieval.reranker import Reranker, RerankerConfigError, get_reranker
from app.services.retrieval.semantic import semantic_search
from app.services.usage import estimate_rerank_cost, record_usage


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk]  # the final top k, best first
    mode: str
    reranked: bool
    reranker_model: str | None
    embedding_model: str | None  # None when no embedding call was made
    embedding_tokens: int
    top_similarity: float | None  # best cosine similarity among semantic candidates
    top_rerank_score: float | None
    candidates: int  # distinct candidates considered before the final cut
    latency_ms: dict[str, int]


def _ms(since: float) -> int:
    return round((time.perf_counter() - since) * 1000)


async def retrieve(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    collection_ids: list[uuid.UUID],
    query: str,
    mode: str | None = None,
    rerank: bool | None = None,
    top_k: int | None = None,
    filters: SearchFilters | None = None,
    query_vector: list[float] | None = None,
    reranker: Reranker | None = None,
) -> RetrievalResult:
    """
    `rerank=None` means "use the reranker if one is configured".
    `query_vector` and `reranker` let the eval harness reuse a cached question
    embedding and a caching reranker (to save API calls across eval runs).
    """
    settings = get_settings()
    mode = mode or settings.retrieval_mode
    top_k = top_k or settings.retrieval_top_k
    reranker = None if rerank is False else (reranker or get_reranker())
    if rerank and reranker is None:
        raise RerankerConfigError("Reranking was requested but RERANKER_PROVIDER=none")

    latency: dict[str, int] = {}
    embedding_model, embedding_tokens = None, 0
    if mode in ("semantic", "hybrid") and query_vector is None:
        embedder = get_embedding_provider()
        started = time.perf_counter()
        embedded = await embedder.embed_query(query)
        latency["embed_query"] = _ms(started)
        query_vector = embedded.vectors[0]
        embedding_model, embedding_tokens = embedder.model, embedded.tokens

    retrieval_started = time.perf_counter()
    semantic: list[RetrievedChunk] = []
    keyword: list[RetrievedChunk] = []
    exact: list[RetrievedChunk] = []  # chunks containing the question's identifiers
    scope = {"tenant_id": tenant_id, "collection_ids": collection_ids, "filters": filters}
    if mode in ("semantic", "hybrid"):
        started = time.perf_counter()
        semantic = await semantic_search(
            session, query_vector=query_vector, limit=settings.retrieval_candidates, **scope
        )
        latency["semantic"] = _ms(started)
    if mode in ("keyword", "hybrid"):
        started = time.perf_counter()
        keyword = await keyword_search(
            session, query=query, limit=settings.retrieval_candidates, **scope
        )
        exact = await identifier_search(
            session,
            identifiers=extract_identifiers(query),
            limit=settings.retrieval_candidates,
            **scope,
        )
        latency["keyword"] = _ms(started)

    lists = [results for results in (semantic, keyword, exact) if results]
    if len(lists) > 1:
        # Rank fusion. An exact identifier match adds an extra "vote".
        candidates = reciprocal_rank_fusion(
            lists, k=settings.rrf_k, limit=settings.retrieval_candidates
        )
    else:
        candidates = lists[0] if lists else []

    top_rerank_score = None
    did_rerank = reranker is not None and bool(candidates)
    if did_rerank:
        pool = candidates[: settings.rerank_candidates]
        # The reranker sees the contextual header too ("Document: ... | Section: ...").
        reranked = await reranker.rerank(query, [c.text_with_context for c in pool], top_k)
        latency["rerank"] = reranked.latency_ms
        final = [
            replace(pool[hit.index], score=hit.score, rerank_score=hit.score, rank=rank)
            for rank, hit in enumerate(reranked.hits, start=1)
        ]
        top_rerank_score = final[0].rerank_score if final else None
    else:
        final = [replace(c, rank=rank) for rank, c in enumerate(candidates[:top_k], start=1)]
    latency["retrieval"] = _ms(retrieval_started)

    return RetrievalResult(
        chunks=final,
        mode=mode,
        reranked=did_rerank,
        reranker_model=reranker.model if did_rerank else None,
        embedding_model=embedding_model,
        embedding_tokens=embedding_tokens,
        top_similarity=max((c.similarity for c in semantic), default=None),
        top_rerank_score=top_rerank_score,
        candidates=len(candidates),
        latency_ms=latency,
    )


def passes_relevance_gate(result: RetrievalResult) -> bool:
    """
    Should we ask the LLM at all? With a reranker we trust its relevance score;
    without one, the best cosine similarity. Keyword-only search without a
    reranker has no comparable score, so any match passes.
    """
    settings = get_settings()
    if not result.chunks:
        return False
    if result.reranked:
        return (result.top_rerank_score or 0.0) >= settings.min_rerank_score
    if result.top_similarity is not None:
        return result.top_similarity >= settings.min_relevance_score
    return True


def gate_score(result: RetrievalResult) -> float | None:
    """The score the relevance gate looked at (for logs and debugging)."""
    return result.top_rerank_score if result.reranked else result.top_similarity


def record_retrieval_usage(
    session: AsyncSession,
    result: RetrievalResult,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
) -> None:
    if result.embedding_model:
        record_usage(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            event_type=UsageEventType.EMBED,
            model=result.embedding_model,
            input_tokens=result.embedding_tokens,
        )
    if result.reranked and result.reranker_model:
        # Rerankers are billed per search, not per token.
        record_usage(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            event_type=UsageEventType.RERANK,
            model=result.reranker_model,
            input_tokens=0,
            cost_usd=estimate_rerank_cost(result.reranker_model),
        )


__all__ = [
    "RetrievalResult",
    "gate_score",
    "passes_relevance_gate",
    "record_retrieval_usage",
    "retrieve",
]
