"""Request/response models for collections and documents."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import CollectionVisibility, DocumentStatus, IngestionJobStatus


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    visibility: CollectionVisibility = CollectionVisibility.RESTRICTED


class CollectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    visibility: str
    created_at: datetime


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    collection_id: uuid.UUID
    title: str
    filename: str
    mime_type: str
    status: DocumentStatus
    error_message: str | None
    page_count: int | None
    # The ORM attribute is `meta` (see models); expose it as "metadata" in JSON.
    metadata: dict = Field(validation_alias="meta")
    created_at: datetime


class IngestionJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: IngestionJobStatus
    attempts: int
    chunks_created: int | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class DocumentDetail(DocumentOut):
    chunk_count: int
    # What the background worker did (or is doing) with this document.
    ingestion: IngestionJobOut | None = None


class DocumentUpdate(BaseModel):
    # Replaces the document's metadata (used by metadata filters), e.g.
    # {"doc_type": "lease", "unit": "4B", "building": "A", "date": "2026-03-01"}.
    metadata: dict[str, str | int | float | bool]


class UploadResult(BaseModel):
    filename: str
    status: Literal["created", "duplicate", "retried", "rejected"]
    document: DocumentOut | None = None
    detail: str | None = None
