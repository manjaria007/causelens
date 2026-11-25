"""A read-only MCP adapter over ``causelens.analytics`` (Task 7). See ``docs/mcp-server.md``."""
from causelens.mcp.datasets import (
    DatasetConfig,
    DatasetConfigError,
    DatasetError,
    DatasetRegistry,
)
from causelens.mcp.server import create_http_app, create_server

__all__ = [
    "DatasetConfig",
    "DatasetConfigError",
    "DatasetError",
    "DatasetRegistry",
    "create_http_app",
    "create_server",
]
