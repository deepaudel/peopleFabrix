"""Anthropic adapter: implements the provider-neutral streaming interface
_run_loop expects (see app/providers/base.py), wrapping AsyncAnthropic's
native message streaming. This is today's only path for the HRIS-write flow
— see app/orchestrator.py's _RedirectToClaude handling.
"""

import json
from typing import AsyncIterator

from anthropic import AsyncAnthropic

from app.providers.base import ToolCallRequest, TurnResult


class AnthropicAdapter:
    provider = "anthropic"

    def __init__(self, client: AsyncAnthropic) -> None:
        self._client = client

    async def stream_turn(
        self, model: str, system: str, messages: list, tools: list[dict]
    ) -> AsyncIterator[dict]:
        async with self._client.messages.stream(
            model=model,
            max_tokens=1024,
            system=system,
            tools=tools,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield {"kind": "answer_delta", "text": text}
            response = await stream.get_final_message()

        tool_calls = [
            ToolCallRequest(id=block.id, name=block.name, input=block.input)
            for block in response.content
            if block.type == "tool_use"
        ]
        text = "\n".join(block.text for block in response.content if block.type == "text").strip()
        yield {
            "kind": "_turn_result",
            "result": TurnResult(
                stop_reason="tool_use" if response.stop_reason == "tool_use" else "end_turn",
                text=text,
                tool_calls=tool_calls,
                assistant_message={"role": "assistant", "content": response.content},
                log_output=[block.model_dump() for block in response.content],
                usage={
                    "input": response.usage.input_tokens,
                    "output": response.usage.output_tokens,
                },
            ),
        }

    @staticmethod
    def tool_result_messages(tool_calls: list[ToolCallRequest], results: list[dict]) -> list[dict]:
        return [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": tc.id, "content": json.dumps(r)}
                    for tc, r in zip(tool_calls, results)
                ],
            }
        ]
