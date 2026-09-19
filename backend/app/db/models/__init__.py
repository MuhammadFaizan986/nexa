"""Import every model here so `Base.metadata` knows all tables (Alembic relies on this)."""

from app.db.models.chat import Conversation, Message, MessageCitation
from app.db.models.document import EMBEDDING_DIM, Chunk, Document
from app.db.models.enums import (
    CollectionVisibility,
    DocumentStatus,
    MessageRole,
    UsageEventType,
    UserRole,
)
from app.db.models.tenant import Collection, Tenant, User
from app.db.models.usage import UsageEvent

__all__ = [
    "EMBEDDING_DIM",
    "Chunk",
    "Collection",
    "CollectionVisibility",
    "Conversation",
    "Document",
    "DocumentStatus",
    "Message",
    "MessageCitation",
    "MessageRole",
    "Tenant",
    "UsageEvent",
    "UsageEventType",
    "User",
    "UserRole",
]
