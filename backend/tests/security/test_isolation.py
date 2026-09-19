"""
Security tests: tenant isolation and collection permissions.

Plan section 9.3: "a user from Tenant B searches for a unique phrase that exists
only in Tenant A -> must return zero results. Same for restricted collections
within a tenant." These tests must never be deleted or weakened. Week 4 extends
them with groups, Row-Level Security and file downloads.
"""

from tests.helpers import parse_sse

SECRET_DOC = b"""# Project Zebra Cobalt

The zebra cobalt merger closes on 14 November. The offer price is 41 dollars per share.
"""
QUESTION = "When does the zebra cobalt merger close and what is the offer price?"


async def test_other_tenant_cannot_find_or_open_documents(client, make_tenant):
    tenant_a = await make_tenant("Tenant A")
    tenant_b = await make_tenant("Tenant B")
    document = (await tenant_a.upload("zebra.md", SECRET_DOC))["document"]

    # Tenant A finds it...
    hits_a = (
        await client.post("/search", headers=tenant_a.headers, json={"query": QUESTION})
    ).json()
    assert hits_a["results"]

    # ...Tenant B finds nothing, can't list it and can't open it by id.
    hits_b = (
        await client.post("/search", headers=tenant_b.headers, json={"query": QUESTION})
    ).json()
    assert hits_b["results"] == []
    assert (await client.get("/documents", headers=tenant_b.headers)).json() == []
    opened = await client.get(f"/documents/{document['id']}", headers=tenant_b.headers)
    assert opened.status_code == 404

    # Chat can't leak it either.
    events = parse_sse(
        (await client.post("/chat", headers=tenant_b.headers, json={"question": QUESTION})).text
    )
    answer = "".join(d["text"] for name, d in events if name == "token")
    assert "41" not in answer and events[-1][1]["no_answer"] is True


async def test_other_tenant_cannot_target_foreign_collection_or_conversation(client, make_tenant):
    tenant_a = await make_tenant("Tenant A")
    tenant_b = await make_tenant("Tenant B")
    await tenant_a.upload("zebra.md", SECRET_DOC)
    foreign_collection = [str(tenant_a.default_collection_id)]

    for path in ("/search", "/chat"):
        body = {"query": QUESTION, "question": QUESTION, "collection_ids": foreign_collection}
        response = await client.post(path, headers=tenant_b.headers, json=body)
        assert response.status_code == 404, path

    upload = await client.post(
        "/documents",
        headers=tenant_b.headers,
        data={"collection_id": foreign_collection[0]},
        files=[("files", ("x.md", b"# hi\n\nsome text"))],
    )
    assert upload.status_code == 404

    events = parse_sse(
        (await client.post("/chat", headers=tenant_a.headers, json={"question": QUESTION})).text
    )
    conversation_id = events[0][1]["conversation_id"]
    stolen = await client.get(f"/conversations/{conversation_id}", headers=tenant_b.headers)
    assert stolen.status_code == 404
    follow_up = await client.post(
        "/chat",
        headers=tenant_b.headers,
        json={"question": "and?", "conversation_id": conversation_id},
    )
    assert follow_up.status_code == 404


async def test_restricted_collection_is_hidden_from_members(client, make_tenant, add_user):
    tenant = await make_tenant()
    member = await add_user(tenant, "member@example.com", "member")
    compliance = (
        await client.post(
            "/collections",
            headers=tenant.headers,
            json={"name": "Compliance", "visibility": "restricted"},
        )
    ).json()
    document = (await tenant.upload("zebra.md", SECRET_DOC, collection_id=compliance["id"]))[
        "document"
    ]

    # The owner (admin) sees it.
    owner_hits = await client.post("/search", headers=tenant.headers, json={"query": QUESTION})
    assert owner_hits.json()["results"]

    # A member of the same tenant does not — not in search, lists, by id, or by chat.
    member_hits = await client.post("/search", headers=member, json={"query": QUESTION})
    assert member_hits.json()["results"] == []
    names = [c["name"] for c in (await client.get("/collections", headers=member)).json()]
    assert names == ["General"]
    assert (await client.get(f"/documents/{document['id']}", headers=member)).status_code == 404
    targeted = await client.post(
        "/search", headers=member, json={"query": QUESTION, "collection_ids": [compliance["id"]]}
    )
    assert targeted.status_code == 404
    events = parse_sse(
        (await client.post("/chat", headers=member, json={"question": QUESTION})).text
    )
    assert events[-1][1]["no_answer"] is True


async def test_upload_permissions_by_role(client, make_tenant, add_user):
    tenant = await make_tenant()
    member = await add_user(tenant, "member@example.com", "member")
    viewer = await add_user(tenant, "viewer@example.com", "viewer")

    def upload_as(headers):
        return client.post(
            "/documents",
            headers=headers,
            data={"collection_id": str(tenant.default_collection_id)},
            files=[("files", ("notes.md", b"# Notes\n\nTeam notes."))],
        )

    assert (await upload_as(viewer)).status_code == 403
    member_upload = await upload_as(member)
    assert member_upload.status_code == 200
    assert member_upload.json()[0]["status"] == "created"


async def test_duplicate_upload_reveals_nothing_about_a_hidden_document(
    client, make_tenant, add_user
):
    tenant = await make_tenant()
    member = await add_user(tenant, "member@example.com", "member")
    compliance = (
        await client.post(
            "/collections",
            headers=tenant.headers,
            json={"name": "Compliance", "visibility": "restricted"},
        )
    ).json()
    await tenant.upload("zebra.md", SECRET_DOC, collection_id=compliance["id"])

    # The member uploads the identical file to a collection they CAN write to.
    response = await client.post(
        "/documents",
        headers=member,
        data={"collection_id": str(tenant.default_collection_id)},
        files=[("files", ("copy.md", SECRET_DOC))],
    )
    result = response.json()[0]
    assert result["status"] == "duplicate"
    assert result["document"] is None  # no title, id or collection of the hidden doc
