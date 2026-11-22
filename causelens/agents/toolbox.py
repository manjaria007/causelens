"""Calling the MCP server through the official Python SDK, with role limits and a log entry for every call.

``McpToolClient`` is a thin async wrapper over ``mcp.Client``. It accepts what the SDK accepts: a Streamable HTTP URL, a
``StdioServerParameters`` (the server runs as a child process) or an ``MCPServer`` instance (in process, for tests).

``Toolbox`` is what a role holds. It refuses a tool outside the role's subset before anything is sent, expands ``Prior``
arguments from the log, calls the server, and appends a ``LoggedCall`` whether the call succeeded or not.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Protocol, Self

from mcp import Client, StdioServerParameters

from causelens.agents.roles import Role
from causelens.agents.state import (
    PRIOR_ARGUMENT_SOURCE,
    InvestigationState,
    LoggedCall,
    Prior,
    thaw,
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


def _describe(exc: BaseException) -> str:
    """The leaf messages of an exception, so a task-group wrapper does not hide the cause."""
    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(_describe(e) for e in exc.exceptions)
    return f"{type(exc).__name__}: {exc}"


class SessionClosed(ConnectionError):
    """The MCP session ended (the server went away or the transport failed) before or during a call."""


class McpToolClient:
    """``mcp.Client`` behind the small ``ToolClient`` interface. Use as ``async with McpToolClient(target) as client``.

    The SDK's client cancels the task that opened it when its transport fails, which would surface in the caller as a
    ``CancelledError`` that looks like the caller's own cancellation. So one task owns the session and the caller awaits a
    future: a transport failure becomes an ordinary ``SessionClosed`` error for the call in flight and for every later call, and
    a real cancellation of the caller still propagates.
    """

    def __init__(self, target: Any):
        self._target = target
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._failure: str | None = None

    async def __aenter__(self) -> Self:
        ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._task = asyncio.create_task(self._own(ready))
        await ready
        return self

    async def __aexit__(self, *exc: object) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        self._queue.put_nowait(None)
        await asyncio.wait({task}, timeout=30)  # never raises the owner's error: a failed session was already reported per call
        if not task.done():
            task.cancel()
            await asyncio.wait({task}, timeout=5)

    async def _own(self, ready: asyncio.Future[None]) -> None:
        pending: asyncio.Future[Any] | None = None
        try:
            async with Client(self._target, cache=None) as client:
                ready.set_result(None)
                while True:
                    item = await self._queue.get()
                    if item is None:
                        return
                    factory, pending = item
                    try:
                        pending.set_result(await factory(client))
                    except Exception as exc:  # noqa: BLE001 - relayed to the caller, which logs it as a failed call
                        pending.set_exception(exc)
                    pending = None
        except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001 - the session died; see the class docstring
            self._failure = _describe(exc) if not isinstance(exc, asyncio.CancelledError) else "the session was cancelled by its transport"
            if not ready.done():
                ready.set_exception(SessionClosed(self._failure))
            error = SessionClosed(f"the MCP session failed: {self._failure}")
            if pending is not None and not pending.done():
                pending.set_exception(error)
            while not self._queue.empty():
                item = self._queue.get_nowait()
                if item is not None and not item[1].done():
                    item[1].set_exception(error)

    async def _submit(self, factory: Any) -> Any:
        if self._task is None:
            raise SessionClosed("the MCP session is not open")
        if self._task.done():
            raise SessionClosed(f"the MCP session has ended: {self._failure or 'closed'}")
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._queue.put_nowait((factory, future))
        if self._task.done() and not future.done():  # the session ended while the request was being queued
            future.set_exception(SessionClosed(f"the MCP session has ended: {self._failure or 'closed'}"))
        return await future

    async def list_tool_names(self) -> list[str]:
        result = await self._submit(lambda client: client.list_tools())
        return [t.name for t in result.tools]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> RawOutcome:
        result = await self._submit(lambda client: client.call_tool(name, arguments))
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
        # What is sent and what is logged are separate copies of the caller's data, so a caller (or a client) that edits its own
        # objects afterwards cannot rewrite the history.
        sent = thaw({k: self._expand(k, v, tool) for k, v in arguments.items() if v is not None})
        logged = thaw({k: ({"$prior": v.seq} if isinstance(v, Prior) else v) for k, v in arguments.items() if v is not None})
        try:
            outcome = await self._client.call_tool(tool, sent)
        except Exception as exc:  # noqa: BLE001 - a transport failure (closed connection, timeout) is a failed call; cancellation is not caught
            outcome = RawOutcome(None, True, f"{type(exc).__name__}: {exc}")
        entry = LoggedCall(
            seq=len(self.state.log) + 1, role=role.name, stage=stage, tool=tool, arguments=logged,
            is_error=outcome.is_error, result=None if outcome.structured is None else thaw(outcome.structured), error=outcome.error,
        )
        self.state.log.append(entry)
        if entry.is_error:
            raise ToolCallError(entry)
        return entry
