# Deploying NEXA

The setup this describes: **Railway** runs Postgres and the API, **Vercel** runs
the web UI. It costs roughly $8–10 a month plus what the AI providers charge per
question, and it takes about 40 minutes the first time.

```
  Browser ──▶ Vercel (Next.js UI)
                  │  calls the API directly
                  ▼
            Railway: FastAPI ──▶ Railway: Postgres + pgvector
                  │                     (documents, chunks, vectors)
                  └──▶ Gemini (embeddings) · Cohere (rerank) · Claude (answers)
```

## Two Railway constraints that shape this

1. **Railway's default Postgres has no `vector` extension.** Deploy the
   **pgvector template** instead — adding the extension to an existing instance
   is awkward enough that Railway's own guidance is to start with the template.
2. **A Railway volume cannot be shared between services.** NEXA's API writes an
   uploaded file to disk and the Celery worker reads it back, so the two would
   be looking at different disks. This deployment therefore runs **without a
   worker**: `INGESTION_MODE=inline` processes an upload inside the request.
   That is fine for a demo, where documents are seeded once and visitors only
   ask questions. To run the worker in production, move file storage to S3 or
   Cloudflare R2 behind `app/services/storage.py` — then both services read the
   same bucket and Redis + a worker service can come back.

---

## 1. Database

1. Railway → **New Project** → **Deploy a template** → search **pgvector**.
2. Once it is up, open the service → **Variables** → copy `DATABASE_URL`.
3. NEXA uses the psycopg 3 driver, so change the scheme:

   ```
   postgresql://...        →  postgresql+psycopg://...
   ```

Migrations run automatically: the API container starts with
`alembic upgrade head`, which also creates the `vector` extension and the HNSW
index.

## 2. API

1. In the same project → **New** → **GitHub Repo** → pick the NEXA repo.
2. **Settings → Root Directory**: `backend` (so Railway builds `backend/Dockerfile`).
3. **Settings → Networking → Generate Domain** — note the URL, e.g.
   `nexa-api-production.up.railway.app`.
4. **Variables** — the full set:

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | the pgvector URL, with `+psycopg` |
   | `ENVIRONMENT` | `prod` |
   | `JWT_SECRET` | a fresh 32+ character random string — **not** your dev one |
   | `CORS_ORIGINS` | your Vercel URL, e.g. `https://nexa.vercel.app` |
   | `INGESTION_MODE` | `inline` |
   | `STORAGE_DIR` | `/data/uploads` |
   | `EMBEDDING_PROVIDER` | `gemini` |
   | `GEMINI_API_KEY` | your key |
   | `LLM_PROVIDER` | `anthropic` |
   | `ANTHROPIC_API_KEY` | your key |
   | `RERANKER_PROVIDER` | `cohere` |
   | `COHERE_API_KEY` | your key |
   | `MAX_QUESTIONS_PER_DAY` | `50` — see "Before you share the link" |
   | `UPLOADS_ENABLED` | `false` for a public demo |

   Generate the secret with:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

5. **Settings → Volumes** → add one mounted at `/data/uploads`. Even with
   uploads disabled, seeding writes the demo documents there.
6. Deploy, then check `https://<your-api>/api/v1/health` returns
   `{"status":"ok","database":"ok"}`.

## 3. Web UI

1. [vercel.com](https://vercel.com) → **Add New → Project** → import the repo.
2. **Root Directory**: `frontend`. Vercel detects Next.js by itself.
3. **Environment Variables**:

   | Variable | Value |
   |---|---|
   | `NEXT_PUBLIC_API_URL` | `https://<your-api>.up.railway.app/api/v1` |
   | `NEXT_PUBLIC_DEMO_LOGIN` | omit for a demo; `false` for a real deployment |

   Both are read at build time, so changing either needs a redeploy.
   `NEXT_PUBLIC_DEMO_LOGIN=false` turns off the pre-filled demo credentials on
   the sign-in page — leave it unset and a visitor can press one button to get
   in, which is what you want when you send someone a link.
4. Deploy, then go back to Railway and set `CORS_ORIGINS` to the Vercel URL.
   Without that, the browser blocks every API call and the UI just says
   "Something went wrong".

## 4. Demo data

The seed script drives the public API, so it can fill production from your
laptop:

```bash
docker compose exec api python /scripts/seed_demo_tenants.py \
  --base-url https://<your-api>.up.railway.app/api/v1
```

It creates the three demo organisations (property, fintech, company), uploads
their documents and indexes them. It is idempotent — running it twice does not
duplicate anything. On the Gemini free tier expect a few minutes, since it
paces itself to stay under 100 embeddings per minute.

Log in at the Vercel URL with any of the demo accounts, e.g.
`owner@harbourview.example.com` in the `harbourview-property-group` workspace.

---

## Before you share the link

Three things, and the first is not optional.

**1. Cap the spending.** Every question costs money at Anthropic. A link posted
somewhere busy, with no cap, is someone else's playground funded by you.

- `MAX_QUESTIONS_PER_DAY=50` per organisation — the API returns `429` after
  that, with a message explaining why.
- Set a hard monthly spend limit in the Anthropic console as the backstop.
  Application-level limits protect against traffic; only a provider-level cap
  protects against a mistake in the application.

**2. Keep other people's documents out.** `UPLOADS_ENABLED=false`. The demo
embeds with Gemini's free tier, which may use submitted data for training —
fine for synthetic demo documents, not for a stranger's real contract.

**3. Rotate the secrets.** A fresh `JWT_SECRET`, and API keys that are not the
ones sitting in your local `.env`.

## What it costs

Railway bills per second: roughly $10 per GB of RAM per month and $20 per vCPU
per month. Postgres and the API together come to about **$8–10/month**, inside
the Hobby plan's $5 credit plus a few dollars. Vercel's free tier covers the UI.

The AI providers are the variable part: about **$0.02 per question** (Claude for
the answer, Gemini free tier for embeddings, Cohere trial for reranking). At
50 questions a day that is roughly $30/month — which is exactly why the daily
cap exists.

## Adding the worker back later

When you want background processing in production:

1. Add an S3 backend to `app/services/storage.py` (Cloudflare R2's free tier is
   10 GB) behind the interface that is already there.
2. Add a Redis service on Railway.
3. Add a second service from the same repo with the start command
   `celery -A app.workers.celery_app worker --loglevel=info`, sharing the API's
   variables plus `REDIS_URL`.
4. Set `INGESTION_MODE=celery` and drop the volume.

Nothing else changes: uploads already return `202` and the UI already polls
while a document is processing.
