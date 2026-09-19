"""
Token + cost tracking.

Every AI call writes a `usage_events` row. From these rows you can show a client
"your assistant answered 3,120 questions this month for $41.20", which is what
you need to price hosting or maintenance packages.

Prices change — check the providers' pricing pages before quoting a client.
A model missing from the table is recorded with cost NULL (tokens are still
recorded, so cost can be back-filled later).
"""

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UsageEvent, UsageEventType

# USD per 1 million tokens: (input, output). Last reviewed 2026-09.
PRICES_PER_MILLION_TOKENS: dict[str, tuple[Decimal, Decimal]] = {
    # Anthropic — https://www.anthropic.com/pricing
    "claude-fable-5-1": (Decimal("10.00"), Decimal("50.00")),
    "claude-opus-5": (Decimal("5.00"), Decimal("25.00")),
    "claude-opus-4-8": (Decimal("5.00"), Decimal("25.00")),  # refusal-fallback target
    "claude-sonnet-5": (Decimal("2.00"), Decimal("10.00")),
    "claude-haiku-4-5": (Decimal("1.00"), Decimal("5.00")),
    # OpenAI embeddings — https://openai.com/api/pricing
    "text-embedding-3-small": (Decimal("0.02"), Decimal("0")),
    "text-embedding-3-large": (Decimal("0.13"), Decimal("0")),
    # Google — https://ai.google.dev/gemini-api/docs/pricing. The free tier costs
    # $0; we record the paid rate so reports show what a client would pay.
    "gemini-embedding-001": (Decimal("0.15"), Decimal("0")),
    # Offline test providers
    "fake-hashing": (Decimal("0"), Decimal("0")),
    "fake-llm": (Decimal("0"), Decimal("0")),
}

# Rerankers are priced per SEARCH (one query + its candidate documents), not per
# token. USD per search. Trial keys are free; this is what paid usage would cost.
PRICES_PER_RERANK_SEARCH: dict[str, Decimal] = {
    # Cohere — https://cohere.com/pricing
    "rerank-v4.0-pro": Decimal("0.0025"),
    "rerank-v4.0-fast": Decimal("0.002"),
    "rerank-v3.5": Decimal("0.002"),
    "fake-reranker": Decimal("0"),
}

_MILLION = Decimal(1_000_000)


def estimate_cost(model: str, input_tokens: int, output_tokens: int = 0) -> Decimal | None:
    prices = PRICES_PER_MILLION_TOKENS.get(model)
    if prices is None:
        return None
    input_price, output_price = prices
    return (input_tokens * input_price + output_tokens * output_price) / _MILLION


def estimate_rerank_cost(model: str, searches: int = 1) -> Decimal | None:
    price = PRICES_PER_RERANK_SEARCH.get(model)
    return price * searches if price is not None else None


def record_usage(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    event_type: UsageEventType,
    model: str,
    input_tokens: int,
    output_tokens: int = 0,
    cost_usd: Decimal | None = None,
) -> None:
    """
    Adds the row to the session; the caller commits together with its other work.
    `cost_usd` overrides the per-token estimate (e.g. per-search reranker pricing).
    """
    session.add(
        UsageEvent(
            tenant_id=tenant_id,
            user_id=user_id,
            event_type=event_type,
            model=model,
            tokens=input_tokens + output_tokens,
            cost_usd=(
                cost_usd
                if cost_usd is not None
                else estimate_cost(model, input_tokens, output_tokens)
            ),
        )
    )
