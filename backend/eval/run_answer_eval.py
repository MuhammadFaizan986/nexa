"""
Answer-quality evaluation (plan section 11.2, Week 6): `make eval-answers`.

The retrieval eval (run_eval.py) asks "did we find the right passage?". This
one asks the four questions that decide whether an answer can be trusted:

    Correctness        Does it say what the reference answer says?
    Faithfulness       Is every claim supported by the passages it was given?
    Citation support   Do the cited passages actually support those sentences?
    Refusal accuracy   Does it say "I don't know" exactly when it should —
                       and NOT when the answer is right there?

The first three need a model to judge them (a human would take an afternoon per
run); the fourth is a string comparison and costs nothing. That split matters,
because this is the first part of NEXA that spends real money on every run:

    --no-judge     free: refusals, citation validity, latency (answers still
                   cost model calls unless everything is cached)
    --limit 5      a handful of questions, to check the harness before paying
                   for all 50

Everything is cached in eval/.cache/answers.json, keyed by the question, the
passages and the models involved, so re-running an unchanged setup is free and
only what actually changed is paid for again.

    docker compose exec api python -m eval.run_answer_eval --limit 5
    docker compose exec api python -m eval.run_answer_eval --yes
    docker compose exec api python -m eval.run_answer_eval --no-judge --yes

Each run prints a report, saves it to eval/reports/, and stores it in the
eval_runs / eval_results tables next to the retrieval runs.
"""

import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from app.core.config import get_settings
from app.db.models import EvalResult, EvalRun
from app.db.rls import maintenance_mode, tenant_scope
from app.db.session import SessionLocal, engine
from app.services.generation.citations import resolve_citations
from app.services.generation.llm import get_llm_provider
from app.services.generation.prompts import (
    NO_ANSWER,
    build_system_prompt,
    build_user_message,
    format_passages,
)
from app.services.retrieval.reranker import Reranker, get_reranker
from app.services.retrieval.retriever import passes_relevance_gate, retrieve
from app.services.usage import estimate_cost, estimate_rerank_cost
from eval.metrics import RankedChunk, chunk_matches, percentile
from eval.run_eval import (
    DOMAIN_TENANTS,
    REPORTS_DIR,
    CachedReranker,
    JsonCache,
    Question,
    cache_key,
    embed_questions,
    load_questions,
    load_scope,
)

CACHE_FILE = REPORTS_DIR.parent / ".cache" / "answers.json"

# Bump this when the judge's rubric changes: it is part of the cache key, so
# old verdicts are re-judged instead of silently mixing two standards.
RUBRIC_VERSION = "1"

# A judge is grading, not answering, so it does not need the strongest model —
# but it does need to be good at close reading. Sonnet is the balance; override
# with --judge-model.
DEFAULT_JUDGE_MODEL = "claude-sonnet-5"

JUDGE_SYSTEM = """\
You grade a document assistant's answers. You are strict, terse and consistent.

You are given a question, the passages the assistant was shown, the answer it \
produced, and (usually) a short reference answer written by a human.

Grade three things:

1. "correctness": does the answer state what the reference answer states?
   - "correct": the key facts match (wording may differ).
   - "partial": some of it matches, or it is hedged or incomplete.
   - "incorrect": it contradicts the reference or misses the point.
   - "unknown": there is no reference answer to compare against.
2. "faithful": is EVERY factual claim in the answer supported by the passages? \
An answer that adds outside knowledge, or states something the passages do not \
say, is not faithful — even if it happens to be true.
3. "citations_support": each sentence carries citation markers like [1]. Do the \
cited passages actually support the sentence they are attached to? If the answer \
makes no factual claims (a refusal), return true.

Reply with ONLY a JSON object, no code fence, no commentary:
{"correctness": "correct|partial|incorrect|unknown", "faithful": true|false, \
"citations_support": true|false, "note": "<10 words on the main problem, or ''>"}"""


@dataclass
class Verdict:
    correctness: str
    faithful: bool
    citations_support: bool
    note: str = ""


@dataclass
class AnswerResult:
    question: Question
    answer: str
    refused: bool
    # Free checks, computed without a model:
    has_citation: bool
    citations_valid: bool  # every [n] pointed at a passage we really sent
    cited_expected_source: bool | None  # did a cited passage match the gold source?
    model: str | None  # the model that actually answered
    retrieval_ms: int
    llm_ms: int
    cost_usd: Decimal
    passages: list = field(default_factory=list)
    citations: list = field(default_factory=list)
    verdict: Verdict | None = None


def strip_json(text: str) -> str:
    """Models like fences even when told not to. Take the first {...} block."""
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    body = fenced.group(1) if fenced else text
    braces = re.search(r"\{.*\}", body, re.DOTALL)
    return (braces.group(0) if braces else body).strip()


def parse_verdict(text: str) -> Verdict | None:
    """A verdict we can't read is no verdict — never a guessed one."""
    try:
        data = json.loads(strip_json(text))
        correctness = str(data["correctness"]).lower()
        if correctness not in {"correct", "partial", "incorrect", "unknown"}:
            return None
        return Verdict(
            correctness=correctness,
            faithful=bool(data["faithful"]),
            citations_support=bool(data["citations_support"]),
            note=str(data.get("note", ""))[:120],
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def build_judge_prompt(question: Question, answer: str, passages: list) -> str:
    reference = question.expected_answer or "(none provided)"
    return (
        f"{format_passages(passages)}\n\n"
        f"<question>{question.question}</question>\n"
        f"<reference_answer>{reference}</reference_answer>\n"
        f"<assistant_answer>{answer}</assistant_answer>\n\n"
        "Grade the assistant's answer."
    )


def said_i_dont_know(answer: str) -> bool:
    """
    Did the assistant decline to answer?

    The prompt fixes one exact sentence for this (prompts.NO_ANSWER), which is
    precisely so that detecting a refusal is a string comparison rather than
    another judgement call.
    """
    normalised = answer.strip().lower()
    return NO_ANSWER.lower() in normalised or normalised.startswith("i don't have enough")


async def answer_question(
    question: Question,
    scope,
    vectors: dict,
    reranker: Reranker | None,
    cache: JsonCache,
    tenant_name: str,
) -> AnswerResult:
    """
    Answer one eval question through the production path.

    Same retrieval, same relevance gate, same prompt as a real request — if any
    of that differs, the numbers describe a system nobody is using.
    """
    settings = get_settings()

    started = time.perf_counter()
    with tenant_scope(scope.tenant_id):
        async with SessionLocal() as session:
            retrieval = await retrieve(
                session,
                tenant_id=scope.tenant_id,
                collection_ids=scope.collection_ids,
                query=question.question,
                # The number of passages a real chat request sends — measuring
                # more would flatter the answers.
                top_k=settings.retrieval_top_k,
                query_vector=vectors.get(question.id),
                reranker=reranker,
            )
    retrieval_ms = round((time.perf_counter() - started) * 1000)
    passages = retrieval.chunks

    cost = Decimal(0)
    if retrieval.embedding_model:
        cost += estimate_cost(retrieval.embedding_model, retrieval.embedding_tokens) or Decimal(0)
    if retrieval.reranked and retrieval.reranker_model:
        cost += estimate_rerank_cost(retrieval.reranker_model) or Decimal(0)

    # The gate refuses before any model call — exactly as in services/chat.py.
    if not passes_relevance_gate(retrieval):
        return AnswerResult(
            question=question,
            answer=NO_ANSWER,
            refused=True,
            has_citation=False,
            citations_valid=True,
            cited_expected_source=None,
            model=None,  # the gate refused before any model was called
            retrieval_ms=retrieval_ms,
            llm_ms=0,
            cost_usd=cost,
            passages=passages,
        )

    llm = get_llm_provider()
    system = build_system_prompt(tenant_name)
    user = build_user_message(question.question, passages)
    key = cache_key(
        "answer",
        question.id,
        settings.llm_model,
        [str(p.chunk_id) for p in passages],
        question.question,
    )
    cached = cache.get(key)
    started = time.perf_counter()
    if cached:
        answer, model, tokens_in, tokens_out, llm_ms = (
            cached["answer"],
            cached["model"],
            cached["input_tokens"],
            cached["output_tokens"],
            cached["llm_ms"],
        )
    else:
        completion = await llm.complete(
            system=system, messages=[{"role": "user", "content": user}], max_tokens=1000
        )
        answer, model = completion.text, completion.model
        tokens_in, tokens_out = completion.input_tokens, completion.output_tokens
        llm_ms = round((time.perf_counter() - started) * 1000)
        cache.set(
            key,
            {
                "answer": answer,
                "model": model,
                "input_tokens": tokens_in,
                "output_tokens": tokens_out,
                "llm_ms": llm_ms,
            },
        )
    cost += estimate_cost(model, tokens_in, tokens_out) or Decimal(0)

    cited = resolve_citations(answer, passages)
    ranked_cited = [
        RankedChunk(c.filename, c.page_start, c.page_end, c.sections or [])
        for c in passages
        if any(citation.chunk_id == c.chunk_id for citation in cited.citations)
    ]
    matched = (
        any(
            chunk_matches(chunk, source)
            for chunk in ranked_cited
            for source in question.expected_sources
        )
        if question.answerable and question.expected_sources and ranked_cited
        else None
    )

    return AnswerResult(
        question=question,
        answer=answer,
        refused=said_i_dont_know(answer),
        has_citation=bool(cited.citations),
        citations_valid=not cited.invalid_numbers,
        cited_expected_source=matched,
        model=model,
        retrieval_ms=retrieval_ms,
        llm_ms=llm_ms,
        cost_usd=cost,
        passages=passages,
        citations=cited.citations,
    )


async def judge(
    result: AnswerResult, cache: JsonCache, model: str
) -> tuple[Verdict | None, Decimal]:
    """Grade one answer, reusing a cached verdict when nothing has changed."""
    key = cache_key(
        "verdict",
        RUBRIC_VERSION,
        model,
        result.question.id,
        result.answer,
        [str(p.chunk_id) for p in result.passages],
    )
    cached = cache.get(key)
    if cached:
        return parse_verdict(json.dumps(cached["verdict"])), Decimal(0)

    llm = get_llm_provider()
    prompt = build_judge_prompt(result.question, result.answer, result.passages)
    try:
        completion = await llm.complete(
            system=JUDGE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            model=model,
        )
    except Exception as exc:  # a judge that fails must not lose the whole run
        print(f"  judge failed for {result.question.id}: {type(exc).__name__}")
        return None, Decimal(0)

    verdict = parse_verdict(completion.text)
    cost = estimate_cost(completion.model, completion.input_tokens, completion.output_tokens)
    if verdict:
        cache.set(
            key,
            {
                "verdict": {
                    "correctness": verdict.correctness,
                    "faithful": verdict.faithful,
                    "citations_support": verdict.citations_support,
                    "note": verdict.note,
                }
            },
        )
    return verdict, cost or Decimal(0)


def share(count: int, total: int) -> float | None:
    return round(count / total, 4) if total else None


def summarize(results: list[AnswerResult]) -> dict:
    """
    Turn the per-question results into the numbers that go in the report.

    Refusals are counted separately for answerable and unanswerable questions,
    because they are two different failures: refusing an answerable question
    makes the assistant useless, while answering an unanswerable one makes it
    untrustworthy — and one number hides both.
    """
    answerable = [r for r in results if r.question.answerable]
    unanswerable = [r for r in results if not r.question.answerable]
    judged = [r for r in results if r.verdict]
    judged_answerable = [r for r in answerable if r.verdict]
    with_reference = [r for r in judged_answerable if r.question.expected_answer]
    cited = [r for r in answerable if not r.refused]

    return {
        "questions": len(results),
        "answerable": len(answerable),
        "unanswerable": len(unanswerable),
        # --- free checks
        "refusal_correct_unanswerable": share(
            sum(r.refused for r in unanswerable), len(unanswerable)
        ),
        "false_refusal_answerable": share(sum(r.refused for r in answerable), len(answerable)),
        "answers_with_citation": share(sum(r.has_citation for r in cited), len(cited)),
        "citations_valid": share(sum(r.citations_valid for r in cited), len(cited)),
        "cited_expected_source": share(
            sum(r.cited_expected_source is True for r in cited),
            sum(r.cited_expected_source is not None for r in cited),
        ),
        # --- judged
        "judged": len(judged),
        "correct": share(
            sum(r.verdict.correctness == "correct" for r in with_reference), len(with_reference)
        ),
        "partial": share(
            sum(r.verdict.correctness == "partial" for r in with_reference), len(with_reference)
        ),
        "faithful": share(sum(r.verdict.faithful for r in judged), len(judged)),
        "citations_support": share(sum(r.verdict.citations_support for r in judged), len(judged)),
        # --- operational
        "latency_p50_ms": percentile([r.retrieval_ms + r.llm_ms for r in results], 50),
        "latency_p95_ms": percentile([r.retrieval_ms + r.llm_ms for r in results], 95),
        "cost_usd_total": float(sum(r.cost_usd for r in results)),
        "cost_usd_per_100": float(sum(r.cost_usd for r in results) / len(results) * 100)
        if results
        else 0.0,
    }


def fmt(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def build_report(summary: dict, results: list[AnswerResult], context: dict) -> str:
    lines = [
        "# Answer-quality evaluation",
        "",
        f"Run at {context['run_at']} · {summary['questions']} questions "
        f"({summary['answerable']} answerable, {summary['unanswerable']} unanswerable)",
        f"Answering model: `{context['model']}` · judge: `{context['judge'] or 'none'}`",
        "",
        "## Does it answer correctly?",
        "",
        "| Metric | Result | What it means |",
        "|---|---|---|",
        f"| Correct | {fmt(summary['correct'])} | matches the reference answer |",
        f"| Partially correct | {fmt(summary['partial'])} | incomplete or hedged |",
        f"| Faithful | {fmt(summary['faithful'])} | every claim supported by the passages |",
        f"| Citations support the claim | {fmt(summary['citations_support'])} | the cited "
        "passage really says it |",
        "",
        "## Does it know when to stop?",
        "",
        "| Metric | Result | What it means |",
        "|---|---|---|",
        f"| Correct refusals | {fmt(summary['refusal_correct_unanswerable'])} | said \"I don't "
        "know\" when the documents don't cover it |",
        f"| False refusals | {fmt(summary['false_refusal_answerable'])} | refused although the "
        "answer was there (lower is better) |",
        "",
        "## Are the citations usable?",
        "",
        "| Metric | Result | What it means |",
        "|---|---|---|",
        f"| Answers with a citation | {fmt(summary['answers_with_citation'])} | every answer "
        "should carry one |",
        f"| Citation numbers valid | {fmt(summary['citations_valid'])} | no [n] pointing at "
        "nothing |",
        f"| Cited the expected source | {fmt(summary['cited_expected_source'])} | the citation "
        "landed on the document the dataset expects |",
        "",
        "## Operational",
        "",
        f"- Latency: p50 {summary['latency_p50_ms']:.0f} ms · p95 "
        f"{summary['latency_p95_ms']:.0f} ms (retrieval + generation)",
        f"- Cost: ${summary['cost_usd_total']:.3f} for this run · "
        f"${summary['cost_usd_per_100']:.2f} per 100 questions",
        "",
    ]

    problems = [
        r
        for r in results
        if (
            r.verdict
            and (r.verdict.correctness in {"incorrect", "partial"} or not r.verdict.faithful)
        )
        or (r.question.answerable and r.refused)
        or (not r.question.answerable and not r.refused)
    ]
    if problems:
        lines += ["## Questions to look at", "", "| id | problem | note |", "|---|---|---|"]
        for r in problems[:15]:
            if r.question.answerable and r.refused:
                problem = "refused an answerable question"
            elif not r.question.answerable and not r.refused:
                problem = "answered an unanswerable question"
            elif r.verdict and not r.verdict.faithful:
                problem = "unfaithful"
            else:
                problem = r.verdict.correctness if r.verdict else "—"
            note = (r.verdict.note if r.verdict else "") or ""
            lines.append(f"| {r.question.id} | {problem} | {note} |")
        lines.append("")

    return "\n".join(lines)


async def store_run(summary: dict, results: list[AnswerResult], context: dict) -> None:
    with maintenance_mode():
        async with SessionLocal() as session:
            run = EvalRun(
                dataset_name="answers",
                config={
                    "model": context["model"],
                    "judge_model": context["judge"],
                    "rubric_version": RUBRIC_VERSION,
                    "label": context["label"],
                },
                metrics=summary,
            )
            session.add(run)
            await session.flush()
            for r in results:
                session.add(
                    EvalResult(
                        run_id=run.id,
                        question_id=r.question.id,
                        retrieved_ids=[str(p.chunk_id) for p in r.passages],
                        answer=r.answer,
                        scores={
                            "refused": r.refused,
                            "has_citation": r.has_citation,
                            "citations_valid": r.citations_valid,
                            "cited_expected_source": r.cited_expected_source,
                            "correctness": r.verdict.correctness if r.verdict else None,
                            "faithful": r.verdict.faithful if r.verdict else None,
                            "citations_support": r.verdict.citations_support if r.verdict else None,
                            "note": r.verdict.note if r.verdict else None,
                            "latency_ms": r.retrieval_ms + r.llm_ms,
                            "cost_usd": float(r.cost_usd),
                        },
                    )
                )
            await session.commit()


def estimated_cost(questions: int, judge: bool) -> float:
    """
    A rough price for the run, printed BEFORE anything is spent.

    Deliberately an over-estimate: ~1.5k input and 250 output tokens for an
    answer, and the same again for a verdict. Cached questions cost nothing, so
    the real figure is usually lower — the report prints what was actually spent.
    """
    per_answer = 0.013
    per_verdict = 0.005 if judge else 0.0
    return questions * (per_answer + per_verdict)


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate answer quality on the eval set.")
    parser.add_argument("--dataset", action="append", choices=list(DOMAIN_TENANTS))
    parser.add_argument("--limit", type=int, help="only the first N questions (cheap smoke test)")
    parser.add_argument("--no-judge", action="store_true", help="free checks only, no LLM judge")
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--label", default="answers")
    parser.add_argument("--no-db", action="store_true")
    parser.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    args = parser.parse_args(argv)

    settings = get_settings()
    domains = args.dataset or list(DOMAIN_TENANTS)
    questions = load_questions(domains)
    if args.limit:
        questions = questions[: args.limit]

    # This is the one part of NEXA that spends money per run, so say what it
    # will cost and get a yes before starting.
    price = estimated_cost(len(questions), not args.no_judge)
    print(
        f"About to answer {len(questions)} questions with {settings.llm_model}"
        + ("" if args.no_judge else f" and grade them with {args.judge_model}")
        + f".\nEstimated cost: up to ${price:.2f} (cached questions are free)."
    )
    if not args.yes:
        if not sys.stdin.isatty():
            print("Re-run with --yes to confirm.")
            return 1
        reply = await asyncio.to_thread(input, "Continue? [y/N] ")
        if reply.strip().lower() not in {"y", "yes"}:
            return 1

    with maintenance_mode():
        async with SessionLocal() as session:
            scopes = {d: await load_scope(session, DOMAIN_TENANTS[d]) for d in domains}
    missing = [DOMAIN_TENANTS[d] for d, scope in scopes.items() if scope is None]
    if missing:
        print(f"Tenants not found: {missing}. Run `make seed` first.")
        return 2

    cache = JsonCache(CACHE_FILE)
    inner = get_reranker()
    reranker = CachedReranker(inner, cache) if inner else None
    vectors = await embed_questions(questions, cache)

    results: list[AnswerResult] = []
    judge_cost = Decimal(0)
    for i, question in enumerate(questions, start=1):
        result = await answer_question(
            question,
            scopes[question.domain],
            vectors,
            reranker,
            cache,
            DOMAIN_TENANTS[question.domain].replace("-", " ").title(),
        )
        if not args.no_judge:
            result.verdict, cost = await judge(result, cache, args.judge_model)
            judge_cost += cost
            result.cost_usd += cost
        results.append(result)
        cache.save()  # save as we go: a crash must not waste what was paid for
        if i % 5 == 0 or i == len(questions):
            print(f"  {i}/{len(questions)} questions")

    summary = summarize(results)
    # Report the model that answered, not the one configured: a run made with a
    # different provider must not be filed under the wrong name.
    answered_by = sorted({r.model for r in results if r.model}) or [settings.llm_model]
    context = {
        "run_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "model": ", ".join(answered_by),
        "judge": None if args.no_judge else args.judge_model,
        "label": args.label,
    }
    report = build_report(summary, results, context)
    print("\n" + report)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    path = REPORTS_DIR / f"answers-{stamp}.md"
    path.write_text(report)
    print(f"Saved {path}")

    if not args.no_db:
        await store_run(summary, results, context)
        print("Stored in eval_runs / eval_results.")

    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
