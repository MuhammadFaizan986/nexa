"""Week 1 schema: tenants, users, collections, documents, chunks.

Written as plain SQL (rather than generated) so it reads like the schema in
section 6 of the project plan. Constraint names follow the naming convention in
app/db/base.py so Alembic autogenerate stays in sync with the models.

Revision ID: 0001
Revises:
Create Date: 2026-09-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pgvector adds the `vector` column type, distance operators (<=>, <->) and
    # HNSW/IVFFlat indexes. gen_random_uuid() is built into Postgres 13+, so the
    # plan's pgcrypto extension isn't needed.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute("""
        CREATE TABLE tenants (
            id          UUID        NOT NULL DEFAULT gen_random_uuid(),
            name        TEXT        NOT NULL,
            slug        TEXT        NOT NULL,
            settings    JSONB       NOT NULL DEFAULT '{}',   -- model choice, tone, branding
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_tenants PRIMARY KEY (id),
            CONSTRAINT uq_tenants_slug UNIQUE (slug)
        )
    """)

    op.execute("""
        CREATE TABLE users (
            id            UUID        NOT NULL DEFAULT gen_random_uuid(),
            tenant_id     UUID        NOT NULL,
            email         TEXT        NOT NULL,
            password_hash TEXT        NOT NULL,
            role          VARCHAR(16) NOT NULL DEFAULT 'member',
            is_active     BOOLEAN     NOT NULL DEFAULT true,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_users PRIMARY KEY (id),
            CONSTRAINT fk_users_tenant_id_tenants
                FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE CASCADE,
            CONSTRAINT uq_users_tenant_id UNIQUE (tenant_id, email),
            CONSTRAINT ck_users_role CHECK (role IN ('owner', 'admin', 'member', 'viewer'))
        )
    """)
    op.execute("CREATE INDEX ix_users_tenant_id ON users (tenant_id)")

    op.execute("""
        CREATE TABLE collections (
            id          UUID        NOT NULL DEFAULT gen_random_uuid(),
            tenant_id   UUID        NOT NULL,
            name        TEXT        NOT NULL,
            description TEXT,
            visibility  VARCHAR(16) NOT NULL DEFAULT 'restricted',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_collections PRIMARY KEY (id),
            CONSTRAINT fk_collections_tenant_id_tenants
                FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE CASCADE,
            CONSTRAINT uq_collections_tenant_id UNIQUE (tenant_id, name),
            CONSTRAINT ck_collections_visibility CHECK (visibility IN ('tenant_wide', 'restricted'))
        )
    """)
    op.execute("CREATE INDEX ix_collections_tenant_id ON collections (tenant_id)")

    op.execute("""
        CREATE TABLE documents (
            id            UUID        NOT NULL DEFAULT gen_random_uuid(),
            tenant_id     UUID        NOT NULL,
            collection_id UUID        NOT NULL,
            title         TEXT        NOT NULL,
            filename      TEXT        NOT NULL,
            mime_type     TEXT        NOT NULL,
            storage_path  TEXT        NOT NULL,
            file_hash     TEXT        NOT NULL,          -- SHA-256, for deduplication
            page_count    INTEGER,
            status        VARCHAR(16) NOT NULL DEFAULT 'pending',
            error_message TEXT,
            metadata      JSONB       NOT NULL DEFAULT '{}',  -- doc_type, property_id, date...
            uploaded_by   UUID,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_documents PRIMARY KEY (id),
            CONSTRAINT fk_documents_tenant_id_tenants
                FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE CASCADE,
            CONSTRAINT fk_documents_collection_id_collections
                FOREIGN KEY (collection_id) REFERENCES collections (id) ON DELETE CASCADE,
            CONSTRAINT fk_documents_uploaded_by_users
                FOREIGN KEY (uploaded_by) REFERENCES users (id),
            CONSTRAINT uq_documents_tenant_id UNIQUE (tenant_id, file_hash),
            CONSTRAINT ck_documents_status
                CHECK (status IN ('pending', 'processing', 'ready', 'failed'))
        )
    """)
    op.execute("CREATE INDEX ix_documents_tenant_id ON documents (tenant_id)")
    op.execute("CREATE INDEX ix_documents_collection_id ON documents (collection_id)")

    op.execute("""
        CREATE TABLE chunks (
            id            UUID    NOT NULL DEFAULT gen_random_uuid(),
            tenant_id     UUID    NOT NULL,   -- denormalised for fast permission filtering
            collection_id UUID    NOT NULL,   -- denormalised for fast permission filtering
            document_id   UUID    NOT NULL,
            chunk_index   INTEGER NOT NULL,
            content       TEXT    NOT NULL,
            token_count   INTEGER NOT NULL,
            page_start    INTEGER,
            page_end      INTEGER,
            section_title TEXT,
            metadata      JSONB   NOT NULL DEFAULT '{}',
            embedding     vector(1536) NOT NULL,
            tsv           tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
            CONSTRAINT pk_chunks PRIMARY KEY (id),
            CONSTRAINT fk_chunks_document_id_documents
                FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE CASCADE
        )
    """)
    op.execute("CREATE INDEX ix_chunks_document_id ON chunks (document_id)")
    op.execute(
        "CREATE INDEX chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute("CREATE INDEX chunks_tsv_gin ON chunks USING gin (tsv)")
    op.execute("CREATE INDEX chunks_tenant_coll ON chunks (tenant_id, collection_id)")
    op.execute("CREATE INDEX chunks_metadata_gin ON chunks USING gin (metadata)")


def downgrade() -> None:
    for table in ("chunks", "documents", "collections", "users", "tenants"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
