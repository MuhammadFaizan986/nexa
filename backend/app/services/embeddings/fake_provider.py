"""
Deterministic offline embeddings — for tests and no-API-key demos.

This is NOT a real semantic model. It uses "feature hashing": every word (and
every pair of adjacent words) is hashed to one of the 1536 dimensions, and the
vector counts how often each lands there. Texts that share words therefore get
similar vectors, so search behaves sensibly for tests — but it can't match
paraphrases ("end my lease" vs "terminate the agreement") the way a real
embedding model can.

Benefits: free, instant, no network, and the same text always gives exactly the
same vector, so tests are reproducible.
"""

import hashlib
import math
import re

from app.services.embeddings.base import EmbeddingProvider, EmbeddingResult

_WORD = re.compile(r"[a-z0-9]+")
# Very common words carry no meaning for matching, so we skip them.
# fmt: off
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have", "in", "is",
    "it", "of", "on", "or", "that", "the", "this", "to", "was", "were", "will", "with", "what",
    "which", "who", "how", "do", "does", "i", "you", "my", "your", "we", "our",
})
# fmt: on


class FakeEmbeddingProvider(EmbeddingProvider):
    def __init__(self, dim: int = 1536) -> None:
        self.model = "fake-hashing"
        self.dim = dim

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        vectors = [self._vector(text) for text in texts]
        tokens = sum(len(_WORD.findall(text.lower())) for text in texts)
        return EmbeddingResult(vectors=vectors, tokens=tokens)

    def _vector(self, text: str) -> list[float]:
        words = [w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS]
        features = words + [f"{a}_{b}" for a, b in zip(words, words[1:], strict=False)]
        vec = [0.0] * self.dim
        for feature in features:
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0  # random sign reduces collisions' bias
            vec[index] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0:
            vec[0] = 1.0  # cosine distance is undefined for the zero vector
            return vec
        return [v / norm for v in vec]
