# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

PeopleFabrix is a FastAPI web app: an internal HR/workforce assistant powered by an agentic loop
(OpenAI by default, Claude reserved for HRIS writes — see "Model routing" below) with tool
access (via MCP) to policy search (RAG over the public GitLab handbook, used as a stand-in
policy corpus), HR records, and workforce analytics. Identity is 4 hardcoded personas standing
in for real SSO. Server-rendered Jinja2 + vanilla JS/CSS frontend — no build step, no SPA
framework.

## Commands

Dependency management is via [uv](https://docs.astral.sh/uv/) (not pip/poetry).

```bash
uv sync                                    # install dependencies
uv run uvicorn app.main:app --reload       # run dev server (http://127.0.0.1:8000, /docs for API docs)
uv run python -m app.rag.ingest            # (re)build the handbook search index — needs OPENAI_API_KEY + internet
```

There is no test suite and no lint/format tooling configured in this repo. Verification is
manual: run the dev server, use `/health` to confirm the MCP tool server started, and exercise
`/api/ask` via the browser or curl. `/api/ask` and `/api/confirm-action` are SSE streams, not
plain JSON — use `curl -N` (disables curl's own buffering) to watch `event:`/`data:` lines
arrive incrementally instead of all at once at the end; this is also the fastest way to isolate
an orchestrator/Langfuse bug from a frontend rendering bug when something looks broken.

Local Docker sanity check before deploying:

```bash
docker build -t peoplefabrix .
docker run -e ANTHROPIC_API_KEY=<key> -e OPENAI_API_KEY=<key> -e PORT=8000 -p 8000:8000 peoplefabrix
```

## Architecture

**Request flow (`app/main.py` → `app/orchestrator.py`):** `POST /api/ask` — 1) resolves the
`session_id` cookie (conversation history scope) and `persona_id` cookie (identity, via
`app.personas.resolve_persona`) — a request with no persona selected is rejected, the frontend
only shows the ask form after a persona is picked, 2) calls `orchestrator.answer_question`,
which runs a manual, provider-neutral tool-use loop (`_run_loop`): call the active model adapter
with the MCP-derived tool defs → if `stop_reason != "tool_use"`, that's the final answer →
otherwise dispatch every tool call through the MCP client (injecting the caller's persona id
server-side, see below), feed the results back, repeat (capped at `MAX_TOOL_ITERATIONS`). Which
provider is "active" can change mid-request — see "Model routing" below. The **clean**
question/answer (not the raw tool-call transcript) is what gets stored in session history via
`app/history.py`, same as before.

**Why a manual loop, not a vendor SDK's Tool Runner:** the HRIS-write confirmation gate (see
below) needs to inspect one specific tool's result mid-loop and short-circuit before feeding it
back to the model — a black-box tool-runner doesn't support that.

**Model routing — OpenAI by default, Claude reserved for HRIS writes:** every new turn
(`answer_question`) starts on OpenAI (`gpt-4o-mini`, `app/providers/openai_provider.py`) — this
is the cost-saving default and covers greetings, policy questions, HRIS reads, and warehouse
queries alike. `app/orchestrator.py:_run_loop` is written against a provider-neutral
`ModelAdapter` interface (`app/providers/base.py`'s `TurnResult`/`ToolCallRequest`), so it never
branches on which vendor is active except for one thing: if a turn's result includes a call to
`hris_write` and the active adapter isn't Anthropic, `_run_loop` raises `_RedirectToClaude`
*before* dispatching anything or mutating `messages`. `answer_question` catches this, discards
the OpenAI attempt entirely (nothing was written to history or dispatched), and restarts the
same turn from scratch on Claude (`claude-sonnet-5`, `app/providers/anthropic_provider.py`) with
its own fresh `MAX_TOOL_ITERATIONS` budget. This reacts to the model's actual decision to write
rather than guessing from question phrasing, so it can't be fooled by wording — at the cost of
one cheap discarded `gpt-4o-mini` call on turns that end up needing a write. If a batch contains
`hris_write` alongside other tool calls, the whole batch is discarded and redone on Claude, not
just the write — this avoids ever mixing OpenAI-shaped (`role: "tool"`) and Anthropic-shaped
(`tool_result` content blocks) messages in the same in-flight `messages` list, which the two
SDKs can't interoperate on. `resume_pending_action` always resumes on Claude (asserted, not
branched on) since a `pending_confirmation` can only be staged after this redirect has already
happened — `entry["messages"]` is only ever valid to replay against the provider that produced
it. `app/mcp_client.py` builds tool schemas for both providers (`claude_tool_defs`/
`openai_tool_defs`) from the same MCP tool list. Note the OpenAI adapter uses raw
`chat.completions.create(stream=True)` with manual chunk accumulation, not the SDK's higher-level
`chat.completions.stream()` helper — that helper requires every tool's function schema to be
marked `"strict": True`, which FastMCP's dynamically-generated schemas aren't.

**MCP tool server (`app/mcp_server/`):** a `FastMCP` app exposing 4 tools — `policy_search`
(thin wrapper around the existing `app/rag/retrieve.py:get_relevant_chunks`, reused verbatim),
`hris_read`, `hris_write`, `warehouse_query`. Spawned once as a subprocess (stdio transport) in
`app/main.py`'s FastAPI `lifespan`, owned by `app/mcp_client.py:MCPClientManager`, reused for
every request — never respawned per-request. `env=os.environ.copy()` is passed explicitly to
the subprocess; the MCP SDK does not auto-inherit the parent's env, and losing
`OPENAI_API_KEY`/`CHROMA_DIR` here would make `policy_search` silently degrade to `[]` per
`retrieve.py`'s existing graceful-failure behavior, not error loudly — if RAG answers start
looking ungrounded, check this first.

**Identity boundary — `actor_persona_id`:** every tool that needs to know "who's asking" takes
an `actor_persona_id` parameter (not `_persona_id` — FastMCP rejects leading-underscore
parameter names). `app/mcp_client.py:to_claude_schema`/`to_openai_schema` both strip this
parameter from the schema the model actually sees; `orchestrator.dispatch_tool` injects the real
value server-side on every call, regardless of which provider is active. The model can never see
or set this — it cannot spoof a different persona's identity. Tools also accept a person's
**name** for `target_persona_id` (resolved via `hris_store.resolve_persona_id`), not just the
internal id — the model only knows people by name from conversation context, and requiring an
internal ID it doesn't have breaks lookups.

**Personas (`app/personas.py`):** 4 hardcoded personas (2 employees, 1 manager, 1 HRBP),
standing in for real SSO. All resolution funnels through `resolve_persona(request)`, called
once per request — today it reads a `persona_id` cookie; the SSO extension point later is
swapping this one function's internals, nothing downstream changes. Selected via a picker
screen shown whenever the cookie is absent (`app/templates/index.html`, `POST
/api/select-persona`); "switch" clears it (`POST /api/clear-persona`).

**Mock HRIS (`app/mcp_server/hris_store.py`):** small in-memory per-persona record store
(PTO balance, employment info). Scoping: employee → self only; manager → self + direct reports;
HRBP → anyone. A real HRIS integration later replaces the bodies of
`read`/`propose_write`/`confirm_write`/`cancel_write` — the calling convention stays the same.

**HRIS-write confirmation gate — a real software gate, not prompt-level courtesy:**
`hris_write` is two-phase: `action="propose"` validates + stages the change and returns a
`pending_id` + description, **without mutating anything**; `action="confirm"`/`"cancel"` (with
that `pending_id`) actually applies or discards it. When `_run_loop` sees a `propose` result
with `status: "pending_confirmation"`, it stages the in-progress `messages` array (always
Claude-shaped — see "Model routing" above) in `app/pending_actions.py` (same in-memory,
single-instance pattern as `history.py`, now also recording `provider` alongside `model`) and
returns `{"type": "pending_action", ...}` **instead of continuing the loop** — the model
physically cannot auto-confirm its own proposal in the same turn, regardless of what the system
prompt says. The frontend renders a distinct `.pending-action` card (Confirm/Cancel), and `POST
/api/confirm-action` → `orchestrator.resume_pending_action` pops the staged entry, dispatches
the real `confirm`/`cancel` to MCP, appends the outcome as a plain-text note (not a synthetic
tool_result — simpler and avoids Anthropic API constraints around unmatched tool_use/tool_result
pairs), and resumes the same `_run_loop` (always on Claude — asserted in code, not branched on)
to get its natural-language confirmation.

**Live step + token streaming ("agent transparency"), over SSE:** `_run_loop`,
`answer_question`, and `resume_pending_action` are `async def` **generators**, not
`return`-once coroutines — `main.py`'s `/api/ask` and `/api/confirm-action` wrap them in a
`StreamingResponse` (`text/event-stream`), forwarding each yielded `{"kind": ...}` dict as one
SSE message (`event: {kind}\ndata: {json}\n\n`). Event kinds: `step` (before each tool
dispatch), `answer_reset` (before every generation's text starts, from either provider — needed
because a generation that ends in `stop_reason=="tool_use"` may have produced throwaway preamble
text that must not persist once the next generation's deltas arrive; also fired when a turn
restarts on Claude after the HRIS-write redirect, to clear the discarded OpenAI attempt's
preamble), `answer_delta` (one per text token — each provider adapter's `stream_turn()` yields
these as it streams; see `app/providers/`), and exactly one terminal `result` (or `error`) per
request — same payload shape as the old buffered JSON response, wrapped via the `_result()`
helper so every yield carries a `kind` key. `dispatch_tool` is otherwise unchanged from the
pre-streaming version.

`@observe` (Langfuse) **does** support decorating an async-generator function — confirmed by
reading the installed SDK: it dispatches via `inspect.isasyncgen`, not the coroutine path, into
a wrapper that preserves OTel context across `yield`s and closes the span once fully drained
*or* explicitly closed (client disconnect → clean span finalization, no leak). No trace
restructuring was needed for streaming — only the calling convention (`async for` instead of
`await`) changed. Delegating a sub-generator requires `async for item in _run_loop(...): yield
item` — **not** `yield from`, which is sync-only and a `SyntaxError` on an async generator.

Frontend (`app/static/js/chat.js`): `streamRequest()` reads `res.body.getReader()`, decodes with
`TextDecoder({stream: true})` (required — without it, a multi-byte UTF-8 character split across
network chunks gets mangled), buffers and splits on `"\n\n"` to find complete SSE messages (one
chunk can contain zero, one, or many; a message can span multiple chunks), and dispatches to
`onStep`/`onAnswerReset`/`onAnswerDelta`/`onResult`/`onError`. Streamed text is appended as
**plain `textContent`**, never `linkify()`'d — that regex-based markdown-link/URL conversion is
unsafe against partial/incomplete text. Only the terminal `onResult` calls the existing
`renderResult()` for the final, fully-`linkify()`'d render, overwriting whatever was built
incrementally — this is why `renderResult`/`renderAnswer`/`renderPendingAction` needed no
changes at all for streaming. The "Show steps" toggle (`localStorage`, default on) only gates
whether `onStep` appends visible `<li>`s — the stream is always fully consumed either way.

**Mock workforce warehouse (`app/mcp_server/warehouse_data.py`):** a small synthetic employee
table backing exactly 3 named, parameterized query templates (`headcount_by_department`,
`average_tenure_by_department`, `pto_usage_trend`) — deliberately not free-form
SQL/text-to-SQL. A real data-warehouse integration later replaces `run_query`'s body. Role-scoped
like the HRIS tools (`_check_access`): employees have no access at all (this is cross-employee
aggregate data, not their own record); managers are auto-scoped to their own department and
denied if they request a `department_filter` for a different one; HRBPs have full access.

**Langfuse tracing (`app/orchestrator.py`):** `@observe(name="ask")` on `answer_question` (and
`@observe(name="confirm-action")` on `resume_pending_action`) creates the root trace;
`propagate_attributes(user_id=persona.id, session_id=..., metadata={"persona_role": ...})`
right after attaches identity to every nested span. Each model call and each MCP tool dispatch
gets its own `start_as_current_observation` (as_type `"generation"`/`"tool"`) — the generation
span is named `f"{adapter.provider}:{model}"` (e.g. `"openai:gpt-4o-mini"`,
`"anthropic:claude-sonnet-5"`), which is also the easiest way to see the actual OpenAI/Claude
cost split in the Langfuse dashboard (Langfuse auto-calculates `totalCost` for both, verified
against live traces — no per-provider cost wiring needed). Uses the
**v4 SDK API** (`get_client()`, `propagate_attributes`, `start_as_current_observation` — not the
v3 names like `start_as_current_span`/`update_current_trace`, which don't exist in the installed
version). Gracefully no-ops if `LANGFUSE_PUBLIC_KEY` isn't set — doesn't crash the app. **Note:**
Langfuse has region-specific API keys — EU keys need `LANGFUSE_HOST=https://cloud.langfuse.com`,
US keys need `https://us.cloud.langfuse.com`; a 401 on span export usually means a region
mismatch, not invalid keys. Also note `get_client()` runs at import time in `orchestrator.py`,
which is why that module calls `load_dotenv()` itself rather than relying on `main.py`'s call —
`main.py`'s imports (which trigger `orchestrator`'s module-level code) resolve before its own
`load_dotenv()` line runs.

**User feedback → Langfuse scores:** `orchestrator._result()` attaches
`langfuse.get_current_trace_id()` to every terminal SSE `result` payload (works because it's
always called from inside the `@observe`-decorated root span of `answer_question`/
`resume_pending_action`; returns `None` if Langfuse isn't configured, which the frontend treats
as "don't render feedback buttons"). The frontend (`renderAnswer` in `chat.js`) renders 👍/👎
buttons under a rendered answer (not under a `pending_action` card) whenever a `trace_id` is
present; clicking one `POST`s `{trace_id, rating}` to `/api/feedback`, which calls
`langfuse.create_score(trace_id=..., name="user_feedback", data_type="BOOLEAN", value=1.0|0.0)`
— synchronous and non-blocking (enqueues to a background thread, no per-request `flush()`
needed), and silently no-ops under the same "Langfuse not configured" condition as everything
else here. Unlike `pending_actions.pop`, this endpoint doesn't check score ownership against
session/persona — a feedback score is a side-channel annotation, not a state mutation, and trace
IDs are 128-bit random hex never exposed anywhere another session could scrape them from.

**RAG pipeline (`app/rag/`)** — unchanged from before the Claude/MCP pivot, now called from the
`policy_search` MCP tool instead of directly from `main.py`:
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
  unset. `OPENAI_API_KEY` is required for this regardless of chat routing — Anthropic has no
  embeddings endpoint, so even on turns that redirect to Claude, retrieval still goes through
  OpenAI.

**Frontend (`app/templates/index.html`, `app/static/`):** navy (`#0f172a`) header bar with the
PeopleFabrix logo (`app/static/img/logo.png`), matching the marketing site's design —
persistent across both the persona picker and the chat view, regardless of light/dark system
theme. `#transcript` accumulates question/answer turns as they come in (not overwritten).
`chat.js`'s `renderResult` branches on the response `type`: `"answer"` goes through the existing
`escapeHtml` → `linkify` path (converts markdown-style `[text](url)` links and bare URLs into
real `<a>` tags for citations); `"pending_action"` renders the Confirm/Cancel card via
`renderPendingAction`, whose own resolution also routes back through `renderResult` — keep any
new rendering logic going through `escapeHtml` first to avoid reintroducing XSS.

**Cache-busting:** static asset URLs are suffixed with `?v={{ asset_version }}`, where
`ASSET_VERSION` is `RAILWAY_DEPLOYMENT_ID` (falls back to `"dev"` locally). This exists because
Railway's edge proxy caches `/static/*` independently of container redeploys — if static assets
seem stale after a deploy, this is the mechanism to check, not just browser cache.

## Deployment (Railway)

Stateful app (local Chroma index + in-memory session/persona/pending-action state) — **must**
run as a single instance with a persistent volume, `CHROMA_DIR` pointed at that volume, and both
`OPENAI_API_KEY` (default chat model + embeddings) and `ANTHROPIC_API_KEY` (HRIS-write path
only) set — both are hard startup requirements even though most traffic only needs one of them,
since any request could redirect mid-loop. Full steps are in `README.md`. After the first deploy (or whenever handbook content should refresh), ingestion is
run against the deployed environment specifically over SSH into the running container
(`railway ssh -- uv run python -m app.rag.ingest`) — `railway run` only executes locally with
Railway's env vars injected, it does *not* reach the deployed volume. The MCP subprocess is
spawned inside the same container by `app.main`'s lifespan — no separate service/deployment
needed for it.

**One codebase, multiple deployments:** there is no per-client or demo/prod code fork. A "demo"
vs. a real client instance is just a separate Railway project built from this same repo, with
its own API keys, `CHROMA_DIR`/volume, and ingested content (different `SEED_URLS` per client,
if not editing config directly then via a client-specific branch/config at deploy time). Setting
`DEMO_MODE=true` only adds a visible "Demo" badge to the UI (see `app/templates/index.html`) —
it does not change response behavior or skip any API calls. The same philosophy applies to
personas/HRIS/warehouse: real SSO, a real HRIS vendor, and a real data warehouse are all
swap-in replacements for specific, isolated functions (see Architecture above) — not a reason
to fork the codebase per client.
