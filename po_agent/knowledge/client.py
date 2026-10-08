"""The knowledge protocol the graph uses, with two implementations: a real MCP client over
stdio, and an in-process one over the same data for tests and offline runs.

Every call is one `mcp.call` span with mcp.server, mcp.tool, result_count and status.
"""
from __future__ import annotations

import contextlib
import json
import sys
import time
from collections.abc import AsyncIterator
from typing import Any, Protocol, Self

from .. import telemetry as T
from ..config import KnowledgeConfig

SERVER_NAME = "enterprise-knowledge"


class Knowledge(Protocol):
    async def call(self, tool: str, **arguments: Any) -> list[dict]: ...


class StaticKnowledge:
    """Same tools, same data, no subprocess. For tests and `knowledge.transport: inprocess`."""

    def __init__(self, telemetry: T.Telemetry | None = None, catalogue: dict | None = None):
        from . import server
        self.telemetry = telemetry
        self._tools = server.TOOLS
        self._catalogue = catalogue
        if catalogue is not None:
            server.load_catalogue = lambda path=None: catalogue  # type: ignore[assignment]

    async def call(self, tool: str, **arguments: Any) -> list[dict]:
        return await _traced(self.telemetry, tool, lambda: self._tools[tool](**arguments))


class MCPKnowledge:
    """A ClientSession over stdio to the configured server command."""

    def __init__(self, config: KnowledgeConfig, telemetry: T.Telemetry | None = None):
        self.config = config
        self.telemetry = telemetry
        self._stack = contextlib.AsyncExitStack()
        self.session = None

    async def __aenter__(self) -> Self:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        cmd, *args = self.config.command
        if cmd == "python":
            cmd = sys.executable
        params = StdioServerParameters(command=cmd, args=args)
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        return self

    async def __aexit__(self, *exc) -> None:
        await self._stack.aclose()

    async def call(self, tool: str, **arguments: Any) -> list[dict]:
        async def go():
            result = await self.session.call_tool(tool, arguments or {})
            if result.isError:
                raise RuntimeError(f"MCP tool {tool} failed: {_text(result)[:300]}")
            structured = getattr(result, "structuredContent", None)
            if isinstance(structured, dict) and "result" in structured:
                return structured["result"]
            text = _text(result)
            data = json.loads(text) if text else []
            return data if isinstance(data, list) else [data]
        return await _traced(self.telemetry, tool, go)


def _text(result) -> str:
    return "".join(getattr(c, "text", "") for c in result.content)


async def _traced(telemetry: T.Telemetry | None, tool: str, fn) -> list[dict]:
    attrs = {T.MCP_SERVER: SERVER_NAME, T.MCP_TOOL: tool}
    if telemetry is None:
        out = fn()
        return await out if hasattr(out, "__await__") else out
    with telemetry.span("mcp.call", attrs, kind=T.SpanKind.CLIENT) as span:
        started = time.perf_counter()
        try:
            out = fn()
            out = await out if hasattr(out, "__await__") else out
        except Exception:
            telemetry.record_mcp(attrs, (time.perf_counter() - started) * 1000, False)
            raise
        ms = (time.perf_counter() - started) * 1000
        span.set_attributes({T.RESULT_COUNT: len(out), T.LATENCY_MS: round(ms, 1)})
        telemetry.record_mcp(attrs, ms, True)
        return out


@contextlib.asynccontextmanager
async def open_knowledge(config: KnowledgeConfig, telemetry: T.Telemetry | None) -> AsyncIterator[Knowledge]:
    if config.transport == "inprocess":
        yield StaticKnowledge(telemetry)
        return
    async with MCPKnowledge(config, telemetry) as k:
        yield k
