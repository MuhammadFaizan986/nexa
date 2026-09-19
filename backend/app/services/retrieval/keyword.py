"""
Keyword (full-text) search with PostgreSQL.

Semantic search matches MEANING; keyword search matches the actual WORDS. It
shines exactly where embeddings are weak: identifiers and exact terms like
"E-204", "INS-2026-0259", "Clause 7.2" or a person's name.

How Postgres does it:
- `chunks.tsv` (a generated tsvector column) stores each chunk's words in
  normalised form ("terminating" -> "termin"), with the contextual header
  weighted lower (B) than the chunk text (A). A GIN index makes lookups fast.
- `websearch_to_tsquery()` turns the question into a tsquery the same way.
- `@@` tests for a match; `ts_rank_cd()` scores how well it matches (more
  query words, closer together, in higher-weighted text = higher score).

One deliberate change from the textbook version: websearch_to_tsquery joins the
words with AND, so "What does error code E-204 mean?" would only match chunks
containing ALL of error, code, e-204 and mean. Users ask questions, not search
queries, so we turn the ANDs into ORs and let ts_rank_cd reward the chunks that
match the most words. (Quoted "exact phrases" still work, as a phrase inside
the OR.)

Identifier boost: Postgres ranking has no IDF ("inverse document frequency"),
i.e. it doesn't know that "WO-6612" is rare and decisive while "work order"
appears in 50 chunks. So in "Which contractor was given work order WO-6612?"
the common words drown out the identifier (measured: the right chunk ranked
14th). BM25-based engines (Elasticsearch, OpenSearch) weight rare terms
automatically; we get the same effect cheaply by pulling identifier-like tokens
out of the question and running them as their own small search, which then
becomes an extra list in the rank fusion (retriever.py).
"""

import re
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.retrieval.base import CHUNK_COLUMNS, RetrievedChunk, SearchFilters

_KEYWORD_SQL = """
    WITH q AS (
        SELECT replace(websearch_to_tsquery('english', :query)::text, ' & ', ' | ')::tsquery
               AS query
    )
    SELECT {columns},
           ts_rank_cd(c.tsv, q.query) AS score
    FROM q, chunks c
    JOIN documents d ON d.id = c.document_id
    WHERE c.tenant_id = :tenant_id                -- tenant isolation
      AND c.collection_id = ANY(:collection_ids)  -- permission filter, BEFORE ranking
      AND d.status = 'ready'
      AND c.tsv @@ q.query
      {filters}
    ORDER BY score DESC, c.id
    LIMIT :limit
"""


# A "word" with at least one digit AND a letter, hyphen, dot or slash:
# WO-6612, E-204, 4B, 7A, 7.2, INS-2026-0259, v2.4.0. Plain numbers (2026,
# $2,450) are too common in business documents to be useful identifiers.
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9./-]*[A-Za-z0-9]|[A-Za-z0-9]")


def extract_identifiers(query: str) -> list[str]:
    identifiers = []
    for token in _TOKEN.findall(query):
        has_digit = any(ch.isdigit() for ch in token)
        has_marker = any(ch.isalpha() or ch in "-./" for ch in token)
        if has_digit and has_marker and token not in identifiers:
            identifiers.append(token)
    return identifiers


async def keyword_search(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    collection_ids: list[uuid.UUID],
    query: str,
    limit: int,
    filters: SearchFilters | None = None,
) -> list[RetrievedChunk]:
    if not collection_ids or not query.strip():
        return []
    filter_sql, filter_params = (filters or SearchFilters()).to_sql()
    rows = await session.execute(
        text(_KEYWORD_SQL.format(columns=CHUNK_COLUMNS, filters=filter_sql)),
        {
            "query": query,
            "tenant_id": tenant_id,
            "collection_ids": collection_ids,
            "limit": limit,
            **filter_params,
        },
    )
    return [
        RetrievedChunk(**row._mapping, rank=rank, keyword_score=row.score)
        for rank, row in enumerate(rows, start=1)
    ]


async def identifier_search(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    collection_ids: list[uuid.UUID],
    identifiers: list[str],
    limit: int,
    filters: SearchFilters | None = None,
) -> list[RetrievedChunk]:
    """Chunks containing any of the exact identifiers (websearch syntax: "a or b")."""
    if not identifiers:
        return []
    return await keyword_search(
        session,
        tenant_id=tenant_id,
        collection_ids=collection_ids,
        query=" or ".join(identifiers),
        limit=limit,
        filters=filters,
    )
