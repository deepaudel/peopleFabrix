"""Mock HRIS: a small in-memory per-persona employee record store.

Stands in for a real HRIS vendor API. Swapping in the real thing later means
replacing the bodies of read()/propose_write()/confirm_write()/cancel_write()
with real API calls — the calling convention (scoping rules, propose/confirm/
cancel shape) stays the same.
"""

import time
import uuid

from app.personas import PERSONAS

WRITABLE_FIELDS = {"pto_balance_days"}

_records: dict[str, dict] = {
    "emp_jane": {
        "pto_balance_days": 14.0,
        "pto_used_ytd": 6.0,
        "employment_type": "Full-time",
        "start_date": "2022-03-14",
    },
    "emp_alex": {
        "pto_balance_days": 9.5,
        "pto_used_ytd": 10.5,
        "employment_type": "Full-time",
        "start_date": "2023-07-01",
    },
    "mgr_sam": {
        "pto_balance_days": 18.0,
        "pto_used_ytd": 4.0,
        "employment_type": "Full-time",
        "start_date": "2019-01-08",
    },
    "hrbp_taylor": {
        "pto_balance_days": 20.0,
        "pto_used_ytd": 2.0,
        "employment_type": "Full-time",
        "start_date": "2018-05-21",
    },
}

_PENDING_TTL_SECONDS = 15 * 60
_pending: dict[str, dict] = {}


def _purge_expired_pending() -> None:
    now = time.time()
    expired = [pid for pid, entry in _pending.items() if now - entry["created_at"] > _PENDING_TTL_SECONDS]
    for pid in expired:
        del _pending[pid]


def resolve_persona_id(identifier: str) -> str | None:
    """Claude only knows people by name from conversation context, not by
    internal persona id — accept either. Not a security boundary: this only
    resolves which record someone means, access is still checked separately.
    """
    if identifier in PERSONAS:
        return identifier
    needle = identifier.strip().lower()
    for persona in PERSONAS.values():
        if persona.display_name.lower() == needle or persona.display_name.lower().split()[0] == needle:
            return persona.id
    return None


def _accessible_targets(actor_persona_id: str) -> set[str]:
    actor = PERSONAS.get(actor_persona_id)
    if actor is None:
        return set()
    if actor.role == "hrbp":
        return set(PERSONAS.keys())
    if actor.role == "manager":
        return {actor.id, *actor.direct_report_ids}
    return {actor.id}


def read(actor_persona_id: str, target_persona_id: str, fields: list[str] | None = None) -> dict:
    resolved = resolve_persona_id(target_persona_id)
    if resolved is None:
        return {"error": "not_found", "message": f"No such person: {target_persona_id}"}
    target_persona_id = resolved

    if target_persona_id not in _accessible_targets(actor_persona_id):
        return {
            "error": "not_authorized",
            "message": "You aren't authorized to view this person's HR record.",
        }

    record = _records.get(target_persona_id, {})
    persona = PERSONAS[target_persona_id]
    full = {
        "persona_id": target_persona_id,
        "display_name": persona.display_name,
        "title": persona.title,
        "department": persona.department,
        **record,
    }
    if fields:
        return {k: full[k] for k in fields if k in full}
    return full


def propose_write(
    actor_persona_id: str,
    target_persona_id: str,
    field: str,
    new_value: str,
    reason: str | None = None,
) -> dict:
    _purge_expired_pending()

    resolved = resolve_persona_id(target_persona_id)
    if resolved is None:
        return {"error": "not_found", "message": f"No such person: {target_persona_id}"}
    target_persona_id = resolved
    if target_persona_id not in _accessible_targets(actor_persona_id):
        return {"error": "not_authorized", "message": "You aren't authorized to modify this person's HR record."}
    if field not in WRITABLE_FIELDS:
        return {"error": "not_writable", "message": f"'{field}' is not a writable field."}

    try:
        parsed_value = float(new_value)
    except ValueError:
        return {"error": "invalid_value", "message": f"'{new_value}' is not a valid number for '{field}'."}

    pending_id = uuid.uuid4().hex
    persona = PERSONAS[target_persona_id]
    old_value = _records.get(target_persona_id, {}).get(field)
    description = (
        f"Set {persona.display_name}'s {field.replace('_', ' ')} to {parsed_value} "
        f"(currently {old_value})"
        + (f" — reason: {reason}" if reason else "")
    )
    _pending[pending_id] = {
        "target_persona_id": target_persona_id,
        "field": field,
        "new_value": parsed_value,
        "reason": reason,
        "created_at": time.time(),
    }
    return {"status": "pending_confirmation", "pending_id": pending_id, "description": description}


def confirm_write(pending_id: str) -> dict:
    _purge_expired_pending()
    entry = _pending.pop(pending_id, None)
    if entry is None:
        return {"error": "not_found", "message": "That pending change has expired or doesn't exist."}

    target_persona_id = entry["target_persona_id"]
    field = entry["field"]
    _records.setdefault(target_persona_id, {})[field] = entry["new_value"]
    return {
        "status": "committed",
        "pending_id": pending_id,
        "new_state": read(target_persona_id, target_persona_id),
    }


def cancel_write(pending_id: str) -> dict:
    entry = _pending.pop(pending_id, None)
    if entry is None:
        return {"error": "not_found", "message": "That pending change has expired or doesn't exist."}
    return {"status": "cancelled", "pending_id": pending_id}
