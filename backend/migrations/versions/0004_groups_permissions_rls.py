"""Week 4 schema: groups, per-collection access, background jobs, Row-Level Security.

Permissions so far were role-based only: admins saw restricted collections,
everyone else saw the tenant-wide ones. Now access is granted to GROUPS
("Compliance Team", "Management"), which is how real organisations think, and
users belong to groups.

Row-Level Security (RLS) is defence in depth (plan section 6). Even if a query
somewhere forgets `WHERE tenant_id = ...`, Postgres itself returns no rows from
another tenant. The app sets `app.tenant_id` at the start of every database
transaction (see app/db/rls.py); admin tools (migrations, re-index, the eval
harness) set `app.maintenance = on` instead, which is the only way to see
across tenants.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RLS_TABLES = ("documents", "chunks")


def upgrade() -> None:
    op.execute("""
        CREATE TABLE groups (
            id          UUID        NOT NULL DEFAULT gen_random_uuid(),
            tenant_id   UUID        NOT NULL,
            name        TEXT        NOT NULL,
            description TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_groups PRIMARY KEY (id),
            CONSTRAINT fk_groups_tenant_id_tenants
                FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE CASCADE,
            CONSTRAINT uq_groups_tenant_id UNIQUE (tenant_id, name)
        )
    """)
    op.execute("CREATE INDEX ix_groups_tenant_id ON groups (tenant_id)")

    op.execute("""
        CREATE TABLE group_members (
            group_id UUID NOT NULL,
            user_id  UUID NOT NULL,
            CONSTRAINT pk_group_members PRIMARY KEY (group_id, user_id),
            CONSTRAINT fk_group_members_group_id_groups
                FOREIGN KEY (group_id) REFERENCES groups (id) ON DELETE CASCADE,
            CONSTRAINT fk_group_members_user_id_users
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """)
    op.execute("CREATE INDEX ix_group_members_user_id ON group_members (user_id)")

    op.execute("""
        CREATE TABLE collection_access (
            collection_id UUID        NOT NULL,
            group_id      UUID        NOT NULL,
            permission    VARCHAR(8)  NOT NULL DEFAULT 'read',
            CONSTRAINT pk_collection_access PRIMARY KEY (collection_id, group_id),
            CONSTRAINT fk_collection_access_collection_id_collections
                FOREIGN KEY (collection_id) REFERENCES collections (id) ON DELETE CASCADE,
            CONSTRAINT fk_collection_access_group_id_groups
                FOREIGN KEY (group_id) REFERENCES groups (id) ON DELETE CASCADE,
            CONSTRAINT ck_collection_access_permission CHECK (permission IN ('read', 'write'))
        )
    """)

    # One row per ingestion attempt: what the worker is doing with a document.
    op.execute("""
        CREATE TABLE ingestion_jobs (
            id             UUID        NOT NULL DEFAULT gen_random_uuid(),
            tenant_id      UUID        NOT NULL,
            document_id    UUID        NOT NULL,
            status         VARCHAR(16) NOT NULL DEFAULT 'queued',
            attempts       INTEGER     NOT NULL DEFAULT 0,
            chunks_created INTEGER,
            error_message  TEXT,
            started_at     TIMESTAMPTZ,
            finished_at    TIMESTAMPTZ,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_ingestion_jobs PRIMARY KEY (id),
            CONSTRAINT fk_ingestion_jobs_document_id_documents
                FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE CASCADE,
            CONSTRAINT ck_ingestion_jobs_status
                CHECK (status IN ('queued', 'running', 'done', 'failed'))
        )
    """)
    op.execute("CREATE INDEX ix_ingestion_jobs_document_id ON ingestion_jobs (document_id)")
    op.execute("CREATE INDEX ix_ingestion_jobs_tenant_id ON ingestion_jobs (tenant_id)")

    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # The application connects as the table owner, and owners are exempt from
        # RLS unless we FORCE it.
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        # Normal requests: only rows of the tenant set by app.tenant_id. An unset
        # variable yields NULL, which matches nothing — RLS fails closed.
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        """)
        # Admin tools (re-index, eval, backups) opt out explicitly and visibly.
        op.execute(f"""
            CREATE POLICY maintenance ON {table}
                USING (current_setting('app.maintenance', true) = 'on')
                WITH CHECK (current_setting('app.maintenance', true) = 'on')
        """)


def downgrade() -> None:
    for table in RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS maintenance ON {table}")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    for table in ("ingestion_jobs", "collection_access", "group_members", "groups"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
