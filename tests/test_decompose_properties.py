"""Property tests for ``decompose_change`` (Task 4.3; design.md "Correctness properties" 1 and 5).

* **Closure**: for every partition, rate effects + mix effects + residual equal the overall change to
  within 1e-9 pp, on arbitrary data including segments that appear or vanish between windows.
* **Row-order invariance**: shuffling the input rows changes nothing, bit for bit.
* **Consistent relabeling**: renaming category values by any bijection (including one that reverses
  their sort order) gives the same numbers under the new names.

Random frames are built from a seed and bounded parameters, so Hypothesis explores sparse, lopsided and
tiny data. Statistical tests (FDR and power on the simulator) use fixed seeds and state their rates.
"""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from causelens.analytics import DIMENSIONS, check_data_quality, decompose_change
from causelens.analytics.data_quality import DataQualityReport, DetectorParams
from causelens.sim.generate import generate

N_DAYS = 16
PRE = ("2026-08-01", "2026-08-08")  # days 0-7
POST = ("2026-08-09", "2026-08-16")  # days 8-15
COMMON = {"deadline": None, "derandomize": True}


def random_frame(
    seed: int,
    n_values: tuple[int, int, int, int],
    presence: float,
    max_sessions: int,
    base_rate: float,
    post_factor: float,
) -> pd.DataFrame:
    """Sparse random metrics. Segments may be absent in one window; some have zero-session days;
    every third segment's conversion is scaled by ``post_factor`` after the split."""
    rng = np.random.default_rng(seed)
    values = [[f"{dim[0]}{i}" for i in range(n)] for dim, n in zip(DIMENSIONS, n_values, strict=True)]
    combos = list(itertools.product(*values))
    mult = rng.uniform(0.4, 1.8, len(combos))
    weight = rng.uniform(0.2, 1.0, len(combos))
    in_pre = rng.random(len(combos)) < presence
    in_post = rng.random(len(combos)) < presence
    in_pre[0] = in_post[0] = True  # both windows always have traffic
    rows = []
    for d in range(N_DAYS):
        post = d >= N_DAYS // 2
        date = (pd.Timestamp("2026-08-01") + pd.Timedelta(days=d)).strftime("%Y-%m-%d")
        for k, combo in enumerate(combos):
            if not (in_post[k] if post else in_pre[k]):
                continue
            sessions = int(rng.integers(0, int(max_sessions * weight[k]) + 1))
            if k == 0:
                sessions = max(sessions, 5)
            rate = min(base_rate * mult[k] * (post_factor if post and k % 3 == 0 else 1.0), 0.95)
            rows.append(
                dict(zip(DIMENSIONS, combo, strict=True))
                | {"date": date, "sessions": sessions, "orders": int(rng.binomial(sessions, rate))}
            )
    return pd.DataFrame(rows)[["date", *DIMENSIONS, "sessions", "orders"]]


frames = st.builds(
    random_frame,
    seed=st.integers(0, 2**31 - 1),
    n_values=st.tuples(st.integers(1, 3), st.integers(1, 3), st.integers(1, 2), st.integers(1, 2)),
    presence=st.floats(0.5, 1.0),
    max_sessions=st.integers(20, 2000),
    base_rate=st.floats(0.01, 0.3),
    post_factor=st.floats(0.5, 1.5),
)


def no_flags(df: pd.DataFrame) -> DataQualityReport:
    """A Task 2 report with nothing flagged, passed through the public ``quality=`` argument.

    Used where a property has nothing to do with exclusions (the data-quality scan is the slow part
    of a call); tests that exercise exclusions run the real ``check_data_quality``."""
    days = sorted(set(df["date"]))
    return DataQualityReport(
        params=DetectorParams(),
        n_rows=len(df),
        date_range=(pd.Timestamp(days[0]).date(), pd.Timestamp(days[-1]).date()),
        n_days_checked=len(days),
        missing_dates=[],
        flags=[],
        excluded_dates=[],
        truncated_baseline_dates=[],
        exclusion_statement="No days were flagged, so no days are excluded from downstream estimates.",
    )


def run(df: pd.DataFrame, **kwargs):
    kwargs.setdefault("min_days_per_window", 1)
    kwargs.setdefault("apply_exclusions", False)
    if kwargs["apply_exclusions"] is False:
        kwargs.setdefault("quality", no_flags(df))
    return decompose_change(df, PRE, POST, **kwargs)


def independent_change_pp(df: pd.DataFrame) -> float:
    pre = df[df["date"] <= "2026-08-08"]
    post = df[df["date"] >= "2026-08-09"]
    return 100 * (post["orders"].sum() / post["sessions"].sum() - pre["orders"].sum() / pre["sessions"].sum())


# ------------------------------------------------------------------ closure


@settings(max_examples=100, **COMMON)
@given(df=frames)
def test_decomposition_closes_for_every_partition(df):
    r = run(df)
    assert r.status == "ok" and len(r.partitions) == 10 and len({p.name for p in r.partitions}) == 10
    assert r.change_pp == pytest.approx(independent_change_pp(df), abs=1e-12)
    for p in r.partitions:
        total = sum(s.rate_effect_pp for s in p.segments) + sum(s.mix_effect_pp for s in p.segments)
        assert abs(r.change_pp - total) <= 1e-9
        assert abs(p.residual_pp) <= 1e-9 and p.reconciles
        assert abs(p.sum_rate_effects_pp + p.sum_mix_effects_pp + p.residual_pp - r.change_pp) <= 1e-9
        assert abs(sum(s.contribution_pp for s in p.segments) - r.change_pp) <= 1e-9
        assert sum(s.share_pre for s in p.segments) == pytest.approx(1.0, abs=1e-12)
        assert sum(s.share_post for s in p.segments) == pytest.approx(1.0, abs=1e-12)
        assert sum(s.sessions_pre for s in p.segments) == r.pre.sessions
        assert sum(s.orders_post for s in p.segments) == r.post.orders
        for s in p.segments:  # per-segment structure
            assert s.contribution_pp == pytest.approx(s.rate_effect_pp + s.mix_effect_pp, abs=1e-15)
            if s.status == "both":
                assert s.rate_effect_pp * s.rate_change_pp >= 0  # weight is positive
                if s.share_post == s.share_pre:
                    assert s.mix_effect_pp == 0
            else:
                assert s.rate_effect_pp == 0 and s.test is None
    # the partitions overlap: every one accounts for the whole change, so summing across them would
    # count each session ten times and must never be done
    grand = sum(s.contribution_pp for p in r.partitions for s in p.segments)
    assert grand == pytest.approx(10 * r.change_pp, abs=1e-8)


@settings(max_examples=30, **COMMON)
@given(df=frames, k=st.integers(2, 7))
def test_scaling_all_counts_leaves_every_effect_unchanged(df, k):
    base, scaled = run(df), run(df.assign(sessions=df["sessions"] * k, orders=df["orders"] * k))
    assert scaled.change_pp == pytest.approx(base.change_pp, abs=1e-12)
    for a, b in zip(base.partitions, scaled.partitions, strict=True):
        for x, y in zip(a.segments, b.segments, strict=True):
            assert x.label == y.label and x.rank == y.rank
            assert y.rate_effect_pp == pytest.approx(x.rate_effect_pp, abs=1e-12)
            assert y.mix_effect_pp == pytest.approx(x.mix_effect_pp, abs=1e-12)


@settings(max_examples=30, **COMMON)
@given(df=frames)
def test_zero_session_rows_are_equivalent_to_absent_rows(df):
    padding = df.assign(sessions=0, orders=0).sample(frac=0.3, random_state=1)
    padding = padding.drop_duplicates(subset=["date", *DIMENSIONS])
    key = ["date", *DIMENSIONS]
    padded = pd.concat([df, padding[~padding.set_index(key).index.isin(df.set_index(key).index)]], ignore_index=True)
    a = run(df).model_dump(exclude={"exclusions", "quality"})
    b = run(padded).model_dump(exclude={"exclusions", "quality"})
    assert a == b


# ------------------------------------------------------------------ row-order invariance


@settings(max_examples=40, **COMMON)
@given(df=frames, shuffle_seed=st.integers(0, 10_000), exclude=st.booleans())
def test_row_order_does_not_change_any_result(df, shuffle_seed, exclude):
    shuffled = df.sample(frac=1.0, random_state=shuffle_seed).reset_index(drop=True)
    kwargs = {"apply_exclusions": exclude}
    assert run(df, **kwargs).model_dump() == run(shuffled, **kwargs).model_dump()


# ------------------------------------------------------------------ consistent relabeling


def relabel_mapping(df: pd.DataFrame, flip: bool) -> dict[str, dict[str, str]]:
    """A bijection per dimension; with ``flip`` the new names sort in the reverse order."""
    mapping = {}
    for dim in DIMENSIONS:
        old = sorted(df[dim].unique())
        new = [f"zz_{dim}_{(len(old) - 1 - i if flip else i):02d}" for i in range(len(old))]
        mapping[dim] = dict(zip(old, new, strict=True))
    return mapping


def _map_label(label: str, mapping: dict[str, dict[str, str]]) -> str:
    parts = []
    for term in label.split(", "):
        dim, value = term.split("=", 1)
        parts.append(f"{dim}={mapping[dim][value]}")
    return ", ".join(parts)


def canonical(result, mapping=None):
    """Everything numeric in a result, keyed by (partition, segment labels), with labels renamed."""

    def rename(dim: str, value: str) -> str:
        return mapping[dim][value] if mapping else value

    top = result.model_dump(exclude={"partitions", "localization", "localization_note", "summary", "assumptions", "exclusions"})
    parts = {}
    for p in result.partitions:
        segs = {
            tuple((d, rename(d, s.labels[d])) for d in p.dimensions): s.model_dump(exclude={"labels", "label"})
            for s in p.segments
        }
        es = None
        if p.explaining_set is not None:
            es = p.explaining_set.model_dump(exclude={"segments", "note"})
            es["segments"] = sorted(_map_label(lab, mapping) if mapping else lab for lab in p.explaining_set.segments)
        parts[p.name] = (p.model_dump(exclude={"segments", "explaining_set"}), segs, es)
    loc = None
    if result.localization is not None:
        loc = result.localization.model_dump(exclude={"segments", "statement"})
        loc["segments"] = sorted(
            _map_label(lab, mapping) if mapping else lab for lab in result.localization.segments
        )
    return top, parts, loc, result.localization_note is None


@settings(max_examples=40, **COMMON)
@given(df=frames, flip=st.booleans(), exclude=st.booleans())
def test_consistent_relabeling_gives_the_same_numbers_under_the_new_names(df, flip, exclude):
    mapping = relabel_mapping(df, flip)
    renamed = df.copy()
    for dim in DIMENSIONS:
        renamed[dim] = df[dim].map(mapping[dim])
    original = run(df, apply_exclusions=exclude)
    relabelled = run(renamed, apply_exclusions=exclude)
    assert canonical(original, mapping) == canonical(relabelled)


def test_relabeling_really_changes_the_order_it_is_meant_to_test():
    df = random_frame(7, (3, 2, 3, 2), 0.9, 500, 0.05, 1.0)
    mapping = relabel_mapping(df, flip=True)
    first = sorted(df["device"].unique())
    assert sorted(mapping["device"][v] for v in first) == [mapping["device"][v] for v in reversed(first)]


# ------------------------------------------------------------------ partitions are independent of each other


@settings(max_examples=10, **COMMON)
@given(df=frames)
def test_dimension_subset_gives_the_same_numbers_for_the_partitions_it_shares(df):
    full = {p.name: p for p in run(df).partitions}
    sub = run(df, dimensions=["device", "browser"])
    assert [p.name for p in sub.partitions] == ["device", "browser", "device × browser"]
    for p in sub.partitions:
        ref = full[p.name]
        assert [(s.label, s.contribution_pp, s.rank) for s in p.segments] == [
            (s.label, s.contribution_pp, s.rank) for s in ref.segments
        ]


# ------------------------------------------------------------------ statistical tests (fixed seeds)
# Measured over simulator seeds 1-30 when these thresholds were set:
#   * no-effect comparisons: 0 of 30 datasets had any discovery with the dispersion adjustment
#     (2 false discoveries in one dataset with the plain binomial test);
#   * planted comparison: the planted segment was significant and localized in 30 of 30, and the mean
#     false discovery proportion was 0.020 (7 of 30 datasets contained at least one false discovery,
#     which FDR control allows: it bounds the expected proportion, not the chance of any false hit).

SEEDS = range(1, 11)
NULL_PRE = ("2026-08-03", "2026-09-13")  # whole weeks, before any planted effect
NULL_POST = ("2026-09-21", "2026-10-11")
TRUE_PRE = ("2026-08-01", "2026-10-13")
TRUE_POST = ("2026-10-14", "2026-10-31")


@pytest.fixture(scope="module")
def simulated():
    out = {}
    for seed in SEEDS:
        metrics, events, _ = generate(seed)
        out[seed] = (metrics, events, check_data_quality(metrics, events))
    return out


def test_no_effect_comparisons_rarely_produce_any_discovery(simulated):
    """Under a global null Benjamini-Hochberg keeps P(any discovery) <= alpha; across 10 datasets at most
    one may contain a segment with q < 0.05 (nominal expectation 0.5)."""
    with_discovery = 0
    for metrics, events, quality in simulated.values():
        r = decompose_change(metrics, NULL_PRE, NULL_POST, events, quality=quality)
        assert r.status == "ok" and all(p.reconciles for p in r.partitions)
        with_discovery += any(s.test.significant for p in r.partitions for s in p.segments if s.test)
    assert with_discovery <= 1, f"{with_discovery}/{len(SEEDS)} null comparisons had a discovery"


def test_the_planted_segment_is_found_and_the_false_discovery_proportion_is_controlled(simulated, ground_truth):
    """Evaluation-only use of ground truth: it labels which segments contain no planted traffic."""
    planted = ground_truth["planted_cause"]["affected_segment"]
    label = ", ".join(f"{k}={v}" for k, v in planted.items())

    def contains_no_planted_traffic(labels: dict[str, str]) -> bool:
        return any(d in labels and labels[d] != v for d, v in planted.items())

    proportions = []
    for seed, (metrics, events, quality) in simulated.items():
        r = decompose_change(metrics, TRUE_PRE, TRUE_POST, events, quality=quality)
        assert r.localization is not None, seed
        assert r.localization.segments == [label] and r.localization.all_selected_significant, seed
        pair = next(p for p in r.partitions if p.name == "device × browser")
        assert pair.segments[0].label == label and pair.segments[0].rank == 1, seed
        assert pair.segments[0].test.q_value < 0.05 and pair.segments[0].test.two_proportion.change_pp.upper < 0, seed
        discoveries = [s for p in r.partitions for s in p.segments if s.test is not None and s.test.significant]
        false = [s for s in discoveries if contains_no_planted_traffic(s.labels)]
        proportions.append(len(false) / max(len(discoveries), 1))
    assert float(np.mean(proportions)) <= 0.05, f"mean false discovery proportion {np.mean(proportions):.3f}"
