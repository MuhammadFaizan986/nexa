"""
Authentication endpoints: register, login, refresh, me.

Registering creates a new tenant (organisation), its owner user and a default
tenant-wide "General" collection, all in one transaction, so a new client can
upload documents immediately.
"""

import re
import unicodedata
import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.deps import CurrentUser, SessionDep
from app.core.security import (
    TokenError,
    burn_password_check_time,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.db.models import Collection, CollectionVisibility, Tenant, User, UserRole
from app.schemas.auth import (
    LoginRequest,
    MeResponse,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    TenantOut,
    TokenPair,
    UserOut,
)

router = APIRouter(tags=["auth"])

DEFAULT_COLLECTION = "General"
_INVALID_CREDENTIALS = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")


def slugify(name: str) -> str:
    """'Harbourview Property Group' -> 'harbourview-property-group'."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:60]


def _violated_constraint(exc: IntegrityError) -> str | None:
    """Name of the database constraint that failed (psycopg exposes it on `diag`)."""
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None)


def issue_tokens(user: User) -> TokenPair:
    access, expires_in = create_token(
        user_id=user.id, tenant_id=user.tenant_id, token_type="access"
    )
    refresh, _ = create_token(user_id=user.id, tenant_id=user.tenant_id, token_type="refresh")
    return TokenPair(access_token=access, refresh_token=refresh, expires_in=expires_in)


@router.post("/auth/register", response_model=RegisterResponse, status_code=201)
async def register(body: RegisterRequest, session: SessionDep) -> RegisterResponse:
    slug = slugify(body.tenant_name)
    if not slug:
        raise HTTPException(422, "Organisation name must contain letters or digits")

    # All three rows are written in ONE transaction: all exist, or none do.
    tenant = Tenant(id=uuid.uuid4(), name=body.tenant_name.strip(), slug=slug)
    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        email=body.email.lower(),
        password_hash=hash_password(body.password),
        role=UserRole.OWNER,
    )
    collection = Collection(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name=DEFAULT_COLLECTION,
        description="Documents visible to everyone in the organisation",
        visibility=CollectionVisibility.TENANT_WIDE,
    )
    try:
        # SQLAlchemy orders INSERTs by relationship(), not by bare foreign keys,
        # so we flush the tenant explicitly before the rows that reference it.
        session.add(tenant)
        await session.flush()
        session.add_all([user, collection])
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if _violated_constraint(exc) == "uq_tenants_slug":
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"An organisation with the handle '{slug}' already exists"
            ) from None
        raise  # anything else is a bug, not a user error: let it surface as a 500

    await session.refresh(user)  # load server-generated created_at
    return RegisterResponse(
        tokens=issue_tokens(user),
        user=UserOut.model_validate(user),
        tenant=TenantOut.model_validate(tenant),
        default_collection_id=collection.id,
    )


@router.post("/auth/login", response_model=TokenPair)
async def login(body: LoginRequest, session: SessionDep) -> TokenPair:
    user = await session.scalar(
        select(User)
        .join(Tenant, Tenant.id == User.tenant_id)
        .where(Tenant.slug == body.tenant_slug.strip().lower(), User.email == body.email.lower())
    )
    if user is None:
        # Same work + same error as a wrong password: attackers can't tell
        # "no such user" from "wrong password" (by message or by timing).
        burn_password_check_time(body.password)
        raise _INVALID_CREDENTIALS
    if not verify_password(body.password, user.password_hash) or not user.is_active:
        raise _INVALID_CREDENTIALS
    return issue_tokens(user)


@router.post("/auth/refresh", response_model=TokenPair)
async def refresh(body: RefreshRequest, session: SessionDep) -> TokenPair:
    try:
        claims = decode_token(body.refresh_token, expected_type="refresh")
        user_id, tenant_id = uuid.UUID(claims["sub"]), uuid.UUID(claims["tid"])
    except (TokenError, ValueError):
        raise _INVALID_CREDENTIALS from None
    user = await session.scalar(select(User).where(User.id == user_id, User.tenant_id == tenant_id))
    if user is None or not user.is_active:
        raise _INVALID_CREDENTIALS
    return issue_tokens(user)


@router.get("/me", response_model=MeResponse)
async def me(user: CurrentUser, session: SessionDep) -> MeResponse:
    tenant = await session.get(Tenant, user.tenant_id)
    return MeResponse(user=UserOut.model_validate(user), tenant=TenantOut.model_validate(tenant))
