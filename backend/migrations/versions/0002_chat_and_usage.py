"""Week 2 schema: conversations, messages, message_citations, usage_events.

Two small additions to the plan's schema:
- messages.metadata  — debug info per answer (refused?, stop reason, retrieved
  chunk ids + scores, invalid citation numbers).
- usage_events.model — which model the tokens were spent on, so cost can be
  recomputed if prices change.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE conversations (
            id          UUID        NOT NULL DEFAULT gen_random_uuid(),
            tenant_id   UUID        NOT NULL,
            user_id     UUID        NOT NULL,
            title       TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_conversations PRIMARY KEY (id),
            CONSTRAINT fk_conversations_user_id_users
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """)
    op.execute("CREATE INDEX ix_conversations_tenant_id ON conversations (tenant_id)")
    op.execute("CREATE INDEX ix_conversations_user_id ON conversations (user_id)")

    op.execute("""
        CREATE TABLE messages (
            id              UUID        NOT NULL DEFAULT gen_random_uuid(),
            conversation_id UUID        NOT NULL,
            role            VARCHAR(16) NOT NULL,
            content         TEXT        NOT NULL,
            rewritten_query TEXT,
            model           TEXT,
            input_tokens    INTEGER,
            output_tokens   INTEGER,
            latency_ms      JSONB,       -- {"retrieval": 25, "llm_first_token": 900, ...}
            feedback        SMALLINT,    -- -1 / 0 / 1 (thumbs)
            metadata        JSONB       NOT NULL DEFAULT '{}',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_messages PRIMARY KEY (id),
            CONSTRAINT fk_messages_conversation_id_conversations
                FOREIGN KEY (conversation_id) REFERENCES conversations (id) ON DELETE CASCADE,
            CONSTRAINT ck_messages_role CHECK (role IN ('user', 'assistant')),
            CONSTRAINT ck_messages_feedback CHECK (feedback IN (-1, 0, 1))
        )
    """)
    op.execute("CREATE INDEX ix_messages_conversation_id ON messages (conversation_id)")

    op.execute("""
        CREATE TABLE message_citations (
            message_id      UUID    NOT NULL,
            citation_number INTEGER NOT NULL,
            chunk_id        UUID,
            document_id     UUID,
            page_start      INTEGER,
            snippet         TEXT,
            CONSTRAINT pk_message_citations PRIMARY KEY (message_id, citation_number),
            CONSTRAINT fk_message_citations_message_id_messages
                FOREIGN KEY (message_id) REFERENCES messages (id) ON DELETE CASCADE,
            CONSTRAINT fk_message_citations_chunk_id_chunks
                FOREIGN KEY (chunk_id) REFERENCES chunks (id) ON DELETE SET NULL,
            CONSTRAINT fk_message_citations_document_id_documents
                FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE SET NULL
        )
    """)

    op.execute("""
        CREATE TABLE usage_events (
            id          BIGSERIAL   NOT NULL,
            tenant_id   UUID        NOT NULL,
            user_id     UUID,
            event_type  VARCHAR(16) NOT NULL,   -- embed | chat | rerank | ingest
            model       TEXT,
            tokens      INTEGER,
            cost_usd    NUMERIC(10, 6),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_usage_events PRIMARY KEY (id)
        )
    """)
    op.execute("CREATE INDEX ix_usage_events_tenant_id ON usage_events (tenant_id)")
    op.execute("CREATE INDEX ix_usage_events_created_at ON usage_events (created_at)")


def downgrade() -> None:
    for table in ("usage_events", "message_citations", "messages", "conversations"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
