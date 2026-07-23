import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from anthropic import APIError
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.mcp_client import MCPClientManager
from app.orchestrator import answer_question, langfuse, resume_pending_action
from app.personas import PERSONA_COOKIE_NAME, PERSONAS, resolve_persona

load_dotenv()

APP_DIR = Path(__file__).parent
SESSION_COOKIE_NAME = "session_id"
ASSET_VERSION = os.environ.get("RAILWAY_DEPLOYMENT_ID", "dev")
DEMO_MODE = os.environ.get("DEMO_MODE", "false").lower() == "true"


@asynccontextmanager
async def lifespan(app: FastAPI):
    mcp = MCPClientManager()
    await mcp.start()
    app.state.mcp = mcp
    yield
    await mcp.stop()
    langfuse.flush()


app = FastAPI(title="peoplefabrix", lifespan=lifespan)


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")


class AskRequest(BaseModel):
    question: str


class ConfirmActionRequest(BaseModel):
    pending_id: str
    decision: Literal["confirm", "cancel"]


def get_or_create_session_id(request: Request) -> tuple[str, bool]:
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        return session_id, False
    return str(uuid.uuid4()), True


def set_session_cookie(response, session_id: str) -> None:
    response.set_cookie(key=SESSION_COOKIE_NAME, value=session_id, httponly=True, samesite="lax")


def set_persona_cookie(response, persona_id: str) -> None:
    response.set_cookie(key=PERSONA_COOKIE_NAME, value=persona_id, httponly=True, samesite="lax")


class SelectPersonaRequest(BaseModel):
    persona_id: str


def _sse_event(item: dict) -> str:
    return f"event: {item['kind']}\ndata: {json.dumps(item)}\n\n"


async def _sse_wrap(generator):
    """Consumes an orchestrator async generator, formatting each yielded
    item as an SSE message. HTTP status can't change once streaming has
    started, so an APIError mid-stream becomes an `error` event instead of
    a different status code."""
    try:
        async for item in generator:
            yield _sse_event(item)
    except APIError as e:
        yield _sse_event({"kind": "error", "error": f"Anthropic API error: {e}"})


def _sse_response(generator) -> StreamingResponse:
    response = StreamingResponse(_sse_wrap(generator), media_type="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@app.get("/", response_class=HTMLResponse)
def read_root(request: Request):
    session_id, is_new = get_or_create_session_id(request)
    persona = resolve_persona(request)
    response = templates.TemplateResponse(
        request,
        "index.html",
        {
            "asset_version": ASSET_VERSION,
            "demo_mode": DEMO_MODE,
            "persona": persona,
            "personas": PERSONAS.values(),
        },
    )
    if is_new:
        set_session_cookie(response, session_id)
    return response


@app.post("/api/select-persona")
def select_persona(payload: SelectPersonaRequest):
    if payload.persona_id not in PERSONAS:
        return JSONResponse(status_code=400, content={"error": "Unknown persona."})
    response = JSONResponse(content={"ok": True})
    set_persona_cookie(response, payload.persona_id)
    return response


@app.post("/api/clear-persona")
def clear_persona():
    response = JSONResponse(content={"ok": True})
    response.delete_cookie(PERSONA_COOKIE_NAME)
    return response


@app.get("/health")
def health(request: Request):
    mcp: MCPClientManager = request.app.state.mcp
    return {
        "status": "healthy",
        "mcp_tools": [t["name"] for t in mcp.claude_tool_defs],
    }


@app.post("/api/ask")
async def ask(payload: AskRequest, request: Request):
    session_id, is_new = get_or_create_session_id(request)
    question = payload.question.strip()

    if not question:
        response = JSONResponse(status_code=400, content={"error": "Question cannot be empty."})
        if is_new:
            set_session_cookie(response, session_id)
        return response

    persona = resolve_persona(request)
    if persona is None:
        response = JSONResponse(status_code=400, content={"error": "No persona selected."})
        if is_new:
            set_session_cookie(response, session_id)
        return response

    if not os.environ.get("ANTHROPIC_API_KEY"):
        response = JSONResponse(
            status_code=500,
            content={"error": "Server is not configured with an ANTHROPIC_API_KEY."},
        )
        if is_new:
            set_session_cookie(response, session_id)
        return response

    mcp: MCPClientManager = request.app.state.mcp

    response = _sse_response(answer_question(question, session_id, persona, mcp))
    if is_new:
        set_session_cookie(response, session_id)
    return response


@app.post("/api/confirm-action")
async def confirm_action(payload: ConfirmActionRequest, request: Request):
    session_id, is_new = get_or_create_session_id(request)

    persona = resolve_persona(request)
    if persona is None:
        response = JSONResponse(status_code=400, content={"error": "No persona selected."})
        if is_new:
            set_session_cookie(response, session_id)
        return response

    mcp: MCPClientManager = request.app.state.mcp

    response = _sse_response(
        resume_pending_action(payload.pending_id, payload.decision, session_id, persona, mcp)
    )
    if is_new:
        set_session_cookie(response, session_id)
    return response


def dev():
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
