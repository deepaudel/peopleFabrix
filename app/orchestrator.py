"""Provider-agnostic tool-use loop: the core of /api/ask.

A manual loop (not either SDK's Tool Runner) because the HRIS-write
confirmation gate needs to inspect one specific tool's result mid-loop and
short-circuit before feeding it back to the model — a black-box tool-runner
doesn't support that cleanly. It's also why this is written against the
provider-neutral ModelAdapter interface (app/providers/base.py) rather than
either vendor SDK directly: OpenAI (gpt-4o-mini) is the default for cost, but
Claude is required for the HRIS-write path (see _RedirectToClaude below).
"""

import base64
import json
import logging
import os

from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from langfuse import get_client, observe, propagate_attributes
from openai import AsyncOpenAI

from app import documents, history, pending_actions
from app.mcp_client import MCPClientManager
from app.personas import Persona
from app.providers.anthropic_provider import AnthropicAdapter
from app.providers.base import TurnResult
from app.providers.openai_provider import OpenAIAdapter

load_dotenv()  # module-level get_client() below reads env vars at import time

logger = logging.getLogger(__name__)
langfuse = get_client()

OPENAI_MODEL = "gpt-4o-mini"
SONNET_MODEL = "claude-sonnet-5"
MAX_TOOL_ITERATIONS = 6

STEP_LABELS = {
    "policy_search": "Searching policy documents",
    "hris_read": "Checking HR record",
    "hris_write": "Preparing HR record change",
    "warehouse_query": "Querying workforce analytics",
    "generate_employment_letter": "Generating employment letter",
}

SYSTEM_PROMPT_TEMPLATE = """You are an internal HR and workforce assistant for the company.
Your role is to help employees and managers understand HR policies, benefits, workplace procedures, and workforce information.

You are currently talking to: {persona_name}, {persona_title} ({persona_department}), role: {persona_role},
based in {persona_location_city}, {persona_location_country}.
Every tool call is automatically scoped to this person's identity — you cannot look up or modify
anyone else's data unless your tools explicitly report you're authorized to (e.g. a manager may
access their direct reports' records; an HRBP may access anyone's).

Core Response Rules

1. Use your tools, don't guess
   * For policy/benefits/procedure questions, call policy_search and base your answer only on
     the returned passages. Do not rely on general knowledge for company-specific policy.
   * Some policies vary by country (e.g. public holidays, statutory leave, benefits eligibility).
     When a policy question could vary by country, factor in this person's location
     ({persona_location_country}) — include country context in your policy_search query (e.g.
     "public holidays for UK team members" rather than just "public holidays"), and if the
     retrieved passages distinguish between countries/entities, answer with the part that applies
     to {persona_location_country} specifically rather than a generic or different country's
     answer. If the passages don't make a country-specific distinction, say the policy appears to
     apply company-wide rather than guessing it's location-specific.
   * For questions about the current person's own HR data (PTO balance, employment info, etc.)
     or (if authorized) someone else's, call hris_read.
   * For workforce-analytics questions (headcount, tenure, PTO usage trends), call
     warehouse_query. This is role-scoped: employees aren't authorized for it at all;
     managers are auto-scoped to their own department; HRBPs can query anything.
   * If a tool returns no relevant results or an authorization error, say so plainly — do not
     invent policy details, numbers, dates, or eligibility rules.
2. Answer the specific question
   * Focus on the user's actual request. When it has multiple parts, answer each part.
3. Cite supporting sources
   * When policy_search results support a claim, cite the source_url near the statement it supports.
4. Handle conflicting information carefully
   * If retrieved policy sources conflict, explain the conflict, cite both, and recommend
     confirming with HR rather than silently picking one.
5. Protect privacy
   * Never reveal another person's HR data unless a tool call confirms you're authorized to
     access it. If a tool denies access, tell the user you can't share that and why.
6. HR record changes require confirmation
   * To change someone's HR record, call hris_write with action="propose" first. This never
     writes anything by itself — it only stages the change and describes it back to you.
   * After proposing, tell the user exactly what would change and ask them to confirm before
     anything is actually written. Do not call action="confirm" yourself in the same turn.
7. Use clear and supportive language
   * Be professional, respectful, and concise. Use bullets when they improve readability.

When Information Is Missing
Use language such as: "The available HR documents/records don't provide enough information to
answer this accurately. I don't want to guess. Please contact HR or the appropriate internal
support team for confirmation."

Important Limitation
You provide informational assistance. You do not make employment decisions, approve requests,
interpret legal obligations, or replace official guidance from HR, Legal, Payroll, Benefits, or
company leadership."""


def build_system_prompt(persona: Persona) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        persona_name=persona.display_name,
        persona_title=persona.title,
        persona_department=persona.department,
        persona_role=persona.role,
        persona_location_city=persona.location_city,
        persona_location_country=persona.location_country,
    )


async def dispatch_tool(mcp: MCPClientManager, tool_name: str, tool_input: dict, persona: Persona) -> dict:
    args = {**tool_input, "actor_persona_id": persona.id}
    with langfuse.start_as_current_observation(
        name=f"tool:{tool_name}", as_type="tool", input=tool_input
    ) as span:
        result = await mcp.call_tool(tool_name, args)
        span.update(output=result)
        return result


def _result(payload: dict) -> dict:
    """Wraps a terminal answer/pending_action payload with the SSE 'kind'
    discriminator, so main.py's event writer can key off item["kind"]
    uniformly for every yielded item, not just step/answer_delta ones. Also
    attaches the current Langfuse trace id (None if Langfuse isn't
    configured) so the frontend can attach user feedback to this turn via
    POST /api/feedback."""
    return {"kind": "result", "trace_id": langfuse.get_current_trace_id(), **payload}


def _pending_confirmation_id(tool_name: str, result: dict) -> str | None:
    """If this tool result represents an HRIS write awaiting user
    confirmation, returns its pending_id — else None. This is the hook the
    loop uses to short-circuit instead of letting the model auto-confirm."""
    if tool_name == "hris_write" and isinstance(result, dict) and result.get("status") == "pending_confirmation":
        return result["pending_id"]
    return None


def _extract_attachment(tool_result: dict, session_id: str, persona_id: str) -> dict | None:
    """If a tool result carries a generated file (any MCP tool can return
    this shape, not just generate_employment_letter — this is a generic
    mechanism for future file-returning tools), stages the decoded bytes
    into the main-process document store and mutates tool_result IN PLACE to
    replace the base64 blob with a lightweight download_token before it's
    fed back to the model — keeps the (relatively large) payload out of the
    model's context. Returns the structured attachment info for the
    frontend, or None if this tool result carries no attachment."""
    if not isinstance(tool_result, dict) or tool_result.get("status") != "document_ready":
        return None
    if "content_base64" not in tool_result:
        return None

    content = base64.b64decode(tool_result.pop("content_base64"))
    filename = tool_result["filename"]
    content_type = tool_result["content_type"]
    token = documents.stage(session_id, persona_id, filename, content, content_type)
    tool_result["download_token"] = token

    return {"download_token": token, "filename": filename, "content_type": content_type}


class _RedirectToClaude(Exception):
    """Raised mid-_run_loop when a non-Anthropic turn decides to call
    hris_write. Nothing has been dispatched or appended to `messages` yet at
    the point this is raised, so the caller can safely restart the whole
    turn from its original messages on the Anthropic adapter instead. See
    answer_question/resume_pending_action for the catch side."""


async def _run_loop(
    adapter,
    model: str,
    system: str,
    messages: list,
    mcp: MCPClientManager,
    persona: Persona,
    session_id: str,
    question: str,
):
    """Async generator: yields {"kind": "answer_reset"/"answer_delta", ...}
    as the model streams each generation's text, {"kind": "step", ...} events
    as tools are dispatched, then exactly one {"kind": "result", ...} as the
    terminal item. Raises _RedirectToClaude if a non-Anthropic adapter's
    turn wants to call hris_write — see that class's docstring."""
    tools = mcp.claude_tool_defs if adapter.provider == "anthropic" else mcp.openai_tool_defs
    attachments: list[dict] = []

    for _ in range(MAX_TOOL_ITERATIONS):
        with langfuse.start_as_current_observation(
            name=f"{adapter.provider}:{model}", as_type="generation", model=model, input=messages
        ) as gen:
            yield {"kind": "answer_reset"}
            result: TurnResult | None = None
            async for item in adapter.stream_turn(model, system, messages, tools):
                if item["kind"] == "_turn_result":
                    result = item["result"]
                else:
                    yield item
            assert result is not None
            gen.update(output=result.log_output, usage_details=result.usage)

        if result.stop_reason != "tool_use":
            history.append_turn(session_id, question, result.text)
            yield _result({"type": "answer", "answer": result.text, "attachments": attachments})
            return

        if adapter.provider != "anthropic" and any(tc.name == "hris_write" for tc in result.tool_calls):
            raise _RedirectToClaude()

        messages.append(result.assistant_message)

        tool_results = []
        gate: tuple[str, str] | None = None  # (pending_id, description)
        for tc in result.tool_calls:
            yield {
                "kind": "step",
                "tool": tc.name,
                "label": STEP_LABELS.get(tc.name, f"Running {tc.name}"),
            }
            tool_result = await dispatch_tool(mcp, tc.name, tc.input, persona)
            attachment = _extract_attachment(tool_result, session_id, persona.id)
            if attachment:
                attachments.append(attachment)
            tool_results.append(tool_result)
            pending_id = _pending_confirmation_id(tc.name, tool_result)
            if pending_id:
                gate = (pending_id, tool_result.get("description", "A change is awaiting your confirmation."))
        messages.extend(adapter.tool_result_messages(result.tool_calls, tool_results))

        if gate:
            pending_id, description = gate
            pending_actions.stage(
                pending_id=pending_id,
                session_id=session_id,
                persona_id=persona.id,
                question=question,
                messages=messages,
                model=model,
                provider=adapter.provider,
            )
            yield _result(
                {
                    "type": "pending_action",
                    "pending_id": pending_id,
                    "description": description,
                    "attachments": attachments,
                }
            )
            return

    fallback = "I wasn't able to finish that — could you rephrase or narrow the question?"
    history.append_turn(session_id, question, fallback)
    yield _result({"type": "answer", "answer": fallback, "attachments": attachments})


@observe(name="ask")
async def answer_question(question: str, session_id: str, persona: Persona, mcp: MCPClientManager):
    with propagate_attributes(
        user_id=persona.id,
        session_id=session_id,
        metadata={"persona_role": persona.role},
    ):
        base_messages = history.get_history(session_id) + [{"role": "user", "content": question}]
        system = build_system_prompt(persona)

        logger.info("routing question to openai:%s: %r", OPENAI_MODEL, question)
        adapter = OpenAIAdapter(AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"]))

        try:
            async for item in _run_loop(
                adapter, OPENAI_MODEL, system, list(base_messages), mcp, persona, session_id, question
            ):
                yield item
        except _RedirectToClaude:
            logger.info("redirecting to anthropic:%s for hris_write: %r", SONNET_MODEL, question)
            adapter = AnthropicAdapter(AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"]))
            async for item in _run_loop(
                adapter, SONNET_MODEL, system, list(base_messages), mcp, persona, session_id, question
            ):
                yield item


@observe(name="confirm-action")
async def resume_pending_action(
    pending_id: str, decision: str, session_id: str, persona: Persona, mcp: MCPClientManager
):
    with propagate_attributes(
        user_id=persona.id,
        session_id=session_id,
        metadata={"persona_role": persona.role, "resumes_pending_id": pending_id},
    ):
        entry = pending_actions.pop(pending_id, session_id, persona.id)
        if entry is None:
            yield _result({"type": "answer", "answer": "That request has expired or was already handled."})
            return

        mcp_action = "confirm" if decision == "confirm" else "cancel"
        yield {
            "kind": "step",
            "tool": "hris_write",
            "label": "Applying HR record change" if decision == "confirm" else "Cancelling change",
        }
        outcome = await dispatch_tool(
            mcp, "hris_write", {"action": mcp_action, "pending_id": pending_id}, persona
        )
        note = (
            f"The user confirmed this change. Result: {json.dumps(outcome)}"
            if decision == "confirm"
            else f"The user declined this change. Result: {json.dumps(outcome)}"
        )
        messages = entry["messages"] + [{"role": "user", "content": note}]

        # A pending_confirmation can only ever be staged after _run_loop's
        # hris_write redirect has already run, so this is always Anthropic —
        # asserted, not branched on, since entry["messages"] is only valid
        # to replay against the same provider that produced it.
        provider = entry.get("provider", "anthropic")
        assert provider == "anthropic", f"unexpected provider for staged HRIS write: {provider!r}"
        adapter = AnthropicAdapter(AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"]))
        system = build_system_prompt(persona)

        async for item in _run_loop(
            adapter, entry["model"], system, messages, mcp, persona, session_id, entry["question"]
        ):
            yield item
