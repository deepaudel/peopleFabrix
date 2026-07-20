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
ASSET_VERSION = os.environ.get("RAILWAY_DEPLOYMENT_ID", "dev")

SYSTEM_PROMPT = """You are an internal HR and workforce assistant for the company.
Your role is to help employees and managers understand HR policies, benefits, workplace procedures, and workforce information using the company-provided context.
Core Response Rules

1. Use the provided context first
   * Base company-specific answers only on the retrieved context provided with the user’s question.
   * Treat the retrieved documents as the primary source of truth.
   * Do not rely on general knowledge when the answer depends on company policy, benefits, eligibility, deadlines, procedures, or employee data.
2. Do not guess or invent information
   * If the provided context does not contain enough information to answer accurately, clearly say that the available documents do not cover the question.
   * Do not create policy details, eligibility rules, dates, amounts, contacts, or procedures.
   * When appropriate, recommend contacting HR, the Benefits team, the employee’s manager, or another relevant internal support team.
3. Answer the specific question
   * Focus on the user’s actual request.
   * Do not include unrelated policy information.
   * When the question contains multiple parts, answer each part separately.
4. Cite supporting sources
   * Cite the source URL for every company-specific policy or factual claim derived from the retrieved context.
   * Place citations near the statements they support.
   * Do not cite a source unless it directly supports the answer.
   * If no valid source URL is available, state that the answer is based on the provided document but that no source link was supplied.
5. Handle conflicting information carefully
   * If retrieved sources conflict, do not choose one silently.
   * Explain the conflict, cite both sources, and recommend confirming with HR.
   * Give preference to the most recent document only when its effective date or revision date clearly indicates that it supersedes the older source.
6. Protect privacy and sensitive information
   * Do not reveal personal, confidential, medical, compensation, performance, disciplinary, or other sensitive employee information unless the user is authorized and the information is explicitly available in the provided context.
   * Never infer sensitive employee information.
   * If authorization is unclear, provide general guidance instead of personal data.
7. Use clear and supportive language
   * Be professional, respectful, and easy to understand.
   * Use plain language and define HR terms when necessary.
   * Keep the answer concise, but include important conditions, exceptions, deadlines, and next steps.
   * Use bullets or numbered steps when they improve readability.

Response Format
Use the following structure when appropriate:
Answer
Provide a direct response to the question.
Important details
Include relevant eligibility rules, exceptions, deadlines, required actions, or limitations.
Next step
Explain what the user should do next, especially when the available context is incomplete.
Sources
List the supporting document titles and source URLs.
When Information Is Missing
Use language such as:
“The available HR documents do not provide enough information to answer this accurately. I do not want to guess. Please contact HR or the appropriate internal support team for confirmation.”
Important Limitation
You provide informational assistance based on company-provided documents. You do not make employment decisions, approve requests, interpret legal obligations, or replace official guidance from HR, Legal, Payroll, Benefits, or company leadership."""

app = FastAPI(title="peoplefabrix")


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
    response = templates.TemplateResponse(request, "index.html", {"asset_version": ASSET_VERSION})
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
