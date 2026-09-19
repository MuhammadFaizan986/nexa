"""
Hybrid search: combine semantic and keyword results with Reciprocal Rank Fusion.

The two searches score on completely different scales (cosine similarity ~0.6
vs ts_rank_cd ~0.05), so adding scores would be meaningless. RRF ignores the
scores and uses only the RANKS:

    rrf_score(chunk) = sum over each list it appears in of  1 / (k + rank)

With k = 60: rank 1 -> 0.0164, rank 2 -> 0.0161, rank 10 -> 0.0143.
A chunk found by BOTH searches gets both contributions, so agreement between
"means the same thing" and "uses the same words" floats to the top, while a
chunk that only one search found can still make the list.

(The plan shows the same idea as one SQL query with a FULL OUTER JOIN. Doing it
in Python keeps each search simple and lets us keep both searches' scores for
debugging.)
"""

import uuid
from dataclasses import replace

from app.services.retrieval.base import RetrievedChunk


def reciprocal_rank_fusion(
    result_lists: list[list[RetrievedChunk]], *, k: int = 60, limit: int = 20
) -> list[RetrievedChunk]:
    fused: dict[uuid.UUID, float] = {}
    merged: dict[uuid.UUID, RetrievedChunk] = {}
    for results in result_lists:
        for rank, chunk in enumerate(results, start=1):
            fused[chunk.chunk_id] = fused.get(chunk.chunk_id, 0.0) + 1.0 / (k + rank)
            seen = merged.get(chunk.chunk_id)
            # Keep every stage's score: similarity from one list, keyword from the other.
            merged[chunk.chunk_id] = (
                chunk
                if seen is None
                else replace(
                    seen,
                    similarity=seen.similarity if seen.similarity is not None else chunk.similarity,
                    keyword_score=(
                        seen.keyword_score
                        if seen.keyword_score is not None
                        else chunk.keyword_score
                    ),
                )
            )

    ordered = sorted(fused, key=lambda chunk_id: fused[chunk_id], reverse=True)[:limit]
    return [
        replace(merged[chunk_id], score=fused[chunk_id], fusion_score=fused[chunk_id], rank=rank)
        for rank, chunk_id in enumerate(ordered, start=1)
    ]
