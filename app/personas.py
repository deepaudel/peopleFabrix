"""Hardcoded personas standing in for real SSO/identity in v1.

All persona resolution funnels through resolve_persona() so that swapping in
real SSO later (mapping a logged-in employee to their internal ID) only
requires changing this one function — nothing downstream (orchestrator, MCP
tool dispatch) needs to know how identity was resolved.
"""

from dataclasses import dataclass, field

from fastapi import Request


@dataclass(frozen=True)
class Persona:
    id: str
    display_name: str
    role: str  # "employee" | "manager" | "hrbp"
    title: str
    department: str
    manager_id: str | None
    direct_report_ids: tuple[str, ...] = field(default_factory=tuple)


PERSONAS: dict[str, Persona] = {
    "emp_jane": Persona(
        id="emp_jane",
        display_name="Jane Chen",
        role="employee",
        title="Software Engineer",
        department="Engineering",
        manager_id="mgr_sam",
    ),
    "emp_alex": Persona(
        id="emp_alex",
        display_name="Alex Rivera",
        role="employee",
        title="Product Analyst",
        department="Product",
        manager_id="mgr_sam",
    ),
    "mgr_sam": Persona(
        id="mgr_sam",
        display_name="Sam Okafor",
        role="manager",
        title="Engineering Manager",
        department="Engineering",
        manager_id="hrbp_taylor",
        direct_report_ids=("emp_jane", "emp_alex"),
    ),
    "hrbp_taylor": Persona(
        id="hrbp_taylor",
        display_name="Taylor Brooks",
        role="hrbp",
        title="HR Business Partner",
        department="People",
        manager_id=None,
    ),
}

PERSONA_COOKIE_NAME = "persona_id"


def resolve_persona(request: Request) -> Persona | None:
    persona_id = request.cookies.get(PERSONA_COOKIE_NAME)
    if not persona_id:
        return None
    return PERSONAS.get(persona_id)
