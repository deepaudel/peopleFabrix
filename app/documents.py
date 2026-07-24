"""In-memory staging store for generated documents (e.g. PDF letters) built
by an MCP tool in the subprocess, staged here by app/orchestrator.py so
app/main.py's GET /api/documents/{token} (running in the main process) can
serve the bytes without reaching into subprocess memory.

Same "single dict, no persistence, single-instance-only" pattern as
pending_actions.py, except get() is non-destructive — a re-download or
double-click shouldn't 404, unlike a pending HR write's one-shot
confirm/cancel. Entries expire after _TTL_SECONDS and are scoped to the
session+persona that created them, same ownership pattern as
pending_actions.py.
"""

import time
import uuid

_TTL_SECONDS = 30 * 60
_entries: dict[str, dict] = {}


def _purge_expired() -> None:
    now = time.time()
    expired = [token for token, e in _entries.items() if now - e["created_at"] > _TTL_SECONDS]
    for token in expired:
        del _entries[token]


def stage(session_id: str, persona_id: str, filename: str, content: bytes, content_type: str) -> str:
    _purge_expired()
    token = uuid.uuid4().hex
    _entries[token] = {
        "session_id": session_id,
        "persona_id": persona_id,
        "filename": filename,
        "content": content,
        "content_type": content_type,
        "created_at": time.time(),
    }
    return token


def get(token: str, session_id: str, persona_id: str) -> dict | None:
    """Returns the staged entry, or None if it doesn't exist, has expired,
    or doesn't belong to this session+persona. Does not remove it — repeat
    downloads within the TTL are expected to work."""
    _purge_expired()
    entry = _entries.get(token)
    if entry is None:
        return None
    if entry["session_id"] != session_id or entry["persona_id"] != persona_id:
        return None
    return entry
