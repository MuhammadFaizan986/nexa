# NEXA architecture: a code-reading guide

This page follows a document and then a question through the code, file by
file, in the order the code runs. Each module's docstring explains why its
technique exists.

Status: **Weeks 0–2 complete**. Semantic search only; hybrid search and
reranking arrive in Week 3.

---

## 1. Request basics (every endpoint)

| Step | File | What happens |
|---|---|---|
| Request id + access log | `backend/app/core/middleware.py` | Assigns `X-Request-ID`, logs method/path/status/duration once per request |
| Auth | `backend/app/core/deps.py` → `get_current_user` | Verifies the JWT, loads the user **by id AND tenant id**, rejects inactive users |
| Tenant | (same) | The tenant is always `user.tenant_id`. No endpoint accepts a tenant id from the client |
| DB session | `backend/app/db/session.py` | One async SQLAlchemy session per request |

Tokens and password hashing live in `backend/app/core/security.py` (Argon2id,
short-lived access token + refresh token, algorithm pinned to HS256).

---

## 2. Ingestion: `POST /api/v1/documents`

```
upload ─▶ validate ─▶ hash + dedup ─▶ store file ─▶ documents row (pending)
       ─▶ parse ─▶ clean ─▶ chunk ─▶ embed (batched) ─▶ chunks rows ─▶ status=ready
```

| Step | File | Key ideas |
|---|---|---|
| Validate + dedup | `backend/app/api/v1/documents.py` | Extension allow-list, size limit while streaming to disk, magic-byte check, SHA-256 dedup per tenant (also enforced by a UNIQUE constraint) |
| Store original | `backend/app/services/storage.py` | Storage key = `<tenant>/<document id>.<ext>`: never the user's filename |
| Orchestrate | `backend/app/services/ingestion/pipeline.py` | Runs synchronously for now; Week 4 calls the same function from a Celery worker. Any error → `status=failed` + readable `error_message` |
| Parse | `backend/app/services/ingestion/parsers/` | Every format becomes the same list of `Block`s (heading / paragraph, with page number). PDF headings come from font size; DOCX from heading styles; MD from `#`; TXT from a cautious heuristic |
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
question ─▶ permission scope ─▶ embed question ─▶ semantic search (top 5, filtered in SQL)
         ─▶ relevance gate ──(too low)──▶ "I don't have enough information…" (no LLM call)
         ─▶ prompt with numbered <passage>s ─▶ LLM streams tokens (SSE) ─▶ validate [n] citations
         ─▶ save message + citations + usage + latency
```

| Step | File | Key ideas |
|---|---|---|
| Scope + conversation | `backend/app/api/v1/chat.py` | Resolves readable collections; loads/creates the conversation (must belong to this user); saves the question; returns a `StreamingResponse` |
| Permissions | `backend/app/services/retrieval/permissions.py` | Deny by default. `tenant_wide` collections for everyone; `restricted` ones only for owners/admins until Week 4 groups. Asking for an unreadable collection → 404 |
| Search | `backend/app/services/retrieval/semantic.py` | `embedding <=> query` with `WHERE tenant_id = … AND collection_id = ANY(…)` **inside** the query; pgvector iterative scans so filters don't starve the result list |
| Relevance gate | `backend/app/services/chat.py` | If the best cosine similarity < `MIN_RELEVANCE_SCORE`, answer "I don't know" without calling the LLM |
| Prompt | `backend/app/services/generation/prompts.py` | Rules in the system prompt; passages XML-escaped inside `<passage id="n">` tags; old `[n]` markers stripped from history |
| LLM | `backend/app/services/generation/llm.py` | One interface, three providers: Claude (default, with server-side refusal fallback), OpenAI, fake |
| Citations | `backend/app/services/generation/citations.py` | Parse `[1]`, `[2][3]`, `[2, 3]`; drop numbers that don't match a passage; pick the best-matching sentence as the snippet |
| Stream | `backend/app/services/chat.py` | SSE events: `meta` → many `token` → `done` (citations, usage, cost, latency) or `error` |
| Usage | `backend/app/services/usage.py` | Every AI call writes a `usage_events` row with tokens and estimated cost |

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
| Semantic search only: exact identifiers like `E-204` or `INS-2026-0259` can be missed | Week 3: keyword + hybrid (RRF) + reranking |
| Chunks don't carry their document's context: the "Termination" section of the 4B and 7A leases look alike, because "Unit 4B" appears only in the title | Week 3: contextual chunk headers |
| Follow-up questions ("and for Unit 7A?") are searched as-is | Week 6: query rewriting |
| Ingestion runs inside the upload request | Week 4: Celery background jobs |
| Restricted collections are admin-only; no groups yet; no RLS | Week 4: groups, collection access, Row-Level Security |
| `MIN_RELEVANCE_SCORE` is a starting guess | Week 3: tune it with the eval set's unanswerable questions |
