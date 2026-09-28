import uuid

import jwt
import pytest

from app.core.security import (
    TokenError,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_password_hash_roundtrip():
    hashed = hash_password("correct horse battery")
    assert hashed.startswith("$argon2id$")
    assert verify_password("correct horse battery", hashed)
    assert not verify_password("wrong password", hashed)
    assert not verify_password("anything", "not-a-hash")


def test_access_token_roundtrip_and_type_check():
    user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    token, lifetime = create_token(user_id=user_id, tenant_id=tenant_id, token_type="access")
    claims = decode_token(token, expected_type="access")
    assert claims["sub"] == str(user_id) and claims["tid"] == str(tenant_id)
    assert lifetime == 15 * 60
    with pytest.raises(TokenError):
        decode_token(token, expected_type="refresh")


def test_tampered_or_unsigned_tokens_are_rejected():
    token, _ = create_token(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), token_type="access")
    with pytest.raises(TokenError):
        decode_token(token[:-2] + "xx", expected_type="access")
    claims = jwt.decode(token, options={"verify_signature": False})
    unsigned = jwt.encode(claims, key=None, algorithm="none")
    with pytest.raises(TokenError):
        decode_token(unsigned, expected_type="access")


def test_platform_database_urls_get_the_psycopg_driver():
    """
    Railway, Render and Heroku hand out `postgres://...`. SQLAlchemy would then
    look for psycopg2, which isn't installed, and the app would die at startup
    with a confusing error — so the scheme is rewritten on the way in.
    """
    from app.core.config import Settings

    plain = Settings(database_url="postgres://u:p@host:5432/db")
    longer = Settings(database_url="postgresql://u:p@host:5432/db")
    already = Settings(database_url="postgresql+psycopg://u:p@host:5432/db")

    assert plain.database_url == "postgresql+psycopg://u:p@host:5432/db"
    assert longer.database_url == "postgresql+psycopg://u:p@host:5432/db"
    assert already.database_url == "postgresql+psycopg://u:p@host:5432/db"
