"""Request/response models for users, groups and collection access (Week 4)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.db.models import CollectionPermission, UserRole


class UserCreate(BaseModel):
    email: EmailStr
    # The admin sets an initial password and passes it on; email invitations
    # with a signup link are a later addition.
    password: str = Field(min_length=10, max_length=128)
    role: UserRole = UserRole.MEMBER


class UserUpdate(BaseModel):
    role: UserRole | None = None
    is_active: bool | None = None


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)


class GroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    created_at: datetime


class GroupDetail(GroupOut):
    member_ids: list[uuid.UUID]


class GroupMemberAdd(BaseModel):
    user_id: uuid.UUID


class AccessGrant(BaseModel):
    group_id: uuid.UUID
    permission: CollectionPermission = CollectionPermission.READ


class CollectionAccessUpdate(BaseModel):
    """Replaces all grants on a collection (send the full list you want to keep)."""

    grants: list[AccessGrant]
