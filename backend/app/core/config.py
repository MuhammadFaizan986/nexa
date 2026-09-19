"""
Application settings.

Every tunable value lives here and is read from environment variables (or a
`.env` file). This is the "12-factor app" approach: the same Docker image runs in
dev, test and production — only the environment changes.

pydantic-settings maps env vars to fields case-insensitively, so the env var
`CHUNK_SIZE_TOKENS=800` sets `settings.chunk_size_tokens = 800`, validated as an int.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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

    # ------------------------------------------------------------------ auth
    jwt_secret: SecretStr = SecretStr(INSECURE_DEFAULT_SECRET)
    jwt_algorithm: str = "HS256"
    # Short-lived access tokens limit the damage if one leaks; the long-lived
    # refresh token is only ever sent to /auth/refresh.
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 7

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

    # ------------------------------------------------------------------ chunking
    # Plan section 9.1: ~500–800 tokens per chunk with ~10–15% overlap.
    chunk_size_tokens: int = 600
    chunk_overlap_tokens: int = 80
    # A section shorter than this is merged with the next one instead of becoming
    # its own tiny chunk (tiny chunks embed poorly and waste retrieval slots).
    chunk_min_tokens: int = 150

    # ------------------------------------------------------------------ retrieval
    retrieval_top_k: int = 5
    # HNSW search breadth. Higher = better recall, slower queries. pgvector's
    # default is 40; we raise it because tenant/collection filters discard rows.
    hnsw_ef_search: int = 100
    # "I don't know" gate: if the best passage's cosine similarity is below this,
    # we don't call the LLM at all. MUST be tuned on your eval set.
    min_relevance_score: float = 0.30
    # How many previous messages (user + assistant) to send as chat history.
    history_messages: int = 6

    # ------------------------------------------------------------------ validators
    @field_validator(
        "llm_effort",
        "embedding_model",
        "openai_api_key",
        "gemini_api_key",
        "anthropic_api_key",
        mode="before",
    )
    @classmethod
    def _empty_string_is_none(cls, value: object) -> object:
        # `LLM_EFFORT=` in .env arrives as "" — treat blank values as "not set".
        if isinstance(value, str) and value.strip() == "":
            return None
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
