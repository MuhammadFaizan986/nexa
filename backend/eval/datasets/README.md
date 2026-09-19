# Evaluation datasets

Hand-written questions with known answers, used to measure NEXA's retrieval and answer quality (plan Section 11). There is one file per demo domain, and each question is grounded in the synthetic documents in `sample_data/<domain>/`.

| File | Questions | Documents it covers |
|---|---|---|
| `property.jsonl` | 17 | `sample_data/property/` |
| `fintech.jsonl` | 17 | `sample_data/fintech/` |
| `company.jsonl` | 16 | `sample_data/company/` |
| **Total** | **50** | |

## Format

JSON Lines: one JSON object per line.

```json
{"id": "prop-001",
 "question": "What is the notice period for terminating the lease for Unit 4B?",
 "expected_answer": "60 days written notice",
 "expected_sources": [{"document": "lease_unit_4b.pdf", "page": 3, "section": "7. Term and Termination"}],
 "type": "factual",
 "answerable": true}
```

| Field | Meaning |
|---|---|
| `id` | Stable id (`prop-`, `fin-`, `comp-` prefix). Keep ids unchanged so results can be compared across eval runs. |
| `question` | What the user asks. |
| `history` | *Only on `follow_up` items.* The earlier conversation turns (`[{"role": "user"/"assistant", "content": ...}]`). The question only makes sense with this history. |
| `expected_answer` | A short, checkable phrase or number, not a paragraph. `null` for unanswerable questions. |
| `expected_sources` | Where the answer lives. An empty list for unanswerable questions. See below. |
| `type` | One of the question types below. |
| `answerable` | `false` only for `unanswerable` items. |

### How `expected_sources` are written

- **PDF** (`.pdf`): `{"document": "<file name>", "page": <1-based page>, "section": "<H2 heading on that page>"}`. The page is exact, because the generator never reflows pages.
- **Markdown / DOCX / TXT**: these formats have no fixed pages, so the location is the heading the answer sits under: `{"document": "<file name>", "section": "<exact heading text>"}`.
  - Markdown: the text of a `#`/`##`/`###` heading, e.g. `"E-204 Beneficiary account closed"`.
  - DOCX: the text of a paragraph styled `Heading 1/2/3`, e.g. `"3. Response Times"`.
  - TXT: the numbered UPPERCASE line that starts the section, e.g. `"1. COLLECTING YOUR KEYS"`.
- A `multi_document` item lists several sources. A good answer needs facts from each of them.

For retrieval metrics (Hit@k, MRR), a retrieved chunk counts as a match when it comes from the same `document` and either covers the `page` (for PDFs) or has a matching `section_title`.

## Question types

| Type | Count | What it tests |
|---|---|---|
| `factual` | 19 | Straight lookups ("What is the late payment fee?"). |
| `exact_identifier` | 7 | Codes and identifiers: `E-204`, `INS-2026-0322`, `WO-6612`, `Clause 7.3`, `insufficient_scope`. Keyword/hybrid search should help most here. |
| `paraphrased` | 7 | Worded differently from the document ("How long do I have to warn the landlord before leaving?"). Semantic search should help most here. |
| `multi_document` | 5 | The answer needs facts from two documents. |
| `follow_up` | 2 | Needs the `history` to be understood ("And what about Unit 7A?"). These test query rewriting (Week 6). |
| `unanswerable` | 10 | Plausible near-misses whose answer is **not** in any document of that domain (e.g. a pet bond amount, gym hours, a Pro-plan rate limit). The correct behaviour is "I don't have enough information", not a guess. |

## How these were checked

Every answerable item was checked automatically: the key facts of `expected_answer` were found in the text extracted from the cited page (PDFs, via PyMuPDF) or the cited section (MD/DOCX/TXT). Every unanswerable item was checked by searching all documents in its domain for the terms that would contain the answer. Result: 50/50 passed.

## Please add your own questions

These questions were written by the same process that wrote the documents. That is exactly the risk the plan warns about ("tuning to your own easy questions"). Before you start tuning chunk sizes, prompts or rerankers:

1. Read through all 50 questions and fix any that feel unnatural.
2. Add at least 10 of your own questions, written the way a real tenant, bank customer or employee would ask. Include awkward phrasings, typos and vague questions.
3. Once a question is used in published results, don't change it. Add new ids instead, so older eval runs stay comparable.
