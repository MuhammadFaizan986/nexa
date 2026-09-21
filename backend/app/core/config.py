"""
Application settings.

Every tunable value lives here and is read from environment variables (or a
`.env` file). This is the "12-factor app" approach: the same Docker image runs in
dev, test and production — only the environment changes.

pydantic-settings maps env vars to fields case-insensitively, so the env var
`CHUNK_SIZE_TOKENS=800` sets `settings.chunk_size_tokens = 800`, validated as an int.
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

INSECURE_DEFAULT_SECRET = "dev-insecure-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Look for .env in the working dir (backend/) and the repo root.
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",  # ignore unrelated vars in .env (e.g. NEXA_API_PORT for compose)
    )

    # ------------------------------------------------------------------ general
    app_name: str = "NEXA"
    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    log_json: bool = False

    # ------------------------------------------------------------------ database
    # `postgresql+psycopg` = SQLAlchemy dialect "postgresql" using the psycopg 3 driver.
    database_url: str = "postgresql+psycopg://nexa:nexa@localhost:5433/nexa"
    # Unprivileged role the app switches into per transaction so Row-Level
    # Security applies (superusers and table owners bypass it). Created by
    # migration 0005. Empty = don't switch (RLS then only guards other roles).
    db_app_role: str = "nexa_app"

    # ------------------------------------------------------------------ auth
    jwt_secret: SecretStr = SecretStr(INSECURE_DEFAULT_SECRET)
    jwt_algorithm: str = "HS256"
    # Short-lived access tokens limit the damage if one leaks; the long-lived
    # refresh token is only ever sent to /auth/refresh.
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 7

    # Browsers refuse cross-origin calls unless the API allows them. The web UI
    # runs on a different port, so it must be listed here (comma-separated).
    # NoDecode: read the env var as a plain string (the default would try to
    # JSON-parse it), then the validator below splits it on commas.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3001"]

    # ------------------------------------------------------------------ background jobs
    redis_url: str = "redis://localhost:6380/0"
    # celery = hand the work to a worker (uploads return immediately).
    # inline  = process inside the upload request (tests, or running without a worker).
    ingestion_mode: Literal["celery", "inline"] = "celery"

    # ------------------------------------------------------------------ uploads
    storage_dir: Path = Path("./data/uploads")
    max_upload_mb: int = 50

    # ------------------------------------------------------------------ embeddings
    embedding_provider: Literal["openai", "gemini", "fake"] = "openai"
    # None = the provider's default model (see services/embeddings/__init__.py).
    embedding_model: str | None = None
    # Must match the `vector(1536)` column in the chunks table. Changing the
    # embedding model to one with a different size needs a migration + re-index.
    embedding_dim: int = 1536
    # How many chunks we send per embeddings API call. Fewer, bigger requests are
    # faster and cheaper on rate limits than one request per chunk.
    embedding_batch_size: int = 128
    openai_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None

    # ------------------------------------------------------------------ LLM
    llm_provider: Literal["anthropic", "openai", "fake"] = "anthropic"
    llm_model: str = "claude-opus-5"
    # Upper bound on tokens the model may produce per answer (thinking + text).
    # It's a cost/latency cap, not a target — answers are normally far shorter.
    llm_max_tokens: int = 16000
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    anthropic_api_key: SecretStr | None = None
    anthropic_refusal_fallback: bool = True

    # ------------------------------------------------------------------ agent
    # Agentic RAG (Week 6): instead of one search, the model is given tools and
    # decides what to do — search again, read a document, compare two of them.
    # It costs more (one model call per step), so it is opt-in per question
    # unless agent_default is on. See services/agent/.
    agent_tools_enabled: bool = True
    agent_default: bool = False
    # A hard stop on tool calls per question: a loop that never converges must
    # not be able to spend an unbounded amount of money.
    agent_max_steps: int = 6

    # ------------------------------------------------- follow-up query rewriting
    # "What about Unit 7A?" means nothing to a search engine. Before retrieving,
    # we ask a small model to rewrite a follow-up into a question that stands on
    # its own ("What is the notice period for Unit 7A?"). See
    # services/generation/rewrite.py.
    query_rewrite: bool = True
    # A cheap, fast model: this is a one-line transformation, not reasoning, and
    # it sits in front of every follow-up question, so latency and cost matter
    # more than depth here.
    rewrite_model: str = "claude-haiku-4-5-20251001"
    rewrite_max_tokens: int = 120
    # Normally we only pay for a rewrite when the question looks like it depends
    # on the conversation (see looks_context_dependent). Set this to rewrite
    # every follow-up instead — more reliable, more calls.
    rewrite_always: bool = False

    # ------------------------------------------------------------------ chunking
    # Plan section 9.1: ~500–800 tokens per chunk with ~10–15% overlap.
    chunk_size_tokens: int = 600
    chunk_overlap_tokens: int = 80
    # A section shorter than this is merged with the next one instead of becoming
    # its own tiny chunk (tiny chunks embed poorly and waste retrieval slots).
    chunk_min_tokens: int = 150
    # Prefix what we embed/index with "Document: <title> | Section: <headings>" so a
    # chunk that never names its document ("7. Termination ...") still matches
    # questions about it ("Unit 4B notice period"). Changing it needs `make reindex`.
    contextual_headers: bool = True

    # ------------------------------------------------------------------ retrieval
    # semantic = meaning only; keyword = exact words only; hybrid = both, fused (RRF).
    retrieval_mode: Literal["semantic", "keyword", "hybrid"] = "hybrid"
    # Passages given to the LLM.
    retrieval_top_k: int = 5
    # How many results each search (semantic, keyword) returns before fusion.
    retrieval_candidates: int = 40
    # How many fused candidates the reranker re-scores (it's the slow, precise step).
    rerank_candidates: int = 20
    # The "k" in Reciprocal Rank Fusion: 1 / (k + rank). 60 is the value from the
    # original RRF paper; larger k flattens the difference between ranks.
    rrf_k: int = 60
    # HNSW search breadth. Higher = better recall, slower queries. pgvector's
    # default is 40; we raise it because tenant/collection filters discard rows.
    hnsw_ef_search: int = 100
    # "I don't know" gates. Without a reranker we gate on the best cosine
    # similarity; with one, on the best rerank score (a much sharper signal).
    # Both MUST be tuned on the eval set: `make eval` prints suggested values.
    min_relevance_score: float = 0.30
    min_rerank_score: float = 0.80  # measured with rerank-v4.0-pro, see docs/evaluation-results.md

    # ------------------------------------------------------------------ reranking
    # none = skip reranking. cohere = Cohere Rerank API. fake = offline, for tests.
    reranker_provider: Literal["none", "cohere", "fake"] = "none"
    reranker_model: str = "rerank-v4.0-pro"
    cohere_api_key: SecretStr | None = None
    # Client-side pacing. Cohere TRIAL keys allow 10 rerank calls per minute; we
    # space calls out instead of hitting 429 errors. 0 = no pacing (production keys).
    rerank_max_per_minute: int = 10
    # How many previous messages (user + assistant) to send as chat history.
    history_messages: int = 6

    # ------------------------------------------------------------------ validators
    @field_validator(
        "llm_effort",
        "embedding_model",
        "openai_api_key",
        "gemini_api_key",
        "anthropic_api_key",
        "cohere_api_key",
        mode="before",
    )
    @classmethod
    def _empty_string_is_none(cls, value: object) -> object:
        # `LLM_EFFORT=` in .env arrives as "" — treat blank values as "not set".
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        # Accept "http://a, http://b" from .env as well as a JSON list.
        if isinstance(value, str) and not value.strip().startswith("["):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("db_app_role")
    @classmethod
    def _valid_identifier(cls, value: str) -> str:
        # This value is interpolated into "SET LOCAL ROLE ..." (identifiers can't
        # be bind parameters), so it must look like a plain identifier.
        if value and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError("DB_APP_ROLE must be a plain SQL identifier")
        return value

    @model_validator(mode="after")
    def _refuse_insecure_production_config(self) -> "Settings":
        # Fail fast at startup rather than run production with a guessable secret.
        if self.environment == "prod":
            secret = self.jwt_secret.get_secret_value()
            if secret == INSECURE_DEFAULT_SECRET or len(secret) < 32:
                raise ValueError(
                    "JWT_SECRET must be set to a random value of 32+ characters in prod"
                )
        return self

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    """Settings are parsed once and cached. Tests set env vars *before* importing the app."""
    return Settings()
