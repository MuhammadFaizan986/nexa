"""Week 3 schema: contextual chunk headers in keyword search, evaluation runs.

1. chunks.context_header — "Document: <title> | Section: <headings>". It is
   embedded together with the chunk text, and it is part of the full-text index,
   so keyword search for "Unit 4B" finds the Termination chunk of the 4B lease
   even though that chunk's own text never says "4B".
2. chunks.tsv is re-created to include the header. setweight() labels header
   words 'B' and content words 'A', so ts_rank_cd() scores a match in the text
   itself higher than a match that only comes from the title.
3. eval_runs / eval_results — every `make eval` run is stored (plan section 11).

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TSV_WITH_HEADER = """
    setweight(to_tsvector('english', coalesce(context_header, '')), 'B')
    || setweight(to_tsvector('english', content), 'A')
"""


def upgrade() -> None:
    op.execute("ALTER TABLE chunks ADD COLUMN context_header TEXT")
    # A generated column's expression can't be altered in place: drop + re-add.
    # Dropping the column also drops its GIN index, so we re-create that too.
    op.execute("ALTER TABLE chunks DROP COLUMN tsv")
    op.execute(
        f"ALTER TABLE chunks ADD COLUMN tsv tsvector GENERATED ALWAYS AS ({TSV_WITH_HEADER}) STORED"
    )
    op.execute("CREATE INDEX chunks_tsv_gin ON chunks USING gin (tsv)")

    op.execute("""
        CREATE TABLE eval_runs (
            id           UUID        NOT NULL DEFAULT gen_random_uuid(),
            dataset_name TEXT        NOT NULL,
            config       JSONB       NOT NULL,   -- retrieval mode, reranker, models, chunking...
            metrics      JSONB,                  -- aggregated results (Hit@k, MRR, latency...)
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_eval_runs PRIMARY KEY (id)
        )
    """)
    op.execute("""
        CREATE TABLE eval_results (
            id            BIGSERIAL NOT NULL,
            run_id        UUID      NOT NULL,
            question_id   TEXT      NOT NULL,
            retrieved_ids JSONB,               -- ranked chunk ids + where they came from
            answer        TEXT,                -- filled by the generation eval (Week 6)
            scores        JSONB,               -- hit@1/3/5, reciprocal rank, top score...
            CONSTRAINT pk_eval_results PRIMARY KEY (id),
            CONSTRAINT fk_eval_results_run_id_eval_runs
                FOREIGN KEY (run_id) REFERENCES eval_runs (id) ON DELETE CASCADE
        )
    """)
    op.execute("CREATE INDEX ix_eval_results_run_id ON eval_results (run_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS eval_results")
    op.execute("DROP TABLE IF EXISTS eval_runs")
    op.execute("ALTER TABLE chunks DROP COLUMN tsv")
    op.execute(
        "ALTER TABLE chunks ADD COLUMN tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', content)) STORED"
    )
    op.execute("CREATE INDEX chunks_tsv_gin ON chunks USING gin (tsv)")
    op.execute("ALTER TABLE chunks DROP COLUMN context_header")
