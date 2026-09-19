"""
Pick the embedding provider from settings: EMBEDDING_PROVIDER=openai | gemini | fake.

Switching provider (or model) means switching to a different "coordinate
system" for meaning: vectors from two models can't be compared, even when both
have 1536 numbers. After a switch, every document must be re-embedded. Each
chunk records `metadata.embedding_model`, so you can always tell which model
made it.
"""

from functools import lru_cache

from app.core.config import get_settings
from app.services.embeddings.base import EmbeddingProvider, EmbeddingResult

# Used when EMBEDDING_MODEL is left empty.
DEFAULT_MODELS = {
    "openai": "text-embedding-3-small",
    "gemini": "gemini-embedding-001",
}


class ProviderConfigError(RuntimeError):
    """A provider was selected but its API key / settings are missing."""


def _require_key(secret, provider: str, env_var: str) -> str:
    if secret is None:
        raise ProviderConfigError(
            f"EMBEDDING_PROVIDER={provider} but {env_var} is not set "
            "(or set EMBEDDING_PROVIDER=fake for offline testing)"
        )
    return secret.get_secret_value()


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    settings = get_settings()
    provider = settings.embedding_provider
    if provider == "fake":
        from app.services.embeddings.fake_provider import FakeEmbeddingProvider

        return FakeEmbeddingProvider(dim=settings.embedding_dim)

    model = settings.embedding_model or DEFAULT_MODELS[provider]
    if provider == "gemini":
        from app.services.embeddings.gemini_provider import GeminiEmbeddingProvider

        return GeminiEmbeddingProvider(
            api_key=_require_key(settings.gemini_api_key, provider, "GEMINI_API_KEY"),
            model=model,
            dim=settings.embedding_dim,
            batch_size=settings.embedding_batch_size,
        )

    from app.services.embeddings.openai_provider import OpenAIEmbeddingProvider

    return OpenAIEmbeddingProvider(
        api_key=_require_key(settings.openai_api_key, provider, "OPENAI_API_KEY"),
        model=model,
        dim=settings.embedding_dim,
        batch_size=settings.embedding_batch_size,
    )


__all__ = [
    "DEFAULT_MODELS",
    "EmbeddingProvider",
    "EmbeddingResult",
    "ProviderConfigError",
    "get_embedding_provider",
]
