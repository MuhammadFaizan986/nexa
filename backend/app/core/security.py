"""
Password hashing and JWT tokens.

Passwords
---------
We never store passwords, only an Argon2id hash. Argon2 is deliberately slow and
memory-hungry, so an attacker who steals the database can't brute-force the
hashes quickly. The hash string embeds its own salt and parameters, e.g.
`$argon2id$v=19$m=65536,t=3,p=4$<salt>$<hash>`.

Tokens
------
After login the client receives two JWTs (signed JSON, verifiable without a DB):

- access token  (15 min)  sent on every API call:  Authorization: Bearer <token>
- refresh token (7 days)  only sent to /auth/refresh to get a new pair

The `type` claim stops a refresh token being used as an access token and vice
versa. The `tid` claim records the tenant, but deps.py still loads the user from
the database on every request, so deactivated users lose access immediately.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

TokenType = Literal["access", "refresh"]

_hasher = PasswordHasher()  # library defaults follow the current OWASP recommendation

# A real hash of a random password. Used to make login take the same time whether
# or not the email exists — otherwise response timing reveals valid emails.
_DUMMY_HASH = _hasher.hash(uuid.uuid4().hex)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def burn_password_check_time(password: str) -> None:
    """Spend the same CPU time as a real check (see _DUMMY_HASH)."""
    verify_password(password, _DUMMY_HASH)


class TokenError(Exception):
    """Token is missing, malformed, expired, badly signed or of the wrong type."""


def create_token(
    *, user_id: uuid.UUID, tenant_id: uuid.UUID, token_type: TokenType
) -> tuple[str, int]:
    """Return (encoded JWT, lifetime in seconds)."""
    settings = get_settings()
    lifetime = (
        timedelta(minutes=settings.access_token_ttl_minutes)
        if token_type == "access"
        else timedelta(days=settings.refresh_token_ttl_days)
    )
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),  # subject = who the token is about
        "tid": str(tenant_id),  # tenant
        "type": token_type,
        "iat": now,  # issued at
        "exp": now + lifetime,  # expiry — PyJWT rejects expired tokens for us
        "jti": uuid.uuid4().hex,  # unique id (enables a revocation list later)
    }
    token = jwt.encode(
        claims, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm
    )
    return token, int(lifetime.total_seconds())


def decode_token(token: str, *, expected_type: TokenType) -> dict:
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            # Pin the algorithm. Accepting whatever the token header says is a
            # classic JWT vulnerability ("alg": "none").
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "tid", "type", "exp"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if claims.get("type") != expected_type:
        raise TokenError(f"expected a {expected_type} token")
    return claims
