async def test_register_login_refresh_me(client, make_tenant):
    tenant = await make_tenant("Harbourview Property Group")
    assert tenant.slug == "harbourview-property-group"

    me = await client.get("/me", headers=tenant.headers)
    assert me.status_code == 200
    assert me.json()["user"]["role"] == "owner"
    assert me.json()["tenant"]["slug"] == tenant.slug

    login = await client.post(
        "/auth/login",
        json={
            "tenant_slug": tenant.slug,
            "email": "OWNER@example.com",  # emails are case-insensitive
            "password": "a-strong-password",
        },
    )
    assert login.status_code == 200

    refreshed = await client.post("/auth/refresh", json={"refresh_token": tenant.refresh_token})
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"]


async def test_register_creates_default_tenant_wide_collection(client, make_tenant):
    tenant = await make_tenant()
    collections = (await client.get("/collections", headers=tenant.headers)).json()
    assert [(c["name"], c["visibility"]) for c in collections] == [("General", "tenant_wide")]


async def test_duplicate_organisation_is_rejected(client, make_tenant):
    await make_tenant("Acme Corp")
    response = await client.post(
        "/auth/register",
        json={
            "tenant_name": "Acme Corp",
            "email": "x@example.com",
            "password": "a-strong-password",
        },
    )
    assert response.status_code == 409


async def test_bad_logins_all_look_the_same(client, make_tenant):
    tenant = await make_tenant()
    attempts = [
        {"tenant_slug": tenant.slug, "email": "owner@example.com", "password": "wrong-password"},
        {
            "tenant_slug": tenant.slug,
            "email": "nobody@example.com",
            "password": "a-strong-password",
        },
        {
            "tenant_slug": "no-such-org",
            "email": "owner@example.com",
            "password": "a-strong-password",
        },
    ]
    for body in attempts:
        response = await client.post("/auth/login", json=body)
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid credentials"}


async def test_protected_endpoints_need_a_valid_access_token(client, make_tenant):
    tenant = await make_tenant()
    assert (await client.get("/me")).status_code == 401
    assert (await client.get("/me", headers={"Authorization": "Bearer garbage"})).status_code == 401
    # A refresh token must not work as an access token.
    as_access = {"Authorization": f"Bearer {tenant.refresh_token}"}
    assert (await client.get("/me", headers=as_access)).status_code == 401


async def test_only_admins_create_collections(client, make_tenant, add_user):
    tenant = await make_tenant()
    member = await add_user(tenant, "member@example.com", "member")
    body = {"name": "Compliance", "visibility": "restricted"}
    assert (await client.post("/collections", json=body, headers=member)).status_code == 403
    assert (await client.post("/collections", json=body, headers=tenant.headers)).status_code == 201
    duplicate = await client.post("/collections", json=body, headers=tenant.headers)
    assert duplicate.status_code == 409


async def test_health(client):
    response = await client.get("/health")
    assert response.json() == {"status": "ok", "database": "ok"}
