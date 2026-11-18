"""Configured datasets for the MCP server: an id resolves to local metrics (and optionally events) CSV files.

The registry is the only code in the server that touches the filesystem, and it only *reads* the configured metrics
and events files. A client never supplies a path, only an id. ``ground_truth.json`` is never read: a configuration
that names one is rejected when the server starts.
"""
import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import NamedTuple, Self

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from causelens.analytics.loaders import (
    DIMENSIONS,
    DataValidationError,
    load_events,
    load_metrics,
)

ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_FORBIDDEN_NAME = "ground_truth"


class DatasetConfigError(ValueError):
    """The server's dataset configuration is invalid (raised at startup, before any tool runs)."""


class DatasetError(ValueError):
    """A tool call named a dataset that is unknown, unreadable or missing something the tool needs."""


@dataclass(frozen=True)
class DatasetConfig:
    id: str
    metrics: Path
    events: Path | None = None
    description: str = ""


class LoadedDataset(NamedTuple):
    metrics: pd.DataFrame
    events: pd.DataFrame | None


class DatasetInfo(BaseModel):
    """What a client may know about a configured dataset (file names, never paths)."""

    model_config = ConfigDict(frozen=True)

    id: str
    description: str
    metrics_file: str
    events_file: str | None
    metrics_sha256: str = Field(description="Fingerprint of the metrics file, so a result can be tied to the data behind it.")
    events_sha256: str | None
    n_rows: int
    date_range: tuple[date, date]
    dimension_values: dict[str, list[str]]
    n_events: int | None


class DatasetCatalog(BaseModel):
    model_config = ConfigDict(frozen=True)

    datasets: list[DatasetInfo]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _check_path(dataset_id: str, role: str, path: Path) -> Path:
    if _FORBIDDEN_NAME in path.name.lower():
        raise DatasetConfigError(
            f"dataset {dataset_id!r}: the {role} file {path.name!r} looks like the answer key; the server never reads it"
        )
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise DatasetConfigError(f"dataset {dataset_id!r}: the {role} file {str(path)!r} does not exist or is not a file")
    return resolved


class DatasetRegistry:
    """Immutable mapping from dataset id to files. Loading is per call and read-only."""

    def __init__(self, configs: Iterable[DatasetConfig]):
        seen: dict[str, DatasetConfig] = {}
        for cfg in configs:
            if not ID_PATTERN.match(cfg.id):
                raise DatasetConfigError(
                    f"dataset id {cfg.id!r} is invalid: use letters, digits, '.', '_' or '-' (at most 64 characters)"
                )
            if cfg.id in seen:
                raise DatasetConfigError(f"dataset id {cfg.id!r} is configured twice")
            metrics = _check_path(cfg.id, "metrics", cfg.metrics)
            events = _check_path(cfg.id, "events", cfg.events) if cfg.events is not None else None
            seen[cfg.id] = DatasetConfig(cfg.id, metrics, events, cfg.description)
        if not seen:
            raise DatasetConfigError("no datasets are configured: pass --dataset ID METRICS_CSV [EVENTS_CSV] or --config FILE")
        self._configs = dict(sorted(seen.items()))

    # ---- construction from the command line / a config file ---------------------------------------------------
    @classmethod
    def from_sources(cls, specs: Sequence[Sequence[str]] = (), config_file: str | Path | None = None) -> Self:
        configs: list[DatasetConfig] = []
        if config_file is not None:
            configs.extend(_read_config_file(Path(config_file)))
        for spec in specs:
            if len(spec) not in (2, 3):
                raise DatasetConfigError("--dataset takes ID METRICS_CSV [EVENTS_CSV]")
            dataset_id, metrics, *rest = spec
            configs.append(DatasetConfig(dataset_id, Path(metrics), Path(rest[0]) if rest else None))
        return cls(configs)

    # ---- queries ----------------------------------------------------------------------------------------------
    @property
    def ids(self) -> list[str]:
        return list(self._configs)

    def config(self, dataset_id: str) -> DatasetConfig:
        try:
            return self._configs[dataset_id]
        except KeyError:
            raise DatasetError(f"unknown dataset_id {dataset_id!r}; configured datasets: {', '.join(self.ids)}") from None

    def load(self, dataset_id: str, *, events: str = "optional") -> LoadedDataset:
        """Read and validate the files. ``events`` is ``"required"`` or ``"optional"``."""
        cfg = self.config(dataset_id)
        if events == "required" and cfg.events is None:
            raise DatasetError(f"dataset {dataset_id!r} has no events file configured, and this tool needs the event log")
        for role, path in (("metrics", cfg.metrics), ("events", cfg.events)):
            if path is not None and not path.is_file():
                raise DatasetError(f"dataset {dataset_id!r}: the {role} file {path.name!r} could not be read (it is no longer there)")
        try:
            metrics = load_metrics(cfg.metrics)
            log = load_events(cfg.events) if cfg.events is not None else None
        except DataValidationError as exc:
            raise DatasetError(f"dataset {dataset_id!r} failed validation: {_without_paths(str(exc), cfg)}") from exc
        except OSError as exc:
            raise DatasetError(f"dataset {dataset_id!r} could not be read: {exc.strerror or 'I/O error'}") from exc
        return LoadedDataset(metrics, log)

    def catalog(self) -> DatasetCatalog:
        infos: list[DatasetInfo] = []
        for cfg in self._configs.values():
            data = self.load(cfg.id)
            days = pd.to_datetime(data.metrics["date"])
            infos.append(
                DatasetInfo(
                    id=cfg.id,
                    description=cfg.description,
                    metrics_file=cfg.metrics.name,
                    events_file=cfg.events.name if cfg.events is not None else None,
                    metrics_sha256=_sha256(cfg.metrics),
                    events_sha256=_sha256(cfg.events) if cfg.events is not None else None,
                    n_rows=len(data.metrics),
                    date_range=(days.min().date(), days.max().date()),
                    dimension_values={d: sorted(map(str, data.metrics[d].unique())) for d in DIMENSIONS},
                    n_events=None if data.events is None else len(data.events),
                )
            )
        return DatasetCatalog(datasets=infos)


def _without_paths(message: str, cfg: DatasetConfig) -> str:
    """A client sees file names, never the server's directory layout."""
    for path in (cfg.metrics, cfg.events):
        if path is not None:
            message = message.replace(str(path), path.name)
    return message


def _read_config_file(path: Path) -> list[DatasetConfig]:
    try:
        raw = json.loads(path.read_text())
    except OSError as exc:
        raise DatasetConfigError(f"cannot read the config file {str(path)!r}: {exc.strerror or exc}") from exc
    except json.JSONDecodeError as exc:
        raise DatasetConfigError(f"the config file {str(path)!r} is not valid JSON: {exc}") from exc
    items = raw.get("datasets") if isinstance(raw, dict) else None
    if not isinstance(items, list) or not items:
        raise DatasetConfigError(f'the config file {str(path)!r} must be {{"datasets": [{{"id": ..., "metrics": ...}}, ...]}}')
    base = path.resolve().parent
    out: list[DatasetConfig] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("metrics"), str):
            raise DatasetConfigError(f"each dataset in {str(path)!r} needs a string 'id' and 'metrics' (and optionally 'events', 'description')")
        unknown = set(item) - {"id", "metrics", "events", "description"}
        if unknown:
            raise DatasetConfigError(f"dataset {item['id']!r} in {str(path)!r} has unknown keys: {sorted(unknown)}")
        events = item.get("events")
        out.append(
            DatasetConfig(
                id=item["id"],
                metrics=base / item["metrics"],
                events=base / events if isinstance(events, str) else None,
                description=str(item.get("description", "")),
            )
        )
    return out
