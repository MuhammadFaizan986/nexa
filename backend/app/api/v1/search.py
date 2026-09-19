"""
Search only (no LLM): returns the ranked passages.

Useful for debugging retrieval ("did it even find the right chunk?") and for
demos: in Week 3 the same endpoint gains mode=keyword|hybrid and rerank=true,
so you can show a client side by side why hybrid search beats pure semantic.
"""

import time

from fastapi import APIRouter, HTTPException, status

from app.core.deps import CurrentUser, SessionDep
from app.db.models import UsageEventType
from app.schemas.chat import SearchHit, SearchRequest, SearchResponse
from app.services.embeddings import get_embedding_provider
from app.services.retrieval.permissions import CollectionAccessError, resolve_search_scope
from app.services.retrieval.semantic import semantic_search
from app.services.usage import record_usage

router = APIRouter(tags=["search"])


@router.post("/search", response_model=SearchResponse)
async def search(body: SearchRequest, user: CurrentUser, session: SessionDep) -> SearchResponse:
    try:
        collection_ids = await resolve_search_scope(session, user, body.collection_ids)
    except CollectionAccessError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Collection not found") from None

    embedder = get_embedding_provider()
    t0 = time.perf_counter()
    query = await embedder.embed_query(body.query)
    t1 = time.perf_counter()
    hits = await semantic_search(
        session,
        tenant_id=user.tenant_id,
        collection_ids=collection_ids,
        query_vector=query.vectors[0],
        limit=body.top_k,
    )
    t2 = time.perf_counter()

    record_usage(
        session,
        tenant_id=user.tenant_id,
        user_id=user.id,
        event_type=UsageEventType.EMBED,
        model=embedder.model,
        input_tokens=query.tokens,
    )
    await session.commit()

    return SearchResponse(
        mode=body.mode,
        results=[SearchHit(**vars(hit)) for hit in hits],
        latency_ms={
            "embed_query": round((t1 - t0) * 1000),
            "retrieval": round((t2 - t1) * 1000),
        },
    )
