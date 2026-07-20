# peopleFabrix

FastAPI web app, managed with [uv](https://docs.astral.sh/uv/).

## Setup

```bash
uv sync
```

Copy `.env.example` to `.env` and set `OPENAI_API_KEY` (required for the
HR assistant's `/api/ask` endpoint).

Then build the handbook search index (crawls the public GitLab People Group
handbook, embeds it, and stores it locally with Chroma). Needs internet
access; re-run whenever you want to refresh the indexed content:

```bash
uv run python -m app.rag.ingest
```

## Run (dev, with auto-reload)

```bash
uv run uvicorn app.main:app --reload
```

Visit http://127.0.0.1:8000 and http://127.0.0.1:8000/docs for the interactive API docs.

## Deploying to Railway

The app is stateful (a local Chroma index + in-memory session history), so
it needs a persistent volume and must run as a **single instance** (no
autoscaling/multiple replicas). Deploy via the included `Dockerfile`:

1. Create a Railway project from this GitHub repo — the `Dockerfile` build
   is auto-detected.
2. Add a **Volume** to the service, mount it (e.g. at `/data`), and set the
   `CHROMA_DIR` env var to a path under it, e.g. `/data/chroma`.
3. Set `OPENAI_API_KEY` (and optionally `OPENAI_MODEL`) as env vars in the
   Railway dashboard — never commit these.
4. Set the health check path to `/health`.
5. After the first deploy, build the handbook index against the deployed
   environment (needs the volume + `OPENAI_API_KEY` already configured):
   ```bash
   railway run python -m app.rag.ingest
   ```
   Re-run the same way whenever the handbook content should refresh.
6. Confirm the service is set to a single instance/replica — session memory
   and the Chroma index are per-process state.

Local sanity check before deploying:

```bash
docker build -t peoplefabrix .
docker run -e OPENAI_API_KEY=<your-key> -e PORT=8000 -p 8000:8000 peoplefabrix
```
