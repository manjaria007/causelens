"""Plain-text rendering of an ``EvalReport``. Reads the report; computes nothing."""
from __future__ import annotations

from causelens.eval.runner import MIN_SEEDS, EvalReport


def render(report: EvalReport) -> str:
    n = len(report.seeds)
    lines = [
        f"CauseLens ground-truth evaluation: {n} seeds ({report.seeds[0]} to {report.seeds[-1]})",
        "",
        f"{'criterion':<22}{'passes':>8}{'rate':>8}   95% Wilson interval   failed seeds",
    ]
    for r in [*report.rates, report.all_criteria]:
        failed = ", ".join(str(s) for s in r.failed_seeds) or "none"
        lines.append(f"{r.name:<22}{f'{r.passes}/{r.n}':>8}{r.rate:>8.0%}   [{r.wilson_lower:>5.1%}, {r.wilson_upper:>5.1%}]       {failed}")
    c = report.coverage_given_estimate
    if c is not None:
        lines.append(
            f"Interval coverage among runs that produced one: {c.passes}/{c.n} ({c.rate:.0%}, Wilson [{c.wilson_lower:.1%}, {c.wilson_upper:.1%}]); "
            f"the criterion above counts a run with no interval as a failure"
        )
    lines += ["", "What each criterion requires:"]
    lines += [f"  {r.name}: {r.description}" for r in report.rates]
    lines += [
        "",
        f"Wrong event supported (incorrect attribution): {len(report.wrong_event_supported_seeds)}/{n}"
        + (f" (seeds {', '.join(map(str, report.wrong_event_supported_seeds))})" if report.wrong_event_supported_seeds else ""),
        "Investigation status: " + ", ".join(f"{k} {v}" for k, v in report.status_counts.items()),
        "Evidence strength:    " + ", ".join(f"{k} {v}" for k, v in report.strength_counts.items()),
    ]
    if report.reference_seed is not None:
        verdict = "PASS" if report.reference_passed else "FAIL"
        lines.append(f"Reference seed {report.reference_seed} (graded against data/ground_truth.json): {verdict}")
        if not report.reference_passed:
            ref = next(o for o in report.outcomes if o.seed == report.reference_seed)
            lines += [f"  {c.name}: {c.detail}" for c in ref.criteria if not c.passed]
    else:
        lines.append("The reference seed was not run, so there is no pass/fail verdict on the reference dataset.")
    if report.below_minimum:
        lines.append(f"NOTE: fewer than {MIN_SEEDS} seeds were run; Requirement 10.3 asks for at least {MIN_SEEDS}.")
    failing = [o for o in report.outcomes if not o.passed]
    if failing:
        lines += ["", "Seeds that did not pass every criterion:"]
        for o in failing:
            why = "; ".join(f"{c.name} ({c.detail})" for c in o.criteria if not c.passed)
            lines.append(f"  seed {o.seed}: {why}")
    return "\n".join(lines)
