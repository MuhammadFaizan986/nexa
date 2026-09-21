"""
Agent mode end to end: the tools against a real database, and the security
boundary they must respect.

The tools are the only route the model has to data, so the question that
matters most here is not "does it find the lease" but "what happens when the
model asks for a document it must not see". The answer has to be the same
whether the document belongs to another tenant or simply doesn't exist.
"""

import uuid

from sqlalchemy import select

from app.db.models import Message, UsageEvent
from app.db.rls import maintenance_mode, use_tenant
from app.db.session import SessionLocal
from app.services.agent.tools import (
    ToolContext,
    compare_documents,
    extract_fields,
    list_documents,
    search_documents,
    summarize_document,
)
from tests.helpers import parse_sse
from tests.integration.test_documents import LEASE_MD

LEASE_7A_MD = b"""# Lease Agreement - Unit 7A

## Rent
The monthly rent for Unit 7A is $3,100, payable on the first day of each month.

## Termination
The tenant must give 30 days written notice to terminate the lease.
"""


async def tool_context(tenant, collection_ids=None) -> tuple[ToolContext, object]:
    """A ToolContext on its own session, scoped to the tenant like a real request."""
    use_tenant(tenant.tenant_id)
    session = SessionLocal()
    return (
        ToolContext(
            session=session,
            tenant_id=tenant.tenant_id,
            collection_ids=collection_ids or [tenant.default_collection_id],
        ),
        session,
    )


# ------------------------------------------------------------------- tools


async def test_list_documents_shows_only_readable_ones(client, make_tenant):
    tenant = await make_tenant()
    await tenant.upload("lease.md", LEASE_MD)
    await tenant.upload("lease7a.md", LEASE_7A_MD)

    ctx, session = await tool_context(tenant)
    async with session:
        result = await list_documents(ctx)
        filtered = await list_documents(ctx, name_contains="7A")

    assert "Lease Agreement - Unit 4B" in result.content
    assert "Lease Agreement - Unit 7A" in result.content
    assert "Unit 4B" not in filtered.content  # the filter really filters


async def test_search_tool_registers_numbered_passages(client, make_tenant):
    tenant = await make_tenant()
    await tenant.upload("lease.md", LEASE_MD)

    ctx, session = await tool_context(tenant)
    async with session:
        result = await search_documents(ctx, query="notice to terminate the lease")

    assert result.passages_found >= 1
    assert 'id="1"' in result.content
    assert len(ctx.passages) == result.passages_found


async def test_summarize_and_extract_read_the_whole_document(client, make_tenant):
    tenant = await make_tenant()
    uploaded = await tenant.upload("lease.md", LEASE_MD)

    ctx, session = await tool_context(tenant)
    async with session:
        summary = await summarize_document(ctx, document_id=uploaded["document"]["id"])
        extract = await extract_fields(
            ctx, document_id=uploaded["document"]["id"], fields=["monthly_rent", "notice_period"]
        )

    assert "60 days written notice" in summary.content
    assert "monthly_rent, notice_period" in extract.content
    assert "never guess" in extract.content  # the anti-hallucination instruction
    # The same chunks were reused, so the numbering didn't restart.
    assert len(ctx.passages) == summary.passages_found


async def test_compare_documents_returns_both_sides(client, make_tenant):
    tenant = await make_tenant()
    a = await tenant.upload("lease.md", LEASE_MD)
    b = await tenant.upload("lease7a.md", LEASE_7A_MD)

    ctx, session = await tool_context(tenant)
    async with session:
        result = await compare_documents(
            ctx,
            document_id_a=a["document"]["id"],
            document_id_b=b["document"]["id"],
            aspect="notice period to terminate",
        )

    assert "Unit 4B" in result.content and "Unit 7A" in result.content
    assert "60 days" in result.content and "30 days" in result.content
    # Each document was searched separately, so both searches are billable.
    assert len(ctx.retrievals) == 2


# -------------------------------------------------------------- the boundary


async def test_a_tool_cannot_reach_another_tenants_document(client, make_tenant):
    theirs = await make_tenant("Kestrel Pay")
    secret = await theirs.upload("lease.md", LEASE_MD)
    mine = await make_tenant("Harbourview Property Group")

    ctx, session = await tool_context(mine)
    async with session:
        result = await summarize_document(ctx, document_id=secret["document"]["id"])

    assert result.error
    # Same wording as a genuinely missing id: a guess reveals nothing.
    assert "No document with id" in result.content


async def test_a_tool_cannot_reach_an_unreadable_collection(client, make_tenant):
    tenant = await make_tenant()
    restricted = (
        await client.post(
            "/collections",
            headers=tenant.headers,
            json={"name": "Board papers", "visibility": "restricted"},
        )
    ).json()
    document = await tenant.upload("lease.md", LEASE_MD, collection_id=uuid.UUID(restricted["id"]))

    # A context that may read only the default collection — what a member
    # without a group grant would get.
    ctx, session = await tool_context(tenant, collection_ids=[tenant.default_collection_id])
    async with session:
        result = await summarize_document(ctx, document_id=document["document"]["id"])
        listed = await list_documents(ctx)

    assert result.error
    assert "Board papers" not in listed.content


async def test_a_made_up_document_id_is_handled(client, make_tenant):
    tenant = await make_tenant()
    ctx, session = await tool_context(tenant)
    async with session:
        nonsense = await summarize_document(ctx, document_id="not-a-uuid")
        missing = await summarize_document(ctx, document_id=str(uuid.uuid4()))

    assert nonsense.error and "not a document id" in nonsense.content
    assert missing.error


# ------------------------------------------------------ through the endpoint


async def test_agent_mode_streams_its_tool_calls_and_cites(client, make_tenant):
    """
    The offline fake model always searches once and then answers, which is
    enough to prove the whole path: tool event, passages numbered, citation
    resolved, trace saved.
    """
    tenant = await make_tenant()
    await tenant.upload("lease.md", LEASE_MD)

    response = await client.post(
        "/chat",
        headers=tenant.headers,
        json={"question": "What is the notice period for Unit 4B?", "agent": True},
    )
    assert response.status_code == 200
    events = parse_sse(response.text)
    names = [name for name, _ in events]

    assert "tool" in names  # the trace reached the client live
    step = next(data for name, data in events if name == "tool")
    assert step["tool"] == "search_documents"
    assert step["number"] == 1

    done = events[-1][1]
    assert done["citations"], "the agent's answer should cite the passage it found"
    assert done["tool_steps"][0]["tool"] == "search_documents"
    assert done["usage"]["cost_usd"] is not None

    # The trace is stored, so reopening the conversation still shows the steps.
    with maintenance_mode():
        async with SessionLocal() as session:
            message = (
                await session.scalars(
                    select(Message).where(Message.role == "assistant").order_by(Message.created_at)
                )
            ).all()[-1]
    assert message.meta["agent"] is True
    assert message.meta["tool_steps"][0]["tool"] == "search_documents"

    # The searches the agent ran are recorded as usage, like any other search.
    async with SessionLocal() as session:
        types = set(await session.scalars(select(UsageEvent.event_type)))
    assert {"embed", "chat"} <= types


async def test_rag_mode_is_still_the_default(client, make_tenant):
    tenant = await make_tenant()
    await tenant.upload("lease.md", LEASE_MD)

    response = await client.post(
        "/chat", headers=tenant.headers, json={"question": "What is the rent for Unit 4B?"}
    )
    events = parse_sse(response.text)

    assert "tool" not in [name for name, _ in events]
    assert events[-1][1]["tool_steps"] == []
