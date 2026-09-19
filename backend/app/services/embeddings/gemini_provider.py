"""
Google Gemini embeddings (gemini-embedding-001) via the official `google-genai` SDK.

Why it's here: Gemini's API has a free tier, so you can develop NEXA without
paying for embeddings. Free-tier data MAY be used by Google to improve its
products, which is fine for our synthetic demo documents but NOT for client
documents. For client work use a paid tier (or OpenAI/Voyage/a local model).

Three details worth understanding:

1. Same size as OpenAI's vectors. The model natively returns 3072 numbers, but
   it's trained so the first N numbers are themselves a good embedding
   ("Matryoshka" embeddings). We ask for 1536 (`output_dimensionality`), which
   fits our `vector(1536)` column with no schema change.

2. Re-normalising. A truncated 1536-number vector is no longer exactly unit
   length, and Google's docs say to normalise it yourself. (Cosine distance
   ignores length anyway, but unit vectors keep every distance measure honest.)

3. Asymmetric task types. Gemini embeds text differently depending on its role:
   RETRIEVAL_DOCUMENT for chunks we store, RETRIEVAL_QUERY for the user's
   question. A question ("how much notice do I give?") and the passage that
   answers it ("the tenant must give 60 days...") look different; telling the
   model which is which improves matching. That's why this provider overrides
   `embed_query()` instead of using the default.

4. Free-tier rate limits. The free tier allows ~100 embeddings PER MINUTE, and
   every text in a batch counts separately, so one 100-chunk document uses a
   whole minute's quota. When Gemini answers 429 RESOURCE_EXHAUSTED it also
   says how long to wait ("Please retry in 8s"); we wait exactly that long and
   try again. A DAILY quota can't be waited out inside a request, so that case
   fails immediately with a clear message.

Tokens: the Gemini API doesn't report token counts for embeddings, so usage is
estimated with our tokenizer (close enough for cost tracking).
"""

import asyncio
import math
import re

from google import genai
from google.genai import errors, types

from app.core.logging import get_logger
from app.services.embeddings.base import EmbeddingProvider, EmbeddingResult
from app.services.ingestion.chunker import count_tokens

log = get_logger(__name__)

MAX_TEXTS_PER_REQUEST = 100  # Gemini API limit per batch embedding call
MAX_QUOTA_WAITS = 5  # rate-limit waits per batch before giving up
MAX_WAIT_SECONDS = 65.0  # never sleep longer than about one quota window
_RETRY_IN = re.compile(r"retry in ([\d.]+)s", re.IGNORECASE)


class EmbeddingQuotaExhausted(RuntimeError):
    """The provider's daily quota is used up; retrying today won't help."""


class GeminiEmbeddingProvider(EmbeddingProvider):
    def __init__(self, *, api_key: str, model: str, dim: int, batch_size: int) -> None:
        self.model = model
        self.dim = dim
        self.batch_size = min(batch_size, MAX_TEXTS_PER_REQUEST)
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=60_000,  # milliseconds (this SDK's unit)
                # The SDK retries timeouts and server errors with exponential
                # backoff. 429 (quota) is left out on purpose: we handle it in
                # _embed_batch() using the wait time Gemini tells us.
                retry_options=types.HttpRetryOptions(
                    attempts=5, http_status_codes=[408, 500, 502, 503, 504]
                ),
            ),
        )

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        return await self._embed(texts, task_type="RETRIEVAL_DOCUMENT")

    async def embed_query(self, text: str) -> EmbeddingResult:
        return await self._embed([text], task_type="RETRIEVAL_QUERY")

    async def _embed(self, texts: list[str], *, task_type: str) -> EmbeddingResult:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            response = await self._embed_batch(texts[start : start + self.batch_size], task_type)
            # One embedding per input, in the same order as the batch.
            vectors.extend(_normalize(e.values) for e in response.embeddings)
        return EmbeddingResult(vectors=vectors, tokens=sum(count_tokens(t) for t in texts))

    async def _embed_batch(self, batch: list[str], task_type: str) -> types.EmbedContentResponse:
        config = types.EmbedContentConfig(task_type=task_type, output_dimensionality=self.dim)
        for attempt in range(MAX_QUOTA_WAITS + 1):
            try:
                return await self._client.aio.models.embed_content(
                    model=self.model, contents=batch, config=config
                )
            except errors.ClientError as exc:
                if exc.code != 429:
                    raise  # bad request, bad key, ...: waiting won't fix it
                if "PerDay" in str(exc.details):
                    raise EmbeddingQuotaExhausted(
                        "Gemini's daily free-tier embedding quota is used up. It resets "
                        "daily; try again later or enable billing for a paid tier."
                    ) from exc
                if attempt == MAX_QUOTA_WAITS:
                    raise
                wait = _retry_delay_seconds(exc, attempt)
                log.info("embeddings.rate_limited", provider="gemini", wait_s=round(wait, 1))
                await asyncio.sleep(wait)
        raise AssertionError("unreachable")


def _retry_delay_seconds(exc: errors.ClientError, attempt: int) -> float:
    """How long Gemini asked us to wait (+1s margin), else exponential backoff."""
    error = exc.details.get("error", {}) if isinstance(exc.details, dict) else {}
    hints = [
        str(d.get("retryDelay", ""))  # structured: {"@type": "...RetryInfo", "retryDelay": "8s"}
        for d in error.get("details", [])
        if isinstance(d, dict) and str(d.get("@type", "")).endswith("RetryInfo")
    ]
    for hint in hints:
        match = re.fullmatch(r"([\d.]+)s", hint)
        if match:
            return min(float(match.group(1)) + 1.0, MAX_WAIT_SECONDS)
    match = _RETRY_IN.search(exc.message or "")  # fallback: "Please retry in 8.1s."
    if match:
        return min(float(match.group(1)) + 1.0, MAX_WAIT_SECONDS)
    return min(5.0 * 2**attempt, MAX_WAIT_SECONDS)


def _normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values] if norm else list(values)
