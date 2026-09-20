"""
Ingestion jobs: the bridge between "a file was uploaded" and "it's searchable".

    upload ──▶ queue_ingestion() ──▶ ingestion_jobs row (queued)
                                 └─▶ Celery task ──▶ run_ingestion_job()
                                                      ├─ status running
                                                      ├─ pipeline.ingest_document()
                                                      └─ status done / failed

Every attempt is recorded, so "why is my document still pending?" has an
answer: which attempt it's on, when it started, and the error if it failed.
Failed documents can be retried with `POST /documents/{id}/reindex`.

`INGESTION_MODE=inline` runs the same code inside the request instead of
queueing it. That's what the tests use, and it's handy for running NEXA without
a worker.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import Document, DocumentStatus, IngestionJob, IngestionJobStatus
from app.services.ingestion.pipeline import ingest_document

log = get_logger(__name__)


async def queue_ingestion(session: AsyncSession, document: Document) -> IngestionJob:
    """Create a job for this document and start it (via the worker, or inline)."""
    job = IngestionJob(
        id=uuid.uuid4(),
        tenant_id=document.tenant_id,
        document_id=document.id,
        status=IngestionJobStatus.QUEUED,
    )
    session.add(job)
    document.status = DocumentStatus.PENDING
    document.error_message = None
    await session.commit()

    if get_settings().ingestion_mode == "inline":
        await run_ingestion_job(session, job.id)
    else:
        # Import here so the API doesn't need Celery's imports at startup.
        from app.workers.tasks import ingest_document_task

        ingest_document_task.delay(str(job.id), str(document.id), str(document.tenant_id))
        log.info("ingestion.queued", job_id=str(job.id), document_id=str(document.id))
    await session.refresh(job)
    return job


async def run_ingestion_job(session: AsyncSession, job_id: uuid.UUID) -> int:
    """Run one job to completion and record how it went. Never raises."""
    job = await session.get(IngestionJob, job_id)
    if job is None:
        log.warning("ingestion.job_missing", job_id=str(job_id))
        return 0

    job.status = IngestionJobStatus.RUNNING
    job.started_at = datetime.now(UTC)
    job.attempts += 1
    await session.commit()

    chunks = await ingest_document(session, job.document_id)

    document = await session.get(Document, job.document_id)
    job = await session.get(IngestionJob, job_id)
    succeeded = document is not None and document.status == DocumentStatus.READY
    job.status = IngestionJobStatus.DONE if succeeded else IngestionJobStatus.FAILED
    job.chunks_created = chunks
    job.error_message = document.error_message if document else "Document disappeared"
    job.finished_at = datetime.now(UTC)
    await session.commit()
    return chunks


async def latest_job(session: AsyncSession, document_id: uuid.UUID) -> IngestionJob | None:
    from sqlalchemy import select

    return await session.scalar(
        select(IngestionJob)
        .where(IngestionJob.document_id == document_id)
        .order_by(IngestionJob.created_at.desc())
        .limit(1)
    )
