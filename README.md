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
