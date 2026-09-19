"""
Alembic environment: how migrations connect to the database.

We run migrations with a normal *synchronous* engine. The psycopg 3 driver
supports both sync and async, so the same DATABASE_URL works for the app (async)
and for Alembic (sync).
"""

from logging.config import fileConfig

from alembic import context
from pgvector.sqlalchemy import Vector
from sqlalchemy import create_engine, pool

from app.core.config import get_settings
from app.db import models  # noqa: F401  (registers all tables on Base.metadata)
from app.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)  # use the [loggers] section of alembic.ini
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: print the SQL instead of running it."""
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(get_settings().database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        # Teach SQLAlchemy's schema reflection about pgvector's `vector` type, so
        # `alembic check` / autogenerate can compare the chunks.embedding column.
        connection.dialect.ischema_names["vector"] = Vector
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
