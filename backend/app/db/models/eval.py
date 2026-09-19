"""
Evaluation runs. Each `make eval` run stores its configuration and metrics, so
you can compare "semantic vs hybrid vs hybrid + reranker" or "before vs after
contextual headers" over time — and show clients measured numbers.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    dataset_name: Mapped[str] = mapped_column(Text)
    config: Mapped[dict] = mapped_column(JSONB)
    metrics: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvalResult(Base):
    __tablename__ = "eval_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("eval_runs.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[str] = mapped_column(Text)
    retrieved_ids: Mapped[list | None] = mapped_column(JSONB)
    answer: Mapped[str | None] = mapped_column(Text)
    scores: Mapped[dict | None] = mapped_column(JSONB)
