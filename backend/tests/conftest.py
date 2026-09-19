"""
Test configuration shared by all tests.

Settings are read from environment variables when the app is first imported, so
we set the test environment HERE, before any `app.*` import happens:

- a separate database (`nexa_test`), so tests never touch your dev data
- the fake embedding + LLM providers: no API keys, no cost, deterministic results
- a temporary upload directory
"""

import os
import tempfile
from urllib.parse import urlsplit, urlunsplit


def _test_database_url() -> str:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit
    base = os.environ.get("DATABASE_URL", "postgresql+psycopg://nexa:nexa@localhost:5433/nexa")
    parts = urlsplit(base)
    return urlunsplit(parts._replace(path="/nexa_test"))


os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DATABASE_URL": _test_database_url(),
        "EMBEDDING_PROVIDER": "fake",
        "LLM_PROVIDER": "fake",
        "STORAGE_DIR": tempfile.mkdtemp(prefix="nexa-test-uploads-"),
        "JWT_SECRET": "test-secret-that-is-long-enough-for-hs256-signing",
        "LOG_LEVEL": "WARNING",
        # The hashing test embedder gives lower similarity scores than a real
        # model, so the "I don't know" gate is set to match it.
        "MIN_RELEVANCE_SCORE": "0.15",
        # Offline word-overlap reranker; its score = share of the question's words
        # found in the passage, so 0.2 means "at least a fifth of them".
        "RERANKER_PROVIDER": "fake",
        "MIN_RERANK_SCORE": "0.2",
    }
)


# ----------------------------------------------------------------------------
# Imports below this line happen AFTER the environment is configured above.
# ----------------------------------------------------------------------------
import uuid  # noqa: E402
from collections.abc import AsyncIterator  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from pathlib import Path  # noqa: E402

import httpx  # noqa: E402
import psycopg  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]
DB_TESTS = ("integration", "security")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Tag everything under tests/integration and tests/security as `integration`."""
    for item in items:
        if any(part in DB_TESTS for part in Path(str(item.fspath)).parts):
            item.add_marker(pytest.mark.integration)


def _plain_psycopg_url(sqlalchemy_url: str) -> str:
    return sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture(scope="session")
def database() -> str:
    """Create the test database if needed and build a fresh schema with the real migrations."""
    url = os.environ["DATABASE_URL"]
    db_name = urlsplit(url).path.lstrip("/")
    admin_url = urlunsplit(urlsplit(_plain_psycopg_url(url))._replace(path="/postgres"))
    with psycopg.connect(admin_url, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,)).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{db_name}"')
    with psycopg.connect(_plain_psycopg_url(url), autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    # Running the real migrations also tests them.
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(config, "head")
    return url


@pytest.fixture
async def clean_db(database: str) -> AsyncIterator[None]:
    """Each DB test starts from empty tables."""
    from app.db.session import engine

    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE tenants, usage_events RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
async def client(clean_db: None) -> AsyncIterator[httpx.AsyncClient]:
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1") as c:
        yield c


@dataclass
class TenantCtx:
    """A registered tenant plus an authenticated owner — the starting point of most tests."""

    client: httpx.AsyncClient
    slug: str
    tenant_id: uuid.UUID
    owner_id: uuid.UUID
    default_collection_id: uuid.UUID
    headers: dict[str, str]
    refresh_token: str

    async def upload(
        self, filename: str, content: bytes, collection_id: uuid.UUID | None = None, **form
    ) -> dict:
        response = await self.client.post(
            "/documents",
            headers=self.headers,
            data={"collection_id": str(collection_id or self.default_collection_id), **form},
            files=[("files", (filename, content))],
        )
        assert response.status_code == 200, response.text
        return response.json()[0]


@pytest.fixture
def make_tenant(client: httpx.AsyncClient):
    async def _make(name: str = "Harbourview Property Group") -> TenantCtx:
        response = await client.post(
            "/auth/register",
            json={
                "tenant_name": name,
                "email": "owner@example.com",
                "password": "a-strong-password",
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        return TenantCtx(
            client=client,
            slug=body["tenant"]["slug"],
            tenant_id=uuid.UUID(body["tenant"]["id"]),
            owner_id=uuid.UUID(body["user"]["id"]),
            default_collection_id=uuid.UUID(body["default_collection_id"]),
            headers={"Authorization": f"Bearer {body['tokens']['access_token']}"},
            refresh_token=body["tokens"]["refresh_token"],
        )

    return _make


@pytest.fixture
def add_user(client: httpx.AsyncClient):
    """Create a user with a given role directly in the DB (the /users endpoint is Week 4)."""

    async def _add(tenant: TenantCtx, email: str, role: str) -> dict[str, str]:
        from app.core.security import hash_password
        from app.db.models import User
        from app.db.session import SessionLocal

        async with SessionLocal() as session:
            session.add(
                User(
                    tenant_id=tenant.tenant_id,
                    email=email,
                    password_hash=hash_password("a-strong-password"),
                    role=role,
                )
            )
            await session.commit()
        response = await client.post(
            "/auth/login",
            json={"tenant_slug": tenant.slug, "email": email, "password": "a-strong-password"},
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _add
