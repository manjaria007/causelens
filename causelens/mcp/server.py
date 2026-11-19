"""Build the MCP server and its HTTP app."""
import contextlib
from collections.abc import Iterator

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.utilities.func_metadata import ArgModelBase
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from causelens.mcp.datasets import DatasetRegistry
from causelens.mcp.tools import TOOL_NAMES, register_tools

SERVER_NAME = "causelens"
SERVER_VERSION = "0.1.0"
DEFAULT_PATH = "/mcp"

INSTRUCTIONS = """\
CauseLens analytics. Every number comes from a deterministic tool; quote tool results, never compute or round them.

- Data-dependent tools take a dataset_id (call list_datasets). Files are read-only and never chosen by the caller.
- Windows are inclusive ISO dates. Conversion rates are fractions; effects and changes are in percentage points (pp).
- Prior results (data quality, change point, decomposition, candidate testing, DiD) are typed JSON: pass a tool's
  output unchanged as the matching argument of the next tool. Use the same dataset_id, focal, quality and
  apply_exclusions throughout so every step rests on the same population and the same excluded days.
- Insufficient data is a normal result with a status and a reason, not an error. An invalid argument is an error that
  names the problem.
- Follow the scientist rules: a coincidence in time or location is not a cause; state the evidence strength and the
  assumptions that come with each result.
"""


@contextlib.contextmanager
def _strict_argument_models() -> Iterator[None]:
    """Make tools registered inside the block reject unknown arguments.

    The SDK's argument models ignore extra keys, so a misspelt optional argument (``mde`` for ``mde_pp``) would be
    dropped silently and the default used. While tools are registered, the base class of the generated argument models
    forbids extras; each model copies that setting when it is created, so the tools keep it after the base is restored.
    The published input schema then carries ``additionalProperties: false``. A test asserts both.
    """
    config = ArgModelBase.model_config
    previous = config.get("extra")
    config["extra"] = "forbid"
    try:
        yield
    finally:
        if previous is None:
            config.pop("extra", None)
        else:
            config["extra"] = previous


def create_server(registry: DatasetRegistry, *, log_level: str = "WARNING") -> MCPServer:
    """An MCP server exposing the analytics tools over the datasets in ``registry``. Read-only."""
    server = MCPServer(
        SERVER_NAME,
        title="CauseLens analytics",
        instructions=INSTRUCTIONS,
        version=SERVER_VERSION,
        log_level=log_level,  # type: ignore[arg-type]
    )
    with _strict_argument_models():
        register_tools(server, registry)
    return server


def create_http_app(
    server: MCPServer,
    *,
    path: str = DEFAULT_PATH,
    host: str = "127.0.0.1",
    stateless: bool = False,
    json_response: bool = False,
    allowed_hosts: list[str] | None = None,
) -> Starlette:
    """The Streamable HTTP ASGI app (mount it, or serve it with uvicorn)."""
    security = None
    if allowed_hosts:
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(allowed_hosts),
            allowed_origins=[f"http://{h}" for h in allowed_hosts] + [f"https://{h}" for h in allowed_hosts],
        )
    return server.streamable_http_app(
        streamable_http_path=path, json_response=json_response, stateless_http=stateless, transport_security=security, host=host
    )


__all__ = ["DEFAULT_PATH", "INSTRUCTIONS", "SERVER_NAME", "SERVER_VERSION", "TOOL_NAMES", "create_http_app", "create_server"]
