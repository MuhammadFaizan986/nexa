"""
Request/response models for authentication.

Pydantic validates every field (types, lengths, email format) before our code runs.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    tenant_name: str = Field(min_length=2, max_length=100, examples=["Harbourview Property Group"])
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)


class LoginRequest(BaseModel):
    # Emails are unique per tenant, so login needs to know which tenant.
    tenant_slug: str = Field(min_length=1, max_length=100, examples=["harbourview-property-group"])
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # access token lifetime, seconds


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)  # build from ORM objects

    id: uuid.UUID
    name: str
    slug: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: str
    is_active: bool
    created_at: datetime


class RegisterResponse(BaseModel):
    tokens: TokenPair
    user: UserOut
    tenant: TenantOut
    default_collection_id: uuid.UUID


class MeResponse(BaseModel):
    user: UserOut
    tenant: TenantOut
