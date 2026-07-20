MAX_HISTORY_TURNS = 6

_sessions: dict[str, list[dict]] = {}


def get_history(session_id: str) -> list[dict]:
    return _sessions.get(session_id, [])


def append_turn(session_id: str, question: str, answer: str) -> None:
    history = _sessions.setdefault(session_id, [])
    history.append({"role": "user", "content": question})
    history.append({"role": "assistant", "content": answer})

    max_messages = MAX_HISTORY_TURNS * 2
    if len(history) > max_messages:
        del history[:-max_messages]
