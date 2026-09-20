"""
Week 4 security: group-based collection access, and Row-Level Security as the
database-level safety net.

These tests answer the question a fintech or legal client will ask: "can you
prove that someone in Support cannot read Compliance, and that Company A can
never see Company B's documents — even if your code has a bug?"
"""

from sqlalchemy import func, select, text

from app.db.models import Chunk, Document
from app.db.rls import maintenance_mode, tenant_scope
from app.db.session import SessionLocal
from tests.helpers import parse_sse

POLICY = b"""# Compliance Policy

## Suspicious matters
A suspicious matter report must be filed with the regulator within 3 business days.
"""
QUESTION = "How many days do we have to file a suspicious matter report?"


async def grant_group_access(client, tenant, collection_id, user_headers, permission="read"):
    """Create a group, put the user in it, and grant it access to the collection."""
    me = (await client.get("/me", headers=user_headers)).json()["user"]
    group = (
        await client.post(
            "/groups", headers=tenant.headers, json={"name": f"Group for {me['email']}"}
        )
    ).json()
    await client.post(
        f"/groups/{group['id']}/members", headers=tenant.headers, json={"user_id": me["id"]}
    )
    response = await client.put(
        f"/collections/{collection_id}/access",
        headers=tenant.headers,
        json={"grants": [{"group_id": group["id"], "permission": permission}]},
    )
    assert response.status_code == 200, response.text
    return group


async def test_group_grant_opens_a_restricted_collection(client, make_tenant, add_user):
    tenant = await make_tenant()
    compliance = (
        await client.post(
            "/collections",
            headers=tenant.headers,
            json={"name": "Compliance", "visibility": "restricted"},
        )
    ).json()
    await tenant.upload("policy.md", POLICY, collection_id=compliance["id"])

    officer = await add_user(tenant, "officer@example.com", "member")
    support = await add_user(tenant, "support@example.com", "member")

    # Before any grant, the compliance officer sees nothing.
    assert (await client.post("/search", headers=officer, json={"query": QUESTION})).json()[
        "results"
    ] == []

    await grant_group_access(client, tenant, compliance["id"], officer)

    # Now the officer can search it...
    hits = (await client.post("/search", headers=officer, json={"query": QUESTION})).json()
    assert hits["results"] and hits["results"][0]["filename"] == "policy.md"
    names = [c["name"] for c in (await client.get("/collections", headers=officer)).json()]
    assert "Compliance" in names

    # ...and support still cannot, in search or in chat.
    assert (await client.post("/search", headers=support, json={"query": QUESTION})).json()[
        "results"
    ] == []
    events = parse_sse(
        (await client.post("/chat", headers=support, json={"question": QUESTION})).text
    )
    assert events[-1][1]["no_answer"] is True
    assert "3 business days" not in "".join(d["text"] for n, d in events if n == "token")


async def test_read_grant_does_not_allow_uploading(client, make_tenant, add_user):
    tenant = await make_tenant()
    compliance = (
        await client.post(
            "/collections",
            headers=tenant.headers,
            json={"name": "Compliance", "visibility": "restricted"},
        )
    ).json()
    member = await add_user(tenant, "member@example.com", "member")

    async def upload():
        return await client.post(
            "/documents",
            headers=member,
            data={"collection_id": compliance["id"]},
            files=[("files", ("policy.md", POLICY))],
        )

    assert (await upload()).status_code == 404  # can't even see it yet
    group = await grant_group_access(client, tenant, compliance["id"], member, "read")
    assert (await upload()).status_code == 403  # visible, but read-only

    await client.put(
        f"/collections/{compliance['id']}/access",
        headers=tenant.headers,
        json={"grants": [{"group_id": group["id"], "permission": "write"}]},
    )
    assert (await upload()).json()[0]["status"] == "created"


async def test_removing_a_member_removes_access(client, make_tenant, add_user):
    tenant = await make_tenant()
    compliance = (
        await client.post(
            "/collections",
            headers=tenant.headers,
            json={"name": "Compliance", "visibility": "restricted"},
        )
    ).json()
    await tenant.upload("policy.md", POLICY, collection_id=compliance["id"])
    member = await add_user(tenant, "leaver@example.com", "member")
    group = await grant_group_access(client, tenant, compliance["id"], member)
    assert (await client.post("/search", headers=member, json={"query": QUESTION})).json()[
        "results"
    ]

    user_id = (await client.get("/me", headers=member)).json()["user"]["id"]
    removed = await client.delete(
        f"/groups/{group['id']}/members/{user_id}", headers=tenant.headers
    )
    assert removed.status_code == 204
    assert (await client.post("/search", headers=member, json={"query": QUESTION})).json()[
        "results"
    ] == []


async def test_admin_endpoints_require_admin_and_stay_in_the_tenant(client, make_tenant, add_user):
    tenant_a = await make_tenant("Tenant A")
    tenant_b = await make_tenant("Tenant B")
    member = await add_user(tenant_a, "member@example.com", "member")

    for method, path, body in [
        ("post", "/users", {"email": "x@example.com", "password": "a-strong-password"}),
        ("post", "/groups", {"name": "Sneaky"}),
        ("get", "/users", None),
    ]:
        call = getattr(client, method)
        response = await call(path, headers=member, **({"json": body} if body else {}))
        assert response.status_code == 403, path

    # A new user always lands in the admin's own tenant.
    created = await client.post(
        "/users",
        headers=tenant_a.headers,
        json={"email": "new@example.com", "password": "a-strong-password", "role": "viewer"},
    )
    assert created.status_code == 201
    assert created.json()["role"] == "viewer"
    b_users = [u["email"] for u in (await client.get("/users", headers=tenant_b.headers)).json()]
    assert "new@example.com" not in b_users

    # Tenant B can't put Tenant A's user into its own group.
    group_b = (await client.post("/groups", headers=tenant_b.headers, json={"name": "B"})).json()
    stolen = await client.post(
        f"/groups/{group_b['id']}/members",
        headers=tenant_b.headers,
        json={"user_id": created.json()["id"]},
    )
    assert stolen.status_code == 404


async def test_deactivated_user_cannot_log_in(client, make_tenant, add_user):
    tenant = await make_tenant()
    await add_user(tenant, "temp@example.com", "member")
    users = (await client.get("/users", headers=tenant.headers)).json()
    temp = next(u for u in users if u["email"] == "temp@example.com")

    patched = await client.patch(
        f"/users/{temp['id']}", headers=tenant.headers, json={"is_active": False}
    )
    assert patched.status_code == 200 and patched.json()["is_active"] is False
    login = await client.post(
        "/auth/login",
        json={
            "tenant_slug": tenant.slug,
            "email": "temp@example.com",
            "password": "a-strong-password",
        },
    )
    assert login.status_code == 401


async def test_row_level_security_blocks_a_query_that_forgets_the_tenant(client, make_tenant):
    """The safety net: the database itself refuses to return another tenant's rows."""
    tenant_a = await make_tenant("Tenant A")
    tenant_b = await make_tenant("Tenant B")
    await tenant_a.upload("policy.md", POLICY)

    # A deliberately buggy query: no WHERE tenant_id anywhere.
    buggy = select(func.count()).select_from(Document)

    with tenant_scope(tenant_a.tenant_id):
        async with SessionLocal() as session:
            assert await session.scalar(buggy) == 1  # its own document only

    with tenant_scope(tenant_b.tenant_id):
        async with SessionLocal() as session:
            assert await session.scalar(buggy) == 0  # Tenant A's row is invisible
            assert await session.scalar(select(func.count()).select_from(Chunk)) == 0

    # No tenant set at all (e.g. a background job that forgot): RLS fails closed.
    with tenant_scope(None):
        async with SessionLocal() as session:
            assert await session.scalar(buggy) == 0

    # Admin tools opt out explicitly, and only then see everything.
    with maintenance_mode():
        async with SessionLocal() as session:
            assert await session.scalar(buggy) == 1


async def test_row_level_security_blocks_writing_into_another_tenant(client, make_tenant):
    tenant_a = await make_tenant("Tenant A")
    tenant_b = await make_tenant("Tenant B")
    document = (await tenant_a.upload("policy.md", POLICY))["document"]

    with tenant_scope(tenant_b.tenant_id):
        async with SessionLocal() as session:
            # Tenant B tries to change Tenant A's document: the row isn't there.
            result = await session.execute(
                text("UPDATE documents SET title = 'hacked' WHERE id = :id"),
                {"id": document["id"]},
            )
            await session.commit()
            assert result.rowcount == 0

    with maintenance_mode():
        async with SessionLocal() as session:
            title = await session.scalar(
                select(Document.title).where(Document.id == document["id"])
            )
    assert title != "hacked"
