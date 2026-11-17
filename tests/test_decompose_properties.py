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
import math

import numpy as np
import pandas as pd
import pytest
from hypothesis import assume, given, settings
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


def absent_zero_rows(df: pd.DataFrame, share: float = 0.5, seed: int = 1) -> pd.DataFrame:
    """Zero-session rows for (date, segment) keys that do NOT occur in ``df``.

    Keys come from the cartesian product of the segments and dates the frame already contains, minus the
    keys it has, so every returned row is new. (An earlier version sampled rows from the frame itself and
    then removed the ones already present, which left nothing, so the property compared a frame with itself.)
    """
    key = ["date", *DIMENSIONS]
    combos = df[list(DIMENSIONS)].drop_duplicates()
    every = combos.merge(pd.DataFrame({"date": sorted(df["date"].unique())}), how="cross")[key]
    absent = every[~every.set_index(key).index.isin(df.set_index(key).index)]
    chosen = absent.sample(frac=share, random_state=seed) if len(absent) else absent
    return chosen.assign(sessions=0, orders=0)[[*key, "sessions", "orders"]]


@settings(max_examples=30, **COMMON)
@given(df=frames)
def test_zero_session_rows_are_equivalent_to_absent_rows(df):
    padding = absent_zero_rows(df)
    assume(len(padding) > 0)
    padded = pd.concat([df, padding], ignore_index=True)
    assert len(padded) == len(df) + len(padding) > len(df)  # rows really were added
    assert not padded.duplicated(subset=["date", *DIMENSIONS]).any()  # ... under keys the frame lacked
    assert (padding["sessions"] == 0).all() and (padding["orders"] == 0).all()
    a = run(df).model_dump(exclude={"exclusions", "quality"})
    b = run(padded).model_dump(exclude={"exclusions", "quality"})
    assert a == b


def test_the_padding_helper_adds_new_rows_on_a_known_frame():
    """Guards the property above against silently comparing a frame with itself."""
    df = random_frame(3, (3, 2, 2, 2), 0.6, 300, 0.05, 1.0)
    padding = absent_zero_rows(df)
    assert len(padding) > 10
    padded = pd.concat([df, padding], ignore_index=True)
    assert len(padded) > len(df) and not padded.duplicated(subset=["date", *DIMENSIONS]).any()
    assert run(df).model_dump(exclude={"exclusions", "quality"}) == run(padded).model_dump(exclude={"exclusions", "quality"})


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
        def canon_set(chosen):
            if chosen is None:
                return None
            out = chosen.model_dump(exclude={"segments", "note"})
            out["segments"] = sorted(_map_label(lab, mapping) if mapping else lab for lab in chosen.segments)
            return out

        parts[p.name] = (
            p.model_dump(exclude={"segments", "explaining_set", "local_set"}),
            segs,
            canon_set(p.explaining_set),
            canon_set(p.local_set),
        )
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


# ------------------------------------------------------------------ localization search and test reliability


@settings(max_examples=40, **COMMON)
@given(
    df=frames,
    threshold=st.sampled_from([0.5, 0.8, 1.0]),
    limit=st.sampled_from([0.2, 0.35, 0.5, 0.8]),
)
def test_local_set_matches_a_brute_force_search_over_all_combinations(df, threshold, limit):
    """Soundness, completeness and optimality: whenever any combination of positively contributing segments
    reaches the target within the traffic limit, a local set is reported, and it is the best by the stated
    order (all significant, fewest segments, least traffic, largest contribution)."""
    r = run(df, explain_threshold=threshold, max_localized_traffic_share=limit)
    assume(abs(r.change_pp) > 1e-9)
    sign = 1.0 if r.change_pp > 0 else -1.0
    target = threshold * abs(r.change_pp)
    for p in r.partitions:
        groups: dict[tuple, list] = {}
        for s in p.segments:
            if sign * s.contribution_pp > 0:
                groups.setdefault((s.sessions_pre, s.sessions_post, s.orders_pre, s.orders_post), []).append(s)
        atoms = list(groups.values())
        assert len(atoms) <= 12 and p.local_search_exhaustive
        best = None
        for k in range(1, len(atoms) + 1):
            for combo in itertools.combinations(atoms, k):
                chosen = [s for atom in combo for s in atom]
                contribution = math.fsum(sign * s.contribution_pp for s in chosen)
                traffic = math.fsum(s.share_post for s in chosen)
                if contribution >= target * (1 - 1e-12) and traffic <= limit * (1 + 1e-12):
                    unsure = any(not (s.test is not None and s.test.significant) for s in chosen)
                    key = (unsure, len(chosen), traffic, -contribution)
                    best = key if best is None or key < best else best
        if best is None:
            assert p.local_set is None and p.local_set_unavailable_reason
            continue
        ls = p.local_set
        assert ls is not None and ls.kind == "traffic_limited_search"
        assert (not ls.all_selected_significant, ls.k) == (best[0], best[1])
        assert ls.traffic_share_post == pytest.approx(best[2], abs=1e-9)
        assert ls.traffic_share_post <= limit + 1e-9
        assert sign * ls.selected_share_of_change * r.change_pp >= target - 1e-9
    # a localization exists exactly when some partition has a local set, and it comes from one
    has_any = any(p.local_set is not None for p in r.partitions)
    assert (r.localization is not None) == has_any
    if r.localization is not None:
        owner = next(p for p in r.partitions if p.name == r.localization.partition)
        assert r.localization.segments == owner.local_set.segments


@settings(max_examples=40, **COMMON)
@given(df=frames)
def test_unreliable_segments_never_get_a_p_value_q_value_or_significance(df):
    r = run(df)
    n_reliable = 0
    for p in r.partitions:
        assert p.n_tests + p.n_unreliable == sum(1 for s in p.segments if s.status == "both")
        for s in p.segments:
            if s.test is None:
                assert s.status != "both"
                continue
            c0, n0, c1, n1 = s.orders_pre, s.sessions_pre, s.orders_post, s.sessions_post
            pooled = (c0 + c1) / (n0 + n1)
            expected_min = min(n0 * pooled, n0 * (1 - pooled), n1 * pooled, n1 * (1 - pooled))
            assert s.test.reliable == (expected_min >= 5.0)
            if s.test.reliable:
                n_reliable += 1
                assert s.test.p_value is not None and s.test.q_value is not None and s.test.basis != "none"
                assert 0 <= s.test.p_value <= 1 and s.test.q_value >= s.test.p_value - 1e-12
                assert s.test.significant == (s.test.q_value < 0.05)
            else:
                assert s.test.p_value is None and s.test.q_value is None and s.test.q_value_scan is None
                assert s.test.significant is False and s.test.basis == "none" and s.test.p_value_exact is not None
    assert r.n_tests_scanned == n_reliable


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
# Measured over simulator seeds 1-30 when these thresholds were set (day-level inference):
#   * no-effect comparisons: 1 of 30 datasets had any discovery (nominal expectation 1.5), 4 segments in it;
#   * planted comparison: the planted segment was significant and localized in 30 of 30, and the mean false
#     discovery proportion was 0.026 (12 of 30 datasets contained at least one false discovery, which FDR
#     control allows: it bounds the expected proportion, not the chance of any false hit).

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
        assert pair.segments[0].test.q_value < 0.05 and pair.segments[0].test.change_pp.upper < 0, seed
        discoveries = [s for p in r.partitions for s in p.segments if s.test is not None and s.test.significant]
        false = [s for s in discoveries if contains_no_planted_traffic(s.labels)]
        proportions.append(len(false) / max(len(discoveries), 1))
    assert float(np.mean(proportions)) <= 0.05, f"mean false discovery proportion {np.mean(proportions):.3f}"


# ------------------------------------------------------------------ calibration through the whole pipeline


def null_frame(seed: int, days: int, n_segments: int = 8, day_noise: float = 0.15, segment_noise: float = 0.05):
    """Eight disjoint segments with constant true rates; conversion varies by day (a shared day effect plus a
    per-segment one), so sessions are not independent trials. Every segment's true change is zero."""
    rng = np.random.default_rng(seed)
    mult = rng.uniform(0.6, 1.5, n_segments)
    weight = rng.uniform(0.5, 1.5, n_segments)
    rows = []
    for d in range(2 * days):
        date = (pd.Timestamp("2026-08-01") + pd.Timedelta(days=d)).strftime("%Y-%m-%d")
        common = 1 + rng.normal(0, day_noise)
        for k in range(n_segments):
            sessions = int(rng.poisson(4000 * weight[k]))
            rate = min(0.03 * mult[k] * max(common * (1 + rng.normal(0, segment_noise)), 0.2), 0.9)
            rows.append(
                {"date": date, "device": f"d{k}", "browser": "b1", "channel": "c1", "customer_type": "t1",
                 "sessions": sessions, "orders": int(rng.binomial(sessions, rate))}
            )
    return pd.DataFrame(rows)[["date", *DIMENSIONS, "sessions", "orders"]]


def null_p_values(days: int, inference: str, n_frames: int, seed0: int = 0) -> np.ndarray:
    ps = []
    for seed in range(seed0, seed0 + n_frames):
        df = null_frame(seed, days)
        end_pre = f"2026-08-{days:02d}"
        r = decompose_change(
            df, ("2026-08-01", end_pre), (f"2026-08-{days + 1:02d}", f"2026-08-{2 * days:02d}"),
            dimensions=["device"], include_pairs=False, inference=inference, min_days_per_window=days,
            apply_exclusions=False, quality=no_flags(df),
        )
        ps += [s.test.p_value for s in r.partitions[0].segments if s.test is not None and s.test.reliable]
    return np.array(ps)


def test_the_default_test_is_calibrated_through_the_pipeline_with_seven_days_per_window():
    """Previously an estimated quasi-binomial dispersion was trusted as known and rejected 7.2% of true nulls at a
    nominal 5% with seven days per window. Measured over 500 frames (4,000 tests, correlated within frames): the
    day-level test rejected 5.8% at 7 days and 3.9% at 14 days, the plain binomial test 34% and 32%."""
    day_level = null_p_values(7, "day_level", n_frames=100)
    plain = null_p_values(7, "binomial", n_frames=100)
    assert float((day_level < 0.05).mean()) <= 0.085
    assert float((day_level < 0.05).mean()) >= 0.02
    assert float((plain < 0.05).mean()) > 0.20  # negative control: the plain test is badly liberal here
