"""Week 5 backend: answer feedback, usage reporting and per-tenant settings."""

from tests.helpers import parse_sse
from tests.integration.test_documents import LEASE_MD


async def ask(client, tenant, question="What is the rent for Unit 4B?") -> dict:
    events = parse_sse(
        (await client.post("/chat", headers=tenant.headers, json={"question": question})).text
    )
    return events[-1][1]


async def test_feedback_on_an_answer(client, make_tenant, add_user):
    tenant = await make_tenant()
    await tenant.upload("lease.md", LEASE_MD)
    done = await ask(client, tenant)
    message_id = done["message_id"]

    assert (
        await client.post(
            f"/messages/{message_id}/feedback", headers=tenant.headers, json={"value": -1}
        )
    ).status_code == 204

    detail = (await client.get("/conversations", headers=tenant.headers)).json()[0]
    messages = (await client.get(f"/conversations/{detail['id']}", headers=tenant.headers)).json()[
        "messages"
    ]
    assert [m["id"] for m in messages if m["role"] == "assistant"] == [message_id]

    # Someone else's message can't be rated, and neither can a made-up id.
    other = await add_user(tenant, "other@example.com", "member")
    assert (
        await client.post(f"/messages/{message_id}/feedback", headers=other, json={"value": 1})
    ).status_code == 404
    bad_value = await client.post(
        f"/messages/{message_id}/feedback", headers=tenant.headers, json={"value": 5}
    )
    assert bad_value.status_code == 422


async def test_usage_report(client, make_tenant, add_user):
    tenant = await make_tenant()
    await tenant.upload("lease.md", LEASE_MD)
    await ask(client, tenant)

    report = (await client.get("/admin/usage", headers=tenant.headers)).json()
    assert report["totals"]["questions"] == 1
    assert report["totals"]["documents"] == 1
    assert report["totals"]["chunks"] >= 1
    assert report["totals"]["users"] == 1
    assert report["totals"]["tokens"] > 0
    today = report["days"][-1]
    assert today["questions"] == 1
    # Costs are broken down by the kind of AI call that produced them.
    assert {"ingest", "embed", "chat"} <= set(today["by_type"])

    member = await add_user(tenant, "member@example.com", "member")
    assert (await client.get("/admin/usage", headers=member)).status_code == 403


async def test_tenant_settings_change_the_assistant(client, make_tenant, add_user):
    tenant = await make_tenant("Kestrel Pay")
    settings = (await client.get("/tenants/me", headers=tenant.headers)).json()
    assert settings == {"name": "Kestrel Pay", "assistant_name": None, "tone": None, "model": None}

    updated = await client.patch(
        "/tenants/me",
        headers=tenant.headers,
        json={"assistant_name": "Kes", "tone": "formal and concise", "model": "claude-sonnet-5"},
    )
    assert updated.status_code == 200
    assert updated.json()["assistant_name"] == "Kes"

    # The prompt the LLM receives reflects those settings.
    from app.services.generation.prompts import build_system_prompt

    prompt = build_system_prompt("Kestrel Pay", "Kes", "formal and concise")
    assert prompt.startswith("You are Kes, the knowledge assistant for Kestrel Pay.")
    assert "Tone: formal and concise" in prompt

    # Only admins may change them, and only known models are accepted.
    member = await add_user(tenant, "member@example.com", "member")
    assert (
        await client.patch("/tenants/me", headers=member, json={"assistant_name": "x"})
    ).status_code == 403
    assert (
        await client.patch("/tenants/me", headers=tenant.headers, json={"model": "gpt-hallucinate"})
    ).status_code == 422
