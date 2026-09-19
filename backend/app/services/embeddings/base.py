"""
Embedding provider interface.

An *embedding* turns text into a list of numbers (a vector) such that texts with
similar MEANING get vectors pointing in similar directions. "How much notice do
I give to end my lease?" and "Either party may terminate with 60 days written
notice" share few words but land close together — that's what makes semantic
search find paraphrases that keyword search misses.

We measure closeness with *cosine similarity* (1.0 = same direction, 0 =
unrelated). pgvector's `<=>` operator returns cosine DISTANCE = 1 - similarity.

Every provider implements the same small interface, so switching from OpenAI to
a local model (e.g. BGE for privacy-sensitive clients) is a config change.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class EmbeddingResult:
    vectors: list[list[float]]
    tokens: int  # billed input tokens, for cost tracking


class EmbeddingProvider(ABC):
    model: str
    dim: int

    @abstractmethod
    async def embed(self, texts: list[str]) -> EmbeddingResult:
        """Embed many texts (batched internally). Output order == input order."""

    async def embed_query(self, text: str) -> EmbeddingResult:
        return await self.embed([text])
