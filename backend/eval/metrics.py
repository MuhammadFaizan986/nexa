"""
Retrieval metrics (plan section 11.2). Pure functions: no database, no API, so
they're easy to test and to explain to a client.

For each answerable question we know WHERE the answer lives (expected_sources:
document + page and/or section). Given the ranked chunks retrieval returned:

    Hit@k     1 if any expected source is in the top k, else 0.
              "How often is the answer in what the LLM gets to read?" (k = 5 = what
              the chat sends to the LLM), and Hit@1 = "how often is it first?".
    MRR@10    Mean Reciprocal Rank: 1/rank of the first correct chunk (0 if not in
              the top 10), averaged. 1.0 = always first; 0.5 = typically second.
    Recall@5  Share of a question's expected sources found in the top 5. Only
              differs from Hit@5 for multi-document questions, where a good answer
              needs facts from SEVERAL documents.

For the "I don't know" gate we look at the top score for answerable vs
unanswerable questions and find the threshold that separates them best.
"""

import math
from dataclasses import dataclass


@dataclass
class RankedChunk:
    """The facts about a retrieved chunk that matching needs."""

    filename: str
    page_start: int | None
    page_end: int | None
    sections: list[str]


def chunk_matches(chunk: RankedChunk, source: dict) -> bool:
    """
    A chunk counts as the source when it is from the same document AND
    - covers the expected page (PDFs), and
    - touches the expected section (when the dataset names one).
    """
    if chunk.filename != source["document"]:
        return False
    page = source.get("page")
    if (
        page is not None
        and chunk.page_start is not None
        and not chunk.page_start <= page <= (chunk.page_end or chunk.page_start)
    ):
        return False
    section = source.get("section")
    return not section or section in chunk.sections


def first_hit_rank(ranked: list[RankedChunk], sources: list[dict]) -> int | None:
    for rank, chunk in enumerate(ranked, start=1):
        if any(chunk_matches(chunk, source) for source in sources):
            return rank
    return None


def reciprocal_rank(ranked: list[RankedChunk], sources: list[dict], k: int = 10) -> float:
    rank = first_hit_rank(ranked[:k], sources)
    return 1.0 / rank if rank else 0.0


def recall_at(ranked: list[RankedChunk], sources: list[dict], k: int) -> float:
    if not sources:
        return 0.0
    top = ranked[:k]
    found = sum(1 for source in sources if any(chunk_matches(c, source) for c in top))
    return found / len(sources)


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile (p50 = median, p95 = 'slow but not the slowest')."""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(pct / 100 * len(ordered)) - 1)
    return ordered[index]


@dataclass
class ThresholdAdvice:
    threshold: float
    accuracy: float  # share of questions the gate classifies correctly at `threshold`
    false_refusals: int  # answerable questions the gate would refuse
    false_answers: int  # unanswerable questions the gate would let through
    answerable_min: float
    unanswerable_max: float


def best_threshold(answerable: list[float], unanswerable: list[float]) -> ThresholdAdvice | None:
    """
    The gate answers when top_score >= threshold. Try every observed score as the
    threshold and keep the most accurate; on ties prefer the LOWEST one, because
    refusing a question we could answer is worse than letting the LLM see a weak
    context (the prompt still tells it to say "I don't know").
    """
    if not answerable or not unanswerable:
        return None
    total = len(answerable) + len(unanswerable)
    best: ThresholdAdvice | None = None
    for threshold in sorted({*answerable, *unanswerable}):
        false_refusals = sum(1 for s in answerable if s < threshold)
        false_answers = sum(1 for s in unanswerable if s >= threshold)
        accuracy = (total - false_refusals - false_answers) / total
        if best is None or accuracy > best.accuracy:
            best = ThresholdAdvice(
                threshold=threshold,
                accuracy=accuracy,
                false_refusals=false_refusals,
                false_answers=false_answers,
                answerable_min=min(answerable),
                unanswerable_max=max(unanswerable),
            )
    return best
