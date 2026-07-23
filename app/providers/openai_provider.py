"""OpenAI adapter: implements the provider-neutral streaming interface
_run_loop expects (see app/providers/base.py), wrapping AsyncOpenAI's Chat
Completions API. This is the default provider for every new turn — see
app/orchestrator.py:answer_question.

Uses raw `create(stream=True)` chunk iteration rather than the SDK's higher-
level `chat.completions.stream()` helper: that helper's auto-parsing path
calls `_validate_input_tools`, which requires every tool's function schema
to be marked `"strict": True` (and shaped to satisfy strict mode — e.g. every
property required, no unconstrained additionalProperties). Our tool schemas
come from FastMCP's dynamically-generated JSON Schema (app/mcp_client.py) and
aren't strict-mode compliant, so the helper raises ValueError before a single
token streams. Raw `create()` has no such requirement.
"""

import json
from typing import AsyncIterator

from openai import AsyncOpenAI

from app.providers.base import ToolCallRequest, TurnResult


class OpenAIAdapter:
    provider = "openai"

    def __init__(self, client: AsyncOpenAI) -> None:
        self._client = client

    async def stream_turn(
        self, model: str, system: str, messages: list, tools: list[dict]
    ) -> AsyncIterator[dict]:
        # OpenAI has no separate top-level system= kwarg — folded into the
        # message list here rather than mutating the caller's `messages`,
        # which must stay provider-neutral for reuse across turns.
        full_messages = [{"role": "system", "content": system}] + messages
        stream = await self._client.chat.completions.create(
            model=model,
            max_completion_tokens=1024,
            messages=full_messages,
            tools=tools,
            stream=True,
            stream_options={"include_usage": True},
        )

        text_parts: list[str] = []
        # Tool-call argument fragments arrive keyed by their position in the
        # response (`delta.tool_calls[i].index`), not necessarily in order or
        # fully formed in a single chunk — accumulate by index, then sort.
        call_slots: dict[int, dict] = {}
        finish_reason: str | None = None
        usage = None

        async for chunk in stream:
            if chunk.usage is not None:
                usage = chunk.usage
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            delta = choice.delta
            if delta.content:
                text_parts.append(delta.content)
                yield {"kind": "answer_delta", "text": delta.content}
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    slot = call_slots.setdefault(tc_delta.index, {"id": None, "name": None, "arguments": ""})
                    if tc_delta.id:
                        slot["id"] = tc_delta.id
                    if tc_delta.function and tc_delta.function.name:
                        slot["name"] = tc_delta.function.name
                    if tc_delta.function and tc_delta.function.arguments:
                        slot["arguments"] += tc_delta.function.arguments

        text = "".join(text_parts)
        tool_calls = [
            ToolCallRequest(id=slot["id"], name=slot["name"], input=json.loads(slot["arguments"] or "{}"))
            for _, slot in sorted(call_slots.items())
        ]

        assistant_message: dict = {"role": "assistant", "content": text or None}
        if tool_calls:
            assistant_message["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.input)},
                }
                for tc in tool_calls
            ]

        yield {
            "kind": "_turn_result",
            "result": TurnResult(
                stop_reason="tool_use" if finish_reason == "tool_calls" else "end_turn",
                text=text,
                tool_calls=tool_calls,
                assistant_message=assistant_message,
                log_output=assistant_message,
                usage={
                    "input": usage.prompt_tokens if usage else 0,
                    "output": usage.completion_tokens if usage else 0,
                },
            ),
        }

    @staticmethod
    def tool_result_messages(tool_calls: list[ToolCallRequest], results: list[dict]) -> list[dict]:
        return [
            {"role": "tool", "tool_call_id": tc.id, "content": json.dumps(r)}
            for tc, r in zip(tool_calls, results)
        ]
