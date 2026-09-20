"""
Which collections may this user read and write?

This is THE security boundary of a multi-tenant RAG system. The rule (plan 9.3):
permission filtering happens INSIDE the search SQL, before ranking — never by
hiding results after the LLM has already seen them. So every search first asks
this module for the list of allowed collection ids, then filters on it.

The model (Week 4):

    users ──< group_members >── groups ──< collection_access >── collections

- Everything is scoped to the user's own tenant, always.
- `tenant_wide` collections: readable by everyone in the tenant.
- `restricted` collections: readable only through a group grant.
- Owners and admins: full access within their tenant.
- Writing (upload, edit metadata, delete): owners/admins anywhere; members in
  tenant-wide collections and wherever a group grants them `write`; viewers never.

Defence in depth: even if a query here were wrong, Postgres Row-Level Security
(app/db/rls.py) still refuses to return another TENANT's rows.
"""

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Collection,
    CollectionAccess,
    CollectionPermission,
    CollectionVisibility,
    GroupMember,
    User,
    UserRole,
)

_ADMIN_ROLES = {UserRole.OWNER, UserRole.ADMIN}


def is_admin(user: User) -> bool:
    return user.role in _ADMIN_ROLES


async def group_ids(session: AsyncSession, user: User) -> list[uuid.UUID]:
    return list(
        await session.scalars(select(GroupMember.group_id).where(GroupMember.user_id == user.id))
    )


def _granted_collection_ids(groups: list[uuid.UUID], *, write: bool = False):
    """Sub-query: collections granted to any of these groups."""
    query = select(CollectionAccess.collection_id).where(CollectionAccess.group_id.in_(groups))
    if write:
        query = query.where(CollectionAccess.permission == CollectionPermission.WRITE)
    return query


async def readable_collections(session: AsyncSession, user: User) -> list[Collection]:
    query = select(Collection).where(Collection.tenant_id == user.tenant_id)
    if not is_admin(user):
        readable = [Collection.visibility == CollectionVisibility.TENANT_WIDE]
        groups = await group_ids(session, user)
        if groups:
            readable.append(Collection.id.in_(_granted_collection_ids(groups)))
        query = query.where(or_(*readable))
    return list(await session.scalars(query.order_by(Collection.name)))


async def readable_collection_ids(session: AsyncSession, user: User) -> list[uuid.UUID]:
    return [c.id for c in await readable_collections(session, user)]


async def can_write_collection(session: AsyncSession, user: User, collection: Collection) -> bool:
    if collection.tenant_id != user.tenant_id or user.role == UserRole.VIEWER:
        return False
    if is_admin(user):
        return True
    if collection.visibility == CollectionVisibility.TENANT_WIDE:
        return True  # the shared "General" collection everyone can contribute to
    groups = await group_ids(session, user)
    if not groups:
        return False
    granted = await session.scalars(_granted_collection_ids(groups, write=True))
    return collection.id in set(granted)


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
