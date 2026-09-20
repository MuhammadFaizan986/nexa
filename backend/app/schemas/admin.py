"""Request/response models for feedback, usage reporting and tenant settings."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.services.usage import PRICES_PER_MILLION_TOKENS

# Models a tenant may choose. Keeping it to models we know the price of means
# usage reports stay accurate, and a typo can't send traffic to nothing.
ALLOWED_MODELS = sorted(m for m in PRICES_PER_MILLION_TOKENS if m.startswith("claude-"))


class FeedbackRequest(BaseModel):
    value: Literal[-1, 0, 1] = Field(description="1 = helpful, -1 = not helpful, 0 = clear")


class UsageDay(BaseModel):
    date: date
    questions: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    by_type: dict[str, float] = Field(default_factory=dict)  # cost per kind of call


class UsageTotals(BaseModel):
    questions: int
    tokens: int
    cost_usd: float
    documents: int
    chunks: int
    users: int


class UsageReport(BaseModel):
    days: list[UsageDay]
    totals: UsageTotals


class TenantSettings(BaseModel):
    name: str
    assistant_name: str | None = None
    tone: str | None = None
    model: str | None = None


class TenantSettingsUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    assistant_name: str | None = Field(default=None, max_length=60)
    tone: str | None = Field(
        default=None,
        max_length=200,
        description="How answers should sound, e.g. 'formal and concise'",
    )
    model: Literal[tuple(ALLOWED_MODELS)] | None = None  # type: ignore[valid-type]
