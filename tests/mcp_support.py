"""Helpers for the MCP tests: a real Streamable HTTP server in a thread and a synchronous wrapper over the SDK client.

Nothing here is mocked. ``serve`` runs the production ASGI app under uvicorn on a free local port, and ``McpSession``
drives it with the official ``mcp.Client`` over HTTP (or over stdio for ``StdioSession``).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, Self

import uvicorn
from mcp import Client, StdioServerParameters
from mcp.types import CallToolResult, ListToolsResult

from causelens.mcp import DatasetRegistry, create_http_app, create_server


class RunningServer:
    def __init__(self, registry: DatasetRegistry, **app_kwargs: Any):
        self.server = create_server(registry)
        app = create_http_app(self.server, **app_kwargs)
        self._uvicorn = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
        self._thread = threading.Thread(target=self._uvicorn.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 15
        while not self._uvicorn.started:
            if time.monotonic() > deadline:
                raise RuntimeError("the test server did not start")
            time.sleep(0.01)
        port = self._uvicorn.servers[0].sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}{app_kwargs.get('path', '/mcp')}"

    def stop(self) -> None:
        self._uvicorn.should_exit = True
        self._thread.join(timeout=10)


@contextmanager
def serve(registry: DatasetRegistry, **app_kwargs: Any) -> Iterator[RunningServer]:
    running = RunningServer(registry, **app_kwargs)
    try:
        yield running
    finally:
        running.stop()


class McpSession:
    """A connected client with a synchronous API. One long-lived task owns the connection (anyio requires it)."""

    def __init__(self, target: str | StdioServerParameters, **client_kwargs: Any):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._ready: concurrent.futures.Future[Client] = concurrent.futures.Future()
        self._queue: asyncio.Queue | None = None
        self._main = asyncio.run_coroutine_threadsafe(self._run(target, client_kwargs), self._loop)
        self.client: Client = self._ready.result(timeout=60)

    async def _run(self, target: str | StdioServerParameters, client_kwargs: dict[str, Any]) -> None:
        self._queue = asyncio.Queue()
        try:
            async with Client(target, cache=None, **client_kwargs) as client:
                self._ready.set_result(client)
                while True:
                    item = await self._queue.get()
                    if item is None:
                        return
                    factory, fut = item
                    try:
                        fut.set_result(await factory(client))
                    except BaseException as exc:  # noqa: BLE001 - relay anything to the calling thread
                        fut.set_exception(exc)
        except BaseException as exc:
            if not self._ready.done():
                self._ready.set_exception(exc)
            raise

    def _submit(self, factory: Callable[[Client], Any], timeout: float = 60) -> Any:
        fut: concurrent.futures.Future = concurrent.futures.Future()
        assert self._queue is not None
        self._loop.call_soon_threadsafe(self._queue.put_nowait, (factory, fut))
        return fut.result(timeout=timeout)

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> CallToolResult:
        return self._submit(lambda c: c.call_tool(name, arguments or {}))

    def timed_call(self, name: str, arguments: dict[str, Any] | None = None) -> tuple[CallToolResult, float]:
        start = time.perf_counter()
        result = self.call(name, arguments)
        return result, time.perf_counter() - start

    def list_tools(self) -> ListToolsResult:
        return self._submit(lambda c: c.list_tools())

    def close(self) -> None:
        if self._queue is not None and not self._main.done():
            self._loop.call_soon_threadsafe(self._queue.put_nowait, None)
            try:
                self._main.result(timeout=15)
            except Exception:  # noqa: BLE001, S110 - shutting down; the test already has its result
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=10)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def structured(result: CallToolResult) -> dict[str, Any]:
    """The structured content of a successful result (fails loudly on a tool error)."""
    assert not result.is_error, result.content[0].text if result.content else "tool error"
    assert result.structured_content is not None
    return result.structured_content


def error_text(result: CallToolResult) -> str:
    assert result.is_error, "expected a tool error"
    return " ".join(getattr(block, "text", "") for block in result.content)
