"""
Reranking: a second, much more precise opinion on the top candidates.

Why a second opinion?
    Embedding search is a *bi-encoder*: the question and each chunk are turned
    into vectors SEPARATELY, and we compare vectors. That's fast (chunk vectors
    are computed once, at upload) but coarse: the model never sees the question
    and the passage together.

    A reranker is a *cross-encoder*: it reads the question and a passage
    TOGETHER and outputs "how well does this passage answer this question".
    Far more accurate, but far slower, so it only re-scores the top ~20 fused
    candidates, never the whole knowledge base.

A bonus: its 0..1 relevance score is a much better "I don't know" signal than
cosine similarity. Similarity says "same topic"; the reranker says "this
passage actually answers it" (plan 9.4).

Providers (RERANKER_PROVIDER): cohere (Cohere Rerank API), fake (offline, for
tests), none (skip reranking). A local cross-encoder (e.g. bge-reranker) can be
added behind the same interface later.
"""

import asyncio
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class RerankHit:
    index: int  # position in the `documents` list we sent
    score: float  # relevance, 0..1


@dataclass
class RerankResult:
    hits: list[RerankHit]  # best first, at most top_n
    model: str
    latency_ms: int  # time spent in the API call itself (excludes pacing waits)


class Reranker(ABC):
    model: str

    @abstractmethod
    async def rerank(self, query: str, documents: list[str], top_n: int) -> RerankResult:
        """Score every document against the query; return the top_n, best first."""


class CohereReranker(Reranker):
    """
    Cohere Rerank via the official SDK (AsyncClientV2.rerank).

    Trial keys allow 10 rerank calls per minute (and 1,000 API calls per month).
    Instead of firing requests and collecting 429 errors, we PACE them: calls are
    spaced at least 60 / RERANK_MAX_PER_MINUTE seconds apart. With a production
    key, set RERANK_MAX_PER_MINUTE=0 to switch pacing off. The SDK also retries
    429/5xx responses itself.
    """

    def __init__(self, *, api_key: str, model: str, max_per_minute: int) -> None:
        import cohere

        self.model = model
        self._client = cohere.AsyncClientV2(api_key=api_key, timeout=30, max_retries=3)
        self._min_interval = 60.0 / max_per_minute if max_per_minute > 0 else 0.0
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0

    async def _pace(self) -> None:
        # The lock makes concurrent requests queue up instead of all firing at once.
        async with self._lock:
            wait = self._next_allowed - time.monotonic()
            if wait > 0:
                log.info("rerank.paced", wait_s=round(wait, 1))
                await asyncio.sleep(wait)
            self._next_allowed = time.monotonic() + self._min_interval

    async def rerank(self, query: str, documents: list[str], top_n: int) -> RerankResult:
        if not documents:
            return RerankResult(hits=[], model=self.model, latency_ms=0)
        await self._pace()
        started = time.perf_counter()
        response = await self._client.rerank(
            model=self.model,
            query=query,
            documents=documents,
            top_n=min(top_n, len(documents)),
        )
        return RerankResult(
            hits=[RerankHit(index=r.index, score=r.relevance_score) for r in response.results],
            model=self.model,
            latency_ms=round((time.perf_counter() - started) * 1000),
        )


_WORD = re.compile(r"[a-z0-9]+")
# fmt: off
_STOPWORDS = frozenset({
    "the", "and", "are", "for", "was", "were", "with", "what", "which", "who", "how",
    "does", "did", "this", "that", "from", "have", "has", "you", "your", "get", "can",
    "about", "into",
})
# fmt: on


def _words(text: str) -> set[str]:
    return {
        w
        for w in _WORD.findall(text.lower())
        if (len(w) > 2 or any(c.isdigit() for c in w)) and w not in _STOPWORDS
    }


class FakeReranker(Reranker):
    """
    Offline stand-in for tests: score = share of the question's words found in
    the passage. Deterministic and free; it doesn't understand meaning.
    """

    model = "fake-reranker"

    async def rerank(self, query: str, documents: list[str], top_n: int) -> RerankResult:
        question = _words(query)
        scores = [
            len(question & _words(doc)) / len(question) if question else 0.0 for doc in documents
        ]
        order = sorted(range(len(documents)), key=lambda i: (-scores[i], i))[:top_n]
        return RerankResult(
            hits=[RerankHit(index=i, score=scores[i]) for i in order],
            model=self.model,
            latency_ms=0,
        )


class RerankerConfigError(RuntimeError):
    pass


@lru_cache
def get_reranker() -> Reranker | None:
    """The configured reranker, or None when RERANKER_PROVIDER=none."""
    settings = get_settings()
    if settings.reranker_provider == "none":
        return None
    if settings.reranker_provider == "fake":
        return FakeReranker()
    if settings.cohere_api_key is None:
        raise RerankerConfigError("RERANKER_PROVIDER=cohere but COHERE_API_KEY is not set")
    return CohereReranker(
        api_key=settings.cohere_api_key.get_secret_value(),
        model=settings.reranker_model,
        max_per_minute=settings.rerank_max_per_minute,
    )
