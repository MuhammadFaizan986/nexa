"""
Ask NEXA a question from the terminal and watch the answer stream in.

    docker compose exec api python /scripts/ask.py --tenant property \
        "What is the notice period for terminating the lease for Unit 4B?"

    # follow-up in the same conversation:
    docker compose exec api python /scripts/ask.py --tenant property \
        --conversation <id printed by the previous run> "And for Unit 7A?"

--tenant accepts a demo shortcut (property | fintech | company, created by
seed_demo_tenants.py) or any tenant slug together with --email/--password.

This is also a readable example of consuming the Server-Sent Events stream:
read the response line by line; "event:" names the event, "data:" carries JSON,
and a blank line ends one event.
"""

import argparse
import json
import sys

import httpx

DEMO = {
    "property": ("harbourview-property-group", "owner@harbourview.example.com"),
    "fintech": ("kestrel-pay", "owner@kestrelpay.example.com"),
    "company": ("lumen-labs", "owner@lumenlabs.example.com"),
}


def login(client: httpx.Client, slug: str, email: str, password: str) -> str:
    response = client.post(
        "/auth/login", json={"tenant_slug": slug, "email": email, "password": password}
    )
    if response.status_code != 200:
        sys.exit(f"Login failed ({response.status_code}): {response.text}")
    return response.json()["access_token"]


def stream_events(response: httpx.Response):
    """Yield (event, data) pairs from an SSE response as they arrive."""
    event, data = None, None
    for line in response.iter_lines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            data = json.loads(line[len("data: ") :])
        elif line == "" and event:
            yield event, data
            event, data = None, None


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask NEXA a question (streaming).")
    parser.add_argument("question")
    parser.add_argument("--tenant", default="property", help="demo name or tenant slug")
    parser.add_argument("--email")
    parser.add_argument("--password", default="nexa-demo-password")
    parser.add_argument("--conversation", help="continue an existing conversation id")
    parser.add_argument("--base-url", default="http://localhost:8000/api/v1")
    args = parser.parse_args()

    slug, email = DEMO.get(args.tenant, (args.tenant, args.email))
    if not email:
        sys.exit("--email is required for non-demo tenants")

    with httpx.Client(base_url=args.base_url, timeout=300) as client:
        token = login(client, slug, args.email or email, args.password)
        body = {"question": args.question}
        if args.conversation:
            body["conversation_id"] = args.conversation

        with client.stream(
            "POST", "/chat", json=body, headers={"Authorization": f"Bearer {token}"}
        ) as response:
            if response.status_code != 200:
                response.read()
                sys.exit(f"Error {response.status_code}: {response.text}")

            print(f"\nQ: {args.question}\nA: ", end="", flush=True)
            for event, data in stream_events(response):
                if event == "meta":
                    conversation_id = data["conversation_id"]
                elif event == "token":
                    print(data["text"], end="", flush=True)
                elif event == "error":
                    print(f"\n\n[error] {data['detail']}")
                    return 1
                elif event == "done":
                    print_footer(data, conversation_id)
    return 0


def print_footer(done: dict, conversation_id: str) -> None:
    print("\n")
    for c in done["citations"]:
        where = " · ".join(
            part
            for part in (
                c["page_start"] and f"page {c['page_start']}",
                c["section_title"],
            )
            if part
        )
        print(f"  [{c['number']}] {c['document_title']} — {where}")
        print(f'      "{c["snippet"]}"')
    if done["invalid_citations"]:
        print(f"  (dropped invalid citation numbers: {done['invalid_citations']})")
    usage, latency = done["usage"], done["latency_ms"]
    cost = f"${float(usage['cost_usd']):.5f}" if usage.get("cost_usd") is not None else "n/a"
    print(
        f"\n  model={done['model'] or '(no LLM call)'}  "
        f"tokens in/out={usage['input_tokens']}/{usage['output_tokens']}  cost={cost}"
    )
    print("  latency ms: " + ", ".join(f"{k}={v}" for k, v in latency.items()))
    print(f"  conversation: {conversation_id}")


if __name__ == "__main__":
    sys.exit(main())
