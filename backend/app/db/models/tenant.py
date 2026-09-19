"""
Tenants, users and collections — the "who owns what" part of the schema.

Multi-tenancy rule used everywhere in NEXA: every tenant-owned row carries a
`tenant_id`, and the tenant is ALWAYS taken from the authenticated user, never
from the request body. A user can't ask for another tenant's data because they
have no way to name another tenant.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.enums import CollectionVisibility, UserRole, sql_in


class Tenant(Base):
    """An organisation / client company. All its data is isolated from other tenants."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text)
    # URL-friendly unique handle, e.g. "harbourview-property". Users log in with it.
    slug: Mapped[str] = mapped_column(Text, unique=True)
    # Per-tenant configuration: assistant name, tone, model choice (Week 5).
    settings: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        # Email is unique *within* a tenant: the same consultant can belong to two
        # client tenants. That's why login asks for the tenant slug too.
        UniqueConstraint("tenant_id", "email"),
        CheckConstraint(f"role IN ({sql_in(UserRole)})", name="role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(
        String(16), default=UserRole.MEMBER, server_default=UserRole.MEMBER.value
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # lazy="raise": touching user.tenant without loading it explicitly raises an
    # error instead of silently firing a query (implicit IO is illegal in async).
    tenant: Mapped[Tenant] = relationship(lazy="raise")


class Collection(Base):
    """
    A folder of documents that share access rules, e.g. "Employee Handbook",
    "Compliance". Permissions are granted per collection (not per document), which
    keeps them manageable for thousands of documents.
    """

    __tablename__ = "collections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name"),
        CheckConstraint(f"visibility IN ({sql_in(CollectionVisibility)})", name="visibility"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(
        String(16),
        default=CollectionVisibility.RESTRICTED,
        server_default=CollectionVisibility.RESTRICTED.value,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
