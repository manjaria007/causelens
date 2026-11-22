"""Ground-truth evaluation (Task 9): full investigations on simulated seeds, graded against the answer key. See ``docs/evaluation.md``."""
from causelens.eval.grading import CRITERIA, Findings, Grade, extract, grade
from causelens.eval.runner import (
    EvalError,
    EvalReport,
    SeedOutcome,
    evaluate,
    run_seed,
    summarize,
)
from causelens.eval.stats import wilson_interval

__all__ = [
    "CRITERIA",
    "EvalError",
    "EvalReport",
    "Findings",
    "Grade",
    "SeedOutcome",
    "evaluate",
    "extract",
    "grade",
    "run_seed",
    "summarize",
    "wilson_interval",
]
