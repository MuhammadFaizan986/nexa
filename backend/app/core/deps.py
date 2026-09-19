"""
FastAPI dependencies shared by the API routes.

A route declares what it needs, e.g. `user: CurrentUser, session: SessionDep`,
and FastAPI runs these functions first. That keeps authentication and tenant
resolution in ONE place instead of copy-pasted into every endpoint.
"""

import uuid
from collections.abc import Callable
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import TokenError, decode_token
from app.db.models import User, UserRole
from app.db.session import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# auto_error=False so we can return our own consistent 401 instead of FastAPI's 403.
_bearer = HTTPBearer(auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise _UNAUTHORIZED
    try:
        claims = decode_token(credentials.credentials, expected_type="access")
        user_id = uuid.UUID(claims["sub"])
        tenant_id = uuid.UUID(claims["tid"])
    except (TokenError, ValueError):
        raise _UNAUTHORIZED from None

    # Load the user matching BOTH ids from the token. The tenant used by every
    # query below comes from this row — never from anything the client sends.
    user = await session.scalar(select(User).where(User.id == user_id, User.tenant_id == tenant_id))
    if user is None or not user.is_active:
        raise _UNAUTHORIZED

    # Every log line for the rest of this request carries these ids.
    structlog.contextvars.bind_contextvars(tenant_id=str(tenant_id), user_id=str(user_id))
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: UserRole) -> Callable:
    """Dependency factory: `Depends(require_roles(UserRole.OWNER, UserRole.ADMIN))`."""

    async def checker(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role")
        return user

    return checker


AdminUser = Annotated[User, Depends(require_roles(UserRole.OWNER, UserRole.ADMIN))]
