"""Calling the MCP server through the official Python SDK, with role limits and a log entry for every call.

``McpToolClient`` is a thin async wrapper over ``mcp.Client``. It accepts what the SDK accepts: a Streamable HTTP URL, a
``StdioServerParameters`` (the server runs as a child process) or an ``MCPServer`` instance (in process, for tests).

``Toolbox`` is what a role holds. It refuses a tool outside the role's subset before anything is sent, expands ``Prior``
arguments from the log, calls the server, and appends a ``LoggedCall`` whether the call succeeded or not.
"""
from __future__ import annotations

import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Protocol, Self

from mcp import Client, StdioServerParameters

from causelens.agents.roles import Role
from causelens.agents.state import (
    PRIOR_ARGUMENT_SOURCE,
    InvestigationState,
    LoggedCall,
    Prior,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class ToolNotAllowed(PermissionError):
    """A role asked for a tool outside its subset."""


class ToolCallError(RuntimeError):
    """The server reported an error for a call. The failed call is in the log (``call``)."""

    def __init__(self, call: LoggedCall):
        super().__init__(f"{call.tool} failed: {call.error}")
        self.call = call


class RawOutcome:
    """What a client returns: the structured content, or the error text."""

    __slots__ = ("error", "is_error", "structured")

    def __init__(self, structured: dict[str, Any] | None, is_error: bool, error: str | None):
        self.structured, self.is_error, self.error = structured, is_error, error


class ToolClient(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> RawOutcome: ...


class McpToolClient:
    """``mcp.Client`` behind the small ``ToolClient`` interface. Use as ``async with McpToolClient(target) as client``."""

    def __init__(self, target: Any):
        self._target = target
        self._stack = AsyncExitStack()
        self._client: Client | None = None

    async def __aenter__(self) -> Self:
        self._client = await self._stack.enter_async_context(Client(self._target, cache=None))
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.aclose()
        self._client = None

    async def list_tool_names(self) -> list[str]:
        assert self._client is not None, "use the client inside 'async with'"
        return [t.name for t in (await self._client.list_tools()).tools]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> RawOutcome:
        assert self._client is not None, "use the client inside 'async with'"
        result = await self._client.call_tool(name, arguments)
        if result.is_error:
            text = " ".join(getattr(block, "text", "") for block in result.content).strip()
            return RawOutcome(None, True, text or "tool error")
        if result.structured_content is None:
            return RawOutcome(None, True, "the tool returned no structured content")
        return RawOutcome(dict(result.structured_content), False, None)


def stdio_target(metrics: Path, events: Path | None, dataset_id: str = "reference") -> StdioServerParameters:
    """A server run as a child process over stdio, with one dataset. The caller never gives the server a path."""
    args = ["-m", "causelens.mcp", "--transport", "stdio", "--dataset", dataset_id, str(metrics)]
    if events is not None:
        args.append(str(events))
    return StdioServerParameters(command=sys.executable, args=args, env={"PYTHONPATH": str(REPO_ROOT)}, cwd=str(REPO_ROOT))


class Toolbox:
    """Role-checked, logged access to the server for one investigation."""

    def __init__(self, client: ToolClient, state: InvestigationState):
        self._client = client
        self.state = state

    def _expand(self, name: str, value: Any, tool: str) -> Any:
        if isinstance(value, Prior):
            source = PRIOR_ARGUMENT_SOURCE.get(name)
            prior = self.state.call(value.seq)
            if source is None or prior.tool != source or prior.is_error or prior.result is None:
                raise ValueError(f"argument {name!r} of {tool} must be the result of {source}, not call {value.seq} ({prior.tool})")
            return prior.result
        return value

    async def call(self, role: Role, stage: str, tool: str, **arguments: Any) -> LoggedCall:
        if tool not in role.tools:
            raise ToolNotAllowed(f"the {role.name} role may not call {tool}; its tools are {sorted(role.tools)}")
        sent = {k: self._expand(k, v, tool) for k, v in arguments.items() if v is not None}
        logged = {k: ({"$prior": v.seq} if isinstance(v, Prior) else v) for k, v in arguments.items() if v is not None}
        outcome = await self._client.call_tool(tool, sent)
        entry = LoggedCall(
            seq=len(self.state.log) + 1, role=role.name, stage=stage, tool=tool, arguments=logged,
            is_error=outcome.is_error, result=outcome.structured, error=outcome.error,
        )
        self.state.log.append(entry)
        if entry.is_error:
            raise ToolCallError(entry)
        return entry
