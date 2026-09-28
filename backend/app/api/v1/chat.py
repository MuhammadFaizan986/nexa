"""
Chat (streaming answers) and conversation history.

POST /chat does the quick, validating work here — permission scope, find or
create the conversation, load history, save the user's message — then hands off
to services/chat.stream_answer(), which streams the answer as Server-Sent Events.

Try it with curl (-N disables buffering so tokens appear as they arrive):

    curl -N -X POST http://localhost:8010/api/v1/chat \\
      -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \\
      -d '{"question": "What is the notice period for terminating the lease for Unit 4B?"}'
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.deps import CurrentUser, SessionDep
from app.db.models import (
    Conversation,
    Document,
    Message,
    MessageCitation,
    MessageRole,
    Tenant,
    UsageEvent,
    UsageEventType,
)
from app.schemas.chat import (
    ChatRequest,
    CitationOut,
    ConversationDetail,
    ConversationOut,
    MessageOut,
)
from app.services.chat import ChatTurn, stream_answer
from app.services.retrieval.permissions import CollectionAccessError, resolve_search_scope

router = APIRouter(tags=["chat"])


@router.post(
    "/chat",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def chat(body: ChatRequest, user: CurrentUser, session: SessionDep) -> StreamingResponse:
    settings = get_settings()
    await _enforce_daily_limit(session, user.tenant_id, settings.max_questions_per_day)
    try:
        collection_ids = await resolve_search_scope(session, user, body.collection_ids)
    except CollectionAccessError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Collection not found") from None

    if body.conversation_id:
        conversation = await _get_own_conversation(session, user, body.conversation_id)
        history = await _recent_messages(session, conversation.id, settings.history_messages)
    else:
        conversation = Conversation(
            id=uuid.uuid4(),
            tenant_id=user.tenant_id,
            user_id=user.id,
            title=body.question.strip()[:80],
        )
        session.add(conversation)
        # Flush now: SQLAlchemy orders INSERTs by relationship(), not bare foreign
        # keys, and the message below references this conversation.
        await session.flush()
        history = []

    question = Message(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content=body.question.strip(),
    )
    session.add(question)
    await session.commit()  # the question is saved even if answering fails later

    tenant = await session.get(Tenant, user.tenant_id)
    turn = ChatTurn(
        tenant_id=user.tenant_id,
        tenant_name=tenant.name,
        user_id=user.id,
        conversation_id=conversation.id,
        user_message_id=question.id,
        question=question.content,
        history=history,
        collection_ids=collection_ids,
        filters=body.to_filters(),
        assistant_name=tenant.settings.get("assistant_name"),
        tone=tenant.settings.get("tone"),
        model=tenant.settings.get("model"),
        agent=body.agent if body.agent is not None else get_settings().agent_default,
    )
    return StreamingResponse(
        stream_answer(turn),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Tell reverse proxies (nginx, Caddy) not to buffer: tokens must flow now.
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(user: CurrentUser, session: SessionDep) -> list[Conversation]:
    rows = await session.scalars(
        select(Conversation)
        .where(Conversation.tenant_id == user.tenant_id, Conversation.user_id == user.id)
        .order_by(Conversation.created_at.desc())
        .limit(100)
    )
    return list(rows)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> ConversationDetail:
    conversation = await _get_own_conversation(session, user, conversation_id)
    messages = list(
        await session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at, Message.role.desc())  # user before assistant
        )
    )
    # One query for all citations of all messages (avoids N+1 queries).
    citation_rows = await session.execute(
        select(MessageCitation, Document.title, Document.filename)
        .outerjoin(Document, Document.id == MessageCitation.document_id)
        .where(MessageCitation.message_id.in_([m.id for m in messages]))
        .order_by(MessageCitation.citation_number)
    )
    citations: dict[uuid.UUID, list[CitationOut]] = {}
    for citation, title, filename in citation_rows:
        citations.setdefault(citation.message_id, []).append(
            CitationOut(
                number=citation.citation_number,
                chunk_id=citation.chunk_id,
                document_id=citation.document_id,
                document_title=title,
                filename=filename,
                page_start=citation.page_start,
                snippet=citation.snippet,
            )
        )
    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        messages=[
            MessageOut(
                id=m.id,
                role=m.role,
                content=m.content,
                model=m.model,
                input_tokens=m.input_tokens,
                output_tokens=m.output_tokens,
                latency_ms=m.latency_ms,
                created_at=m.created_at,
                citations=citations.get(m.id, []),
                search_query=(m.meta or {}).get("search_query"),
                tool_steps=(m.meta or {}).get("tool_steps") or [],
            )
            for m in messages
        ],
    )


async def _enforce_daily_limit(session, tenant_id: uuid.UUID, limit: int) -> None:
    """
    Stop a public demo from spending an unbounded amount of money.

    Every answer costs real money at the model provider, so a link posted
    somewhere busy is someone else's playground funded by us. Counting the
    chat usage events already written for today is enough: it needs no new
    table, and it counts exactly the calls that were actually billed.

    MAX_QUESTIONS_PER_DAY=0 (the default) means no limit, which is what you
    want locally.
    """
    if limit <= 0:
        return
    since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    asked = await session.scalar(
        select(func.count())
        .select_from(UsageEvent)
        .where(
            UsageEvent.tenant_id == tenant_id,
            UsageEvent.event_type == UsageEventType.CHAT,
            UsageEvent.created_at >= since,
        )
    )
    if (asked or 0) >= limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"This demo allows {limit} questions per day and today's are used up. "
            "Try again tomorrow.",
        )


async def _get_own_conversation(session, user, conversation_id: uuid.UUID) -> Conversation:
    conversation = await session.get(Conversation, conversation_id)
    if (
        conversation is None
        or conversation.tenant_id != user.tenant_id
        or conversation.user_id != user.id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return conversation


async def _recent_messages(session, conversation_id: uuid.UUID, limit: int) -> list[dict]:
    """The last `limit` messages, oldest first, as {"role", "content"} dicts."""
    rows = await session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    return [{"role": m.role, "content": m.content} for m in reversed(list(rows))]
