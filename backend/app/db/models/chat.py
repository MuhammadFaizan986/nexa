"""
Conversations, messages and citations.

Storing every answer together with (a) the exact passages it cited, (b) token
usage and (c) per-stage latency means you can later answer questions like
"why did the assistant say that?", "what does a question cost?" and "which stage
is slow?" — all essential for evaluation, debugging and client reporting.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.enums import MessageRole, sql_in


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(f"role IN ({sql_in(MessageRole)})", name="role"),
        CheckConstraint("feedback IN (-1, 0, 1)", name="feedback"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    # Week 6: follow-up questions get rewritten into standalone search queries.
    rewritten_query: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    # e.g. {"embed_query": 180, "retrieval": 25, "llm_first_token": 900, "llm_total": 2100}
    latency_ms: Mapped[dict | None] = mapped_column(JSONB)
    feedback: Mapped[int | None] = mapped_column(SmallInteger)  # thumbs: -1 / 0 / 1 (Week 5)
    # Debug info not in the original plan schema: whether we refused, the LLM stop
    # reason, which chunks were retrieved and their scores, invalid citation numbers.
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, server_default=text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MessageCitation(Base):
    """One row per [n] marker the answer actually used (validated against the passages)."""

    __tablename__ = "message_citations"

    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True
    )
    citation_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    # SET NULL (not CASCADE): if a document is deleted later, the historical answer
    # keeps its citation row (with the saved snippet) instead of silently changing.
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chunks.id", ondelete="SET NULL"))
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL")
    )
    page_start: Mapped[int | None] = mapped_column(Integer)
    snippet: Mapped[str | None] = mapped_column(Text)
