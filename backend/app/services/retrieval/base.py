"""
Shared pieces of the retrieval layer: the result type every search returns, the
columns they select, and metadata filters.

Retrieval in NEXA is a small pipeline (see retriever.py):

    semantic search ─┐
                     ├─ Reciprocal Rank Fusion ─ reranker ─ top k passages
    keyword search  ─┘

Every stage returns the same `RetrievedChunk`, and each stage fills in its own
score, so you can always see WHY a passage ranked where it did.
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any


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
    score: float  # the score of the LAST stage that ranked it (see below)
    rank: int  # 1 = best
    # [char_offset, page, section] where each page/section starts in `content`.
    spans: list | None = None
    sections: list | None = None  # every section the chunk touches
    context_header: str | None = None  # "Document: ... | Section: ..."
    # Per-stage scores (None = that stage didn't see this chunk):
    similarity: float | None = None  # cosine similarity, semantic search
    keyword_score: float | None = None  # ts_rank_cd, keyword search
    fusion_score: float | None = None  # RRF score, hybrid search
    rerank_score: float | None = None  # 0..1 relevance from the reranker

    @property
    def text_with_context(self) -> str:
        """What rerankers see: the contextual header + the chunk text."""
        if self.context_header:
            return f"{self.context_header}\n\n{self.content}"
        return self.content


# Columns every search selects (aliases match RetrievedChunk's fields).
CHUNK_COLUMNS = """
    c.id                        AS chunk_id,
    c.document_id,
    d.title                     AS document_title,
    d.filename,
    c.content,
    c.page_start,
    c.page_end,
    c.section_title,
    c.context_header,
    c.metadata -> 'spans'       AS spans,
    c.metadata -> 'sections'    AS sections
"""


@dataclass
class SearchFilters:
    """
    Metadata filters, applied INSIDE the search SQL (like permissions), so they
    narrow what is ranked rather than hiding results afterwards.

    `metadata` matches the document metadata given at upload, e.g.
    {"doc_type": "lease", "building": "C"}: every key must match exactly.
    `date_from` / `date_to` compare the document's "date" metadata (ISO format,
    "2026-03-01"); documents without a date are excluded when a date filter is set.
    """

    metadata: dict[str, Any] = field(default_factory=dict)
    date_from: date | None = None
    date_to: date | None = None

    def is_empty(self) -> bool:
        return not (self.metadata or self.date_from or self.date_to)

    def to_sql(self) -> tuple[str, dict[str, Any]]:
        """Extra `AND ...` clauses for a query aliasing chunks as `c`, plus their params."""
        clauses: list[str] = []
        params: dict[str, Any] = {}
        if self.metadata:
            # JSONB containment (@>) on the whole column can use the GIN index
            # chunks_metadata_gin. Chunks store the document's metadata under "doc".
            clauses.append("c.metadata @> CAST(:filter_metadata AS jsonb)")
            params["filter_metadata"] = json.dumps({"doc": self.metadata})
        # ISO dates ("2026-03-01") sort correctly as plain strings.
        if self.date_from:
            clauses.append("(c.metadata #>> '{doc,date}') >= :filter_date_from")
            params["filter_date_from"] = self.date_from.isoformat()
        if self.date_to:
            clauses.append("(c.metadata #>> '{doc,date}') <= :filter_date_to")
            params["filter_date_to"] = self.date_to.isoformat()
        return "".join(f"\n  AND {clause}" for clause in clauses), params
