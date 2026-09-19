"""
Which collections may this user read / write?

This is THE security boundary of a multi-tenant RAG system. The rule (plan 9.3):
permission filtering happens INSIDE the search SQL, before ranking — never by
hiding results after the LLM has already seen them. So every search first asks
this module for the list of allowed collection ids, then filters on it.

Rules for Weeks 1–3 (deny by default):
  - Everything is scoped to the user's own tenant.
  - `tenant_wide` collections: every user in the tenant can read them.
  - `restricted` collections: only owners/admins, until Week 4 adds groups and
    `collection_access` grants ("Compliance team can read Compliance").
  - Writing (uploading): owners/admins anywhere; members only into collections
    they can read that are tenant-wide; viewers never.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Collection, CollectionVisibility, User, UserRole

_ADMIN_ROLES = {UserRole.OWNER, UserRole.ADMIN}


def is_admin(user: User) -> bool:
    return user.role in _ADMIN_ROLES


async def readable_collections(session: AsyncSession, user: User) -> list[Collection]:
    query = select(Collection).where(Collection.tenant_id == user.tenant_id)
    if not is_admin(user):
        query = query.where(Collection.visibility == CollectionVisibility.TENANT_WIDE)
    return list(await session.scalars(query.order_by(Collection.name)))


async def readable_collection_ids(session: AsyncSession, user: User) -> list[uuid.UUID]:
    return [c.id for c in await readable_collections(session, user)]


def can_write_collection(user: User, collection: Collection) -> bool:
    if collection.tenant_id != user.tenant_id:
        return False
    if is_admin(user):
        return True
    return (
        user.role == UserRole.MEMBER and collection.visibility == CollectionVisibility.TENANT_WIDE
    )


class CollectionAccessError(Exception):
    """Requested a collection that doesn't exist or isn't readable (we don't say which)."""


async def resolve_search_scope(
    session: AsyncSession, user: User, requested: list[uuid.UUID] | None
) -> list[uuid.UUID]:
    """
    The collection ids a search may touch: everything readable, or the requested
    subset of it. Asking for an unreadable collection is an error rather than
    silently searching less — otherwise the user would get a misleading
    "I don't know". We report "not found" instead of "forbidden" so a user can't
    probe which collection ids exist.
    """
    allowed = await readable_collection_ids(session, user)
    if not requested:
        return allowed
    allowed_set = set(allowed)
    if any(cid not in allowed_set for cid in requested):
        raise CollectionAccessError
    return list(dict.fromkeys(requested))  # de-duplicate, keep order
