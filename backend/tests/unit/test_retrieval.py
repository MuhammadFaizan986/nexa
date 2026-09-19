"""Unit tests for the retrieval building blocks: RRF, filters, rerankers, context headers."""

import uuid
from datetime import date

from app.services.ingestion.pipeline import build_context_header
from app.services.retrieval.base import RetrievedChunk, SearchFilters
from app.services.retrieval.hybrid import reciprocal_rank_fusion
from app.services.retrieval.keyword import extract_identifiers
from app.services.retrieval.reranker import FakeReranker


def chunk(name: str, **scores) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid5(uuid.NAMESPACE_DNS, name),
        document_id=uuid.uuid4(),
        document_title=name,
        filename=f"{name}.md",
        content=f"content of {name}",
        page_start=None,
        page_end=None,
        section_title=None,
        score=0.0,
        rank=0,
        **scores,
    )


def test_rrf_rewards_agreement_between_searches():
    semantic = [chunk("a", similarity=0.9), chunk("b", similarity=0.8), chunk("c", similarity=0.7)]
    keyword = [chunk("c", keyword_score=0.5), chunk("d", keyword_score=0.4)]
    fused = reciprocal_rank_fusion([semantic, keyword], k=60, limit=10)

    names = [c.document_title for c in fused]
    # "c" is only 3rd semantically, but BOTH searches found it, so it wins.
    assert names[0] == "c"
    assert set(names) == {"a", "b", "c", "d"}  # union of both lists
    assert [c.rank for c in fused] == [1, 2, 3, 4]
    top = fused[0]
    assert top.fusion_score == top.score == 1 / 63 + 1 / 61
    # Both searches' scores survive the merge (useful for debugging).
    assert (top.similarity, top.keyword_score) == (0.7, 0.5)


def test_rrf_respects_limit():
    fused = reciprocal_rank_fusion([[chunk(str(i)) for i in range(30)]], limit=5)
    assert len(fused) == 5


def test_filters_build_parameterised_sql():
    sql, params = SearchFilters(
        metadata={"doc_type": "lease"}, date_from=date(2026, 1, 1), date_to=date(2026, 12, 31)
    ).to_sql()
    assert "c.metadata @> CAST(:filter_metadata AS jsonb)" in sql
    assert params == {
        "filter_metadata": '{"doc": {"doc_type": "lease"}}',
        "filter_date_from": "2026-01-01",
        "filter_date_to": "2026-12-31",
    }
    # Values go in as bind parameters, never pasted into the SQL (no injection).
    assert "lease" not in sql
    assert SearchFilters().to_sql() == ("", {})


async def test_fake_reranker_orders_by_question_word_overlap():
    result = await FakeReranker().rerank(
        "What is the notice period to terminate the lease?",
        ["Rent is due monthly.", "The tenant must give notice to terminate the lease."],
        top_n=2,
    )
    assert [h.index for h in result.hits] == [1, 0]
    assert result.hits[0].score > result.hits[1].score


def test_context_header_skips_duplicate_title():
    header = build_context_header(
        "Lease Agreement - Unit 4B", ["Lease Agreement - Unit 4B", "7. Termination"]
    )
    assert header == "Document: Lease Agreement - Unit 4B | Section: 7. Termination"
    assert build_context_header("FAQ", []) == "Document: FAQ"


def test_reranker_input_includes_context_header():
    c = chunk("a", context_header="Document: Lease 4B | Section: Termination")
    assert c.text_with_context.startswith("Document: Lease 4B")
    assert chunk("b").text_with_context == "content of b"


def test_extract_identifiers():
    question = (
        "Which contractor handled WO-6612 for Unit 4B (Clause 7.2, INS-2026-0259) "
        "in 2026 for $2,450?"
    )
    assert extract_identifiers(question) == ["WO-6612", "4B", "7.2", "INS-2026-0259"]
    assert extract_identifiers("What does error code E-204 mean in v2.4.0?") == ["E-204", "v2.4.0"]
    assert extract_identifiers("How many days of annual leave after 3 years?") == []
