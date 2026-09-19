"""Small helpers shared by the DB tests."""

import json


def parse_sse(body: str) -> list[tuple[str, dict]]:
    """Turn an SSE response body into [(event_name, data_dict), ...]."""
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        events.append((lines["event"], json.loads(lines["data"])))
    return events
