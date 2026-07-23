"""MCP server exposing peopleFabrix's 4 tools: policy_search, hris_read,
hris_write, warehouse_query.

Run standalone for local testing:

    uv run python -m app.mcp_server.server

In production this is spawned as a subprocess by app.mcp_client.MCPClientManager
over stdio — it is never run directly by a human in that path.

Every tool that needs to know "who is asking" takes an internal `actor_persona_id`
argument. This is stripped from the schema Claude sees (in app/mcp_client.py)
and injected server-side by the orchestrator on every dispatch, so Claude can
never spoof a different persona's identity.
"""

from typing import Literal

from mcp.server.fastmcp import FastMCP

from app.mcp_server import hris_store, warehouse_data
from app.rag.config import TOP_K

mcp = FastMCP("peoplefabrix-tools")


@mcp.tool()
def policy_search(question: str, top_k: int = TOP_K) -> list[dict]:
    """Search the internal HR policy handbook for passages relevant to a question.

    Returns a list of {text, source_url, title} chunks. Always cite source_url
    when using a result to answer the user.
    """
    from app.rag.retrieve import get_relevant_chunks

    return get_relevant_chunks(question, top_k=top_k)


@mcp.tool()
def hris_read(
    actor_persona_id: str,
    target_persona_id: str | None = None,
    fields: list[str] | None = None,
) -> dict:
    """Read an HR record (PTO balance, employment info, etc).

    Omit target_persona_id to read the caller's own record. target_persona_id
    accepts either the person's full name (e.g. "Jane Chen") or a first name
    as it appears in conversation — you don't need an internal ID. Managers
    can read their direct reports' records; HRBPs can read anyone's;
    employees can only read their own. Unauthorized requests return an
    error, not a crash.
    """
    target = target_persona_id or actor_persona_id
    return hris_store.read(actor_persona_id, target, fields)


@mcp.tool()
def hris_write(
    actor_persona_id: str,
    action: Literal["propose", "confirm", "cancel"],
    target_persona_id: str | None = None,
    field: Literal["pto_balance_days"] | None = None,
    new_value: str | None = None,
    reason: str | None = None,
    pending_id: str | None = None,
) -> dict:
    """Propose, confirm, or cancel a change to an HR record.

    target_persona_id (for action="propose") accepts a person's name, same as
    hris_read — no internal ID needed. "pto_balance_days" is currently the
    only writable field. This is a two-phase write, gated by explicit user
    confirmation:
    1. action="propose" validates and stages the change WITHOUT writing it,
       returning a pending_id and a human-readable description. Always use
       this first and let the user confirm before ever calling "confirm".
    2. action="confirm" (with the pending_id from step 1) actually applies
       the change.
    3. action="cancel" discards a pending change without applying it.
    """
    if action == "propose":
        target = target_persona_id or actor_persona_id
        if field is None or new_value is None:
            return {"error": "invalid_request", "message": "field and new_value are required to propose a write."}
        return hris_store.propose_write(actor_persona_id, target, field, new_value, reason)
    if action == "confirm":
        if pending_id is None:
            return {"error": "invalid_request", "message": "pending_id is required to confirm a write."}
        return hris_store.confirm_write(pending_id)
    if action == "cancel":
        if pending_id is None:
            return {"error": "invalid_request", "message": "pending_id is required to cancel a write."}
        return hris_store.cancel_write(pending_id)
    return {"error": "invalid_request", "message": f"Unknown action: {action}"}


@mcp.tool()
def warehouse_query(
    actor_persona_id: str,
    query_name: Literal["headcount_by_department", "average_tenure_by_department", "pto_usage_trend"],
    department_filter: str | None = None,
) -> dict:
    """Run a pre-vetted workforce-analytics query (not free-form SQL).

    Available queries: headcount_by_department, average_tenure_by_department,
    pto_usage_trend. department_filter is optional and narrows any of them to
    a single department. Access is role-scoped: employees are not authorized
    for this tool at all; managers are automatically scoped to their own
    department (a department_filter for a different department is denied);
    HRBPs can query any department or the whole company. Denied requests
    return an error, not a crash.
    """
    return warehouse_data.run_query(actor_persona_id, query_name, department_filter)


if __name__ == "__main__":
    mcp.run(transport="stdio")
