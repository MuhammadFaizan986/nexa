"""
The visit notification endpoint (Week 8).

This is the only unauthenticated write endpoint in the API, so what is tested
here is mostly what it refuses to do: notify twice for one session, send
anything when it isn't configured, or send without limit.
"""

import app.api.v1.events as events
from app.core.config import get_settings
from app.services import notify
from app.services.notify import Visit


def visit_body(session_id: str = "session-1", **overrides) -> dict:
    return {
        "path": "/",
        "referrer": "https://www.linkedin.com/feed/",
        "session_id": session_id,
        **overrides,
    }


async def test_a_visit_is_accepted_and_returns_nothing(client, monkeypatch):
    sent: list[Visit] = []

    async def fake_notify(visit):
        sent.append(visit)

    monkeypatch.setattr(events, "notify_visit", fake_notify)
    events._seen_sessions.clear()

    response = await client.post("/events/visit", json=visit_body())

    assert response.status_code == 204
    assert response.content == b""
    assert len(sent) == 1
    assert sent[0].source == "LinkedIn"  # the referrer is the point of all this


async def test_the_same_session_only_notifies_once(client, monkeypatch):
    """A refresh, or clicking into the app, is the same person arriving once."""
    sent = []

    async def fake_notify(visit):
        sent.append(visit)

    monkeypatch.setattr(events, "notify_visit", fake_notify)
    events._seen_sessions.clear()

    await client.post("/events/visit", json=visit_body("session-repeat"))
    await client.post("/events/visit", json=visit_body("session-repeat", path="/login"))
    await client.post("/events/visit", json=visit_body("session-other"))

    assert len(sent) == 2  # two sessions, not three requests


async def test_nothing_is_sent_when_notifications_are_not_configured(monkeypatch):
    """No key, no destination, no surprises — and no warning noise either."""
    monkeypatch.setattr(get_settings(), "resend_api_key", None)
    monkeypatch.setattr(get_settings(), "notify_email_to", None)

    called = False

    async def fail(*args, **kwargs):  # pragma: no cover - must not run
        nonlocal called
        called = True

    monkeypatch.setattr(notify.httpx, "AsyncClient", fail)
    await notify.notify_visit(Visit(path="/", referrer=None, user_agent=None))

    assert not called


def test_the_hourly_cap_stops_an_inbox_being_flooded(monkeypatch):
    monkeypatch.setattr(get_settings(), "notify_max_per_hour", 3)
    notify._sent.clear()

    allowed = [notify._within_hourly_cap() for _ in range(5)]

    assert allowed == [True, True, True, False, False]


def test_the_referrer_is_turned_into_something_readable():
    assert Visit("/", "https://www.linkedin.com/feed/", None).source == "LinkedIn"
    assert Visit("/", "https://github.com/someone", None).source == "GitHub"
    assert Visit("/", None, None).source == "direct or unknown"
    assert Visit("/", "https://example.com/blog", None).source == "https://example.com/blog"


def test_the_user_agent_is_summarised():
    iphone = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    )
    mac_chrome = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
    )

    assert Visit("/", None, iphone).device == "iPhone · Safari"
    assert Visit("/", None, mac_chrome).device == "Mac · Chrome"
    assert Visit("/", None, None).device == "unknown"
