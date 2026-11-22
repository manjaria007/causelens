"""``python -m causelens.eval --seeds 20``: run full investigations on simulated seeds and grade them against the answer key.

Exit status: 0 when the reference seed (42) passes every criterion, 1 when it does not, 2 for bad arguments or an untrustworthy
answer key. With seeds that do not include the reference there is no verdict (exit 0 after printing a note). The per-criterion rates
over all seeds are reported, not gated: the requirement asks to report how often each criterion passes.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from causelens.eval.report import render
from causelens.eval.runner import (
    MIN_SEEDS,
    REFERENCE_SEED,
    REFERENCE_TRUTH,
    EvalError,
    evaluate,
)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m causelens.eval", description="Grade full investigations against the simulator's answer key.")
    ap.add_argument("--seeds", type=int, default=MIN_SEEDS, help=f"How many seeds to run (default and minimum {MIN_SEEDS}).")
    ap.add_argument("--first-seed", type=int, default=REFERENCE_SEED, help=f"The first seed; seeds are consecutive (default {REFERENCE_SEED}, the reference).")
    ap.add_argument("--allow-fewer-seeds", action="store_true", help=f"Allow fewer than {MIN_SEEDS} seeds (development only; the report says so).")
    ap.add_argument("--truth", type=Path, default=REFERENCE_TRUTH, help="The reference answer key (default data/ground_truth.json).")
    ap.add_argument("--json", type=Path, metavar="FILE", help="Also write the full report as JSON.")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.seeds < 1:
        print("causelens.eval: --seeds must be at least 1", file=sys.stderr)
        return 2
    if args.seeds < MIN_SEEDS and not args.allow_fewer_seeds:
        print(f"causelens.eval: Requirement 10.3 asks for at least {MIN_SEEDS} seeds; pass --allow-fewer-seeds for a development run", file=sys.stderr)
        return 2
    seeds = [args.first_seed + i for i in range(args.seeds)]
    try:
        report = evaluate(seeds, args.truth)
    except EvalError as exc:
        print(f"causelens.eval: {exc}", file=sys.stderr)
        return 2
    print(render(report))
    if args.json:
        args.json.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return 1 if report.reference_passed is False else 0


if __name__ == "__main__":
    sys.exit(main())
