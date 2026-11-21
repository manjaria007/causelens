"""Helpers for the agents tests: an in-process MCP server over files, and ready-made requests. Nothing is mocked.

``serve_target`` builds the production ``MCPServer`` (the same ``create_server`` the CLI uses) over a ``DatasetRegistry``.
The orchestrator reaches it through the official ``mcp.Client``, exactly as it would over HTTP or stdio.
"""
from __future__ import annotations

from pathlib import Path

from causelens.agents import (
    Investigation,
    InvestigationRequest,
    Window,
    investigate_sync,
)
from causelens.mcp import DatasetConfig, DatasetRegistry, create_server

PRE = Window(start="2026-08-01", end="2026-10-13")
POST = Window(start="2026-10-14", end="2026-10-31")
QUESTION = "Why did checkout conversion fall?"


def request(dataset_id: str = "reference", **kwargs) -> InvestigationRequest:
    return InvestigationRequest(question=QUESTION, dataset_id=dataset_id, pre_window=kwargs.pop("pre", PRE), post_window=kwargs.pop("post", POST), **kwargs)


def server_over(metrics: Path, events: Path | None, dataset_id: str = "reference"):
    return create_server(DatasetRegistry([DatasetConfig(dataset_id, metrics, events)]))


def run(metrics: Path, events: Path | None, **kwargs) -> Investigation:
    return investigate_sync(request(**kwargs), server_over(metrics, events))


def write_frames(directory: Path, metrics, events) -> tuple[Path, Path]:
    metrics.to_csv(directory / "metrics_daily.csv", index=False)
    events.to_csv(directory / "events.csv", index=False)
    return directory / "metrics_daily.csv", directory / "events.csv"
