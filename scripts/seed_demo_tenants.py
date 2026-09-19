"""
Seed the three demo tenants (Property, Fintech, Company) from sample_data/.

Run it inside the api container while the stack is up:

    docker compose exec api python /scripts/seed_demo_tenants.py

It uses the public HTTP API exactly like a real client would, so it doubles as
an end-to-end smoke test of register -> login -> upload -> ingest.

Safe to run repeatedly: an existing tenant is logged into instead of
re-registered, and files already uploaded come back as "duplicate".

All sample documents are synthetic (fictional companies). With the OpenAI
embedding provider, seeding all 16 documents costs well under one US cent.
"""

import argparse
import sys
from pathlib import Path

import httpx

SAMPLE_DATA = Path("/sample_data")
DEMO_PASSWORD = "nexa-demo-password"

TENANTS = {
    # domain folder -> (organisation name, owner email)
    "property": ("Harbourview Property Group", "owner@harbourview.example.com"),
    "fintech": ("Kestrel Pay", "owner@kestrelpay.example.com"),
    "company": ("Lumen Labs", "owner@lumenlabs.example.com"),
}
SUPPORTED = {".pdf", ".docx", ".md", ".txt"}


def authenticate(client: httpx.Client, name: str, email: str) -> tuple[str, str]:
    """Register the tenant, or log in if it already exists. Returns (slug, access token)."""
    response = client.post(
        "/auth/register",
        json={"tenant_name": name, "email": email, "password": DEMO_PASSWORD},
    )
    if response.status_code == 201:
        body = response.json()
        return body["tenant"]["slug"], body["tokens"]["access_token"]
    if response.status_code != 409:
        response.raise_for_status()

    slug = name.lower().replace(" ", "-")
    response = client.post(
        "/auth/login", json={"tenant_slug": slug, "email": email, "password": DEMO_PASSWORD}
    )
    response.raise_for_status()
    return slug, response.json()["access_token"]


def seed(client: httpx.Client, domain: str, name: str, email: str) -> bool:
    slug, token = authenticate(client, name, email)
    headers = {"Authorization": f"Bearer {token}"}
    collections = client.get("/collections", headers=headers).json()
    general = next(c for c in collections if c["name"] == "General")

    files = sorted(p for p in (SAMPLE_DATA / domain).iterdir() if p.suffix in SUPPORTED)
    print(f"\n== {name}  (tenant slug: {slug})  — uploading {len(files)} files")
    handles = [open(p, "rb") for p in files]  # noqa: SIM115 (closed below)
    try:
        response = client.post(
            "/documents",
            headers=headers,
            data={"collection_id": general["id"], "metadata": f'{{"domain": "{domain}"}}'},
            files=[("files", (p.name, h)) for p, h in zip(files, handles, strict=True)],
        )
    finally:
        for h in handles:
            h.close()
    response.raise_for_status()

    ok = True
    for result in response.json():
        document = result.get("document") or {}
        state = document.get("status", "-")
        line = f"  {result['status']:<9} {state:<7} {result['filename']}"
        if document.get("page_count"):
            line += f"  ({document['page_count']} pages)"
        if state == "failed":
            ok = False
            line += f"\n            error: {document.get('error_message')}"
        if result["status"] == "rejected":
            ok = False
            line += f"\n            {result.get('detail')}"
        print(line)
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base-url", default="http://localhost:8000/api/v1")
    args = parser.parse_args()

    # Ingestion runs inside the upload request until Week 4, so allow a long timeout.
    with httpx.Client(base_url=args.base_url, timeout=600) as client:
        # A list (not a generator) so every tenant is seeded even if one reports a problem.
        results = [seed(client, domain, *TENANTS[domain]) for domain in TENANTS]
        all_ok = all(results)

    print(f"\nDemo logins (password for all: {DEMO_PASSWORD}):")
    for name, email in TENANTS.values():
        print(f"  {name:<28} tenant_slug={name.lower().replace(' ', '-'):<28} email={email}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
