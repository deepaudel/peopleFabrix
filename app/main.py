import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from openai import APIError, OpenAI
from pydantic import BaseModel

from app import history
from app.rag.retrieve import get_relevant_chunks

load_dotenv()

APP_DIR = Path(__file__).parent
DEFAULT_MODEL = "gpt-4o-mini"
SESSION_COOKIE_NAME = "session_id"

SYSTEM_PROMPT = (
    "You are an internal HR and workforce assistant for the company. "
    "You help employees with questions about HR policies, benefits, and "
    "their team or workforce. Answer clearly and concisely. "
    "Some questions will come with relevant handbook context attached - "
    "when that's the case, base your answer on it and cite the source URL. "
    "If a question depends on company-specific policy or data you don't "
    "have access to (no context was provided or it doesn't cover the "
    "question), say so plainly instead of guessing."
)

app = FastAPI(title="peoplefabrix")

app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    answer: str


def get_or_create_session_id(request: Request) -> tuple[str, bool]:
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        return session_id, False
    return str(uuid.uuid4()), True


def set_session_cookie(response, session_id: str) -> None:
    response.set_cookie(key=SESSION_COOKIE_NAME, value=session_id, httponly=True, samesite="lax")


def build_user_content(question: str, chunks: list[dict]) -> str:
    if not chunks:
        return question

    context_block = "\n\n---\n\n".join(
        f"Source: {c['title']} ({c['source_url']})\n{c['text']}" for c in chunks
    )
    return f"Relevant handbook context:\n\n{context_block}\n\n---\n\nQuestion: {question}"


@app.get("/", response_class=HTMLResponse)
def read_root(request: Request):
    session_id, is_new = get_or_create_session_id(request)
    response = templates.TemplateResponse(request, "index.html")
    if is_new:
        set_session_cookie(response, session_id)
    return response


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/api/ask")
def ask(payload: AskRequest, request: Request):
    session_id, is_new = get_or_create_session_id(request)
    question = payload.question.strip()

    if not question:
        response = JSONResponse(status_code=400, content={"error": "Question cannot be empty."})
        if is_new:
            set_session_cookie(response, session_id)
        return response

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        response = JSONResponse(
            status_code=500,
            content={"error": "Server is not configured with an OPENAI_API_KEY."},
        )
        if is_new:
            set_session_cookie(response, session_id)
        return response

    model = os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)
    client = OpenAI(api_key=api_key)

    chunks = get_relevant_chunks(question)
    prior_history = history.get_history(session_id)
    messages = (
        [{"role": "system", "content": SYSTEM_PROMPT}]
        + prior_history
        + [{"role": "user", "content": build_user_content(question, chunks)}]
    )

    try:
        completion = client.chat.completions.create(model=model, max_tokens=1024, messages=messages)
    except APIError as e:
        response = JSONResponse(status_code=502, content={"error": f"OpenAI API error: {e}"})
        if is_new:
            set_session_cookie(response, session_id)
        return response

    answer = completion.choices[0].message.content
    history.append_turn(session_id, question, answer)

    response = JSONResponse(content=AskResponse(answer=answer).model_dump())
    if is_new:
        set_session_cookie(response, session_id)
    return response


def dev():
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
