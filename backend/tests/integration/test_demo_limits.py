"""
The two switches that make a public demo safe to link to (Week 8).

A public URL means strangers using your API budget and your storage. Both
guards are off by default — local development should have no limits — so these
tests are also the documentation for how to turn them on.
"""

from app.core.config import get_settings
from tests.helpers import parse_sse
from tests.integration.test_documents import LEASE_MD


async def test_questions_are_capped_per_day_when_a_limit_is_set(client, make_tenant, monkeypatch):
    tenant = await make_tenant()
    await tenant.upload("lease.md", LEASE_MD)
    monkeypatch.setattr(get_settings(), "max_questions_per_day", 1)

    first = await client.post(
        "/chat", headers=tenant.headers, json={"question": "What is the rent for Unit 4B?"}
    )
    assert first.status_code == 200
    assert parse_sse(first.text)[-1][0] == "done"  # the first question goes through

    second = await client.post(
        "/chat", headers=tenant.headers, json={"question": "And the notice period?"}
    )
    assert second.status_code == 429
    assert "per day" in second.json()["detail"]


async def test_no_limit_by_default(client, make_tenant):
    tenant = await make_tenant()
    await tenant.upload("lease.md", LEASE_MD)

    for _ in range(3):
        response = await client.post(
            "/chat", headers=tenant.headers, json={"question": "What is the rent for Unit 4B?"}
        )
        assert response.status_code == 200


async def test_the_limit_is_per_tenant(client, make_tenant, monkeypatch):
    """One busy demo tenant must not lock out a real customer."""
    busy = await make_tenant("Harbourview Property Group")
    await busy.upload("lease.md", LEASE_MD)
    monkeypatch.setattr(get_settings(), "max_questions_per_day", 1)
    await client.post("/chat", headers=busy.headers, json={"question": "What is the rent?"})

    other = await make_tenant("Kestrel Pay")
    await other.upload("lease.md", LEASE_MD)
    response = await client.post(
        "/chat", headers=other.headers, json={"question": "What is the rent?"}
    )

    assert response.status_code == 200


async def test_uploads_can_be_switched_off(client, make_tenant, monkeypatch):
    tenant = await make_tenant()
    monkeypatch.setattr(get_settings(), "uploads_enabled", False)

    response = await client.post(
        "/documents",
        headers=tenant.headers,
        data={"collection_id": str(tenant.default_collection_id)},
        files=[("files", ("lease.md", LEASE_MD))],
    )

    assert response.status_code == 403
    assert "disabled" in response.json()["detail"]
    # Asking questions still works — only adding documents is blocked.
    assert (await client.get("/documents", headers=tenant.headers)).status_code == 200
