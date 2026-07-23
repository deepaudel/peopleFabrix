"""Provider-neutral shapes that app/orchestrator.py's _run_loop operates on,
so it never needs to know whether the current turn is running on Claude or
OpenAI. Each provider adapter (anthropic_provider.py, openai_provider.py)
translates its own SDK's response shape into these before handing control
back to _run_loop.
"""

from dataclasses import dataclass
from typing import Literal


@dataclass
class ToolCallRequest:
    id: str
    name: str
    input: dict


@dataclass
class TurnResult:
    stop_reason: Literal["tool_use", "end_turn"]
    text: str
    tool_calls: list[ToolCallRequest]
    assistant_message: dict
    log_output: object
    usage: dict  # {"input": int, "output": int}
