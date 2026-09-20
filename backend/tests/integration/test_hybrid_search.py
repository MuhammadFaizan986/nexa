"""
Week 3 retrieval, end to end: keyword search, hybrid fusion, reranking,
contextual chunk headers, metadata filters and editing metadata.
"""

from sqlalchemy import select

from app.db.models import Chunk
from app.db.rls import maintenance_mode
from app.db.session import SessionLocal
from tests.helpers import parse_sse


def section(title: str, sentence: str, n: int = 14) -> str:
    """A section long enough (~170 tokens) to become its own chunk."""
    filler = " ".join(f"{sentence} Note {i} about how this rule is applied." for i in range(n))
    return f"## {title}\n\n{filler}\n\n"


ERROR_CODES = (
    "# Payment Error Codes\n\n"
    + section("E-204 Beneficiary account closed", "The receiving account was closed.")
    + section("E-205 Rejected by beneficiary bank", "The receiving bank rejected it.")
).encode()


def lease(unit: str) -> bytes:
    # The Termination section never mentions the unit: only the title does.
    return (
        f"# Lease Agreement - Unit {unit}\n\n"
        + section("Rent", "Rent is paid monthly in advance.")
        + section("Termination", "The tenant must give written notice to end the tenancy.")
    ).encode()


async def search(client, tenant, **body) -> dict:
    response = await client.post("/search", headers=tenant.headers, json=body)
    assert response.status_code == 200, response.text
    return response.json()


async def test_keyword_search_nails_exact_identifiers(client, make_tenant):
    tenant = await make_tenant()
    await tenant.upload("errors.md", ERROR_CODES)
    body = await search(client, tenant, query="E-204", mode="keyword", rerank=False)
    top = body["results"][0]
    assert top["section_title"] == "E-204 Beneficiary account closed"
    assert top["keyword_score"] > 0 and top["similarity"] is None
    assert body["reranked"] is False


async def test_modes_and_per_stage_scores(client, make_tenant):
    tenant = await make_tenant()
    await tenant.upload("errors.md", ERROR_CODES)
    query = "What does error code E-204 mean?"

    semantic = await search(client, tenant, query=query, mode="semantic", rerank=False)
    assert all(hit["keyword_score"] is None for hit in semantic["results"])

    hybrid = await search(client, tenant, query=query, mode="hybrid", rerank=False)
    assert all(hit["fusion_score"] is not None for hit in hybrid["results"])

    reranked = await search(client, tenant, query=query)  # default: hybrid + reranker
    assert reranked["reranked"] is True
    top = reranked["results"][0]
    assert top["section_title"] == "E-204 Beneficiary account closed"
    assert top["score"] == top["rerank_score"]

    bad = await client.post("/search", headers=tenant.headers, json={"query": "x", "mode": "magic"})
    assert bad.status_code == 422


async def test_contextual_header_lets_keyword_search_find_the_right_lease(client, make_tenant):
    tenant = await make_tenant()
    lease_4b = (await tenant.upload("lease_4b.md", lease("4B")))["document"]
    await tenant.upload("lease_7a.md", lease("7A"))

    with maintenance_mode():  # RLS: direct queries need the admin view
        async with SessionLocal() as session:
            headers = list(
                await session.scalars(
                    select(Chunk.context_header).where(Chunk.document_id == lease_4b["id"])
                )
            )
    assert "Document: Lease Agreement - Unit 4B | Section: Termination" in headers

    body = await search(
        client, tenant, query="Unit 7A termination notice", mode="keyword", rerank=False
    )
    top = body["results"][0]
    # Only the header says "7A", yet the 7A lease's Termination chunk ranks first.
    assert (top["document_title"], top["section_title"]) == (
        "Lease Agreement - Unit 7A",
        "Termination",
    )


async def test_metadata_and_date_filters(client, make_tenant):
    tenant = await make_tenant()
    await tenant.upload(
        "lease_4b.md", lease("4B"), metadata='{"doc_type": "lease", "date": "2026-03-01"}'
    )
    await tenant.upload(
        "lease_7a.md", lease("7A"), metadata='{"doc_type": "lease", "date": "2025-11-01"}'
    )
    await tenant.upload("errors.md", ERROR_CODES, metadata='{"doc_type": "reference"}')
    query = "written notice"

    leases = await search(client, tenant, query=query, filters={"doc_type": "lease"})
    assert {hit["filename"] for hit in leases["results"]} == {"lease_4b.md", "lease_7a.md"}

    recent = await search(
        client, tenant, query=query, filters={"doc_type": "lease"}, date_from="2026-01-01"
    )
    assert {hit["filename"] for hit in recent["results"]} == {"lease_4b.md"}

    none = await search(client, tenant, query=query, filters={"doc_type": "invoice"})
    assert none["results"] == []


async def test_editing_metadata_updates_filters_immediately(client, make_tenant, add_user):
    tenant = await make_tenant()
    document = (await tenant.upload("errors.md", ERROR_CODES, metadata='{"doc_type": "draft"}'))[
        "document"
    ]
    response = await client.patch(
        f"/documents/{document['id']}",
        headers=tenant.headers,
        json={"metadata": {"doc_type": "reference"}},
    )
    assert response.status_code == 200
    assert response.json()["metadata"] == {"doc_type": "reference"}

    assert (await search(client, tenant, query="E-204", filters={"doc_type": "reference"}))[
        "results"
    ]
    assert (await search(client, tenant, query="E-204", filters={"doc_type": "draft"}))[
        "results"
    ] == []

    viewer = await add_user(tenant, "viewer@example.com", "viewer")
    forbidden = await client.patch(
        f"/documents/{document['id']}", headers=viewer, json={"metadata": {"doc_type": "x"}}
    )
    assert forbidden.status_code == 403


async def test_chat_uses_hybrid_search_reranking_and_filters(client, make_tenant):
    tenant = await make_tenant()
    await tenant.upload("errors.md", ERROR_CODES, metadata='{"doc_type": "reference"}')
    question = {"question": "What does error code E-204 mean?"}

    events = parse_sse((await client.post("/chat", headers=tenant.headers, json=question)).text)
    done = events[-1][1]
    assert done["no_answer"] is False
    assert done["citations"][0]["filename"] == "errors.md"
    assert {"semantic", "keyword", "rerank"} <= set(done["latency_ms"])

    # Filtered to a document type that doesn't exist: nothing to answer from.
    filtered = {**question, "filters": {"doc_type": "lease"}}
    events = parse_sse((await client.post("/chat", headers=tenant.headers, json=filtered)).text)
    assert events[-1][1]["no_answer"] is True


async def test_identifier_boost_beats_common_words(client, make_tenant):
    """Every section says "work order ... contractor"; only one has the asked-for id."""
    tenant = await make_tenant()
    sections = "".join(
        section(f"Report {n}", f"Work order WO-{n} was raised with a contractor for repairs.")
        for n in range(1001, 1007)
    )
    await tenant.upload("orders.md", f"# Work Orders\n\n{sections}".encode())
    question = "Which contractor was given work order WO-1004 and what was it for?"
    for mode in ("keyword", "hybrid"):
        body = await search(client, tenant, query=question, mode=mode, rerank=False)
        assert "WO-1004" in body["results"][0]["content"], mode
