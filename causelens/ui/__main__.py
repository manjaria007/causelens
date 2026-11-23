"""``python -m causelens.ui``: investigate a dataset and write the console as one self-contained HTML file.

    python -m causelens.sim.generate            # once: writes data/metrics_daily.csv and data/events.csv
    python -m causelens.ui --out console.html   # runs the investigation in process and writes the page

By default the investigation runs against the data files through an in-process MCP server and the template reasoner, offline.
``--server URL`` uses an already running Streamable HTTP server instead (the dataset id must be configured there). No hosted model is
called. Exit status: 0 when the page was written (an investigation that stopped is still shown, with its reason), 2 for bad
arguments or missing data.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from causelens.agents import McpToolClient, investigate
from causelens.agents.state import InvestigationRequest, Window
from causelens.mcp import DatasetConfig, DatasetRegistry, create_server
from causelens.ui.render import render_html
from causelens.ui.view import build_view

ROOT = Path(__file__).resolve().parents[2]
QUESTION = "Why did checkout conversion fall?"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m causelens.ui", description="Write the CauseLens investigation console as an HTML file.")
    ap.add_argument("--metrics", type=Path, default=ROOT / "data" / "metrics_daily.csv", help="Daily metrics CSV (default data/metrics_daily.csv).")
    ap.add_argument("--events", type=Path, default=ROOT / "data" / "events.csv", help="Events CSV (default data/events.csv).")
    ap.add_argument("--no-events", action="store_true", help="Investigate without an event log.")
    ap.add_argument("--dataset-id", default="reference", help="Dataset id (default reference).")
    ap.add_argument("--pre", nargs=2, metavar=("START", "END"), default=["2026-08-01", "2026-10-13"], help="Baseline window, ISO dates (inclusive).")
    ap.add_argument("--post", nargs=2, metavar=("START", "END"), default=["2026-10-14", "2026-10-31"], help="Comparison window, ISO dates (inclusive).")
    ap.add_argument("--question", default=QUESTION, help="The question, echoed at the top of the page.")
    ap.add_argument("--server", metavar="URL", help="Use a running Streamable HTTP MCP server instead of an in-process one.")
    ap.add_argument("--out", type=Path, default=Path("console.html"), help="Where to write the page (default console.html).")
    return ap


def _fail(message: str) -> int:
    print(f"causelens.ui: {message}", file=sys.stderr)
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        request = InvestigationRequest(
            question=args.question, dataset_id=args.dataset_id, pre_window=Window(start=args.pre[0], end=args.pre[1]),
            post_window=Window(start=args.post[0], end=args.post[1]),
        )
    except ValidationError as exc:
        return _fail(f"invalid window or argument: {exc.errors()[0]['msg']}")
    if args.server:
        target: object = args.server
    else:
        events = None if args.no_events else args.events
        for path in (args.metrics, events):
            if path is not None and not path.is_file():
                return _fail(f"{path} not found; generate the reference data with 'python -m causelens.sim.generate' or pass --metrics/--events")
        try:
            target = create_server(DatasetRegistry([DatasetConfig(args.dataset_id, args.metrics, events)]))
        except Exception as exc:  # noqa: BLE001 - a bad dataset configuration is a usage error, reported in one line
            return _fail(str(exc))

    async def go():
        async with McpToolClient(target) as client:
            return await investigate(request, client)

    run = asyncio.run(go())
    page = render_html(build_view(run))
    args.out.write_text(page, encoding="utf-8")
    print(f"causelens.ui: wrote {args.out} (status: {run.result.status}, evidence strength: {run.result.evidence.strength})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
