# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

peopleFabrix is a FastAPI web app: an internal HR/workforce assistant. It answers employee
questions grounded in the public GitLab handbook (used here as a stand-in policy corpus) via
RAG, with per-session conversational memory. Server-rendered Jinja2 + vanilla JS/CSS frontend
— no build step, no SPA framework.

## Commands

Dependency management is via [uv](https://docs.astral.sh/uv/) (not pip/poetry).

```bash
uv sync                                    # install dependencies
uv run uvicorn app.main:app --reload       # run dev server (http://127.0.0.1:8000, /docs for API docs)
uv run python -m app.rag.ingest            # (re)build the handbook search index — needs OPENAI_API_KEY + internet
```

There is no test suite and no lint/format tooling configured in this repo.

Local Docker sanity check before deploying:

```bash
docker build -t peoplefabrix .
docker run -e OPENAI_API_KEY=<your-key> -e PORT=8000 -p 8000:8000 peoplefabrix
```

## Architecture

**Request flow (`app/main.py`):** `POST /api/ask` — 1) reads/creates a `session_id` cookie,
2) calls `app/rag/retrieve.py` to embed the question and fetch top-k chunks from Chroma,
3) builds the OpenAI message list as `[system prompt] + prior session history + [current
question with retrieved context prepended]`, 4) calls OpenAI chat completions, 5) stores the
**clean** question/answer (without RAG context) in session history, 6) returns the answer.
The system prompt enforces: answer only from retrieved context, never guess, always cite
source URLs, flag conflicting sources, and give a fixed deflection line when context is
missing — don't weaken these when touching the prompt.

**Session memory (`app/history.py`):** in-memory dict keyed by `session_id`, capped at
`MAX_HISTORY_TURNS` (6) turns. Not Redis/a database — state is per-process, which is why the
app must run as a single instance (see Deployment).

**RAG pipeline (`app/rag/`):**
- `config.py` — `SEED_URLS` (GitLab handbook sections), Chroma path/collection name, chunk
  size/overlap, `TOP_K`, crawl limits.
- `ingest.py` — standalone script (`python -m app.rag.ingest`), run manually/on-demand, never
  automatically. BFS-crawls all `SEED_URLS` together (shared visited-set), following only
  links whose URL is prefixed by one of the seed URLs, up to `MAX_PAGES`. Extracts main content
  via BeautifulSoup, chunks it (char-based sliding window), embeds via OpenAI, then **drops and
  fully recreates** the Chroma collection (no incremental upsert — simplest correctness story
  for a small, infrequently-updated corpus).
- `retrieve.py` — `get_relevant_chunks(question)`: embeds the question, queries Chroma for the
  nearest chunks, returns `[]` gracefully if the index doesn't exist yet or `OPENAI_API_KEY` is
  unset (so `/api/ask` still degrades to ungrounded answers rather than erroring).

**Frontend (`app/templates/`, `app/static/`):** single page, `#transcript` accumulates
question/answer turns as they come in (not overwritten). `chat.js` escapes all model output
before rendering, then selectively converts markdown-style `[text](url)` links and bare URLs
into real `<a>` tags for citations — keep new rendering logic going through `escapeHtml` first
to avoid reintroducing XSS.

**Cache-busting:** static asset URLs are suffixed with `?v={{ asset_version }}`, where
`ASSET_VERSION` is `RAILWAY_DEPLOYMENT_ID` (falls back to `"dev"` locally). This exists because
Railway's edge proxy caches `/static/*` independently of container redeploys — if static assets
seem stale after a deploy, this is the mechanism to check, not just browser cache.

## Deployment (Railway)

Stateful app (local Chroma index + in-memory session history) — **must** run as a single
instance with a persistent volume, `CHROMA_DIR` pointed at that volume, and `OPENAI_API_KEY`
set. Full steps are in `README.md`. After the first deploy (or whenever handbook content should
refresh), ingestion is run against the deployed environment specifically over SSH into the
running container (`railway ssh -- uv run python -m app.rag.ingest`) — `railway run` only
executes locally with Railway's env vars injected, it does *not* reach the deployed volume.

**One codebase, multiple deployments:** there is no per-client or demo/prod code fork. A "demo"
vs. a real client instance is just a separate Railway project built from this same repo, with
its own `OPENAI_API_KEY`, `CHROMA_DIR`/volume, and ingested content (different `SEED_URLS` per
client, if not editing config directly then via a client-specific branch/config at deploy time).
Setting `DEMO_MODE=true` only adds a visible "Demo" badge to the UI (see `app/templates/index.html`)
— it does not change response behavior or skip the OpenAI/RAG calls.
