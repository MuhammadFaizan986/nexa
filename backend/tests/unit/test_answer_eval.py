"""
The scoring logic of the answer-quality eval (Week 6).

The harness itself needs a database and a paid model, so what is tested here is
everything that decides what the numbers MEAN: reading a judge's verdict,
detecting a refusal, and turning per-question results into the report's rates.
Get these wrong and the report is confidently misleading, which is worse than
having no report at all.
"""

from decimal import Decimal

import pytest

from app.services.generation.prompts import NO_ANSWER
from eval.run_answer_eval import (
    AnswerResult,
    Verdict,
    build_judge_prompt,
    estimated_cost,
    parse_verdict,
    said_i_dont_know,
    summarize,
)
from eval.run_eval import Question


def question(
    qid: str = "prop-001", answerable: bool = True, expected_answer: str | None = "60 days"
) -> Question:
    return Question(
        id=qid,
        domain="property",
        question="What is the notice period for Unit 4B?",
        type="factual",
        answerable=answerable,
        expected_sources=[{"document": "lease_unit_4b.pdf", "page": 3}],
        expected_answer=expected_answer,
    )


def result(
    q: Question | None = None,
    *,
    answer: str = "60 days written notice [1]",
    refused: bool = False,
    has_citation: bool = True,
    citations_valid: bool = True,
    cited_expected_source: bool | None = True,
    verdict: Verdict | None = None,
) -> AnswerResult:
    return AnswerResult(
        question=q or question(),
        answer=answer,
        refused=refused,
        has_citation=has_citation,
        citations_valid=citations_valid,
        cited_expected_source=cited_expected_source,
        model="claude-opus-5",
        retrieval_ms=100,
        llm_ms=900,
        cost_usd=Decimal("0.01"),
        verdict=verdict,
    )


# ------------------------------------------------------- reading the verdict


def test_a_plain_json_verdict_is_read():
    verdict = parse_verdict(
        '{"correctness": "correct", "faithful": true, "citations_support": true, "note": ""}'
    )
    assert verdict == Verdict("correct", True, True, "")


def test_a_fenced_verdict_with_chatter_is_still_read():
    """Models add code fences and a friendly sentence however firmly you ask."""
    verdict = parse_verdict(
        'Sure! Here is my grade:\n```json\n{"correctness": "PARTIAL", "faithful": false, '
        '"citations_support": true, "note": "missed the second clause"}\n```\nHope that helps.'
    )
    assert verdict is not None
    assert verdict.correctness == "partial"
    assert verdict.faithful is False
    assert verdict.note == "missed the second clause"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "I think it was quite good really",
        '{"correctness": "excellent", "faithful": true, "citations_support": true}',  # not a grade
        '{"faithful": true}',  # missing fields
    ],
)
def test_an_unreadable_verdict_is_no_verdict(text):
    # Never guess: a question with no verdict is excluded from the judged rates
    # rather than counted as a pass.
    assert parse_verdict(text) is None


# ------------------------------------------------------- detecting a refusal


def test_the_fixed_refusal_sentence_is_detected():
    assert said_i_dont_know(NO_ANSWER)
    assert said_i_dont_know(f"  {NO_ANSWER}  ")
    assert said_i_dont_know("I don't have enough information about the pool.")


def test_a_real_answer_is_not_a_refusal():
    assert not said_i_dont_know("The tenant must give 60 days written notice [1].")
    # Mentioning what is missing, while still answering, is not a refusal.
    assert not said_i_dont_know(
        "The notice period is 60 days [1]. The passages don't mention the pet bond."
    )


# ------------------------------------------------------------- the summary


def test_refusals_are_counted_separately_for_each_kind_of_question():
    results = [
        # answerable, answered — good
        result(),
        # answerable, refused — a false refusal
        result(question("prop-002"), refused=True, has_citation=False),
        # unanswerable, refused — good
        result(question("prop-003", answerable=False, expected_answer=None), refused=True),
        # unanswerable, answered anyway — the dangerous one
        result(question("prop-004", answerable=False, expected_answer=None), refused=False),
    ]
    summary = summarize(results)

    assert summary["answerable"] == 2 and summary["unanswerable"] == 2
    assert summary["refusal_correct_unanswerable"] == 0.5
    assert summary["false_refusal_answerable"] == 0.5


def test_citation_rates_ignore_refusals():
    """A refusal has nothing to cite, so counting it would drag the rate down."""
    results = [
        result(),
        result(question("prop-002"), refused=True, has_citation=False, citations_valid=True),
    ]
    summary = summarize(results)

    assert summary["answers_with_citation"] == 1.0  # 1 of 1 real answer, not 1 of 2


def test_correctness_only_counts_questions_with_a_reference_answer():
    results = [
        result(verdict=Verdict("correct", True, True)),
        result(question("prop-002"), verdict=Verdict("partial", True, True)),
        # No reference answer to compare against: judged for faithfulness only.
        result(
            question("prop-003", expected_answer=None),
            verdict=Verdict("unknown", False, True),
        ),
    ]
    summary = summarize(results)

    assert summary["correct"] == 0.5 and summary["partial"] == 0.5  # 2 comparable questions
    assert summary["faithful"] == round(2 / 3, 4)  # faithfulness applies to all three
    assert summary["judged"] == 3


def test_unjudged_questions_are_excluded_not_counted_as_passes():
    results = [result(verdict=Verdict("correct", True, True)), result(question("prop-002"))]
    summary = summarize(results)

    assert summary["judged"] == 1
    assert summary["faithful"] == 1.0  # 1 of 1 judged, not 1 of 2


def test_a_run_with_no_unanswerable_questions_reports_nothing_rather_than_zero():
    summary = summarize([result()])

    # 0% correct refusals would read as a failure; there was simply nothing to refuse.
    assert summary["refusal_correct_unanswerable"] is None


def test_cost_is_reported_per_run_and_per_hundred_questions():
    summary = summarize([result(), result(question("prop-002"))])

    assert summary["cost_usd_total"] == pytest.approx(0.02)
    assert summary["cost_usd_per_100"] == pytest.approx(1.0)


# ------------------------------------------------------------- the estimate


def test_the_estimate_is_shown_before_spending_and_drops_without_a_judge():
    assert estimated_cost(50, judge=True) > estimated_cost(50, judge=False)
    assert estimated_cost(5, judge=True) < estimated_cost(50, judge=True)


def test_the_judge_sees_the_passages_the_answer_was_written_from():
    from tests.unit.test_generation import passage

    prompt = build_judge_prompt(question(), "60 days [1]", [passage("Notice is 60 days.")])

    assert "Notice is 60 days." in prompt
    assert "<reference_answer>60 days</reference_answer>" in prompt
    assert "<assistant_answer>60 days [1]</assistant_answer>" in prompt
