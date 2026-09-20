"""
Seed the three demo tenants (Property, Fintech, Company) from sample_data/.

Run it inside the api container while the stack is up:

    docker compose exec api python /scripts/seed_demo_tenants.py

It uses the public HTTP API exactly like a real client would, so it doubles as
an end-to-end smoke test of register -> login -> upload -> ingest.

Safe to run repeatedly: an existing tenant is logged into instead of
re-registered, and files already uploaded come back as "duplicate" (their
metadata is updated if it changed, so metadata filters always have data).

All sample documents are synthetic (fictional companies). With the OpenAI
embedding provider, seeding all 16 documents costs well under one US cent.
"""

import argparse
import json
import sys
import time
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

# Per-document metadata, used by metadata filters, e.g. {"doc_type": "lease"} or
# a date range on "date" (ISO format). A real client would send this at upload.
DOC_METADATA: dict[str, dict] = {
    # property
    "building_c_rules.md": {"doc_type": "house_rules", "building": "C"},
    "inspection_reports_2026.pdf": {"doc_type": "inspection_report"},
    "lease_unit_4b.pdf": {"doc_type": "lease", "unit": "4B", "building": "A", "date": "2026-03-01"},
    "lease_unit_7a.pdf": {"doc_type": "lease", "unit": "7A", "building": "C", "date": "2025-11-01"},
    "maintenance_policy.docx": {"doc_type": "policy"},
    "tenant_welcome_guide.txt": {"doc_type": "guide"},
    # fintech
    "customer_faq.md": {"doc_type": "faq", "audience": "customer"},
    "fee_schedule.pdf": {"doc_type": "fee_schedule", "audience": "customer"},
    "internal_chargeback_procedure.txt": {"doc_type": "procedure", "audience": "internal"},
    "kyc_aml_policy.docx": {"doc_type": "policy", "audience": "compliance"},
    "payment_error_codes.md": {"doc_type": "reference", "audience": "customer"},
    # company
    "api_documentation.md": {"doc_type": "technical_docs"},
    "employee_handbook.pdf": {"doc_type": "hr_policy"},
    "it_security_policy.docx": {"doc_type": "policy"},
    "onboarding_sop.txt": {"doc_type": "sop"},
    "release_notes.md": {"doc_type": "release_notes", "date": "2026-08-12"},
}


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
    ok = True
    # One request per file, so each document gets its own metadata.
    for path in files:
        metadata = {"domain": domain, **DOC_METADATA.get(path.name, {})}
        with path.open("rb") as handle:
            response = client.post(
                "/documents",
                headers=headers,
                data={"collection_id": general["id"], "metadata": json.dumps(metadata)},
                files=[("files", (path.name, handle))],
            )
        response.raise_for_status()
        result = response.json()[0]
        document = result.get("document") or {}

        # Already uploaded earlier: make sure its metadata is current.
        if result["status"] == "duplicate" and document and document["metadata"] != metadata:
            patched = client.patch(
                f"/documents/{document['id']}", headers=headers, json={"metadata": metadata}
            )
            patched.raise_for_status()
            result["status"] = "updated"

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
    return wait_until_processed(client, headers) and ok


def wait_until_processed(client: httpx.Client, headers: dict, timeout: int = 900) -> bool:
    """
    With INGESTION_MODE=celery the upload only queues the work, so wait for the
    worker to finish before declaring the tenant seeded.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        documents = client.get("/documents", headers=headers).json()
        busy = [d for d in documents if d["status"] in ("pending", "processing")]
        if not busy:
            failed = [d for d in documents if d["status"] == "failed"]
            for document in failed:
                print(f"  failed    {document['filename']}: {document['error_message']}")
            return not failed
        print(f"  ... waiting for the worker: {len(busy)} document(s) still processing")
        time.sleep(3)
    print("  timed out waiting for the worker (is `docker compose up worker` running?)")
    return False


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
