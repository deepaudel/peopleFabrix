"""MCP client: spawns the peopleFabrix MCP tool server once (as a subprocess,
stdio transport) and keeps it alive for the life of the FastAPI process.

Also builds the Claude-facing tool schema from the MCP server's tool list,
stripping the internal ACTOR_PERSONA_PARAM so Claude never sees or sets it —
the orchestrator injects it server-side on every dispatch (see
app/orchestrator.py:dispatch_tool). This is the security boundary that stops
Claude from spoofing a different persona's identity.
"""

import json
import os
import sys
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Tool

ACTOR_PERSONA_PARAM = "actor_persona_id"


def to_claude_schema(tool: Tool) -> dict:
    schema = dict(tool.inputSchema)
    properties = dict(schema.get("properties", {}))
    properties.pop(ACTOR_PERSONA_PARAM, None)
    schema["properties"] = properties
    if "required" in schema:
        schema["required"] = [r for r in schema["required"] if r != ACTOR_PERSONA_PARAM]
    return {"name": tool.name, "description": tool.description or "", "input_schema": schema}


def parse_mcp_result(result) -> Any:
    """Unwrap a CallToolResult into plain Python data.

    FastMCP only populates structuredContent for return-type annotations it
    can build a JSON Schema for (e.g. list[...]) — plain `dict` return types
    (used throughout this project's tools) come back with structuredContent
    unset, so we fall back to parsing the text content as JSON. Verified
    against the actual installed mcp package, not assumed.
    """
    if result.structuredContent is not None:
        content = result.structuredContent
        if isinstance(content, dict) and set(content.keys()) == {"result"}:
            return content["result"]
        return content
    if result.content:
        first = result.content[0]
        if hasattr(first, "text"):
            try:
                return json.loads(first.text)
            except json.JSONDecodeError:
                return first.text
    return None


class MCPClientManager:
    def __init__(self) -> None:
        self.session: ClientSession | None = None
        self.claude_tool_defs: list[dict] = []
        self._stack: AsyncExitStack | None = None

    async def start(self) -> None:
        self._stack = AsyncExitStack()
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp_server.server"],
            env=os.environ.copy(),
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        tools = await self.session.list_tools()
        self.claude_tool_defs = [to_claude_schema(t) for t in tools.tools]

    async def stop(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self.session = None

    async def call_tool(self, name: str, arguments: dict) -> Any:
        assert self.session is not None, "MCPClientManager.start() must be called first"
        result = await self.session.call_tool(name, arguments)
        return parse_mcp_result(result)
