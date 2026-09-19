"""
The ingestion pipeline: stored file -> searchable chunks.

    parse  ->  clean  ->  chunk  ->  add context headers  ->  embed (batched)
           ->  store chunks  ->  status=ready

In Weeks 1–3 this runs synchronously inside the upload request. In Week 4 the
exact same function is called from a Celery worker instead, so uploads return
immediately — which is why it takes a document id and loads everything itself.

Failure handling: any error marks the document `failed` with a readable
`error_message` (and leaves no half-written chunks behind, because chunks are
only committed together with the `ready` status in one transaction).
"""

import asyncio
import time
import uuid

from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import Chunk, Document, DocumentStatus, UsageEventType
from app.services.embeddings import get_embedding_provider
from app.services.ingestion.chunker import chunk_blocks
from app.services.ingestion.cleaner import clean_document
from app.services.ingestion.parsers import ParsedDocument, parse_file
from app.services.storage import get_storage
from app.services.usage import record_usage

log = get_logger(__name__)


class IngestionError(Exception):
    pass


def build_context_header(title: str, heading_path: list[str]) -> str:
    """
    "Document: Residential Tenancy Agreement - Unit 4B | Section: 7. Term and Termination"

    Contextual chunk headers (plan 9.1): a chunk like "Clause 7.2 ... 60 days
    written notice" never mentions WHICH lease it belongs to, so on its own it
    matches a question about Unit 7A just as well as one about Unit 4B. Putting
    the document title and heading trail in front of what we embed and
    keyword-index fixes that. The chunk's `content` stays clean for citations.
    """
    path = [h for h in heading_path if h != title]  # the title is often the first heading
    header = f"Document: {title}"
    if path:
        header += " | Section: " + " > ".join(path)
    return header


async def ingest_document(session: AsyncSession, document_id: uuid.UUID) -> int:
    """Process one document. Returns the number of chunks created (0 on failure)."""
    settings = get_settings()
    started = time.perf_counter()

    document = await session.get(Document, document_id)
    if document is None:
        raise IngestionError(f"Document {document_id} not found")
    document.status = DocumentStatus.PROCESSING
    document.error_message = None
    await session.commit()

    try:
        # 1-2. Parse + clean. PyMuPDF/python-docx are CPU-bound, synchronous
        # libraries; running them in a worker thread keeps the async event loop
        # free to serve other requests meanwhile.
        path = get_storage().path(document.storage_path)
        parsed: ParsedDocument = await asyncio.to_thread(parse_file, path, document.filename)
        cleaned = clean_document(parsed)

        # 3. Chunk.
        chunks = chunk_blocks(
            cleaned.blocks,
            chunk_size=settings.chunk_size_tokens,
            overlap=settings.chunk_overlap_tokens,
            min_chunk=settings.chunk_min_tokens,
        )
        if not chunks:
            raise IngestionError("No text could be extracted from this document")

        # 4. Embed all chunks (the provider batches the API calls). With contextual
        #    headers on, the model sees "Document: ... | Section: ..." + the text.
        title = (cleaned.title or document.title)[:300]
        headers = [
            build_context_header(title, chunk.heading_path) if settings.contextual_headers else None
            for chunk in chunks
        ]
        embedder = get_embedding_provider()
        embedded = await embedder.embed(
            [
                f"{header}\n\n{chunk.content}" if header else chunk.content
                for header, chunk in zip(headers, chunks, strict=True)
            ]
        )

        # 5. Store. Deleting first makes the function safe to re-run (re-index).
        await session.execute(delete(Chunk).where(Chunk.document_id == document.id))
        await session.execute(
            insert(Chunk),
            [
                {
                    "tenant_id": document.tenant_id,
                    "collection_id": document.collection_id,
                    "document_id": document.id,
                    "chunk_index": chunk.index,
                    "content": chunk.content,
                    "token_count": chunk.token_count,
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "section_title": chunk.section_title,
                    "context_header": header,
                    "meta": {
                        "heading_path": chunk.heading_path,
                        "sections": chunk.sections,
                        # Where each page/section starts inside `content`, so a
                        # citation can point at the exact page of the quoted sentence.
                        "spans": chunk.spans,
                        # Which model made the vector: if you switch models later,
                        # old and new vectors are NOT comparable -> re-index.
                        "embedding_model": embedder.model,
                        # Document-level metadata (doc_type, property_id, ...) copied
                        # onto each chunk so Week 3 filters can use the GIN index.
                        "doc": document.meta,
                    },
                    "embedding": vector,
                }
                for chunk, header, vector in zip(chunks, headers, embedded.vectors, strict=True)
            ],
        )

        document.status = DocumentStatus.READY
        document.page_count = cleaned.page_count
        document.title = title
        record_usage(
            session,
            tenant_id=document.tenant_id,
            user_id=document.uploaded_by,
            event_type=UsageEventType.INGEST,
            model=embedder.model,
            input_tokens=embedded.tokens,
        )
        await session.commit()
    except Exception as exc:
        await session.rollback()
        log.warning(
            "document.ingest_failed",
            document_id=str(document_id),
            error_type=type(exc).__name__,
        )
        failed = await session.get(Document, document_id)
        if failed is not None:
            failed.status = DocumentStatus.FAILED
            failed.error_message = f"{type(exc).__name__}: {exc}"[:1000]
            await session.commit()
        return 0

    log.info(
        "document.ingested",
        document_id=str(document_id),
        chunks=len(chunks),
        pages=cleaned.page_count,
        embedding_tokens=embedded.tokens,
        duration_ms=round((time.perf_counter() - started) * 1000),
    )
    return len(chunks)
