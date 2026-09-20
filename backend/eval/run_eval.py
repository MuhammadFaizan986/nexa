"""
Retrieval evaluation harness (plan section 11): `make eval`.

Runs every question in eval/datasets/*.jsonl through the SAME retrieval code
the chat uses, in four configurations, and measures how often the passage that
contains the answer was found (and how high it ranked):

    semantic       meaning only (pgvector cosine similarity)
    keyword        exact words only (Postgres full-text search)
    hybrid         both, fused with Reciprocal Rank Fusion
    hybrid_rerank  hybrid, then the reranker re-scores the top 20

Only retrieval is measured here, so there are no LLM calls. Answer quality
(faithfulness, citation accuracy) is the Week 6 generation eval.

API usage is kept low: question embeddings and reranker results are cached in
eval/.cache/, so re-running against an unchanged index costs nothing. With a
Cohere trial key (10 reranks/minute) a fresh hybrid_rerank run takes ~5 minutes.

    docker compose exec api python -m eval.run_eval
    docker compose exec api python -m eval.run_eval --config hybrid --config hybrid_rerank
    docker compose exec api python -m eval.run_eval --label "chunk-size-800"

Each run prints a report, saves it to eval/reports/, and stores it in the
eval_runs / eval_results tables.
"""

import argparse
import asyncio
import hashlib
import json
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Chunk, Collection, Document, EvalResult, EvalRun, Tenant
from app.db.rls import maintenance_mode, tenant_scope
from app.db.session import SessionLocal, engine
from app.services.embeddings import get_embedding_provider
from app.services.retrieval.reranker import Reranker, RerankHit, RerankResult, get_reranker
from app.services.retrieval.retriever import retrieve
from eval.metrics import (
    RankedChunk,
    ThresholdAdvice,
    best_threshold,
    chunk_matches,
    first_hit_rank,
    percentile,
    recall_at,
)

EVAL_DIR = Path(__file__).resolve().parent
DATASETS_DIR = EVAL_DIR / "datasets"
REPORTS_DIR = EVAL_DIR / "reports"
CACHE_FILE = EVAL_DIR / ".cache" / "api_cache.json"

# Which demo tenant (created by `make seed`) holds each dataset's documents.
DOMAIN_TENANTS = {
    "property": "harbourview-property-group",
    "fintech": "kestrel-pay",
    "company": "lumen-labs",
}
CONFIGS = {
    "semantic": {"mode": "semantic", "rerank": False},
    "keyword": {"mode": "keyword", "rerank": False},
    "hybrid": {"mode": "hybrid", "rerank": False},
    "hybrid_rerank": {"mode": "hybrid", "rerank": True},
}
TOP_K = 10  # retrieve 10 so MRR@10 can be computed; the chat sends the top 5


# ----------------------------------------------------------------------------- caching


class JsonCache:
    """A tiny persistent key -> value store for API results (one JSON file)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict = json.loads(path.read_text()) if path.exists() else {}

    def get(self, key: str):
        return self.data.get(key)

    def set(self, key: str, value) -> None:
        self.data[key] = value

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data))


def cache_key(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


class CachedReranker(Reranker):
    """
    Wraps the real reranker: an identical (query, candidates) request is answered
    from the cache. The key includes the candidate TEXTS, so re-indexing (e.g.
    adding contextual headers) naturally invalidates it.
    """

    def __init__(self, inner: Reranker, cache: JsonCache) -> None:
        self.inner = inner
        self.model = inner.model
        self.cache = cache
        self.api_calls = 0

    async def rerank(self, query: str, documents: list[str], top_n: int) -> RerankResult:
        key = cache_key("rerank", self.model, query, documents, top_n)
        cached = self.cache.get(key)
        if cached is not None:
            hits = [RerankHit(**hit) for hit in cached["hits"]]
            return RerankResult(hits=hits, model=self.model, latency_ms=cached["latency_ms"])
        result = await self.inner.rerank(query, documents, top_n)
        self.api_calls += 1
        self.cache.set(
            key,
            {
                "hits": [{"index": h.index, "score": h.score} for h in result.hits],
                "latency_ms": result.latency_ms,
            },
        )
        self.cache.save()  # save as we go: a crash mid-run doesn't waste the calls
        return result


# ----------------------------------------------------------------------------- inputs


@dataclass
class Question:
    id: str
    domain: str
    question: str
    type: str
    answerable: bool
    expected_sources: list[dict]
    expected_answer: str | None


def load_questions(domains: list[str]) -> list[Question]:
    questions = []
    for domain in domains:
        for line in (DATASETS_DIR / f"{domain}.jsonl").read_text().splitlines():
            if line.strip():
                item = json.loads(line)
                questions.append(
                    Question(
                        id=item["id"],
                        domain=domain,
                        question=item["question"],
                        type=item["type"],
                        answerable=item["answerable"],
                        expected_sources=item["expected_sources"],
                        expected_answer=item.get("expected_answer"),
                    )
                )
    return questions


@dataclass
class TenantScope:
    tenant_id: uuid.UUID
    collection_ids: list[uuid.UUID]
    index: list[RankedChunk]  # every chunk of the tenant, for the reachability check
    chunks_with_header: int
    embedding_models: set[str]


async def load_scope(session, slug: str) -> TenantScope | None:
    tenant = await session.scalar(select(Tenant).where(Tenant.slug == slug))
    if tenant is None:
        return None
    # The eval searches everything the tenant has (like an owner would).
    collection_ids = list(
        await session.scalars(select(Collection.id).where(Collection.tenant_id == tenant.id))
    )
    rows = await session.execute(
        select(
            Chunk.page_start, Chunk.page_end, Chunk.meta, Chunk.context_header, Document.filename
        )
        .join(Document, Document.id == Chunk.document_id)
        .where(Chunk.tenant_id == tenant.id)
    )
    index, with_header, models = [], 0, set()
    for page_start, page_end, meta, header, filename in rows:
        index.append(RankedChunk(filename, page_start, page_end, meta.get("sections", [])))
        with_header += header is not None
        models.add(meta.get("embedding_model", "?"))
    return TenantScope(tenant.id, collection_ids, index, with_header, models)


def unreachable_sources(question: Question, index: list[RankedChunk]) -> list[dict]:
    """Expected sources that NO chunk in the index matches (a dataset/index mismatch)."""
    return [s for s in question.expected_sources if not any(chunk_matches(c, s) for c in index)]


async def embed_questions(questions: list[Question], cache: JsonCache) -> dict[str, list[float]]:
    embedder = get_embedding_provider()
    dim = get_settings().embedding_dim
    vectors, api_calls = {}, 0
    for q in questions:
        key = cache_key("embed_query", embedder.model, dim, q.question)
        vector = cache.get(key)
        if vector is None:
            vector = (await embedder.embed_query(q.question)).vectors[0]
            cache.set(key, vector)
            api_calls += 1
        vectors[q.id] = vector
    cache.save()
    print(f"Question embeddings: {len(questions)} ({api_calls} new API calls, rest cached)")
    return vectors


# ----------------------------------------------------------------------------- running


@dataclass
class QuestionResult:
    question: Question
    rank: int | None  # rank of the first correct chunk (None = not in the top 10)
    recall5: float
    gate_score: float | None  # what the "I don't know" gate would look at
    latency_ms: int  # searches + rerank API time (excludes client-side pacing waits)
    retrieved: list[dict]


@dataclass
class ConfigRun:
    name: str
    mode: str
    rerank: bool
    reranker_model: str | None
    results: list[QuestionResult] = field(default_factory=list)


async def run_config(
    name: str,
    questions: list[Question],
    scopes: dict[str, TenantScope],
    vectors: dict[str, list[float]],
    reranker: Reranker | None,
) -> ConfigRun:
    spec = CONFIGS[name]
    run = ConfigRun(name, spec["mode"], spec["rerank"], reranker.model if spec["rerank"] else None)
    for i, q in enumerate(questions, start=1):
        scope = scopes[q.domain]
        # Search as that tenant, exactly as a real request would, so Row-Level
        # Security is part of what we measure.
        with tenant_scope(scope.tenant_id):
            async with SessionLocal() as session:
                result = await retrieve(
                    session,
                    tenant_id=scope.tenant_id,
                    collection_ids=scope.collection_ids,
                    query=q.question,
                    mode=spec["mode"],
                    rerank=spec["rerank"],
                    top_k=TOP_K,
                    query_vector=vectors.get(q.id),
                    reranker=reranker if spec["rerank"] else None,
                )
        ranked = [
            RankedChunk(c.filename, c.page_start, c.page_end, c.sections or [])
            for c in result.chunks
        ]
        run.results.append(
            QuestionResult(
                question=q,
                rank=first_hit_rank(ranked, q.expected_sources) if q.answerable else None,
                recall5=recall_at(ranked, q.expected_sources, 5) if q.answerable else 0.0,
                gate_score=(result.top_rerank_score if spec["rerank"] else result.top_similarity),
                latency_ms=sum(
                    result.latency_ms.get(k, 0) for k in ("semantic", "keyword", "rerank")
                ),
                retrieved=[
                    {
                        "chunk_id": str(c.chunk_id),
                        "filename": c.filename,
                        "pages": [c.page_start, c.page_end],
                        "section": c.section_title,
                    }
                    for c in result.chunks
                ],
            )
        )
        if spec["rerank"] and i % 10 == 0:
            print(f"  {name}: {i}/{len(questions)} questions")
    return run


# ----------------------------------------------------------------------------- scoring


def summarize(run: ConfigRun, excluded: set[str]) -> dict:
    scored = [r for r in run.results if r.question.answerable and r.question.id not in excluded]
    n = len(scored) or 1

    def hit(r: QuestionResult, k: int) -> bool:
        return r.rank is not None and r.rank <= k

    by_type: dict[str, list[QuestionResult]] = {}
    for r in scored:
        by_type.setdefault(r.question.type, []).append(r)

    gate_answerable = [r.gate_score for r in scored if r.gate_score is not None]
    gate_unanswerable = [
        r.gate_score for r in run.results if not r.question.answerable and r.gate_score is not None
    ]
    advice = best_threshold(gate_answerable, gate_unanswerable)
    latencies = [r.latency_ms for r in run.results]
    return {
        "questions_scored": len(scored),
        "hit@1": sum(hit(r, 1) for r in scored) / n,
        "hit@3": sum(hit(r, 3) for r in scored) / n,
        "hit@5": sum(hit(r, 5) for r in scored) / n,
        "mrr@10": sum(1.0 / r.rank if r.rank else 0.0 for r in scored) / n,
        "recall@5": sum(r.recall5 for r in scored) / n,
        "hit@5_by_type": {
            t: sum(hit(r, 5) for r in rs) / len(rs) for t, rs in sorted(by_type.items())
        },
        "latency_ms_p50": percentile(latencies, 50),
        "latency_ms_p95": percentile(latencies, 95),
        "gate": advice.__dict__ if advice else None,
    }


# ----------------------------------------------------------------------------- reporting


def pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def build_report(
    label: str,
    runs: list[ConfigRun],
    summaries: dict[str, dict],
    context: dict,
    unreachable: dict[str, list[dict]],
) -> str:
    lines = [
        f"## Retrieval eval: {label}",
        "",
        f"- Run at: {context['run_at']}",
        f"- Questions: {context['answerable']} answerable scored "
        f"(+{context['unanswerable']} unanswerable, used for the gate)",
        f"- Index: {context['chunks']} chunks, embedding model {context['embedding_models']}, "
        f"contextual headers on {context['header_share']} of chunks",
        f"- Chunking: {context['chunk_size']} tokens, overlap {context['overlap']}; "
        f"RRF k={context['rrf_k']}; reranker: {context['reranker_model'] or 'none'}",
        "",
        "| Config | Hit@1 | Hit@3 | Hit@5 | MRR@10 | Recall@5 | p50 ms | p95 ms |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for run in runs:
        s = summaries[run.name]
        lines.append(
            f"| {run.name} | {pct(s['hit@1'])} | {pct(s['hit@3'])} | {pct(s['hit@5'])} | "
            f"{s['mrr@10']:.2f} | {pct(s['recall@5'])} | {s['latency_ms_p50']} | "
            f"{s['latency_ms_p95']} |"
        )

    types = sorted({t for s in summaries.values() for t in s["hit@5_by_type"]})
    lines += ["", "Hit@5 by question type:", "", "| Config | " + " | ".join(types) + " |"]
    lines.append("|---|" + "---|" * len(types))
    for run in runs:
        by_type = summaries[run.name]["hit@5_by_type"]
        lines.append(
            f"| {run.name} | " + " | ".join(pct(by_type.get(t, 0.0)) for t in types) + " |"
        )

    lines += ["", '"I don\'t know" gate (top score: answerable vs unanswerable questions):', ""]
    for run in runs:
        gate = summaries[run.name]["gate"]
        if gate is None:
            lines.append(f"- {run.name}: no comparable score (keyword ranks aren't calibrated)")
            continue
        advice = ThresholdAdvice(**gate)
        score_name = "rerank score" if run.rerank else "similarity"
        lines.append(
            f"- {run.name} ({score_name}): best threshold **{advice.threshold:.3f}** -> "
            f"{pct(advice.accuracy)} correct ({advice.false_refusals} answerable refused, "
            f"{advice.false_answers} unanswerable let through). Answerable min "
            f"{advice.answerable_min:.3f}, unanswerable max {advice.unanswerable_max:.3f}."
        )

    best = max(runs, key=lambda r: (summaries[r.name]["hit@5"], summaries[r.name]["mrr@10"]))
    misses = [
        r
        for r in best.results
        if r.question.answerable
        and r.question.id not in unreachable
        and (r.rank is None or r.rank > 5)
    ]
    lines += ["", f"Misses for the best config ({best.name}), answer not in the top 5:", ""]
    for r in misses or []:
        top = r.retrieved[0] if r.retrieved else {}
        expected = "; ".join(
            f"{s['document']} p{s.get('page', '-')} {s.get('section', '')}".strip()
            for s in r.question.expected_sources
        )
        lines.append(
            f'- {r.question.id} ({r.question.type}) "{r.question.question}" '
            f"expected {expected} | rank {r.rank or '>10'} | top: {top.get('filename')} "
            f"p{top.get('pages', [None])[0]} {top.get('section')}"
        )
    if not misses:
        lines.append("- none")
    if unreachable:
        lines += ["", "Excluded (expected source matches no chunk in the index):", ""]
        lines += [f"- {qid}: {sources}" for qid, sources in unreachable.items()]
    return "\n".join(lines) + "\n"


async def store_runs(
    label: str, domains: list[str], runs: list[ConfigRun], summaries: dict, context: dict
) -> None:
    async with SessionLocal() as session:
        for run in runs:
            eval_run = EvalRun(
                id=uuid.uuid4(),
                dataset_name="+".join(domains),
                config={
                    "label": label,
                    "config": run.name,
                    "mode": run.mode,
                    "rerank": run.rerank,
                    "reranker_model": run.reranker_model,
                    **{
                        k: context[k]
                        for k in (
                            "embedding_models",
                            "header_share",
                            "chunk_size",
                            "overlap",
                            "rrf_k",
                            "candidates",
                        )
                    },
                    "top_k": TOP_K,
                },
                metrics=summaries[run.name],
            )
            session.add(eval_run)
            await session.flush()
            session.add_all(
                EvalResult(
                    run_id=eval_run.id,
                    question_id=r.question.id,
                    retrieved_ids=r.retrieved,
                    scores={
                        "rank": r.rank,
                        "reciprocal_rank": 1.0 / r.rank if r.rank else 0.0,
                        "recall@5": r.recall5,
                        "gate_score": r.gate_score,
                        "latency_ms": r.latency_ms,
                    },
                )
                for r in run.results
            )
        await session.commit()


# ----------------------------------------------------------------------------- main


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate retrieval quality on the eval set.")
    parser.add_argument("--config", action="append", choices=list(CONFIGS), help="repeatable")
    parser.add_argument("--dataset", action="append", choices=list(DOMAIN_TENANTS))
    parser.add_argument("--label", default="eval", help="name for this run, e.g. 'baseline'")
    parser.add_argument("--no-db", action="store_true", help="don't store the run in the DB")
    args = parser.parse_args(argv)

    settings = get_settings()
    domains = args.dataset or list(DOMAIN_TENANTS)
    config_names = args.config or list(CONFIGS)
    questions = load_questions(domains)

    # Reading every tenant's index at once is an admin job, so it opts out of
    # Row-Level Security explicitly (app/db/rls.py).
    with maintenance_mode():
        async with SessionLocal() as session:
            scopes = {d: await load_scope(session, DOMAIN_TENANTS[d]) for d in domains}
    missing = [DOMAIN_TENANTS[d] for d, scope in scopes.items() if scope is None]
    if missing:
        print(f"Tenants not found: {missing}. Run `make seed` first.")
        return 2

    # Semantic search only makes sense if the question is embedded with the SAME
    # model as the chunks. Refuse to produce misleading numbers.
    index_models = set().union(*(s.embedding_models for s in scopes.values()))
    current_model = get_embedding_provider().model
    if index_models != {current_model}:
        print(
            f"Index was embedded with {sorted(index_models)} but EMBEDDING_PROVIDER now uses "
            f"{current_model!r}. Run `make reindex` first."
        )
        return 2

    reranker: Reranker | None = None
    cache = JsonCache(CACHE_FILE)
    if "hybrid_rerank" in config_names:
        inner = get_reranker()
        if inner is None:
            print("Skipping hybrid_rerank: RERANKER_PROVIDER=none (set it to cohere in .env).")
            config_names.remove("hybrid_rerank")
        else:
            reranker = CachedReranker(inner, cache)

    unreachable: dict[str, list[dict]] = {}
    for q in questions:
        if q.answerable:
            lost = unreachable_sources(q, scopes[q.domain].index)
            if lost:
                unreachable[q.id] = lost

    needs_vectors = any(CONFIGS[name]["mode"] != "keyword" for name in config_names)
    vectors = await embed_questions(questions, cache) if needs_vectors else {}

    runs = []
    for name in config_names:
        print(f"Running {name}...")
        runs.append(await run_config(name, questions, scopes, vectors, reranker))
    summaries = {run.name: summarize(run, set(unreachable)) for run in runs}

    total_chunks = sum(len(s.index) for s in scopes.values())
    with_header = sum(s.chunks_with_header for s in scopes.values())
    context = {
        "run_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "answerable": sum(q.answerable and q.id not in unreachable for q in questions),
        "unanswerable": sum(not q.answerable for q in questions),
        "chunks": total_chunks,
        "embedding_models": ", ".join(sorted(index_models)),
        "header_share": pct(with_header / total_chunks if total_chunks else 0.0),
        "chunk_size": settings.chunk_size_tokens,
        "overlap": settings.chunk_overlap_tokens,
        "rrf_k": settings.rrf_k,
        "candidates": settings.retrieval_candidates,
        "reranker_model": reranker.model if reranker else None,
    }
    report = build_report(args.label, runs, summaries, context, unreachable)
    print("\n" + report)
    if reranker is not None:
        print(f"Reranker API calls this run: {reranker.api_calls} (the rest came from the cache)")

    REPORTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    slug = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in args.label)
    (REPORTS_DIR / f"{stamp}_{slug}.md").write_text(report)
    (REPORTS_DIR / f"{stamp}_{slug}.json").write_text(
        json.dumps({"label": args.label, "context": context, "summaries": summaries}, indent=2)
    )
    if not args.no_db:
        await store_runs(args.label, domains, runs, summaries, context)
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
