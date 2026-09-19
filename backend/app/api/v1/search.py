"""
Search only (no LLM): returns the ranked passages with every stage's score.

Useful for debugging retrieval ("did it even find the right chunk?") and for
client demos: run the same question with mode=semantic, keyword and hybrid,
with and without the reranker, and show side by side why hybrid + reranking
wins (exact identifiers like "E-204" are the classic example).
"""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, status

from app.core.deps import CurrentUser, SessionDep
from app.schemas.chat import SearchHit, SearchRequest, SearchResponse
from app.services.retrieval.permissions import CollectionAccessError, resolve_search_scope
from app.services.retrieval.reranker import RerankerConfigError
from app.services.retrieval.retriever import record_retrieval_usage, retrieve

router = APIRouter(tags=["search"])


@router.post("/search", response_model=SearchResponse)
async def search(body: SearchRequest, user: CurrentUser, session: SessionDep) -> SearchResponse:
    try:
        collection_ids = await resolve_search_scope(session, user, body.collection_ids)
    except CollectionAccessError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Collection not found") from None

    try:
        result = await retrieve(
            session,
            tenant_id=user.tenant_id,
            collection_ids=collection_ids,
            query=body.query,
            mode=body.mode,
            rerank=body.rerank,
            top_k=body.top_k,
            filters=body.to_filters(),
        )
    except RerankerConfigError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None

    record_retrieval_usage(session, result, tenant_id=user.tenant_id, user_id=user.id)
    await session.commit()
    return SearchResponse(
        mode=result.mode,
        reranked=result.reranked,
        results=[SearchHit(**asdict(chunk)) for chunk in result.chunks],
        latency_ms=result.latency_ms,
    )
