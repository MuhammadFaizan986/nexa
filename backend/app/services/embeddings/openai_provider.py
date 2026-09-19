"""
OpenAI embeddings (text-embedding-3-small, 1536 dimensions).

Batching: one API call can embed many texts at once. Sending 128 chunks per call
instead of 1 makes ingestion dramatically faster and uses far fewer requests
against your rate limit.

Retries: the OpenAI SDK already retries connection errors, 408/409/429 and 5xx
responses with exponential backoff (wait 0.5s, 1s, 2s, ... plus jitter). We raise
`max_retries` from the default 2 to 5 because ingestion is a background-style
job where waiting a bit is much better than failing a 200-page document.
"""

from openai import AsyncOpenAI

from app.services.embeddings.base import EmbeddingProvider, EmbeddingResult


class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(self, *, api_key: str, model: str, dim: int, batch_size: int) -> None:
        self.model = model
        self.dim = dim
        self.batch_size = batch_size
        self._client = AsyncOpenAI(api_key=api_key, max_retries=5, timeout=60.0)

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        vectors: list[list[float]] = []
        tokens = 0
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            response = await self._client.embeddings.create(model=self.model, input=batch)
            # The API returns one item per input with an `index`; sort to be safe.
            ordered = sorted(response.data, key=lambda item: item.index)
            vectors.extend(item.embedding for item in ordered)
            tokens += response.usage.prompt_tokens
        return EmbeddingResult(vectors=vectors, tokens=tokens)
