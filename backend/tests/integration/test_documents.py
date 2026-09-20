"""Upload -> ingestion -> chunks, including the real sample PDFs from sample_data/."""

import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Chunk
from app.db.rls import maintenance_mode
from app.db.session import SessionLocal

LEASE_MD = b"""# Lease Agreement - Unit 4B

## Rent
The monthly rent for Unit 4B is $2,450, payable on the first day of each month.

## Termination
The tenant must give 60 days written notice to terminate the lease.
"""


def sample_data_dir() -> Path | None:
    candidates = [
        os.environ.get("SAMPLE_DATA_DIR", ""),
        "/sample_data",  # mounted by docker-compose
        str(Path(__file__).resolve().parents[3] / "sample_data"),
    ]
    return next((Path(c) for c in candidates if c and Path(c, "property").is_dir()), None)


async def chunks_of(document_id: str) -> list[Chunk]:
    # Row-Level Security hides rows of other tenants, so a test helper that
    # queries the database directly has to ask for the admin view explicitly.
    with maintenance_mode():
        async with SessionLocal() as session:
            rows = await session.scalars(
                select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.chunk_index)
            )
            return list(rows)


async def test_upload_markdown_creates_searchable_chunks(client, make_tenant):
    tenant = await make_tenant()
    result = await tenant.upload("lease.md", LEASE_MD, metadata='{"doc_type": "lease"}')

    assert result["status"] == "created"
    document = result["document"]
    assert document["status"] == "ready", document["error_message"]
    assert document["title"] == "Lease Agreement - Unit 4B"  # from the first heading
    assert document["metadata"] == {"doc_type": "lease"}

    detail = (await client.get(f"/documents/{document['id']}", headers=tenant.headers)).json()
    assert detail["chunk_count"] >= 1

    chunks = await chunks_of(document["id"])
    assert "60 days written notice" in " ".join(c.content for c in chunks)
    assert chunks[0].meta["doc"] == {"doc_type": "lease"}
    assert chunks[0].meta["embedding_model"] == "fake-hashing"


async def test_duplicate_upload_returns_existing_document(make_tenant):
    tenant = await make_tenant()
    first = await tenant.upload("lease.md", LEASE_MD)
    second = await tenant.upload("renamed-copy.md", LEASE_MD)
    assert second["status"] == "duplicate"
    assert second["document"]["id"] == first["document"]["id"]


async def test_reuploading_a_failed_document_retries_processing(make_tenant, monkeypatch):
    """A failure (here: missing API key) must not be locked in by deduplication."""
    import app.services.ingestion.pipeline as pipeline
    from app.services.embeddings import ProviderConfigError

    def missing_key():
        raise ProviderConfigError("OPENAI_API_KEY is not set")

    tenant = await make_tenant()
    with monkeypatch.context() as patch:
        patch.setattr(pipeline, "get_embedding_provider", missing_key)
        first = await tenant.upload("lease.md", LEASE_MD)
    assert first["document"]["status"] == "failed"
    assert "OPENAI_API_KEY" in first["document"]["error_message"]

    # Key "fixed": uploading the same file again re-runs the pipeline.
    second = await tenant.upload("lease.md", LEASE_MD)
    assert second["status"] == "retried"
    assert second["document"]["id"] == first["document"]["id"]
    assert second["document"]["status"] == "ready"
    assert second["document"]["error_message"] is None

    # Once it's ready, a third upload is an ordinary duplicate.
    assert (await tenant.upload("lease.md", LEASE_MD))["status"] == "duplicate"


async def test_bad_files_are_rejected_individually(client, make_tenant):
    tenant = await make_tenant()
    response = await client.post(
        "/documents",
        headers=tenant.headers,
        data={"collection_id": str(tenant.default_collection_id)},
        files=[
            ("files", ("virus.exe", b"MZ...")),
            ("files", ("fake.pdf", b"this is not a pdf")),
            ("files", ("empty.txt", b"")),
            ("files", ("good.md", LEASE_MD)),
        ],
    )
    statuses = [(r["filename"], r["status"]) for r in response.json()]
    assert statuses == [
        ("virus.exe", "rejected"),
        ("fake.pdf", "rejected"),
        ("empty.txt", "rejected"),
        ("good.md", "created"),
    ]


async def test_too_large_file_is_rejected(make_tenant, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_mb", 0)
    tenant = await make_tenant()
    result = await tenant.upload("lease.md", LEASE_MD)
    assert result["status"] == "rejected"
    assert "limit" in result["detail"]


async def test_unparseable_file_is_marked_failed_with_a_reason(make_tenant):
    tenant = await make_tenant()
    result = await tenant.upload("broken.pdf", b"%PDF-1.7 truncated garbage")
    assert result["status"] == "created"
    assert result["document"]["status"] == "failed"
    assert "ParseError" in result["document"]["error_message"]


async def test_invalid_metadata_json(client, make_tenant):
    tenant = await make_tenant()
    response = await client.post(
        "/documents",
        headers=tenant.headers,
        data={"collection_id": str(tenant.default_collection_id), "metadata": "[1, 2]"},
        files=[("files", ("lease.md", LEASE_MD))],
    )
    assert response.status_code == 422


async def test_list_documents_filters_by_status(client, make_tenant):
    tenant = await make_tenant()
    await tenant.upload("lease.md", LEASE_MD)
    await tenant.upload("broken.pdf", b"%PDF-1.7 truncated garbage")
    ready = (await client.get("/documents?status=ready", headers=tenant.headers)).json()
    failed = (await client.get("/documents?status=failed", headers=tenant.headers)).json()
    assert [d["filename"] for d in ready] == ["lease.md"]
    assert [d["filename"] for d in failed] == ["broken.pdf"]


@pytest.mark.skipif(sample_data_dir() is None, reason="sample_data/ not available")
async def test_sample_lease_pdf_keeps_page_numbers(client, make_tenant):
    """The plan's Week 2 check: the notice period must be found on page 3 of the lease."""
    tenant = await make_tenant()
    pdf = (sample_data_dir() / "property" / "lease_unit_4b.pdf").read_bytes()
    document = (await tenant.upload("lease_unit_4b.pdf", pdf))["document"]
    assert document["status"] == "ready", document["error_message"]
    assert document["page_count"] == 5

    chunks = await chunks_of(document["id"])
    notice = [c for c in chunks if "60 days written notice" in c.content]
    assert notice and notice[0].page_start == 3
    assert notice[0].section_title == "7. Term and Termination"
    # Running header/footer were stripped by the cleaner.
    assert not any("Confidential" in c.content or "Page 3 of 5" in c.content for c in chunks)


@pytest.mark.skipif(sample_data_dir() is None, reason="sample_data/ not available")
async def test_fifty_page_pdf_every_chunk_has_its_page(make_tenant):
    """The plan's Week 1 'done when': a 50-page PDF with correct page numbers per chunk."""
    tenant = await make_tenant()
    pdf = (sample_data_dir() / "property" / "inspection_reports_2026.pdf").read_bytes()
    document = (await tenant.upload("inspection_reports_2026.pdf", pdf))["document"]
    assert document["status"] == "ready"
    assert document["page_count"] == 50

    chunks = await chunks_of(document["id"])
    assert {c.page_start for c in chunks} == set(range(1, 51))  # every page is represented
    # One unit's report per page: no chunk may mix two pages (two units).
    assert all(c.page_start == c.page_end for c in chunks)
    unit_4b = [c for c in chunks if "INS-2026-0259" in c.content]
    assert unit_4b and all(c.page_start == 3 for c in unit_4b)


@pytest.mark.skipif(sample_data_dir() is None, reason="sample_data/ not available")
async def test_citation_points_at_the_page_of_the_quoted_line(make_tenant):
    """The annual-leave line is on page 2, even when its chunk starts on page 1."""
    from app.services.generation.citations import locate_in_chunk
    from app.services.retrieval.semantic import RetrievedChunk

    tenant = await make_tenant()
    pdf = (sample_data_dir() / "company" / "employee_handbook.pdf").read_bytes()
    document = (await tenant.upload("employee_handbook.pdf", pdf))["document"]
    line = "After 3 completed years of service: 21 days per year"
    [chunk] = [c for c in await chunks_of(document["id"]) if line in c.content]

    retrieved = RetrievedChunk(
        chunk_id=chunk.id,
        document_id=chunk.document_id,
        document_title=document["title"],
        filename="employee_handbook.pdf",
        content=chunk.content,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        section_title=chunk.section_title,
        score=1.0,
        rank=1,
        spans=chunk.meta["spans"],
    )
    snippet = chunk.content[chunk.content.index(line) :].split("\n")[0]
    assert locate_in_chunk(retrieved, snippet)[0] == 2


async def test_background_mode_returns_immediately_and_records_a_job(
    client, make_tenant, monkeypatch
):
    """With INGESTION_MODE=celery the upload only queues work; a worker does it."""
    queued: list[tuple] = []

    class FakeTask:
        def delay(self, *args):
            queued.append(args)

    monkeypatch.setattr(get_settings(), "ingestion_mode", "celery")
    import app.workers.tasks as tasks

    monkeypatch.setattr(tasks, "ingest_document_task", FakeTask())

    tenant = await make_tenant()
    document = (await tenant.upload("lease.md", LEASE_MD))["document"]
    assert document["status"] == "pending"  # not processed yet
    assert len(queued) == 1

    detail = (await client.get(f"/documents/{document['id']}", headers=tenant.headers)).json()
    assert detail["ingestion"]["status"] == "queued"
    assert detail["chunk_count"] == 0

    # Now run what the worker would have run.
    from app.db.rls import tenant_scope
    from app.services.ingestion.jobs import run_ingestion_job

    job_id, document_id, tenant_id = queued[0]
    with tenant_scope(uuid.UUID(tenant_id)):
        async with SessionLocal() as session:
            chunks = await run_ingestion_job(session, uuid.UUID(job_id))
    assert chunks > 0

    detail = (await client.get(f"/documents/{document['id']}", headers=tenant.headers)).json()
    assert detail["status"] == "ready"
    assert detail["ingestion"]["status"] == "done"
    assert detail["ingestion"]["attempts"] == 1
    assert detail["ingestion"]["chunks_created"] == detail["chunk_count"] > 0


async def test_reindex_delete_and_download(client, make_tenant, add_user):
    tenant = await make_tenant()
    document = (await tenant.upload("lease.md", LEASE_MD))["document"]

    # Re-index: same document id, processed again.
    response = await client.post(f"/documents/{document['id']}/reindex", headers=tenant.headers)
    assert response.status_code == 202
    detail = response.json()
    assert detail["status"] == "ready" and detail["ingestion"]["attempts"] == 1
    assert len(await chunks_of(document["id"])) == detail["chunk_count"]

    # Download returns the original bytes.
    file_response = await client.get(f"/documents/{document['id']}/file", headers=tenant.headers)
    assert file_response.status_code == 200
    assert file_response.content == LEASE_MD

    # A viewer may read but not change or delete.
    viewer = await add_user(tenant, "viewer@example.com", "viewer")
    assert (
        await client.get(f"/documents/{document['id']}/file", headers=viewer)
    ).status_code == 200
    assert (await client.delete(f"/documents/{document['id']}", headers=viewer)).status_code == 403

    deleted = await client.delete(f"/documents/{document['id']}", headers=tenant.headers)
    assert deleted.status_code == 204
    assert (
        await client.get(f"/documents/{document['id']}", headers=tenant.headers)
    ).status_code == 404
    assert await chunks_of(document["id"]) == []  # chunks went with it


async def test_csv_and_html_uploads_are_searchable(client, make_tenant):
    tenant = await make_tenant()
    rent_roll = b"Unit,Monthly rent,Lease start\n4B,2450,2026-03-01\n7A,3100,2025-11-01\n"
    wiki = (
        b"<html><head><title>Refunds</title></head><body>"
        b"<nav>Home</nav><h1>Refund Policy</h1>"
        b"<p>Refunds are issued within 5 business days of approval.</p></body></html>"
    )
    csv_doc = (await tenant.upload("rent_roll.csv", rent_roll))["document"]
    html_doc = (await tenant.upload("refunds.html", wiki))["document"]
    assert (csv_doc["status"], csv_doc["mime_type"]) == ("ready", "text/csv")
    assert (html_doc["status"], html_doc["title"]) == ("ready", "Refunds")

    hits = (
        await client.post(
            "/search", headers=tenant.headers, json={"query": "monthly rent for unit 4B"}
        )
    ).json()["results"]
    assert "Unit: 4B; Monthly rent: 2450" in hits[0]["content"]
