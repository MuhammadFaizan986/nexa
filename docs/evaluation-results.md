# Retrieval evaluation results

Measured with `make eval` on 2026-09-19. Every number below comes from an actual
run. Raw reports are written to `backend/eval/reports/` and stored in the
`eval_runs` / `eval_results` tables.

## Method

- **Dataset:** 50 questions in `backend/eval/datasets/` over 16 synthetic
  documents in 3 demo tenants (property, fintech, company). 40 are answerable:
  19 factual, 7 exact identifiers, 7 paraphrased, 5 multi-document and 2
  follow-ups. The other 10 are unanswerable. Each answerable question lists
  where its answer lives (document + page and/or section).
- **What's measured:** retrieval only, with no LLM involved. For each question
  the harness runs the same `retrieve()` code the chat uses and checks where the
  chunk containing the answer ranks.
- **Metrics:**
  - **Hit@k:** the answer is in the top k. The chat sends the top 5 to the LLM,
    so Hit@5 is "could the LLM answer?".
  - **MRR@10:** the average of 1/rank of the first correct chunk.
  - **Recall@5:** the share of a question's sources in the top 5. This matters
    for multi-document questions.
- **Index:** 179 chunks, `gemini-embedding-001` (1536 dims), chunks of about 600
  tokens with 80-token overlap, 40 candidates per search list, RRF k = 60.

## Results

| Index / change | Config | Hit@1 | Hit@3 | Hit@5 | MRR@10 | Recall@5 |
|---|---|---|---|---|---|---|
| Baseline | semantic | 70% | 85% | 92% | 0.79 | 90% |
| | keyword | 60% | 85% | 85% | 0.73 | 81% |
| | hybrid (RRF) | 70% | 85% | 95% | 0.79 | 92% |
| + contextual chunk headers | semantic | **85%** | **95%** | 95% | **0.90** | 92% |
| | keyword | 70% | 82% | 85% | 0.77 | 81% |
| | hybrid (RRF) | 80% | 90% | 92% | 0.85 | 90% |
| + identifier boost | semantic | 85% | 95% | 95% | 0.90 | 92% |
| | keyword | 65% | 82% | 88% | 0.76 | 82% |
| | hybrid (RRF) | 82% | **95%** | **95%** | 0.88 | 92% |
| | **hybrid + Cohere Rerank** | **92%** | **98%** | **98%** | **0.95** | **96%** |

Hit@5 by question type, final index:

| Config | Exact identifier | Factual | Paraphrased | Multi-document | Follow-up |
|---|---|---|---|---|---|
| semantic | 86% | 100% | 100% | 100% | 50% |
| keyword | 100% | 100% | 57% | 100% | 0% |
| hybrid | 100% | 100% | 100% | 100% | 0% |
| hybrid + rerank | **100%** | **100%** | **100%** | **100%** | 50% |

The reranker (`rerank-v4.0-pro`) re-scores the top 20 hybrid candidates. The
only question it still misses is the follow-up "And what about Unit 7A?".

**Latency.** Database retrieval takes p50 2–3 ms and p95 ≤ 5 ms at 179 chunks.
The **reranker adds p50 1.16 s and p95 3.6 s** (Cohere API time on a trial key,
excluding NEXA's own pacing). Embedding the question (Gemini) adds about
0.7–1.3 s. So reranking buys +10 points of Hit@1 for about a second per
question. Options if that's too slow: `rerank-v4.0-fast`, fewer candidates
(`RERANK_CANDIDATES`), or a production key. Scale benchmarks come in Week 7.

## The "I don't know" gate

When the best passage's score is below the threshold, NEXA answers "I don't
have enough information" without calling the LLM.

| Signal | Answerable (40) | Unanswerable (10) | Separation |
|---|---|---|---|
| Cosine similarity | min 0.566 | up to 0.744 | none: any threshold that refuses an unanswerable question also refuses real ones |
| **Rerank score** | min **0.834** | 0.34 (off-topic) to 0.91 | good for off-topic questions, partial for near-misses |

We set `MIN_RERANK_SCORE=0.80`. On this set it refused **0 of 40** answerable
questions and stopped **5 of 10** unanswerable ones before the LLM. The
suggested optimum of 0.834 sits exactly on a real question's score, so it
leaves no margin, and wrongly refusing a real question hurts trust more than
letting the LLM see a near-miss.

The unanswerable questions were written as **near-misses**, which makes them
hard. The highest-scoring one was "How much is the pet bond for the cat at Unit
4B?" (0.91). The lease has a general $4,900 bond and a cat clause, but no pet
bond. The retrieved Bond section really *is* relevant, so no gate can catch
this; the LLM has to notice that the specific fact is missing. Measuring that
is the Week 6 generation eval. Truly off-topic questions ("Can employees bring
their dogs to the office?", 0.34) are refused cheaply.

## What we learned

1. **Contextual chunk headers were the biggest single win.** Semantic Hit@1 went
   from 70% to 85% and MRR from 0.79 to 0.90. A chunk like "7. Term and
   Termination… 60 days written notice" never says *which* lease it belongs to.
   Prefixing "Document: Residential Tenancy Agreement - Unit 4B | Section: 7.
   Term and Termination" to what gets embedded fixes that.
2. **Keyword search alone is the weakest overall, but essential for
   identifiers.** It gets only 57% of paraphrased questions right, because the
   wording differs from the document's, yet it gets 100% of exact identifiers
   once they're boosted.
3. **Postgres full-text ranking has no IDF.** Postgres doesn't know that
   `WO-6612` is rare and decisive while "work order" appears in 50 chunks, so in
   "Which contractor was given work order WO-6612?" the right chunk ranked 14th.
   The fix: pull identifier-like tokens out of the question and fuse an
   identifier-only search into RRF. Hybrid's exact-identifier Hit@5 rose from
   86% to 100%.
4. **Similarity can't decide "I don't know".** Unanswerable questions reached a
   top similarity of 0.744, while one answerable question scored only 0.566, so
   no threshold separates them. With the best threshold (0.566), all 10
   unanswerable questions would still reach the LLM. This is why the plan gates
   on the **reranker's** score instead (plan 9.4).
5. **Follow-up questions fail, as expected.** "And what about Unit 7A?" is
   searched without its conversation. That's Week 6 (query rewriting).
6. **Hybrid alone vs semantic alone:** equal at Hit@5 (95%), and semantic is
   slightly better at Hit@1 (85% vs 82%). Hybrid finds the most candidates
   (never missing an exact identifier), but fusion alone isn't great at
   picking the *best* one.
7. **The reranker adds that first-place precision.** Hybrid + rerank reached
   Hit@1 92% and MRR 0.95, the best on every metric. Its score is also the
   only usable "I don't know" signal. The cost is about 1.2 s per question.

## Caveats

- **This is a small set.** With 40 scored questions, one question is 2.5
  percentage points, so treat differences of a single question as noise.
- **The questions and documents were written together** (both synthetic, by
  the AI assistant, in Week 0). Add questions written independently, ideally by
  someone who hasn't read the code, before quoting these numbers to clients.
- **The contextual-header comparison re-indexed the same documents**, with
  nothing else changed. The reranker has only been measured on the final index.
- **The rerank threshold is tuned on 40 answerable questions.** If users get
  "I don't know" for questions the documents do answer, lower
  `MIN_RERANK_SCORE` (0.75 still stops 2 of 10 unanswerable questions here).

## Reproduce

```bash
make seed                                  # demo tenants + documents (idempotent)
make reindex                               # after changing chunking/embedding/headers
make eval ARGS='--label my-experiment'     # all configurations
make eval ARGS='--config hybrid_rerank --label rerank'
```

Question embeddings and reranker results are cached in `backend/eval/.cache/`,
so re-running against an unchanged index makes no API calls.
