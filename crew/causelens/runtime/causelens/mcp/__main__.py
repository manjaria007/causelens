"""Run the CauseLens MCP server.

    python -m causelens.mcp --dataset reference data/metrics_daily.csv data/events.csv            # Streamable HTTP
    python -m causelens.mcp --dataset reference data/metrics_daily.csv data/events.csv --transport stdio

With the stdio transport, stdout carries only the protocol. Everything else (logs, warnings, errors) goes to stderr.
"""
import argparse
import logging
import os
import sys
from collections.abc import Sequence

from causelens.mcp.datasets import DatasetConfigError, DatasetRegistry
from causelens.mcp.server import DEFAULT_PATH, create_http_app, create_server

CONFIG_ENV = "CAUSELENS_MCP_CONFIG"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m causelens.mcp", description="CauseLens analytics as a read-only MCP server.")
    ap.add_argument(
        "--dataset", nargs="+", action="append", default=[], metavar=("ID", "FILE"),
        help="A dataset: ID METRICS_CSV [EVENTS_CSV]. Repeat for several datasets.",
    )
    ap.add_argument("--config", help=f"JSON file {{'datasets': [...]}} (default: ${CONFIG_ENV}); paths are relative to the file.")
    ap.add_argument("--transport", choices=("streamable-http", "stdio"), default="streamable-http")
    ap.add_argument("--host", default="127.0.0.1", help="Bind address for HTTP (default 127.0.0.1).")
    ap.add_argument("--port", type=int, default=8000, help="Port for HTTP (default 8000).")
    ap.add_argument("--path", default=DEFAULT_PATH, help=f"URL path of the MCP endpoint (default {DEFAULT_PATH}).")
    ap.add_argument("--stateless", action="store_true", help="HTTP without server-side sessions.")
    ap.add_argument("--json-response", action="store_true", help="Answer HTTP POSTs with JSON instead of an event stream.")
    ap.add_argument("--allowed-host", action="append", default=[], metavar="HOST[:PORT]",
                    help="Extra Host header value to accept (needed when binding a non-loopback address). Repeatable.")
    ap.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="WARNING")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Logs go to stderr in every transport. Stdout is the protocol channel under stdio.
    logging.basicConfig(level=args.log_level, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    try:
        registry = DatasetRegistry.from_sources(args.dataset, args.config or os.environ.get(CONFIG_ENV))
    except DatasetConfigError as exc:
        print(f"causelens.mcp: {exc}", file=sys.stderr)
        return 2
    server = create_server(registry, log_level=args.log_level)
    if args.transport == "stdio":
        server.run("stdio")
        return 0
    import uvicorn

    app = create_http_app(
        server, path=args.path, host=args.host, stateless=args.stateless, json_response=args.json_response,
        allowed_hosts=args.allowed_host,
    )
    print(f"causelens.mcp: serving {', '.join(registry.ids)} at http://{args.host}:{args.port}{args.path}", file=sys.stderr)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level.lower())
    return 0


if __name__ == "__main__":
    sys.exit(main())
