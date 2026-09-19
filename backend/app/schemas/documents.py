"""Request/response models for collections and documents."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import CollectionVisibility, DocumentStatus


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


class DocumentDetail(DocumentOut):
    chunk_count: int


class UploadResult(BaseModel):
    filename: str
    status: Literal["created", "duplicate", "retried", "rejected"]
    document: DocumentOut | None = None
    detail: str | None = None
