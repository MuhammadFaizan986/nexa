# NEXA architecture: a code-reading guide

This page follows a document and then a question through the code, file by
file, in the order the code runs. Each module's docstring explains why its
technique exists.

Status: **Weeks 0–5 complete**: hybrid retrieval with reranking, contextual
chunk headers, metadata filters, a retrieval eval
([results](evaluation-results.md)), group permissions with Row-Level Security,
and background ingestion.

---

## 1. Request basics (every endpoint)

| Step | File | What happens |
|---|---|---|
| Request id + access log | `backend/app/core/middleware.py` | Assigns `X-Request-ID`, logs method/path/status/duration once per request |
| Auth | `backend/app/core/deps.py` → `get_current_user` | Verifies the JWT, loads the user **by id AND tenant id**, rejects inactive users |
| Tenant | (same) | The tenant is always `user.tenant_id`. No endpoint accepts a tenant id from the client |
| Row-Level Security | `backend/app/db/rls.py` | Sets `app.tenant_id` and switches to an unprivileged role for every transaction, so Postgres itself hides other tenants' rows. Admin tools use `maintenance_mode()` |
| DB session | `backend/app/db/session.py` | One async SQLAlchemy session per request |

Tokens and password hashing live in `backend/app/core/security.py` (Argon2id,
short-lived access token + refresh token, algorithm pinned to HS256).

---

## 2. Ingestion: `POST /api/v1/documents`

```
API:    upload ─▶ validate ─▶ hash + dedup ─▶ store file ─▶ documents row (pending)
               ─▶ ingestion_jobs row (queued) ─▶ Redis ─▶ response (immediately)
Worker: job ─▶ parse ─▶ clean ─▶ chunk ─▶ contextual header ─▶ embed (batched)
            ─▶ chunks rows ─▶ status=ready (or failed, with the reason)
```

| Step | File | Key ideas |
|---|---|---|
| Validate + dedup | `backend/app/api/v1/documents.py` | Extension allow-list, size limit while streaming to disk, magic-byte check, SHA-256 dedup per tenant (also enforced by a UNIQUE constraint) |
| Store original | `backend/app/services/storage.py` | Storage key = `<tenant>/<document id>.<ext>`: never the user's filename |
| Queue | `backend/app/services/ingestion/jobs.py` | Creates an `ingestion_jobs` row and hands it to Celery (`INGESTION_MODE=inline` runs it in the request instead). Records attempts, timings and errors |
| Worker | `backend/app/workers/` | One long-lived event loop per worker process; enters `tenant_scope()` because there's no request to set the tenant |
| Orchestrate | `backend/app/services/ingestion/pipeline.py` | parse → clean → chunk → embed → store, in one transaction. Any error → `status=failed` + readable `error_message` |
| Parse | `backend/app/services/ingestion/parsers/` | Every format becomes the same list of `Block`s (heading / paragraph, with page number). PDF headings come from font size; DOCX from heading styles; MD from `#`; HTML from `<h1>`…`<h6>` (navigation stripped); CSV rows become "Column: value" lines; TXT from a cautious heuristic |
| Clean | `backend/app/services/ingestion/cleaner.py` | Unicode normalisation; removes running headers/footers that repeat at the top/bottom of many pages |
| Chunk | `backend/app/services/ingestion/chunker.py` | Structure-aware: new section → new chunk; packs whole sentences up to ~600 tokens; 80-token overlap inside long sections; merges tiny sections only into siblings |
| Embed | `backend/app/services/embeddings/` | OpenAI `text-embedding-3-small` or Gemini `gemini-embedding-001` (free tier; separate document/query task types), batched with retries; or the offline `fake` provider for tests |
| Store chunks | `backend/app/db/models/document.py` | `chunks` has the vector (HNSW index), a generated `tsvector` (GIN index, used from Week 3), page range, section title, and copied `tenant_id`/`collection_id` for fast permission filtering |

Try it on the sample data without the API:

```bash
docker compose run --rm --no-deps api python -c "
from pathlib import Path
from app.services.ingestion.parsers import parse_file
from app.services.ingestion.cleaner import clean_document
from app.services.ingestion.chunker import chunk_blocks
f = Path('/sample_data/property/lease_unit_4b.pdf')
for c in chunk_blocks(clean_document(parse_file(f, f.name)).blocks):
    print(c.index, f'p{c.page_start}-{c.page_end}', c.section_title, c.token_count)"
```

---

## 3. Question answering: `POST /api/v1/chat`

```
question ─▶ permission scope ─▶ embed ─▶ semantic (40) + keyword (40) + identifier search
         ─▶ RRF fusion ─▶ top 20 ─▶ reranker ─▶ top 5        (all filtered inside the SQL)
         ─▶ relevance gate ──(too low)──▶ "I don't have enough information…" (no LLM call)
         ─▶ prompt with numbered <passage>s ─▶ LLM streams tokens (SSE) ─▶ validate [n] citations
         ─▶ save message + citations + usage + latency
```

| Step | File | Key ideas |
|---|---|---|
| Scope + conversation | `backend/app/api/v1/chat.py` | Resolves readable collections; loads/creates the conversation (must belong to this user); saves the question; returns a `StreamingResponse` |
| Permissions | `backend/app/services/retrieval/permissions.py` | Deny by default: `tenant_wide` collections for everyone, `restricted` ones only through a group grant (read or write); admins see all of their tenant. Asking for an unreadable collection → 404 |
| Pipeline | `backend/app/services/retrieval/retriever.py` | `retrieve()`: the same code serves `/search`, `/chat` and `make eval`; every stage can be switched on/off |
| Semantic | `backend/app/services/retrieval/semantic.py` | `embedding <=> query` with `WHERE tenant_id = … AND collection_id = ANY(…)` **inside** the query; pgvector iterative scans so filters don't starve the result list |
| Keyword | `backend/app/services/retrieval/keyword.py` | Postgres full-text (`tsv @@ query`, `ts_rank_cd`); ANDs turned into ORs for natural questions; identifier boost because Postgres ranking has no IDF |
| Fusion | `backend/app/services/retrieval/hybrid.py` | Reciprocal Rank Fusion: `1 / (60 + rank)` summed across lists; keeps every stage's score |
| Rerank | `backend/app/services/retrieval/reranker.py` | Cohere Rerank (cross-encoder) re-scores the top 20; paced for trial keys; fake reranker for tests |
| Filters | `backend/app/services/retrieval/base.py` | `SearchFilters`: document metadata (`@>` on the GIN index) and a date range, as extra SQL clauses |
| Relevance gate | `backend/app/services/retrieval/retriever.py` | Rerank score < `MIN_RERANK_SCORE` (or, without a reranker, similarity < `MIN_RELEVANCE_SCORE`) → "I don't know" without calling the LLM |
| Prompt | `backend/app/services/generation/prompts.py` | Rules in the system prompt; passages XML-escaped inside `<passage id="n">` tags; old `[n]` markers stripped from history |
| LLM | `backend/app/services/generation/llm.py` | One interface, three providers: Claude (default, with server-side refusal fallback), OpenAI, fake |
| Citations | `backend/app/services/generation/citations.py` | Parse `[1]`, `[2][3]`, `[2, 3]`; drop numbers that don't match a passage; pick the best-matching sentence as the snippet |
| Stream | `backend/app/services/chat.py` | SSE events: `meta` → many `token` → `done` (citations, usage, cost, latency) or `error` |
| Usage | `backend/app/services/usage.py` | Every AI call writes a `usage_events` row with tokens and estimated cost |

---

## 3b. The web UI (`frontend/`)

| Piece | File | What it does |
|---|---|---|
| API client | `frontend/src/lib/api.ts` | The only place that calls the API: tokens, automatic refresh on 401, and the SSE reader that turns the chat stream into tokens |
| Session | `frontend/src/lib/auth.tsx` | Who is signed in; guards every page under `(app)/` |
| Ask | `frontend/src/app/(app)/chat/page.tsx` | Conversations, streamed answers, source chips, thumbs up/down |
| Sources | `frontend/src/components/CitationPanel.tsx` | The quoted sentence, its page/section, and a link to the original file |
| Documents | `frontend/src/app/(app)/documents/page.tsx` | Drag-and-drop upload; polls only while the worker is busy |
| Admin | `frontend/src/app/(app)/admin/page.tsx` | Cost per day, people, groups, collection grants, assistant settings |
| Landing | `frontend/src/app/page.tsx` | The public page: a self-running demo that types a question and streams the answer |
| Pipeline diagram | `frontend/src/components/Pipeline.tsx` | The six stages (chunking → embedding → retrieval → reranking → answer) lighting up one at a time, with a plain-language caption for each |
| Brand | `frontend/src/components/brand.tsx` | The NEXA mark — an "N" drawn as a retrieval path — used as logo, favicon and the assistant's avatar |
| Theme | `frontend/src/components/theme.tsx` | Light / dark / system, remembered per browser, applied in `<head>` so there is no flash of the wrong theme |
| Design tokens | `frontend/src/app/globals.css` | Colours, radii and every animation; each token has a light and a dark value, and all motion stops under `prefers-reduced-motion` |

---

## 4. Data model

```
tenants ─┬─ users
         ├─ collections ── documents ── chunks (vector + tsvector)
         ├─ conversations ── messages ── message_citations ──▶ chunks / documents
         └─ usage_events
```

Migrations are hand-written SQL in `backend/migrations/versions/` (readable as
schema documentation). `alembic check` confirms they match the SQLAlchemy
models.

---

## 5. Known limitations (and the week that fixes them)

| Limitation | Fixed in |
|---|---|
| ~~Semantic search only: exact identifiers like `E-204` can be missed~~ | ✅ Week 3: keyword + identifier boost + RRF (exact identifiers now 100% Hit@5) |
| ~~Chunks don't carry their document's context (4B vs 7A leases)~~ | ✅ Week 3: contextual chunk headers (semantic Hit@1 70% → 85%) |
| Postgres keyword ranking has no IDF (rare words aren't weighted up) | Mitigated by the identifier boost; a BM25 extension (e.g. ParadeDB) is an option at scale |
| Follow-up questions ("and for Unit 7A?") are searched as-is | Week 6: query rewriting |
| ~~Ingestion runs inside the upload request~~ | ✅ Week 4: Celery worker + `ingestion_jobs` |
| ~~No web interface~~ | ✅ Week 5: Next.js UI (`frontend/`) |
| The UI keeps tokens in localStorage (readable by any script on the page) | Week 7: move to httpOnly cookies |
| ~~Restricted collections are admin-only; no groups; no RLS~~ | ✅ Week 4: groups, per-collection grants, Row-Level Security |
| Similarity can't separate answerable from unanswerable questions | Gate on the reranker score; tune `MIN_RERANK_SCORE` from `make eval` |
