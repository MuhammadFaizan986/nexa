"""
Groups and per-collection access (Week 4).

Access is granted to GROUPS, not to individual users, because that's how
organisations actually work: "the Compliance team can read Compliance", and
people join or leave the team. One grant then covers everyone in it.

    users ──< group_members >── groups ──< collection_access >── collections

A collection is readable by a user when it is `tenant_wide`, or when one of the
user's groups has a grant on it (read or write). Owners and admins see
everything in their own tenant.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.enums import CollectionPermission, sql_in


class Group(Base):
    __tablename__ = "groups"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GroupMember(Base):
    __tablename__ = "group_members"

    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class CollectionAccess(Base):
    """One grant: this group may read (or write) this collection."""

    __tablename__ = "collection_access"
    __table_args__ = (
        CheckConstraint(f"permission IN ({sql_in(CollectionPermission)})", name="permission"),
    )

    collection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    permission: Mapped[str] = mapped_column(
        String(8), default=CollectionPermission.READ, server_default=CollectionPermission.READ.value
    )
