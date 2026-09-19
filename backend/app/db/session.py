"""
Database engine + session factory.

- The *engine* owns a pool of connections to Postgres (created once per process).
- A *session* is a unit of work: you load/modify objects, then commit or roll
  back. FastAPI gives each request its own session via `get_session()`.

Everything here is async (`AsyncSession`) so a request waiting on Postgres
doesn't block the server from handling other requests — important because our
chat endpoint holds a request open while the LLM streams.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

engine = create_async_engine(
    get_settings().database_url,
    # Check a pooled connection is still alive before using it (Postgres restarts,
    # idle timeouts). Costs a tiny round-trip, saves confusing errors.
    pool_pre_ping=True,
)

# expire_on_commit=False: after commit, objects keep their loaded attribute values.
# In async code a post-commit attribute access would otherwise trigger an implicit
# (and forbidden) lazy database load.
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, always closed afterwards."""
    async with SessionLocal() as session:
        yield session
