# PeopleFabrix

FastAPI web app, managed with [uv](https://docs.astral.sh/uv/). An internal HR/workforce
assistant powered by Claude, with tool access (via MCP) to policy search, HR records, and
workforce analytics.

## Setup

```bash
uv sync
```

Copy `.env.example` to `.env` and set:

- `ANTHROPIC_API_KEY` — required, powers the Claude orchestrator.
- `OPENAI_API_KEY` — required, but only for RAG embeddings (`text-embedding-3-small`); no
  longer used for chat.
- `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` — optional, enables tracing.
  The app runs fine without these (tracing just no-ops). If you get 401s from Langfuse, check
  you're using the right cloud region host (`https://cloud.langfuse.com` for EU,
  `https://us.cloud.langfuse.com` for US) — keys are region-specific.

Then build the handbook search index (crawls the public GitLab People Group handbook, embeds
it, and stores it locally with Chroma — used as the `policy_search` tool's content). Needs
internet access; re-run whenever you want to refresh the indexed content:

```bash
uv run python -m app.rag.ingest
```

## Run (dev, with auto-reload)

```bash
uv run uvicorn app.main:app --reload
```

Visit http://127.0.0.1:8000 and http://127.0.0.1:8000/docs for the interactive API docs.

You'll land on a persona picker first (there's no real login yet — see `app/personas.py` for
the hardcoded personas). Pick one, then ask questions. `/health` reports the MCP tools
discovered at startup, useful for confirming the tool server spawned correctly.

## Architecture at a glance

- **Orchestrator** (`app/orchestrator.py`) — a manual Claude tool-use loop, not the SDK's Tool
  Runner, because the HRIS-write confirmation gate needs to inspect one specific tool result
  mid-loop and short-circuit.
- **Tools** are exposed via an MCP server (`app/mcp_server/`), spawned once as a subprocess at
  FastAPI startup and reused for the app's lifetime — not a Dockerfile concern, it runs inside
  the same image/venv.
- **Personas** (`app/personas.py`) stand in for real SSO — see `CLAUDE.md` for the extension
  point when real identity is available.
- **HRIS/warehouse tools are mocks** with realistic per-persona data, not real vendor
  integrations — see `CLAUDE.md` for what a real integration would replace.

See `CLAUDE.md` for the full request-flow and architecture writeup.

## Deploying to Railway

The app is stateful (a local Chroma index + in-memory session/persona/pending-action state), so
it needs a persistent volume and must run as a **single instance** (no autoscaling/multiple
replicas). Deploy via the included `Dockerfile`:

1. Create a Railway project from this GitHub repo — the `Dockerfile` build is auto-detected.
2. Add a **Volume** to the service, mount it (e.g. at `/data`), and set the `CHROMA_DIR` env var
   to a path under it, e.g. `/data/chroma`.
3. Set `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` (and optionally `LANGFUSE_*`) as env vars in the
   Railway dashboard — never commit these.
4. Set the health check path to `/health`.
5. After the first deploy, build the handbook index against the deployed environment (needs the
   volume + `OPENAI_API_KEY` already configured):
   ```bash
   railway ssh -- uv run python -m app.rag.ingest
   ```
   (`railway run` only executes locally against injected env vars — it does not reach the
   deployed volume. `railway ssh` runs inside the actual container.) Re-run the same way
   whenever the handbook content should refresh.
6. Confirm the service is set to a single instance/replica — session memory, the Chroma index,
   and pending HRIS-write confirmations are all per-process state.

Local sanity check before deploying:

```bash
docker build -t peoplefabrix .
docker run -e ANTHROPIC_API_KEY=<your-key> -e OPENAI_API_KEY=<your-key> -e PORT=8000 -p 8000:8000 peoplefabrix
```
