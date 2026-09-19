"""The eval harness's metrics and caching, tested without any database or API."""

import pytest

from app.services.retrieval.reranker import FakeReranker
from eval.metrics import (
    RankedChunk,
    best_threshold,
    chunk_matches,
    first_hit_rank,
    percentile,
    recall_at,
    reciprocal_rank,
)
from eval.run_eval import CachedReranker, JsonCache

LEASE_P3 = {"document": "lease.pdf", "page": 3, "section": "7. Termination"}
RULES = {"document": "rules.md", "section": "1. Pets"}


def c(filename, pages=(None, None), sections=()):
    return RankedChunk(filename, pages[0], pages[1], list(sections))


def test_chunk_matching_rules():
    assert chunk_matches(c("lease.pdf", (2, 3), ["7. Termination"]), LEASE_P3)
    assert not chunk_matches(c("lease.pdf", (4, 4), ["7. Termination"]), LEASE_P3)  # page
    assert not chunk_matches(c("lease.pdf", (3, 3), ["8. Vacating"]), LEASE_P3)  # section
    assert not chunk_matches(c("other.pdf", (3, 3), ["7. Termination"]), LEASE_P3)
    assert chunk_matches(c("rules.md", sections=["Rules", "1. Pets"]), RULES)


def test_rank_metrics():
    ranked = [
        c("x.md"),
        c("rules.md", sections=["1. Pets"]),
        c("lease.pdf", (3, 3), ["7. Termination"]),
    ]
    assert first_hit_rank(ranked, [RULES]) == 2
    assert reciprocal_rank(ranked, [RULES]) == 0.5
    assert reciprocal_rank(ranked, [{"document": "missing.md"}]) == 0.0
    # Multi-document question: both sources in the top 3, only one in the top 2.
    assert recall_at(ranked, [RULES, LEASE_P3], 3) == 1.0
    assert recall_at(ranked, [RULES, LEASE_P3], 2) == 0.5


def test_best_threshold_prefers_the_lowest_equally_good_value():
    advice = best_threshold(answerable=[0.9, 0.8, 0.7], unanswerable=[0.3, 0.2])
    assert advice.accuracy == 1.0 and advice.threshold == 0.7
    assert (advice.false_refusals, advice.false_answers) == (0, 0)

    overlapping = best_threshold(answerable=[0.9, 0.5], unanswerable=[0.6, 0.1])
    assert overlapping.accuracy == 0.75
    assert best_threshold([], [0.1]) is None


def test_percentile():
    assert percentile([5, 1, 3, 2, 4], 50) == 3
    assert percentile([10] * 19 + [100], 95) == 10
    assert percentile([], 50) is None


async def test_cached_reranker_calls_the_api_once_per_identical_request(tmp_path):
    cache = JsonCache(tmp_path / "cache.json")
    reranker = CachedReranker(FakeReranker(), cache)
    args = ("notice to terminate", ["notice to terminate the lease", "rent"], 2)
    first = await reranker.rerank(*args)
    second = await reranker.rerank(*args)
    assert reranker.api_calls == 1
    assert [h.index for h in first.hits] == [h.index for h in second.hits]
    # The cache survives a restart (new object, same file).
    again = CachedReranker(FakeReranker(), JsonCache(tmp_path / "cache.json"))
    await again.rerank(*args)
    assert again.api_calls == 0


@pytest.mark.parametrize("value", [0.0, 1.0])
def test_threshold_handles_edges(value):
    assert best_threshold([value], [value]).accuracy == 0.5
