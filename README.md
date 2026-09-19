# NEXA: AI Knowledge Base Platform (RAG)

A private, multi-tenant AI knowledge assistant: upload your documents, ask
questions, and get answers with **source citations** (document + page), while
each organisation's data stays isolated and permission-controlled.

Built from scratch (no LangChain/LlamaIndex) with FastAPI, PostgreSQL +
pgvector, OpenAI or Gemini embeddings, and Anthropic Claude, following an
8-week roadmap: hybrid search, reranking and evaluation (Week 3), groups,
Row-Level Security and background jobs (Week 4), a web UI (Week 5), agentic
RAG (Week 6), hardening and benchmarks (Week 7), and deployment (Week 8).

**Status: Weeks 0–2 complete.** Ingestion pipeline, semantic search, and
streaming chat with validated citations. Next up is Week 3: hybrid search,
reranking and the retrieval eval.

---

## Quick start

Requirements: Docker. Nothing else needs to be installed locally.

```bash
cp .env.example .env          # then set your API keys (see "Which API keys?" below)
make up                       # Postgres (pgvector), Redis, API on http://localhost:8010
make seed                     # 3 demo tenants + 16 synthetic documents from sample_data/
make ask Q="What is the notice period for terminating the lease for Unit 4B?"
make ask TENANT=fintech Q="What does error code E-204 mean?"
make ask TENANT=company Q="How many days of annual leave do I get after 3 years?"
```

Interactive API docs: <http://localhost:8010/docs>. Run `make help` for all
shortcuts.

### Which API keys?

- **Embeddings:** either OpenAI (`EMBEDDING_PROVIDER=openai` + `OPENAI_API_KEY`,
  paid) or Google Gemini (`EMBEDDING_PROVIDER=gemini` + `GEMINI_API_KEY`, free
  tier: get a key at <https://aistudio.google.com/apikey>). Gemini's free tier
  may use your data to improve Google's products. That's fine for the synthetic
  sample data, but never use it for real client documents.
- **Answers:** `ANTHROPIC_API_KEY` for Claude. Anthropic has no free tier; new
  Console accounts get a small one-time trial credit.

> **No API keys yet?** Set `EMBEDDING_PROVIDER=fake`, `LLM_PROVIDER=fake` and
> `MIN_RELEVANCE_SCORE=0.05` in `.env`. Everything runs offline, but the fake
> providers only test the plumbing: their answers are not meaningful.
> **Don't mix providers in one database.** Vectors from different embedding
> models aren't comparable, so wipe the data (`docker compose down -v`) after
> switching `EMBEDDING_PROVIDER`.

### Using the API directly (curl)

```bash
API=http://localhost:8010/api/v1

# 1. Create an organisation + owner (also creates a "General" collection)
curl -s -X POST $API/auth/register -H 'Content-Type: application/json' \
  -d '{"tenant_name": "Acme Corp", "email": "me@acme.example.com", "password": "a-strong-password"}'
# -> copy tokens.access_token and default_collection_id from the response
TOKEN=...; COLLECTION=...

# 2. Upload one or more files (PDF, DOCX, MD, TXT); ingestion runs immediately
curl -s -X POST $API/documents -H "Authorization: Bearer $TOKEN" \
  -F collection_id=$COLLECTION -F files=@sample_data/property/lease_unit_4b.pdf \
  -F 'metadata={"doc_type": "lease"}'

# 3. Search only (see what retrieval finds, with scores)
curl -s -X POST $API/search -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"query": "notice period to end the lease"}'

# 4. Ask: the answer streams as Server-Sent Events (-N = don't buffer)
curl -N -X POST $API/chat -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"question": "What is the notice period for terminating the lease?"}'
```

The chat stream sends `meta` (conversation id), many `token` events, then
`done` with the citations (document, page, snippet), token usage, estimated
cost and per-stage latency. Pass `conversation_id` to ask a follow-up.

---

## What's implemented (Weeks 0–2)

- **Ingestion**: PDF (page-accurate, headings found by font size), DOCX
  (heading styles and tables), Markdown and TXT. Cleaning removes repeated
  headers and footers. Structure-aware chunking of ~600 tokens with overlap,
  where every chunk keeps its page range and section. Embeddings are batched.
  Uploads are deduplicated by SHA-256, and failed files keep a readable error.
- **Retrieval**: pgvector HNSW cosine search, filtered by tenant and permitted
  collections *inside* the SQL, with iterative index scans so filtering can't
  starve the results.
- **Answers**: Claude (default) or OpenAI behind one provider interface,
  streamed over SSE. The prompt makes the model cite `[n]` for every claim,
  and invalid citation numbers are dropped and recorded. A relevance gate
  returns "I don't have enough information…" without calling the LLM, and
  document text is treated as data, never as instructions.
- **Platform**: multi-tenant data model, JWT auth (access + refresh), roles
  (owner/admin/member/viewer) and deny-by-default collection permissions.
  Conversation history, per-message token/latency tracking, `usage_events`
  with cost estimates, and structured JSON-ready logs with request ids.
- **Tests**: 67 tests covering parsers, the cleaner, the chunker, citations,
  auth, uploads, search, streaming chat, **cross-tenant isolation and
  restricted-collection leakage**, and the 50-page PDF page-number check.
- **Sample data and eval set**: 16 synthetic documents across three domains
  (property, fintech, company) and 50 evaluation questions (10 of them
  unanswerable). See [`sample_data/README.md`](sample_data/README.md) and
  [`backend/eval/datasets/README.md`](backend/eval/datasets/README.md).

## Tests

```bash
make test        # everything: uses a separate `nexa_test` database, fake providers, no API cost
make test-unit   # parsers / chunker / citations only (no database)
make lint
```

## Project layout

```
backend/
  app/
    api/v1/            HTTP endpoints (auth, collections, documents, search, chat, health)
    core/              settings, logging, security (JWT/passwords), dependencies, middleware
    db/models/         SQLAlchemy models (tenants, documents, chunks, chat, usage)
    schemas/           Pydantic request/response models
    services/
      ingestion/       parsers/ -> cleaner -> chunker -> pipeline
      embeddings/      OpenAI, Gemini + offline fake provider
      retrieval/       permissions + semantic search
      generation/      LLM providers, prompts, citations
      chat.py          the streaming question-answering pipeline
      usage.py         token + cost tracking
  migrations/          Alembic migrations (hand-written SQL)
  tests/               unit/, integration/, security/
  eval/datasets/       50 evaluation questions (JSONL)
docs/architecture.md   code-reading guide: follow a document and a question through the code
sample_data/           synthetic demo documents (+ _source/ and generator)
scripts/               seed_demo_tenants.py, ask.py, generate_sample_data.py
```

**New to the codebase?** Read [`docs/architecture.md`](docs/architecture.md)
first. It walks through the code in the order a request runs, and every
module's docstring explains the RAG concept it implements.

## Key settings (`.env`)

| Variable | Default | Notes |
|---|---|---|
| `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` | `openai` / *(provider default)* | `openai` → `text-embedding-3-small`, `gemini` → `gemini-embedding-001` (free tier). Changing either requires re-indexing (vectors aren't comparable across models) |
| `LLM_PROVIDER` / `LLM_MODEL` | `anthropic` / `claude-opus-5` | e.g. `claude-sonnet-5` or `claude-haiku-4-5` are cheaper. Compare them with the eval harness before switching |
| `LLM_EFFORT` | *(API default)* | `low`…`max`: trades answer depth for latency and cost |
| `ANTHROPIC_REFUSAL_FALLBACK` | `true` | If Claude's safety classifiers decline a request, Anthropic retries it on a fallback model server-side |
| `CHUNK_SIZE_TOKENS` / `CHUNK_OVERLAP_TOKENS` | `600` / `80` | Week 3 experiment: compare 300 / 600 / 1000 |
| `RETRIEVAL_TOP_K` | `5` | Passages given to the LLM |
| `MIN_RELEVANCE_SCORE` | `0.30` | The "I don't know" gate. **A starting guess:** tune it on the eval set's unanswerable questions |

## Security notes

- The tenant always comes from the authenticated user, never from the request
  body. Permission filters run inside the search SQL, before ranking.
- Unknown or inaccessible documents, collections and conversations return
  `404` (not `403`), so their ids can't be probed.
- Passwords are hashed with Argon2id. Logins with a wrong password and with an
  unknown user return the same error in the same time.
- Uploads are checked by extension, size and magic bytes, and stored under
  generated names.
- Logs contain ids, counts and timings, never document text or secrets.
- The sample data is **synthetic**. Tell clients which third-party APIs
  (OpenAI for embeddings, Anthropic for answers) process their documents.
