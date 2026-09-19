"""Request/response models for search, chat and conversations."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    # Week 3 adds "keyword" and "hybrid" (+ a rerank flag) — exposing the mode
    # lets you demo the difference to clients live.
    mode: Literal["semantic"] = "semantic"
    collection_ids: list[uuid.UUID] | None = None
    top_k: int = Field(default=10, ge=1, le=50)


class SearchHit(BaseModel):
    rank: int
    score: float
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
    results: list[SearchHit]
    latency_ms: dict[str, int]


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: uuid.UUID | None = None  # omit to start a new conversation
    collection_ids: list[uuid.UUID] | None = None


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


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]
