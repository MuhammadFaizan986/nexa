"""Week 4: a non-superuser role so Row-Level Security actually applies.

Migration 0004 enabled RLS, but it had no effect: the application connects as
`nexa`, the database owner and a SUPERUSER, and **superusers bypass RLS
entirely** (FORCE ROW LEVEL SECURITY only covers table owners). A test that
deliberately "forgot" the tenant filter proved it — rows from another tenant
came back.

The fix is the standard one: a second role with no login of its own that the
application switches into for the duration of each transaction
(`SET LOCAL ROLE nexa_app`, see app/db/rls.py). That role is neither superuser
nor table owner, so the policies apply to it. Migrations and admin tools keep
running as `nexa` and are unaffected.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "nexa_app"


def upgrade() -> None:
    connection = op.get_bind()
    owner = connection.exec_driver_sql("SELECT current_user").scalar()

    # NOLOGIN: nothing can connect AS this role; the app only switches into it.
    op.execute(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} NOLOGIN;
            END IF;
        END
        $$
    """)
    # The login role must be a member of it to be allowed to SET ROLE.
    op.execute(f"GRANT {APP_ROLE} TO {owner}")

    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
    # ...and on whatever later migrations create.
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
        f"GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE}"
    )


def downgrade() -> None:
    connection = op.get_bind()
    owner = connection.exec_driver_sql("SELECT current_user").scalar()
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {APP_ROLE}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
        f"REVOKE USAGE, SELECT ON SEQUENCES FROM {APP_ROLE}"
    )
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {APP_ROLE}")
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {APP_ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {APP_ROLE}")
    # The role itself is left in place: other databases may still use it.
