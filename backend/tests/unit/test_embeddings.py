"""
Embedding provider tests. The Gemini provider is tested against a stub client, so
no network or API key is needed: we check what we SEND (batching, task types,
dimensions) and what we do with the answer (order, normalisation).
"""

import math
from types import SimpleNamespace

import pytest
from google.genai import errors

from app.core.config import get_settings
from app.services.embeddings import ProviderConfigError, gemini_provider, get_embedding_provider
from app.services.embeddings.gemini_provider import (
    EmbeddingQuotaExhausted,
    GeminiEmbeddingProvider,
)


class StubModels:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.failures: list[Exception] = []  # raised (in order) before succeeding

    async def embed_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self.failures:
            raise self.failures.pop(0)
        # Deliberately NOT unit length, and encodes the text index so order is checkable.
        embeddings = [
            SimpleNamespace(values=[3.0, 4.0 + int(text.split()[-1])]) for text in contents
        ]
        return SimpleNamespace(embeddings=embeddings)


@pytest.fixture
def gemini() -> tuple[GeminiEmbeddingProvider, StubModels]:
    provider = GeminiEmbeddingProvider(
        api_key="test-key", model="gemini-embedding-001", dim=1536, batch_size=128
    )
    models = StubModels()
    provider._client = SimpleNamespace(aio=SimpleNamespace(models=models))
    return provider, models


async def test_gemini_batches_at_100_and_keeps_order(gemini):
    provider, models = gemini
    texts = [f"chunk {i}" for i in range(250)]
    result = await provider.embed(texts)

    assert [len(c["contents"]) for c in models.calls] == [100, 100, 50]  # API max is 100
    assert len(result.vectors) == 250
    # Vector i was built from text i: order survived batching.
    assert result.vectors[0] == pytest.approx([3 / 5, 4 / 5])
    assert result.tokens > 0


async def test_gemini_uses_document_vs_query_task_types_and_1536_dims(gemini):
    provider, models = gemini
    await provider.embed(["chunk 1"])
    await provider.embed_query("question 2")
    doc_config, query_config = models.calls[0]["config"], models.calls[1]["config"]
    assert doc_config.task_type == "RETRIEVAL_DOCUMENT"
    assert query_config.task_type == "RETRIEVAL_QUERY"
    assert doc_config.output_dimensionality == query_config.output_dimensionality == 1536


async def test_gemini_vectors_are_normalised(gemini):
    provider, _ = gemini
    result = await provider.embed(["chunk 7", "chunk 20"])
    for vector in result.vectors:
        assert math.sqrt(sum(v * v for v in vector)) == pytest.approx(1.0)


def quota_error(quota_id: str, retry_delay: str = "8s") -> errors.ClientError:
    """A 429 shaped like the real Gemini response."""
    return errors.ClientError(
        429,
        {
            "error": {
                "code": 429,
                "status": "RESOURCE_EXHAUSTED",
                "message": f"You exceeded your current quota. Please retry in {retry_delay}.",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                        "violations": [{"quotaId": quota_id}],
                    },
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": retry_delay,
                    },
                ],
            }
        },
    )


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    """Record asyncio.sleep calls in the provider instead of really waiting."""
    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(gemini_provider.asyncio, "sleep", fake_sleep)
    return recorded


async def test_gemini_waits_as_told_on_per_minute_quota_then_succeeds(gemini, sleeps):
    provider, models = gemini
    per_minute = "EmbedContentRequestsPerMinutePerUserPerProjectPerModel-FreeTier"
    models.failures = [quota_error(per_minute, "8s"), quota_error(per_minute, "2.5s")]
    result = await provider.embed(["chunk 1"])
    assert len(result.vectors) == 1
    assert sleeps == [9.0, 3.5]  # Gemini's retryDelay + 1s margin
    assert len(models.calls) == 3


async def test_gemini_daily_quota_fails_fast_with_clear_error(gemini, sleeps):
    provider, models = gemini
    models.failures = [quota_error("EmbedContentRequestsPerDayPerUserPerProjectPerModel-FreeTier")]
    with pytest.raises(EmbeddingQuotaExhausted, match="daily"):
        await provider.embed(["chunk 1"])
    assert sleeps == []


async def test_gemini_non_quota_errors_are_not_retried(gemini, sleeps):
    provider, models = gemini
    models.failures = [errors.ClientError(400, {"error": {"code": 400, "message": "bad"}})]
    with pytest.raises(errors.ClientError):
        await provider.embed(["chunk 1"])
    assert sleeps == [] and len(models.calls) == 1


@pytest.fixture
def provider_settings(monkeypatch):
    """Change embedding settings for one test; the cached provider is rebuilt around it."""
    get_embedding_provider.cache_clear()
    yield lambda **values: [
        monkeypatch.setattr(get_settings(), key, value) for key, value in values.items()
    ]
    get_embedding_provider.cache_clear()


def test_factory_builds_gemini_with_default_model(provider_settings):
    provider_settings(embedding_provider="gemini", embedding_model=None, gemini_api_key=None)
    with pytest.raises(ProviderConfigError, match="GEMINI_API_KEY"):
        get_embedding_provider()

    from pydantic import SecretStr

    get_embedding_provider.cache_clear()
    provider_settings(gemini_api_key=SecretStr("test-key"))
    provider = get_embedding_provider()
    assert isinstance(provider, GeminiEmbeddingProvider)
    assert provider.model == "gemini-embedding-001"
