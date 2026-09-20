"""Import every model here so `Base.metadata` knows all tables (Alembic relies on this)."""

from app.db.models.access import CollectionAccess, Group, GroupMember
from app.db.models.chat import Conversation, Message, MessageCitation
from app.db.models.document import EMBEDDING_DIM, Chunk, Document, IngestionJob
from app.db.models.enums import (
    CollectionPermission,
    CollectionVisibility,
    DocumentStatus,
    IngestionJobStatus,
    MessageRole,
    UsageEventType,
    UserRole,
)
from app.db.models.eval import EvalResult, EvalRun
from app.db.models.tenant import Collection, Tenant, User
from app.db.models.usage import UsageEvent

__all__ = [
    "EMBEDDING_DIM",
    "Chunk",
    "Collection",
    "CollectionAccess",
    "CollectionPermission",
    "CollectionVisibility",
    "Conversation",
    "Document",
    "DocumentStatus",
    "EvalResult",
    "EvalRun",
    "Group",
    "GroupMember",
    "IngestionJob",
    "IngestionJobStatus",
    "Message",
    "MessageCitation",
    "MessageRole",
    "Tenant",
    "UsageEvent",
    "UsageEventType",
    "User",
    "UserRole",
]
