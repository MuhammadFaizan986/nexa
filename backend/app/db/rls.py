"""
Row-Level Security: the database's own tenant guard.

Every query NEXA writes already filters by `tenant_id`. RLS is the safety net
for the day someone forgets one: Postgres refuses to return another tenant's
rows at all (migration 0004 enables it on `documents` and `chunks`).

Postgres decides using a session variable, `app.tenant_id`. This module sets it
at the start of EVERY database transaction, from a context variable that the
authentication dependency fills in:

    request  ──▶ get_current_user() ──▶ use_tenant(user.tenant_id)
             ──▶ any query ──▶ transaction begins ──▶ SET app.tenant_id
             ──▶ Postgres only shows that tenant's rows

Why a context variable and not a plain argument? Because connections come from a
pool shared by all requests: the value must belong to the *current task*, and it
must be re-applied on every transaction (a COMMIT resets transaction-local
settings).

Admin tools (re-index, the eval harness, backups) legitimately work across
tenants. They call `maintenance_mode()`, which sets `app.maintenance = on` and
is the ONLY way to see past the policy.

One subtlety that makes or breaks all of this: **PostgreSQL superusers ignore
RLS**, and the role the app connects as owns the tables. So for tenant-scoped
work we also switch into a plain, unprivileged role for the duration of the
transaction (`SET LOCAL ROLE nexa_app`, created in migration 0005). Policies
apply to that role, and the switch is undone automatically when the transaction
ends. Migrations and admin tools stay on the owner role.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.config import get_settings

_SET_CONFIG = text(
    "SELECT set_config('app.tenant_id', :tenant_id, true),"
    "       set_config('app.maintenance', :maintenance, true)"
)

_current_tenant: ContextVar[uuid.UUID | None] = ContextVar("nexa_current_tenant", default=None)
_maintenance: ContextVar[bool] = ContextVar("nexa_maintenance", default=False)


def use_tenant(tenant_id: uuid.UUID | None) -> None:
    """All queries from here on (in this task) see only this tenant's rows."""
    _current_tenant.set(tenant_id)


def current_tenant() -> uuid.UUID | None:
    return _current_tenant.get()


@contextmanager
def tenant_scope(tenant_id: uuid.UUID | None) -> Iterator[None]:
    token = _current_tenant.set(tenant_id)
    try:
        yield
    finally:
        _current_tenant.reset(token)


@contextmanager
def maintenance_mode() -> Iterator[None]:
    """
    Bypass tenant isolation for admin tools that legitimately span tenants
    (scripts/reindex.py, the eval harness, backups). Never use it in a request.
    """
    token = _maintenance.set(True)
    try:
        yield
    finally:
        _maintenance.reset(token)


async def apply_to_session(session: AsyncSession) -> None:
    """
    Apply the current tenant to a transaction that is ALREADY open.

    The listener below only fires when a transaction *begins*, and a request's
    first query (loading the user) happens before we know who the user is. So
    authentication calls this straight after `use_tenant()` to bring the open
    transaction up to date; later transactions are handled by the listener.
    """
    tenant_id = _current_tenant.get()
    maintenance = _maintenance.get()
    await session.execute(
        _SET_CONFIG,
        {
            "tenant_id": str(tenant_id) if tenant_id else "",
            "maintenance": "on" if maintenance else "off",
        },
    )
    app_role = get_settings().db_app_role
    if app_role and not maintenance:
        await session.execute(text(f"SET LOCAL ROLE {app_role}"))


@event.listens_for(Session, "after_begin")
def _apply_rls_settings(session: Session, transaction, connection) -> None:
    """
    Runs when a transaction starts (including the new one after each COMMIT).
    `set_config(..., true)` makes the setting transaction-local, so it can never
    leak to the next request that borrows this pooled connection.
    """
    tenant_id = _current_tenant.get()
    maintenance = _maintenance.get()
    connection.exec_driver_sql(
        "SELECT set_config('app.tenant_id', %s, true), set_config('app.maintenance', %s, true)",
        (str(tenant_id) if tenant_id else "", "on" if maintenance else "off"),
    )
    app_role = get_settings().db_app_role
    if app_role and not maintenance:
        # Drop superuser/owner privileges for this transaction so the policies
        # actually apply. SET LOCAL is undone at COMMIT or ROLLBACK.
        connection.exec_driver_sql(f"SET LOCAL ROLE {app_role}")
