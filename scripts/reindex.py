"""
Re-run ingestion (parse -> clean -> chunk -> embed) for documents already uploaded.

Use it after changing anything that shapes the chunks: parsers, the cleaner, the
chunker, or the embedding provider/model. Original files are kept in storage, so
nothing needs re-uploading.

    docker compose exec api python /scripts/reindex.py                   # everything
    docker compose exec api python /scripts/reindex.py --tenant kestrel-pay
    docker compose exec api python /scripts/reindex.py --status failed   # retry failures

Every chunk is embedded again, which costs API calls. On Gemini's free tier that's
free but rate-limited (~100 chunks/minute), so the sample data takes a few minutes.
Past answers keep their citations (page + snippet) even though chunk ids change.
"""

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.core.logging import configure_logging
from app.db.models import Document, DocumentStatus, Tenant
from app.db.session import SessionLocal, engine
from app.services.ingestion.pipeline import ingest_document


async def reindex(tenant_slug: str | None, status: str | None) -> int:
    configure_logging()
    query = (
        select(Document.id, Document.filename, Tenant.slug)
        .join(Tenant, Tenant.id == Document.tenant_id)
        .order_by(Tenant.slug, Document.filename)
    )
    if tenant_slug:
        query = query.where(Tenant.slug == tenant_slug)
    if status:
        query = query.where(Document.status == status)
    async with SessionLocal() as session:
        rows = (await session.execute(query)).all()

    print(f"Re-indexing {len(rows)} document(s)...")
    failures = 0
    for document_id, filename, slug in rows:
        # A fresh session per document: one failure can't affect the others.
        async with SessionLocal() as session:
            chunks = await ingest_document(session, document_id)
            document = await session.get(Document, document_id)
        if document.status == DocumentStatus.READY:
            print(f"  ready   {slug}/{filename}  ({chunks} chunks)")
        else:
            failures += 1
            print(f"  FAILED  {slug}/{filename}\n          {document.error_message}")
    await engine.dispose()
    print(f"Done: {len(rows) - failures} ready, {failures} failed.")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-run ingestion for existing documents.")
    parser.add_argument("--tenant", help="only this tenant slug")
    parser.add_argument("--status", choices=[s.value for s in DocumentStatus])
    args = parser.parse_args()
    return asyncio.run(reindex(args.tenant, args.status))


if __name__ == "__main__":
    sys.exit(main())
