"""
"Someone is looking at your demo" emails.

When the demo link goes out on LinkedIn or into an application, it is useful to
know the moment somebody opens it — and, more usefully, where they came from.

What this can honestly tell you:

    when · which page · the referrer (so: which post worked) · rough country
    · phone or laptop, which browser

What it cannot tell you is who they are. IP addresses do not carry names, and
the services that claim otherwise are guessing from corporate networks. A
recruiter reading on their phone is anonymous, and no amount of tooling changes
that.

Two protections, because the endpoint that triggers this is public and
unauthenticated:

- a cap on messages per hour, so nobody can use it to flood an inbox;
- the visitor's IP is used to look up a country and then dropped — it is never
  logged or stored.
"""

import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

RESEND_URL = "https://api.resend.com/emails"
GEO_URL = "https://ipapi.co/{ip}/json/"

# Timestamps of messages sent in the last hour. In-process, which is exactly
# right here: the cap exists to protect an inbox, and one API container is all
# this deployment runs. With several containers each would keep its own count.
_sent: deque[float] = deque()


@dataclass
class Visit:
    """What we know about one arrival."""

    path: str
    referrer: str | None
    user_agent: str | None
    ip: str | None = None
    country: str | None = None

    @property
    def source(self) -> str:
        """Where they came from, in words — the single most useful field."""
        if not self.referrer:
            return "direct or unknown"
        for name, needle in (
            ("LinkedIn", "linkedin"),
            ("GitHub", "github"),
            ("Google", "google"),
            ("X/Twitter", "t.co"),
            ("Facebook", "facebook"),
            ("WhatsApp", "whatsapp"),
        ):
            if needle in self.referrer.lower():
                return name
        return self.referrer

    @property
    def device(self) -> str:
        """A readable one-liner instead of a 200-character user agent."""
        agent = (self.user_agent or "").lower()
        if not agent:
            return "unknown"
        platform = (
            "iPhone"
            if "iphone" in agent
            else "iPad"
            if "ipad" in agent
            else "Android"
            if "android" in agent
            else "Mac"
            if "macintosh" in agent
            else "Windows"
            if "windows" in agent
            else "Linux"
            if "linux" in agent
            else "unknown device"
        )
        browser = (
            "Edge"
            if "edg/" in agent
            else "Chrome"
            if "chrome" in agent
            else "Safari"
            if "safari" in agent
            else "Firefox"
            if "firefox" in agent
            else "browser"
        )
        return f"{platform} · {browser}"


def _within_hourly_cap() -> bool:
    """True if we may send one more message this hour (and counts it)."""
    settings = get_settings()
    cutoff = time.monotonic() - 3600
    while _sent and _sent[0] < cutoff:
        _sent.popleft()
    if len(_sent) >= settings.notify_max_per_hour:
        return False
    _sent.append(time.monotonic())
    return True


async def _country_of(ip: str | None) -> str | None:
    """
    Best-effort country from the IP, which is then discarded.

    Never raises and never delays anything a user is waiting for: this runs in
    a background task, with a short timeout, and a failure simply means the
    email says "unknown".
    """
    if not ip or not get_settings().visit_geo_lookup:
        return None
    # Private and loopback addresses (local development) have no country.
    if ip.startswith(("10.", "192.168.", "172.16.", "127.", "::1")):
        return None
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(GEO_URL.format(ip=ip))
            data = response.json()
        parts = [data.get("city"), data.get("country_name")]
        return ", ".join(p for p in parts if p) or None
    except Exception:  # geography is a nicety, never a reason to fail
        return None


def _body(visit: Visit, country: str | None) -> tuple[str, str]:
    when = datetime.now(UTC).strftime("%d %b %Y, %H:%M UTC")
    subject = f"NEXA demo: a visitor from {visit.source}"
    lines = [
        "Someone just opened your NEXA demo.",
        "",
        f"When:      {when}",
        f"Page:      {visit.path}",
        f"Came from: {visit.source}",
        f"Where:     {country or 'unknown'}",
        f"Device:    {visit.device}",
        "",
        "Their IP was used to look up the location and then discarded.",
    ]
    return subject, "\n".join(lines)


async def notify_visit(visit: Visit) -> None:
    """
    Send the email. Never raises — a notification must not break a page load.

    Called as a background task, so the visitor's request has already been
    answered by the time any of this runs.
    """
    settings = get_settings()
    if not (settings.resend_api_key and settings.notify_email_to):
        return  # not configured: nothing to do, and nothing to warn about

    if not _within_hourly_cap():
        log.info("notify.throttled", path=visit.path)
        return

    country = await _country_of(visit.ip)
    subject, text = _body(visit, country)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                RESEND_URL,
                headers={"Authorization": f"Bearer {settings.resend_api_key.get_secret_value()}"},
                json={
                    "from": settings.notify_email_from,
                    "to": [settings.notify_email_to],
                    "subject": subject,
                    "text": text,
                },
            )
        if response.status_code >= 400:
            log.warning("notify.rejected", status=response.status_code, body=response.text[:200])
        else:
            log.info("notify.sent", source=visit.source, country=country)
    except Exception as exc:
        log.warning("notify.failed", error_type=type(exc).__name__)
