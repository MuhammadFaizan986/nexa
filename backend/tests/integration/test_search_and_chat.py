"""Search + streaming chat, end to end, with the fake (offline) embedding + LLM providers."""

from sqlalchemy import func, select

from app.db.models import UsageEvent
from app.db.session import SessionLocal
from app.services.generation.prompts import NO_ANSWER
from tests.helpers import parse_sse
from tests.integration.test_documents import LEASE_MD

IT_POLICY_MD = b"""# IT Security Policy

## Passwords
Passwords must be at least 14 characters long and multi-factor authentication is required.

## Laptops
All laptops must use full disk encryption and lock after five minutes.
"""


async def test_search_ranks_the_relevant_document_first(client, make_tenant):
    tenant = await make_tenant()
    await tenant.upload("lease.md", LEASE_MD)
    await tenant.upload("it_policy.md", IT_POLICY_MD)

    response = await client.post(
        "/search",
        headers=tenant.headers,
        json={"query": "How many days written notice to terminate the lease?"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "hybrid" and body["reranked"] is True  # the defaults
    assert {"embed_query", "semantic", "keyword", "rerank", "retrieval"} <= set(body["latency_ms"])
    top = body["results"][0]
    assert top["document_title"] == "Lease Agreement - Unit 4B"
    assert top["rank"] == 1
    assert top["rerank_score"] is not None and top["similarity"] is not None
    assert body["results"][0]["score"] >= body["results"][-1]["score"]


async def test_chat_streams_answer_with_validated_citations(client, make_tenant):
    tenant = await make_tenant()
    await tenant.upload("it_policy.md", IT_POLICY_MD)

    response = await client.post(
        "/chat",
        headers=tenant.headers,
        json={"question": "How long must passwords be? Is multi-factor authentication required?"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(response.text)
    names = [name for name, _ in events]
    assert names[0] == "meta" and names[-1] == "done"
    assert names.count("token") > 1  # really streamed, not one blob

    answer = "".join(data["text"] for name, data in events if name == "token")
    done = events[-1][1]
    assert "[1]" in answer
    assert done["no_answer"] is False
    assert done["model"] == "fake-llm"
    assert done["citations"][0]["number"] == 1
    assert done["citations"][0]["document_title"] == "IT Security Policy"
    assert done["citations"][0]["snippet"]
    assert {"embed_query", "retrieval", "llm_first_token", "llm_total", "total"} <= set(
        done["latency_ms"]
    )

    # The conversation, both messages and the citation were saved.
    conversation_id = events[0][1]["conversation_id"]
    detail = (await client.get(f"/conversations/{conversation_id}", headers=tenant.headers)).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["citations"][0]["document_title"] == "IT Security Policy"

    # Usage was recorded for both the query embedding and the LLM call.
    async with SessionLocal() as session:
        types = set(await session.scalars(select(UsageEvent.event_type)))
    assert {"ingest", "embed", "chat"} <= types


async def test_follow_up_question_continues_the_conversation(client, make_tenant):
    tenant = await make_tenant()
    await tenant.upload("lease.md", LEASE_MD)
    first = parse_sse(
        (
            await client.post(
                "/chat", headers=tenant.headers, json={"question": "What is the rent for Unit 4B?"}
            )
        ).text
    )
    conversation_id = first[0][1]["conversation_id"]
    second = await client.post(
        "/chat",
        headers=tenant.headers,
        json={
            "question": "And the notice to terminate the lease?",
            "conversation_id": conversation_id,
        },
    )
    assert parse_sse(second.text)[-1][0] == "done"

    detail = (await client.get(f"/conversations/{conversation_id}", headers=tenant.headers)).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant", "user", "assistant"]
    listed = (await client.get("/conversations", headers=tenant.headers)).json()
    assert [c["id"] for c in listed] == [conversation_id]


async def test_unrelated_question_gets_i_dont_know_without_calling_the_llm(client, make_tenant):
    tenant = await make_tenant()
    await tenant.upload("lease.md", LEASE_MD)

    events = parse_sse(
        (
            await client.post(
                "/chat",
                headers=tenant.headers,
                json={"question": "Explain quantum chromodynamics and gluon confinement"},
            )
        ).text
    )
    done = events[-1][1]
    assert "".join(d["text"] for n, d in events if n == "token") == NO_ANSWER
    assert done["no_answer"] is True
    assert done["citations"] == []
    assert done["model"] is None  # the LLM was never called

    async with SessionLocal() as session:
        chat_events = await session.scalar(
            select(func.count()).select_from(UsageEvent).where(UsageEvent.event_type == "chat")
        )
    assert chat_events == 0


async def test_chat_with_no_documents_says_i_dont_know(client, make_tenant):
    tenant = await make_tenant()
    events = parse_sse(
        (
            await client.post(
                "/chat", headers=tenant.headers, json={"question": "What is the rent?"}
            )
        ).text
    )
    assert events[-1][1]["no_answer"] is True
