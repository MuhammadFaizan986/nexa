"""
Admin endpoints the web UI needs: usage/cost reporting and tenant settings.

Usage answers "what did this month cost, and what is being asked?" — the
numbers you show a client when quoting a maintenance or hosting package.
Tenant settings let each client name their assistant, set its tone and pick a
model, without touching code or the shared .env.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import Date, cast, func, select

from app.core.deps import AdminUser, CurrentUser, SessionDep
from app.db.models import (
    Chunk,
    Conversation,
    Document,
    Message,
    MessageRole,
    Tenant,
    UsageEvent,
    User,
)
from app.schemas.admin import (
    FeedbackRequest,
    TenantSettings,
    TenantSettingsUpdate,
    UsageDay,
    UsageReport,
    UsageTotals,
)

router = APIRouter(tags=["admin"])


@router.post("/messages/{message_id}/feedback", status_code=204)
async def set_feedback(
    message_id: uuid.UUID, body: FeedbackRequest, user: CurrentUser, session: SessionDep
) -> None:
    """
    Thumbs up/down on an answer (1 / -1, or 0 to clear it).

    Feedback is the cheapest quality signal you get from real users: filter
    messages by `feedback = -1` to find the questions worth investigating, and
    the stored citations and scores show why the answer went wrong.
    """
    message = await session.get(Message, message_id)
    conversation = await session.get(Conversation, message.conversation_id) if message else None
    if (
        message is None
        or conversation is None
        or conversation.tenant_id != user.tenant_id
        or conversation.user_id != user.id
        or message.role != MessageRole.ASSISTANT
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")

    message.feedback = body.value
    await session.commit()


@router.get("/admin/usage", response_model=UsageReport)
async def usage_report(
    admin: AdminUser, session: SessionDep, days: int = Query(default=30, ge=1, le=365)
) -> UsageReport:
    """Tokens, cost and questions per day for this tenant, plus current totals."""
    since = datetime.now(UTC) - timedelta(days=days - 1)

    # AI spend per day and per kind of call (embedding, chat, reranking...).
    usage_rows = await session.execute(
        select(
            cast(UsageEvent.created_at, Date).label("day"),
            UsageEvent.event_type,
            func.sum(UsageEvent.tokens),
            func.sum(UsageEvent.cost_usd),
        )
        .where(UsageEvent.tenant_id == admin.tenant_id, UsageEvent.created_at >= since)
        .group_by("day", UsageEvent.event_type)
    )
    # Questions answered per day.
    question_rows = await session.execute(
        select(cast(Message.created_at, Date).label("day"), func.count())
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Conversation.tenant_id == admin.tenant_id,
            Message.role == MessageRole.ASSISTANT,
            Message.created_at >= since,
        )
        .group_by("day")
    )

    per_day: dict[date, UsageDay] = {}

    def day_entry(day: date) -> UsageDay:
        return per_day.setdefault(day, UsageDay(date=day))

    for day, event_type, tokens, cost in usage_rows:
        entry = day_entry(day)
        entry.tokens += int(tokens or 0)
        entry.cost_usd += float(cost or 0)
        entry.by_type[event_type] = entry.by_type.get(event_type, 0.0) + float(cost or 0)
    for day, questions in question_rows:
        day_entry(day).questions += int(questions)

    counts = (
        await session.execute(
            select(
                select(func.count())
                .select_from(Document)
                .where(Document.tenant_id == admin.tenant_id)
                .scalar_subquery(),
                select(func.count())
                .select_from(Chunk)
                .where(Chunk.tenant_id == admin.tenant_id)
                .scalar_subquery(),
                select(func.count())
                .select_from(User)
                .where(User.tenant_id == admin.tenant_id)
                .scalar_subquery(),
            )
        )
    ).one()

    days_sorted = sorted(per_day.values(), key=lambda d: d.date)
    return UsageReport(
        days=days_sorted,
        totals=UsageTotals(
            questions=sum(d.questions for d in days_sorted),
            tokens=sum(d.tokens for d in days_sorted),
            cost_usd=round(sum(d.cost_usd for d in days_sorted), 6),
            documents=counts[0],
            chunks=counts[1],
            users=counts[2],
        ),
    )


@router.get("/tenants/me", response_model=TenantSettings)
async def get_tenant_settings(user: CurrentUser, session: SessionDep) -> TenantSettings:
    tenant = await session.get(Tenant, user.tenant_id)
    return TenantSettings(name=tenant.name, **tenant.settings)


@router.patch("/tenants/me", response_model=TenantSettings)
async def update_tenant_settings(
    body: TenantSettingsUpdate, admin: AdminUser, session: SessionDep
) -> TenantSettings:
    """
    Per-client settings: what the assistant is called, how it should sound, and
    which model answers. They're stored on the tenant and used when building the
    prompt (services/generation/prompts.py).
    """
    tenant = await session.get(Tenant, admin.tenant_id)
    if body.name:
        tenant.name = body.name.strip()
    settings = dict(tenant.settings)
    for field, value in body.model_dump(exclude_none=True, exclude={"name"}).items():
        settings[field] = value
    # SQLAlchemy only notices a JSONB change when the attribute is reassigned.
    tenant.settings = settings
    await session.commit()
    await session.refresh(tenant)
    return TenantSettings(name=tenant.name, **tenant.settings)
