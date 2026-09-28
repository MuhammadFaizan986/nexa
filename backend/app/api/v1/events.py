"""
Front-of-house events: someone opened the demo.

This is the only unauthenticated write endpoint in the API, which makes it the
one most worth being careful with. Three things keep it boring:

- it stores nothing and returns nothing;
- the work happens in a background task, so the response is immediate and a
  slow email can't hold up a page load;
- one visitor can trigger at most one notification per session (the browser
  sends a session id it generates once), and the sender caps messages per hour
  regardless (services/notify.py).
"""

from fastapi import APIRouter, BackgroundTasks, Request, status
from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.services.notify import Visit, notify_visit

log = get_logger(__name__)
router = APIRouter(tags=["events"])

# Session ids we've already notified about, newest last. A plain set with a cap
# is enough: this only needs to stop a page refresh from sending a second
# email, and it can forget everything on restart without harm.
_seen_sessions: list[str] = []
_MAX_REMEMBERED = 2000


class VisitIn(BaseModel):
    path: str = Field(default="/", max_length=200)
    referrer: str | None = Field(default=None, max_length=500)
    session_id: str = Field(max_length=64)


@router.post("/events/visit", status_code=status.HTTP_204_NO_CONTENT)
async def record_visit(body: VisitIn, request: Request, background: BackgroundTasks) -> None:
    """Tell the owner that somebody opened the demo."""
    if body.session_id in _seen_sessions:
        return
    _seen_sessions.append(body.session_id)
    del _seen_sessions[:-_MAX_REMEMBERED]

    # Behind Railway's proxy the visitor's address is the first entry of
    # X-Forwarded-For; request.client would be the proxy itself.
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() or (request.client.host if request.client else None)

    visit = Visit(
        path=body.path,
        referrer=body.referrer,
        user_agent=request.headers.get("user-agent"),
        ip=ip,
    )
    log.info("visit", path=visit.path, source=visit.source, device=visit.device)
    background.add_task(notify_visit, visit)
