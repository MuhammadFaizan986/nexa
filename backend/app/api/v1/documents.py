"""
Document upload and listing.

Upload flow for each file (plan section 5.2):
  1. validate the extension and size, and check the bytes really are that type
  2. stream the file to a temp file while computing its SHA-256 hash
  3. duplicate in this tenant?  -> return the existing document, don't re-process
     (unless its earlier processing FAILED: then processing is retried, so e.g.
     fixing a missing API key and re-uploading just works)
  4. move the file into storage + create the `documents` row (status=pending)
  5. ingest it (parse -> clean -> chunk -> embed -> store)
     Weeks 1–3: synchronously, inside this request.
     Week 4:    enqueued to a Celery worker; the request returns immediately.

Each file gets its own result (created / duplicate / retried / rejected), so
one bad file in a batch doesn't fail the others.
"""

import hashlib
import json
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.deps import CurrentUser, SessionDep
from app.core.logging import get_logger
from app.db.models import Chunk, Collection, Document, DocumentStatus, User
from app.schemas.documents import DocumentDetail, DocumentOut, DocumentUpdate, UploadResult
from app.services.ingestion.parsers import MIME_TYPES, SUPPORTED_EXTENSIONS
from app.services.ingestion.pipeline import ingest_document
from app.services.retrieval.permissions import can_write_collection, readable_collection_ids
from app.services.storage import get_storage

router = APIRouter(prefix="/documents", tags=["documents"])
log = get_logger(__name__)

_READ_CHUNK = 1024 * 1024  # stream uploads 1 MiB at a time: memory use stays flat
# First bytes ("magic numbers") that identify a real file of each type.
_MAGIC = {".pdf": b"%PDF-", ".docx": b"PK\x03\x04"}


class _FileTooLarge(Exception):
    pass


@router.post("", response_model=list[UploadResult])
async def upload_documents(
    user: CurrentUser,
    session: SessionDep,
    files: Annotated[list[UploadFile], File(description="One or more files")],
    collection_id: Annotated[uuid.UUID, Form()],
    metadata: Annotated[
        str | None, Form(description='Optional JSON object, e.g. {"doc_type": "lease"}')
    ] = None,
) -> list[UploadResult]:
    collection = await session.get(Collection, collection_id)
    readable = await readable_collection_ids(session, user)
    if collection is None or collection.id not in readable:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Collection not found")
    if not can_write_collection(user, collection):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't upload to this collection")
    doc_meta = _parse_metadata(metadata)

    return [await _upload_one(session, user, collection, f, doc_meta) for f in files]


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    user: CurrentUser,
    session: SessionDep,
    collection_id: uuid.UUID | None = None,
    status_filter: Annotated[DocumentStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Document]:
    query = (
        select(Document)
        .where(
            Document.tenant_id == user.tenant_id,
            Document.collection_id.in_(await readable_collection_ids(session, user)),
        )
        .order_by(Document.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if collection_id:
        query = query.where(Document.collection_id == collection_id)
    if status_filter:
        query = query.where(Document.status == status_filter)
    return list(await session.scalars(query))


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(
    document_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> DocumentDetail:
    document = await _get_readable_document(session, user, document_id)
    chunk_count = await session.scalar(
        select(func.count()).select_from(Chunk).where(Chunk.document_id == document.id)
    )
    return DocumentDetail(
        **DocumentOut.model_validate(document).model_dump(), chunk_count=chunk_count or 0
    )


@router.patch("/{document_id}", response_model=DocumentOut)
async def update_document(
    document_id: uuid.UUID, body: DocumentUpdate, user: CurrentUser, session: SessionDep
) -> Document:
    """
    Replace a document's metadata. Every chunk keeps a copy under
    metadata.doc (so filters can use the chunks' GIN index), so we update those
    copies in the same transaction. No re-embedding is needed: metadata isn't
    part of what gets embedded.
    """
    document = await _get_readable_document(session, user, document_id)
    collection = await session.get(Collection, document.collection_id)
    if collection is None or not can_write_collection(user, collection):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't edit this document")

    document.meta = body.metadata
    await session.execute(
        text(
            "UPDATE chunks SET metadata = jsonb_set(metadata, '{doc}', CAST(:meta AS jsonb)) "
            "WHERE document_id = :document_id"
        ),
        {"meta": json.dumps(body.metadata), "document_id": document.id},
    )
    await session.commit()
    await session.refresh(document)
    return document


# ----------------------------------------------------------------------------- helpers


async def _get_readable_document(session, user: User, document_id: uuid.UUID) -> Document:
    document = await session.get(Document, document_id)
    if (
        document is None
        or document.tenant_id != user.tenant_id
        or document.collection_id not in await readable_collection_ids(session, user)
    ):
        # 404 (not 403) so users can't discover which document ids exist.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return document


def _parse_metadata(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(422, "metadata must be valid JSON") from None
    if not isinstance(value, dict):
        raise HTTPException(422, "metadata must be a JSON object")
    return value


async def _upload_one(
    session, user: User, collection: Collection, upload: UploadFile, doc_meta: dict
) -> UploadResult:
    settings = get_settings()
    storage = get_storage()
    # Path(...).name drops any directory part a client might send ("../../x.pdf").
    filename = Path(upload.filename or "").name or "unnamed"
    extension = Path(filename).suffix.lower()

    if extension not in SUPPORTED_EXTENSIONS:
        allowed = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        return UploadResult(
            filename=filename, status="rejected", detail=f"Unsupported type. Allowed: {allowed}"
        )

    tmp_path = storage.tmp_dir / uuid.uuid4().hex
    try:
        file_hash, size = await _spool_to_disk(upload, tmp_path, settings.max_upload_bytes)
    except _FileTooLarge:
        tmp_path.unlink(missing_ok=True)
        return UploadResult(
            filename=filename,
            status="rejected",
            detail=f"File exceeds the {settings.max_upload_mb} MB limit",
        )

    rejection = _content_problem(tmp_path, extension, size)
    if rejection:
        tmp_path.unlink(missing_ok=True)
        return UploadResult(filename=filename, status="rejected", detail=rejection)

    existing = await _find_duplicate(session, user.tenant_id, file_hash)
    if existing:
        tmp_path.unlink(missing_ok=True)
        return await _handle_duplicate(session, user, filename, existing)

    document_id = uuid.uuid4()
    storage_key = storage.key_for(user.tenant_id, document_id, extension)
    storage.save(storage_key, tmp_path)
    document = Document(
        id=document_id,
        tenant_id=user.tenant_id,
        collection_id=collection.id,
        title=_title_from_filename(filename),
        filename=filename,
        mime_type=MIME_TYPES[extension],
        storage_path=storage_key,
        file_hash=file_hash,
        meta=doc_meta,
        uploaded_by=user.id,
        status=DocumentStatus.PENDING,
    )
    session.add(document)
    try:
        await session.commit()
    except IntegrityError:
        # Two identical files uploaded at the same moment: the UNIQUE
        # (tenant_id, file_hash) constraint lets exactly one win.
        await session.rollback()
        storage.delete(storage_key)
        existing = await _find_duplicate(session, user.tenant_id, file_hash)
        return await _handle_duplicate(session, user, filename, existing)

    log.info("document.uploaded", document_id=str(document_id), bytes=size, type=extension)
    await ingest_document(session, document_id)
    await session.refresh(document)
    return UploadResult(
        filename=filename, status="created", document=DocumentOut.model_validate(document)
    )


async def _spool_to_disk(upload: UploadFile, destination: Path, max_bytes: int) -> tuple[str, int]:
    """Copy the upload to disk in 1 MiB pieces, hashing as we go; stop if too big."""
    hasher = hashlib.sha256()
    size = 0
    with destination.open("wb") as out:
        while piece := await upload.read(_READ_CHUNK):
            size += len(piece)
            if size > max_bytes:
                raise _FileTooLarge
            hasher.update(piece)
            out.write(piece)
    return hasher.hexdigest(), size


def _content_problem(path: Path, extension: str, size: int) -> str | None:
    if size == 0:
        return "File is empty"
    magic = _MAGIC.get(extension)
    if magic:
        with path.open("rb") as f:
            if f.read(len(magic)) != magic:
                return f"File content is not a valid {extension} file"
    return None


async def _find_duplicate(session, tenant_id: uuid.UUID, file_hash: str) -> Document | None:
    return await session.scalar(
        select(Document).where(Document.tenant_id == tenant_id, Document.file_hash == file_hash)
    )


async def _handle_duplicate(
    session, user: User, filename: str, existing: Document | None
) -> UploadResult:
    readable = await readable_collection_ids(session, user)
    if existing is None or existing.collection_id not in readable:
        # The identical file sits in a collection this user can't read. Say it's
        # a duplicate, but return nothing about it (no title, id or collection).
        return UploadResult(
            filename=filename,
            status="duplicate",
            detail="An identical file already exists in this organisation.",
        )

    if existing.status == DocumentStatus.FAILED:
        # Deduplication must not make a failure permanent. The original bytes are
        # still in storage, so we simply run the pipeline again (e.g. after an
        # API key was added or a parser bug was fixed).
        collection = await session.get(Collection, existing.collection_id)
        if collection is not None and can_write_collection(user, collection):
            log.info("document.retry", document_id=str(existing.id))
            await ingest_document(session, existing.id)
            await session.refresh(existing)
            return UploadResult(
                filename=filename,
                status="retried",
                document=DocumentOut.model_validate(existing),
                detail="This file failed to process before, so it was processed again.",
            )

    return UploadResult(
        filename=filename,
        status="duplicate",
        document=DocumentOut.model_validate(existing),
        detail="This file was already uploaded; the existing document is returned.",
    )


def _title_from_filename(filename: str) -> str:
    """'lease_unit_4b.pdf' -> 'Lease Unit 4b' (the parser may find a better title later)."""
    stem = Path(filename).stem.replace("_", " ").replace("-", " ").strip()
    return (stem[:1].upper() + stem[1:])[:300] if stem else filename
