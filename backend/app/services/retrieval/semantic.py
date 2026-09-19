"""
Semantic (vector) search with pgvector.

Idea: embed the question, then find the chunks whose embeddings are nearest to
it by cosine distance (`<=>`). Postgres answers that with the HNSW index.

Two pgvector details that matter in a multi-tenant system (plan section 9.2):

1. Filtered HNSW can return TOO FEW rows.
   The index walks the graph to find, say, the 40 nearest vectors in the WHOLE
   table, and only then applies `WHERE tenant_id = ...`. If this tenant owns 1%
   of all chunks, most of those 40 belong to other tenants and get filtered
   out — you might get 2 results instead of 5. pgvector 0.8+ fixes this with
   *iterative index scans*: `hnsw.iterative_scan = relaxed_order` keeps walking
   the graph until enough rows pass the filter. `relaxed_order` means results
   can come back slightly out of order, so we re-sort them in an outer query
   (the MATERIALIZED CTE pattern from the pgvector README).

2. `hnsw.ef_search` = how many candidates the index explores (default 40).
   Higher = better recall, slightly slower. We use 100.

Both are set with `set_config(..., is_local => true)`, i.e. only for the current
transaction, so they can't leak into other requests sharing a pooled connection.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings


@dataclass
class RetrievedChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    filename: str
    content: str
    page_start: int | None
    page_end: int | None
    section_title: str | None
    score: float  # cosine similarity: 1.0 = identical direction, ~0 = unrelated
    rank: int  # 1 = best
    # [char_offset, page, section] where each page/section starts in `content`
    # (see chunker.py). None for chunks indexed before spans existed.
    spans: list | None = None


_SEMANTIC_SQL = text("""
    WITH nearest AS MATERIALIZED (
        SELECT c.id,
               c.embedding <=> CAST(:query_vec AS vector) AS distance
        FROM chunks c
        WHERE c.tenant_id = :tenant_id                -- tenant isolation
          AND c.collection_id = ANY(:collection_ids)  -- permission filter, BEFORE ranking
        ORDER BY c.embedding <=> CAST(:query_vec AS vector)
        LIMIT :limit
    )
    SELECT c.id            AS chunk_id,
           c.document_id,
           d.title         AS document_title,
           d.filename,
           c.content,
           c.page_start,
           c.page_end,
           c.section_title,
           c.metadata -> 'spans' AS spans,
           1 - n.distance  AS score
    FROM nearest n
    JOIN chunks c    ON c.id = n.id
    JOIN documents d ON d.id = c.document_id
    WHERE d.status = 'ready'
    ORDER BY n.distance
""")


def to_pgvector(vector: list[float]) -> str:
    """pgvector's text format: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{v:.7g}" for v in vector) + "]"


async def semantic_search(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    collection_ids: list[uuid.UUID],
    query_vector: list[float],
    limit: int,
) -> list[RetrievedChunk]:
    if not collection_ids:
        return []  # the user can't read anything: nothing to search

    settings = get_settings()
    await session.execute(text("SELECT set_config('hnsw.iterative_scan', 'relaxed_order', true)"))
    await session.execute(
        text("SELECT set_config('hnsw.ef_search', :ef, true)"),
        {"ef": str(max(settings.hnsw_ef_search, limit))},
    )
    rows = await session.execute(
        _SEMANTIC_SQL,
        {
            "query_vec": to_pgvector(query_vector),
            "tenant_id": tenant_id,
            "collection_ids": collection_ids,
            "limit": limit,
        },
    )
    return [RetrievedChunk(**row._mapping, rank=rank) for rank, row in enumerate(rows, start=1)]
