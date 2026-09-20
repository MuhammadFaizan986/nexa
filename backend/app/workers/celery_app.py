"""
Celery: the background worker that processes uploads.

Until Week 4, parsing, chunking and embedding happened inside the upload
request. Uploading 100 files meant a request that took minutes and could time
out. Now the API only stores the file and puts a job on a queue (Redis); a
separate worker process does the slow work and updates the document's status.

    docker compose up          -> starts `api` and `worker`
    docker compose logs worker -> watch jobs being processed

Run a worker by hand with:

    celery -A app.workers.celery_app worker --loglevel=info
"""

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "nexa",
    broker=settings.redis_url,  # where jobs are queued
    backend=settings.redis_url,  # where results/status are stored
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_acks_late=True,  # a job is only removed from the queue once it finished
    worker_prefetch_multiplier=1,  # don't hoard jobs: ingestion tasks are long
    task_track_started=True,
    task_time_limit=30 * 60,  # a very large document still has to finish eventually
    task_soft_time_limit=25 * 60,
    timezone="UTC",
)
