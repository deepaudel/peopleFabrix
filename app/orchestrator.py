"""Claude + MCP agentic loop: the core of /api/ask.

A manual loop (not the SDK's Tool Runner) because the HRIS-write confirmation
gate (added in a later step) needs to inspect one specific tool's result
mid-loop and short-circuit before feeding it back to Claude — a black-box
tool-runner doesn't support that cleanly.
"""

import json
import logging
import os

from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from langfuse import get_client, observe, propagate_attributes

from app import history, pending_actions
from app.mcp_client import MCPClientManager
from app.personas import Persona

load_dotenv()  # module-level get_client() below reads env vars at import time

logger = logging.getLogger(__name__)
langfuse = get_client()

SONNET_MODEL = "claude-sonnet-5"
HAIKU_MODEL = "claude-haiku-4-5-20251001"
MAX_TOOL_ITERATIONS = 6

SYSTEM_PROMPT_TEMPLATE = """You are an internal HR and workforce assistant for the company.
Your role is to help employees and managers understand HR policies, benefits, workplace procedures, and workforce information.

You are currently talking to: {persona_name}, {persona_title} ({persona_department}), role: {persona_role}.
Every tool call is automatically scoped to this person's identity — you cannot look up or modify
anyone else's data unless your tools explicitly report you're authorized to (e.g. a manager may
access their direct reports' records; an HRBP may access anyone's).

Core Response Rules

1. Use your tools, don't guess
   * For policy/benefits/procedure questions, call policy_search and base your answer only on
     the returned passages. Do not rely on general knowledge for company-specific policy.
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
    )


def choose_model(question: str) -> str:
    """Deterministic routing, not a classifier: default to Sonnet; only short
    greeting/acknowledgment turns get Haiku. tools= stays attached either way,
    so a misclassified "hi, what's my PTO" can still trigger a tool call —
    this only picks the starting model, it doesn't withhold capability.
    """
    import re

    trivial = re.compile(r"^(hi|hello|hey|thanks|thank you|bye|ok|okay|great|cool)\b", re.I)
    q = question.strip()
    if len(q.split()) <= 6 and trivial.match(q):
        return HAIKU_MODEL
    return SONNET_MODEL


def extract_text(response) -> str:
    return "\n".join(block.text for block in response.content if block.type == "text").strip()


async def dispatch_tool(mcp: MCPClientManager, tool_name: str, tool_input: dict, persona: Persona) -> dict:
    args = {**tool_input, "actor_persona_id": persona.id}
    with langfuse.start_as_current_observation(
        name=f"tool:{tool_name}", as_type="tool", input=tool_input
    ) as span:
        result = await mcp.call_tool(tool_name, args)
        span.update(output=result)
        return result


def _pending_confirmation_id(tool_name: str, result: dict) -> str | None:
    """If this tool result represents an HRIS write awaiting user
    confirmation, returns its pending_id — else None. This is the hook the
    loop uses to short-circuit instead of letting Claude auto-confirm."""
    if tool_name == "hris_write" and isinstance(result, dict) and result.get("status") == "pending_confirmation":
        return result["pending_id"]
    return None


async def _run_loop(
    client: AsyncAnthropic,
    model: str,
    system: str,
    messages: list,
    mcp: MCPClientManager,
    persona: Persona,
    session_id: str,
    question: str,
) -> dict:
    for _ in range(MAX_TOOL_ITERATIONS):
        with langfuse.start_as_current_observation(
            name=f"claude:{model}", as_type="generation", model=model, input=messages
        ) as gen:
            response = await client.messages.create(
                model=model,
                max_tokens=1024,
                system=system,
                tools=mcp.claude_tool_defs,
                messages=messages,
            )
            gen.update(
                output=[block.model_dump() for block in response.content],
                usage_details={
                    "input": response.usage.input_tokens,
                    "output": response.usage.output_tokens,
                },
            )

        if response.stop_reason != "tool_use":
            answer = extract_text(response)
            history.append_turn(session_id, question, answer)
            return {"type": "answer", "answer": answer}

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        gate: tuple[str, str] | None = None  # (pending_id, description)
        for block in response.content:
            if block.type != "tool_use":
                continue
            result = await dispatch_tool(mcp, block.name, block.input, persona)
            tool_results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result)}
            )
            pending_id = _pending_confirmation_id(block.name, result)
            if pending_id:
                gate = (pending_id, result.get("description", "A change is awaiting your confirmation."))
        messages.append({"role": "user", "content": tool_results})

        if gate:
            pending_id, description = gate
            pending_actions.stage(
                pending_id=pending_id,
                session_id=session_id,
                persona_id=persona.id,
                question=question,
                messages=messages,
                model=model,
            )
            return {"type": "pending_action", "pending_id": pending_id, "description": description}

    fallback = "I wasn't able to finish that — could you rephrase or narrow the question?"
    history.append_turn(session_id, question, fallback)
    return {"type": "answer", "answer": fallback}


@observe(name="ask")
async def answer_question(question: str, session_id: str, persona: Persona, mcp: MCPClientManager) -> dict:
    with propagate_attributes(
        user_id=persona.id,
        session_id=session_id,
        metadata={"persona_role": persona.role},
    ):
        client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        model = choose_model(question)
        logger.info("routing question to %s: %r", model, question)

        messages = history.get_history(session_id) + [{"role": "user", "content": question}]
        system = build_system_prompt(persona)

        return await _run_loop(client, model, system, messages, mcp, persona, session_id, question)


@observe(name="confirm-action")
async def resume_pending_action(
    pending_id: str, decision: str, session_id: str, persona: Persona, mcp: MCPClientManager
) -> dict:
    with propagate_attributes(
        user_id=persona.id,
        session_id=session_id,
        metadata={"persona_role": persona.role, "resumes_pending_id": pending_id},
    ):
        entry = pending_actions.pop(pending_id, session_id, persona.id)
        if entry is None:
            return {"type": "answer", "answer": "That request has expired or was already handled."}

        mcp_action = "confirm" if decision == "confirm" else "cancel"
        outcome = await dispatch_tool(
            mcp, "hris_write", {"action": mcp_action, "pending_id": pending_id}, persona
        )
        note = (
            f"The user confirmed this change. Result: {json.dumps(outcome)}"
            if decision == "confirm"
            else f"The user declined this change. Result: {json.dumps(outcome)}"
        )
        messages = entry["messages"] + [{"role": "user", "content": note}]

        client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        system = build_system_prompt(persona)

        return await _run_loop(
            client, entry["model"], system, messages, mcp, persona, session_id, entry["question"]
        )
