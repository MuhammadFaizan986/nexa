"""
Admin endpoints for users, groups and collection access (Week 4).

The shape of a real setup, using the plan's fintech demo:

    POST /users               -> alice (member), bob (member)
    POST /groups              -> "Compliance Team"
    POST /groups/{id}/members -> alice
    PUT  /collections/{id}/access with grants=[{group: Compliance Team, permission: read}]

Alice can now search the Compliance collection; Bob asking the same question
gets "I don't have enough information", because the permission filter runs
inside the search SQL (services/retrieval/permissions.py).

All of these require the owner or admin role, and everything is scoped to the
caller's own tenant.
"""

import uuid

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.core.deps import AdminUser, SessionDep
from app.core.security import hash_password
from app.db.models import Collection, CollectionAccess, Group, GroupMember, User
from app.schemas.access import (
    AccessGrant,
    CollectionAccessUpdate,
    GroupCreate,
    GroupDetail,
    GroupMemberAdd,
    GroupOut,
    UserCreate,
    UserUpdate,
)
from app.schemas.auth import UserOut

router = APIRouter(tags=["admin"])


# ----------------------------------------------------------------------------- users


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(body: UserCreate, admin: AdminUser, session: SessionDep) -> User:
    user = User(
        tenant_id=admin.tenant_id,  # always the admin's own tenant
        email=body.email.lower(),
        password_hash=hash_password(body.password),
        role=body.role,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "This email already exists") from None
    await session.refresh(user)
    return user


@router.get("/users", response_model=list[UserOut])
async def list_users(admin: AdminUser, session: SessionDep) -> list[User]:
    return list(
        await session.scalars(
            select(User).where(User.tenant_id == admin.tenant_id).order_by(User.email)
        )
    )


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID, body: UserUpdate, admin: AdminUser, session: SessionDep
) -> User:
    """Change a role, or deactivate someone. Deactivated users can't log in."""
    user = await _tenant_user(session, admin, user_id)
    if user.id == admin.id and (body.is_active is False or body.role is not None):
        raise HTTPException(400, "You can't change your own role or deactivate yourself")
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    await session.commit()
    await session.refresh(user)
    return user


# ----------------------------------------------------------------------------- groups


@router.post("/groups", response_model=GroupOut, status_code=201)
async def create_group(body: GroupCreate, admin: AdminUser, session: SessionDep) -> Group:
    group = Group(tenant_id=admin.tenant_id, name=body.name.strip(), description=body.description)
    session.add(group)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A group with this name exists") from None
    await session.refresh(group)
    return group


@router.get("/groups", response_model=list[GroupOut])
async def list_groups(admin: AdminUser, session: SessionDep) -> list[Group]:
    return list(
        await session.scalars(
            select(Group).where(Group.tenant_id == admin.tenant_id).order_by(Group.name)
        )
    )


@router.get("/groups/{group_id}", response_model=GroupDetail)
async def get_group(group_id: uuid.UUID, admin: AdminUser, session: SessionDep) -> GroupDetail:
    group = await _tenant_group(session, admin, group_id)
    members = list(
        await session.scalars(select(GroupMember.user_id).where(GroupMember.group_id == group.id))
    )
    return GroupDetail(**GroupOut.model_validate(group).model_dump(), member_ids=members)


@router.post("/groups/{group_id}/members", response_model=GroupDetail, status_code=201)
async def add_group_member(
    group_id: uuid.UUID, body: GroupMemberAdd, admin: AdminUser, session: SessionDep
) -> GroupDetail:
    group = await _tenant_group(session, admin, group_id)
    await _tenant_user(session, admin, body.user_id)  # must be in the same tenant
    session.add(GroupMember(group_id=group.id, user_id=body.user_id))
    try:
        await session.commit()
    except IntegrityError:  # already a member
        await session.rollback()
    return await get_group(group_id, admin, session)


@router.delete("/groups/{group_id}/members/{user_id}", status_code=204)
async def remove_group_member(
    group_id: uuid.UUID, user_id: uuid.UUID, admin: AdminUser, session: SessionDep
) -> Response:
    group = await _tenant_group(session, admin, group_id)
    await session.execute(
        delete(GroupMember).where(GroupMember.group_id == group.id, GroupMember.user_id == user_id)
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ collection access


@router.get("/collections/{collection_id}/access", response_model=list[AccessGrant])
async def get_collection_access(
    collection_id: uuid.UUID, admin: AdminUser, session: SessionDep
) -> list[AccessGrant]:
    collection = await _tenant_collection(session, admin, collection_id)
    grants = await session.scalars(
        select(CollectionAccess).where(CollectionAccess.collection_id == collection.id)
    )
    return [AccessGrant(group_id=g.group_id, permission=g.permission) for g in grants]


@router.put("/collections/{collection_id}/access", response_model=list[AccessGrant])
async def set_collection_access(
    collection_id: uuid.UUID,
    body: CollectionAccessUpdate,
    admin: AdminUser,
    session: SessionDep,
) -> list[AccessGrant]:
    """Replace the grants on a collection with exactly the list given."""
    collection = await _tenant_collection(session, admin, collection_id)
    tenant_groups = set(
        await session.scalars(select(Group.id).where(Group.tenant_id == admin.tenant_id))
    )
    unknown = [g.group_id for g in body.grants if g.group_id not in tenant_groups]
    if unknown:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown group(s): {unknown}")

    await session.execute(
        delete(CollectionAccess).where(CollectionAccess.collection_id == collection.id)
    )
    session.add_all(
        CollectionAccess(
            collection_id=collection.id, group_id=grant.group_id, permission=grant.permission
        )
        for grant in body.grants
    )
    await session.commit()
    return body.grants


# ----------------------------------------------------------------------------- helpers


async def _tenant_user(session: SessionDep, admin: User, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None or user.tenant_id != admin.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


async def _tenant_group(session: SessionDep, admin: User, group_id: uuid.UUID) -> Group:
    group = await session.get(Group, group_id)
    if group is None or group.tenant_id != admin.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not found")
    return group


async def _tenant_collection(
    session: SessionDep, admin: User, collection_id: uuid.UUID
) -> Collection:
    collection = await session.get(Collection, collection_id)
    if collection is None or collection.tenant_id != admin.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Collection not found")
    return collection
