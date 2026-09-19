"""
SQLAlchemy declarative base shared by all models.

The naming convention gives every index/constraint a predictable name
(e.g. `fk_documents_tenant_id_tenants`). Without it Postgres invents names, and
future Alembic migrations that drop or alter a constraint can't refer to it
reliably.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
