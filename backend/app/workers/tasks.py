"""
The background tasks themselves.

Two things are worth understanding here:

1. **Celery is synchronous, NEXA is async.** Each worker process keeps ONE
   event loop alive and runs every task on it (`_run`). Creating a new loop per
   task would break the pooled database connections and the HTTP clients of the
   embedding provider, which are bound to the loop that created them.

2. **A worker has no HTTP request, so nothing sets the tenant.** The task
   receives the tenant id and enters `tenant_scope()` itself, otherwise
   Row-Level Security would (correctly) hide the document it is meant to
   process. See app/db/rls.py.
"""

import asyncio
import uuid
from collections.abc import Coroutine
from typing import Any

from app.core.logging import configure_logging, get_logger
from app.db.rls import tenant_scope
from app.db.session import SessionLocal
from app.workers.celery_app import celery_app

log = get_logger(__name__)
_loop: asyncio.AbstractEventLoop | None = None


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run a coroutine on this worker process's long-lived event loop."""
    global _loop
    if _loop is None or _loop.is_closed():
        configure_logging()
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop.run_until_complete(coro)


@celery_app.task(name="nexa.ingest_document")
def ingest_document_task(job_id: str, document_id: str, tenant_id: str) -> int:
    """Parse, chunk and embed one document. Returns the number of chunks created."""
    from app.services.ingestion.jobs import run_ingestion_job

    async def work() -> int:
        with tenant_scope(uuid.UUID(tenant_id)):
            async with SessionLocal() as session:
                return await run_ingestion_job(session, uuid.UUID(job_id))

    log.info("worker.job_received", job_id=job_id, document_id=document_id)
    return _run(work())
