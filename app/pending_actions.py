"""In-memory staging store for paused Claude conversations awaiting a
human's explicit confirm/cancel decision on a proposed HR-record write.

Same "single dict, no persistence, single-instance-only" pattern as
history.py — consistent with the rest of this app's architecture. Entries
expire after _PENDING_TTL_SECONDS and are scoped to the session+persona that
created them so one browser session can't confirm another's pending action.
"""

import time

_PENDING_TTL_SECONDS = 15 * 60
_entries: dict[str, dict] = {}


def _purge_expired() -> None:
    now = time.time()
    expired = [pid for pid, e in _entries.items() if now - e["created_at"] > _PENDING_TTL_SECONDS]
    for pid in expired:
        del _entries[pid]


def stage(
    pending_id: str,
    session_id: str,
    persona_id: str,
    question: str,
    messages: list,
    model: str,
    provider: str,
) -> None:
    _purge_expired()
    _entries[pending_id] = {
        "session_id": session_id,
        "persona_id": persona_id,
        "question": question,
        "messages": messages,
        "model": model,
        "provider": provider,
        "created_at": time.time(),
    }


def pop(pending_id: str, session_id: str, persona_id: str) -> dict | None:
    """Removes and returns the staged entry, or None if it doesn't exist,
    has expired, or doesn't belong to this session+persona."""
    _purge_expired()
    entry = _entries.get(pending_id)
    if entry is None:
        return None
    if entry["session_id"] != session_id or entry["persona_id"] != persona_id:
        return None
    del _entries[pending_id]
    return entry
