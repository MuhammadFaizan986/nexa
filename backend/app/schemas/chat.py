"""Request/response models for search, chat and conversations."""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.retrieval.base import SearchFilters

MetadataValue = str | int | float | bool


class FilterFields(BaseModel):
    """Optional metadata filters, shared by /search and /chat."""

    filters: dict[str, MetadataValue] | None = Field(
        default=None,
        description='Document metadata that must match exactly, e.g. {"doc_type": "lease"}',
        examples=[{"doc_type": "lease", "building": "C"}],
    )
    date_from: date | None = Field(default=None, description='Document "date" metadata >= this')
    date_to: date | None = Field(default=None, description='Document "date" metadata <= this')

    def to_filters(self) -> SearchFilters | None:
        filters = SearchFilters(
            metadata=self.filters or {}, date_from=self.date_from, date_to=self.date_to
        )
        return None if filters.is_empty() else filters


class SearchRequest(FilterFields):
    query: str = Field(min_length=1, max_length=2000)
    # Exposing the mode lets you demo, live, why hybrid beats pure semantic search.
    mode: Literal["semantic", "keyword", "hybrid"] = "hybrid"
    # None = use the reranker if one is configured; false = skip it; true = require it.
    rerank: bool | None = None
    collection_ids: list[uuid.UUID] | None = None
    top_k: int = Field(default=10, ge=1, le=50)


class SearchHit(BaseModel):
    rank: int
    score: float  # score of the last stage that ranked the result
    similarity: float | None = None  # semantic search (cosine similarity)
    keyword_score: float | None = None  # keyword search (ts_rank_cd)
    fusion_score: float | None = None  # hybrid (RRF)
    rerank_score: float | None = None  # reranker relevance, 0..1
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    filename: str
    page_start: int | None
    page_end: int | None
    section_title: str | None
    content: str


class SearchResponse(BaseModel):
    mode: str
    reranked: bool
    results: list[SearchHit]
    latency_ms: dict[str, int]


class ChatRequest(FilterFields):
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: uuid.UUID | None = None  # omit to start a new conversation
    collection_ids: list[uuid.UUID] | None = None
    # Week 6: answer with tools — the model searches, reads and compares by
    # itself until it can answer. Better for questions that span documents,
    # and more expensive (a model call per step). None = the server default.
    agent: bool | None = None


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    created_at: datetime


class CitationOut(BaseModel):
    number: int
    chunk_id: uuid.UUID | None
    document_id: uuid.UUID | None
    document_title: str | None
    filename: str | None
    page_start: int | None
    snippet: str | None


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: dict | None
    created_at: datetime
    citations: list[CitationOut] = []
    # Week 6: set when a follow-up was rewritten before searching, so the UI can
    # show what was actually searched for — on reload too, not just live.
    search_query: str | None = None
    # Week 6: the tool calls behind an agent answer, so the trace is still
    # there when the conversation is reopened.
    tool_steps: list[dict] = []


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]
