#!/usr/bin/env bash
# Launch the CauseLens MCP server over stdio from a fresh checkout.
#
# Creates the ignored .venv and data/metrics_daily.csv when missing, then execs the
# server. stdout carries only MCP protocol messages, so every setup step writes to
# stderr. Committed files (data/events.csv, data/ground_truth.json) are never written.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"
METRICS="$ROOT/data/metrics_daily.csv"
EVENTS="$ROOT/data/events.csv"

log() { echo "[causelens-setup] $*" >&2; }

# All setup commands run with stdout redirected to stderr.
if ! "$PY" -c "import numpy, pandas, scipy, statsmodels, pydantic, mcp" >/dev/null 2>&1; then
  log "preparing virtualenv at $VENV"
  BASE_PY="${PYTHON:-python3}"
  [ -x "$PY" ] || "$BASE_PY" -m venv "$VENV" >&2
  # Runtime dependencies only: skip pytest, hypothesis and ruff.
  grep -Ev '^(pytest|hypothesis|ruff)\b' "$ROOT/requirements.txt" > "$VENV/runtime-requirements.txt"
  "$PY" -m pip install --quiet --disable-pip-version-check -r "$VENV/runtime-requirements.txt" >&2
fi

if [ ! -s "$METRICS" ]; then
  log "generating $METRICS (seed 42)"
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  # Generate into a scratch directory, then copy only the ignored metrics file.
  (cd "$ROOT" && PYTHONPATH="$ROOT" "$PY" -m causelens.sim.generate --seed 42 --out "$TMP" >&2)
  cp "$TMP/metrics_daily.csv" "$METRICS.tmp" && mv "$METRICS.tmp" "$METRICS"
  trap - EXIT; rm -rf "$TMP"
fi

cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$PY" -m causelens.mcp --transport stdio --dataset reference "$METRICS" "$EVENTS"
