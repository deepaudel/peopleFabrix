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
    location_city: str
    location_country: str
    hire_date: str  # ISO 8601 (YYYY-MM-DD)
    manager_id: str | None
    direct_report_ids: tuple[str, ...] = field(default_factory=tuple)


PERSONAS: dict[str, Persona] = {
    "emp_jane": Persona(
        id="emp_jane",
        display_name="Jane Chen",
        role="employee",
        title="Software Engineer",
        department="Engineering",
        location_city="Charlotte",
        location_country="USA",
        hire_date="2022-03-14",  # matches hris_store's start_date for this persona
        manager_id="mgr_sam",
    ),
    "emp_alex": Persona(
        id="emp_alex",
        display_name="Alex Rivera",
        role="employee",
        title="Product Analyst",
        department="Engineering",
        location_city="London",
        location_country="UK",
        hire_date="2023-07-01",  # matches hris_store's start_date for this persona
        manager_id="mgr_sam",
    ),
    "mgr_sam": Persona(
        id="mgr_sam",
        display_name="Sam Okafor",
        role="manager",
        title="Engineering Manager",
        department="Engineering",
        location_city="Charlotte",
        location_country="USA",
        hire_date="2019-01-08",  # matches hris_store's start_date for this persona
        manager_id="hrbp_taylor",
        direct_report_ids=("emp_jane", "emp_alex"),
    ),
    "hrbp_taylor": Persona(
        id="hrbp_taylor",
        display_name="Taylor Brooks",
        role="hrbp",
        title="HR Business Partner",
        department="People",
        location_city="Charlotte",
        location_country="USA",
        hire_date="2018-05-21",  # matches hris_store's start_date for this persona
        manager_id=None,
    ),
}

PERSONA_COOKIE_NAME = "persona_id"


def resolve_persona(request: Request) -> Persona | None:
    persona_id = request.cookies.get(PERSONA_COOKIE_NAME)
    if not persona_id:
        return None
    return PERSONAS.get(persona_id)
