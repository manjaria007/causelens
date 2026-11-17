"""Statistics behind ``decompose_change``: Benjamini-Hochberg and the two-proportion test.

``statsmodels`` is used only as an independent reference here, never inside the analytics.
"""
from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.proportion import confint_proportions_2indep, proportions_ztest

from causelens.analytics.decompose import (
    _day_level_core,
    benjamini_hochberg,
    day_level_test,
    fisher_exact_p,
    two_proportion_test,
)

# ------------------------------------------------------------------ Benjamini-Hochberg


def test_bh_matches_a_hand_computed_example():
    # p ascending: .001 .01 .03 .04 .5 ; m=5 -> scaled .005 .025 .05 .05 .5 ; cumulative min from the top
    q = benjamini_hochberg([0.04, 0.001, 0.5, 0.03, 0.01])
    assert q == pytest.approx([0.05, 0.005, 0.5, 0.05, 0.025])


def test_bh_equal_p_values_get_equal_q_values_and_q_is_capped_at_one():
    q = benjamini_hochberg([0.2, 0.2, 0.2, 0.9, 1.0])
    assert q[0] == q[1] == q[2] and max(q) <= 1.0 and q[4] == 1.0


def test_bh_edge_cases():
    assert benjamini_hochberg([]) == []
    assert benjamini_hochberg([0.03]) == [0.03]
    assert benjamini_hochberg([0.0, 0.0]) == [0.0, 0.0]


@pytest.mark.parametrize("bad", [[-0.1], [1.1], [float("nan")], [0.2, float("nan")]])
def test_bh_rejects_invalid_p_values(bad):
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        benjamini_hochberg(bad)


@settings(max_examples=200, deadline=None, derandomize=True)
@given(st.lists(st.floats(0, 1), min_size=1, max_size=60))
def test_bh_agrees_with_statsmodels_and_has_the_defining_properties(p):
    q = np.array(benjamini_hochberg(p))
    ref = multipletests(p, method="fdr_bh")[1]
    assert np.allclose(q, ref, rtol=1e-12, atol=1e-15)
    arr = np.array(p)
    assert np.all(q >= arr - 1e-15) and np.all(q <= 1.0)  # adjusting never lowers a p-value
    order = np.argsort(arr, kind="stable")
    assert np.all(np.diff(q[order]) >= -1e-15)  # monotone in p
    perm = np.random.default_rng(0).permutation(len(p))
    assert np.allclose(np.array(benjamini_hochberg(arr[perm].tolist())), q[perm])  # order-invariant


def test_bh_controls_the_false_discovery_rate_by_simulation():
    """Mix of 80 true nulls and 20 real effects: the false discovery proportion averages below 0.05."""
    rng = np.random.default_rng(11)
    fdp = []
    for _ in range(400):
        null_p = rng.uniform(size=80)
        alt_p = rng.beta(0.2, 40, size=20)  # tiny p-values
        q = np.array(benjamini_hochberg(np.r_[null_p, alt_p].tolist()))
        rejected = q < 0.05
        fdp.append(rejected[:80].sum() / max(rejected.sum(), 1))
    assert np.mean(fdp) <= 0.05 + 0.01


# ------------------------------------------------------------------ two-proportion test


@pytest.mark.parametrize(
    ("c0", "n0", "c1", "n1"),
    [(120, 4000, 90, 3500), (3, 50, 9, 60), (1000, 10**6, 1100, 10**6), (0, 100, 5, 100), (50, 50, 40, 50)],
)
def test_matches_statsmodels_z_test_and_newcombe_interval(c0, n0, c1, n1):
    r = two_proportion_test(c0, n0, c1, n1)
    pooled = (c0 + c1) / (n0 + n1)
    assert 0 < pooled < 1 or r.z_statistic is None
    z, p = proportions_ztest([c1, c0], [n1, n0])
    assert r.z_statistic == pytest.approx(z, rel=1e-10) and r.p_value == pytest.approx(p, rel=1e-10)
    lo, hi = confint_proportions_2indep(c1, n1, c0, n0, method="newcomb", compare="diff")
    assert r.change_pp.lower == pytest.approx(100 * lo, abs=1e-9) and r.change_pp.upper == pytest.approx(100 * hi, abs=1e-9)
    assert r.change_pp.estimate == pytest.approx(100 * (c1 / n1 - c0 / n0))
    assert r.change_pp.lower <= r.change_pp.estimate <= r.change_pp.upper


def test_identical_rates_give_p_one_and_a_zero_centred_interval():
    r = two_proportion_test(30, 1000, 60, 2000)
    assert r.p_value == pytest.approx(1.0) and r.change_pp.estimate == 0
    assert r.change_pp.lower < 0 < r.change_pp.upper


@pytest.mark.parametrize(("c0", "c1"), [(0, 0), (500, 500)])
def test_zero_variance_pooled_rate_is_p_one_without_nan(c0, c1):
    r = two_proportion_test(c0, 500, c1, 500)
    assert r.z_statistic is None and r.p_value == 1.0
    assert "nan" not in r.model_dump_json().lower()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"count_pre": 5, "n_pre": 0, "count_post": 1, "n_post": 10},
        {"count_pre": -1, "n_pre": 10, "count_post": 1, "n_post": 10},
        {"count_pre": 11, "n_pre": 10, "count_post": 1, "n_post": 10},
        {"count_pre": 1, "n_pre": 10, "count_post": 1, "n_post": 10, "level": 1.0},
    ],
)
def test_two_proportion_test_rejects_invalid_arguments(kwargs):
    with pytest.raises(ValueError):
        two_proportion_test(**kwargs)


def test_small_sample_means_a_small_expected_cell_in_the_pooled_table():
    assert two_proportion_test(2, 100, 3, 100).small_sample
    assert not two_proportion_test(200, 1000, 300, 1000).small_sample
    # exactly 5 expected in the smallest cell is acceptable, just below it is not
    ok = two_proportion_test(5, 100, 5, 100)  # pooled 10/200: expected successes per arm = 5.0
    assert ok.expected_min_count == pytest.approx(5.0) and not ok.small_sample
    assert two_proportion_test(4, 100, 5, 100).small_sample


def test_one_sparse_arm_is_small_even_when_the_pooled_counts_are_large():
    """0 orders in 1 session before versus 900 in 1,000 after: pooled counts are large, one arm is not.

    The plain z-test calls this significant (p = 0.0028); Fisher's exact p-value is 0.10."""
    r = two_proportion_test(0, 1, 900, 1000)
    assert r.small_sample and r.expected_min_count < 1
    assert r.p_value < 0.01
    assert fisher_exact_p(0, 1, 900, 1000) == pytest.approx(0.100899, abs=1e-5)


def test_fisher_exact_p_matches_scipy_and_is_one_for_identical_tables():
    from scipy import stats

    for c0, n0, c1, n1 in [(3, 40, 9, 60), (0, 10, 5, 12), (30, 100, 30, 100)]:
        expected = stats.fisher_exact([[c0, n0 - c0], [c1, n1 - c1]]).pvalue
        assert fisher_exact_p(c0, n0, c1, n1) == pytest.approx(expected)
    assert fisher_exact_p(30, 100, 30, 100) == pytest.approx(1.0)


def test_p_values_are_uniform_under_the_null_by_simulation():
    """Equal true rates: about 5% of tests fall below 0.05 and the p-values are roughly uniform."""
    rng = np.random.default_rng(5)
    p = np.array([
        two_proportion_test(int(rng.binomial(5000, 0.03)), 5000, int(rng.binomial(5000, 0.03)), 5000).p_value
        for _ in range(1500)
    ])
    assert 0.03 <= (p < 0.05).mean() <= 0.07
    assert 0.44 <= (p < 0.5).mean() <= 0.56


def test_newcombe_interval_covers_the_true_difference_at_about_its_level():
    rng = np.random.default_rng(6)
    covered = 0
    trials = 1500
    for _ in range(trials):
        c0, c1 = int(rng.binomial(2000, 0.03)), int(rng.binomial(2000, 0.04))
        ci = two_proportion_test(c0, 2000, c1, 2000).change_pp
        covered += ci.lower <= 1.0 <= ci.upper
    assert 0.93 <= covered / trials <= 0.975


# ------------------------------------------------------------------ day-level test


def _floor(orders: float, sessions: float) -> float:
    """Binomial variance floor of a pooled rate, from the rate shrunk by half an observation."""
    shrunk = (orders + 0.5) / (sessions + 1)
    return shrunk * (1 - shrunk) / sessions


def _independent_day_level(s0, o0, s1, o1, level=0.95):
    """The day-level test recomputed with plain Python/numpy, written from the documented formulas."""
    from scipy import stats

    out = {}
    variances, extra = [], []
    for name, s, o in (("pre", s0, o0), ("post", s1, o1)):
        s, o = np.asarray(s, float), np.asarray(o, float)
        active = s > 0
        d = int(active.sum())
        rate = o.sum() / s.sum()
        raw = d / (d - 1) * np.sum((o[active] - rate * s[active]) ** 2) / s.sum() ** 2
        floor = _floor(o.sum(), s.sum())
        variances.append(max(raw, floor))
        extra.append(np.inf if raw < floor else d - 1)
        out[name] = (d, rate, raw, floor)
    v0, v1 = variances
    diff = out["post"][1] - out["pre"][1]
    se = np.sqrt(v0 + v1)
    df = min((v0 + v1) ** 2 / (v0**2 / extra[0] + v1**2 / extra[1]), 1e6)
    t = diff / se
    crit = stats.t.ppf(1 - (1 - level) / 2, df)
    return {"t": t, "df": df, "p": 2 * stats.t.sf(abs(t), df), "diff": diff, "se": se,
            "lower": diff - crit * se, "upper": diff + crit * se, "parts": out}


def test_day_level_matches_an_independent_hand_computation_in_both_variance_regimes():
    """Before window: smooth days, so the binomial floor binds. After window: noisy days, so the estimate binds."""
    s0, o0 = [1000.0] * 4, [30.0, 31, 29, 30]
    s1, o1 = [1000.0] * 3, [10.0, 60, 30]
    r = day_level_test(s0, o0, s1, o1)
    ref = _independent_day_level(s0, o0, s1, o1)
    assert r.variance_floored_pre and not r.variance_floored_post  # both regimes are exercised
    assert r.t_statistic == pytest.approx(ref["t"]) and r.degrees_of_freedom == pytest.approx(ref["df"])
    assert r.p_value == pytest.approx(ref["p"])
    assert r.change_pp.estimate == pytest.approx(100 * ref["diff"])
    assert r.change_pp.lower == pytest.approx(100 * ref["lower"]) and r.change_pp.upper == pytest.approx(100 * ref["upper"])
    assert r.std_error_pp == pytest.approx(100 * ref["se"]) and (r.n_days_pre, r.n_days_post) == (4, 3)
    raw, floor = ref["parts"]["pre"][2:]
    assert r.design_effect_pre == pytest.approx(raw / floor) and r.design_effect_pre < 1  # smoother than binomial


def test_day_level_matches_the_independent_computation_on_noisy_data_without_the_floor():
    s0, o0 = [100.0, 120, 90, 110], [3.0, 12, 2, 9]
    s1, o1 = [105.0, 95, 130], [15.0, 2, 20]
    r = day_level_test(s0, o0, s1, o1)
    ref = _independent_day_level(s0, o0, s1, o1)
    assert not r.variance_floored_pre and not r.variance_floored_post
    assert r.degrees_of_freedom == pytest.approx(ref["df"]) and r.p_value == pytest.approx(ref["p"])
    assert r.degrees_of_freedom < 6  # estimated variances: Welch df below the pooled d-1 + d-1


def test_day_level_is_never_degenerate():
    """Identical daily rates, zero orders and all orders used to give zero variance, so p = 0 or a zero-width
    interval; every window now carries at least binomial sampling uncertainty."""
    same = day_level_test([1000] * 5, [30] * 5, [2000] * 5, [60] * 5)
    assert same.p_value == 1.0 and same.t_statistic == 0.0 and same.change_pp.estimate == 0.0
    assert same.change_pp.lower < 0 < same.change_pp.upper
    moved = day_level_test([1000] * 5, [30] * 5, [1000] * 5, [20] * 5)
    assert 0.0 < moved.p_value < 0.01 and moved.change_pp.lower < moved.change_pp.estimate < moved.change_pp.upper
    for orders in (0, 1000):
        edge = day_level_test([1000] * 3, [orders] * 3, [1000] * 3, [orders] * 3)
        assert edge.p_value == 1.0 and edge.change_pp.lower < 0 < edge.change_pp.upper
        assert edge.std_error_pp > 0 and edge.degrees_of_freedom is not None
    for r in (same, moved, edge):
        text = r.model_dump_json().lower()
        assert "nan" not in text and "infinity" not in text


def test_zero_session_days_are_not_observations():
    """A segment's absent days must neither add degrees of freedom nor change the result."""
    s0, o0, s1, o1 = [100.0, 120, 90, 110], [3.0, 12, 2, 9], [105.0, 95, 130], [15.0, 2, 20]
    plain = day_level_test(s0, o0, s1, o1)
    padded = day_level_test([0, *s0, 0, 0], [0, *o0, 0, 0], [0, 0, *s1, 0], [0, 0, *o1, 0])
    assert padded.model_dump() == plain.model_dump()
    assert (padded.n_days_pre, padded.n_days_post) == (4, 3)


def test_a_single_active_day_per_window_is_refused_not_given_certainty():
    """100 sessions and 10 orders on one day before, 100 and 11 on one day after, padded to 14 days each:
    it used to report 14 days per window, p = 0 and a zero-width interval."""
    pad = [0.0] * 13
    for one, two in [([100.0, *pad], [10.0, *pad]), ([*pad, 100.0], [*pad, 10.0])]:
        with pytest.raises(ValueError, match="at least 3 days with sessions in each window"):
            day_level_test(one, two, [100.0, *pad], [11.0, *pad])
    with pytest.raises(ValueError, match="got 2 before and 3 after"):
        day_level_test([100.0, 100.0, *pad], [10.0, 10.0, *pad], [100.0] * 3, [11.0] * 3)
    ok = day_level_test([100.0] * 3, [10.0, 12, 8], [100.0] * 3, [11.0, 13, 9])  # exactly three days is enough
    assert (ok.n_days_pre, ok.n_days_post) == (3, 3) and ok.change_pp.lower < ok.change_pp.upper


def test_day_level_reflects_day_to_day_variation_and_the_number_of_days():
    smooth = day_level_test([1000] * 6, [30, 30, 31, 29, 30, 30], [1000] * 6, [36, 36, 37, 35, 36, 36])
    noisy = day_level_test([1000] * 6, [10, 50, 31, 12, 49, 28], [1000] * 6, [20, 52, 37, 24, 51, 32])
    assert noisy.p_value > smooth.p_value and noisy.std_error_pp > smooth.std_error_pp
    # the same noisy pattern over more days gives a narrower interval: few days cost width through the t distribution
    long = day_level_test([1000] * 24, [10, 50, 31, 12, 49, 28] * 4, [1000] * 24, [20, 52, 37, 24, 51, 32] * 4)
    assert (long.change_pp.upper - long.change_pp.lower) < (noisy.change_pp.upper - noisy.change_pp.lower)
    three = day_level_test([1000] * 3, [20, 40, 10], [1000] * 3, [30, 50, 20])
    assert three.degrees_of_freedom is not None and three.degrees_of_freedom < 6


@pytest.mark.parametrize(
    "args",
    [
        ([1000, 1000], [30, 30], [1000, 1000, 1000], [30, 30, 30]),  # two days before
        ([1000, 1000, 1000], [30, 30, 30], [0, 0, 0], [0, 0, 0]),  # no sessions after
        ([1000, 1000, 1000], [30, 30], [1000, 1000, 1000], [30, 30, 30]),  # length mismatch
        ([1000, 1000, 1000], [30, 2000, 30], [1000, 1000, 1000], [30, 30, 30]),  # orders above sessions
        ([1000, 1000, 1000], [-1, 30, 30], [1000, 1000, 1000], [30, 30, 30]),
        ([-5, 1000, 1000], [0, 30, 30], [1000, 1000, 1000], [30, 30, 30]),  # negative sessions
        ([[1000, 1000, 1000]], [[30, 30, 30]], [1000, 1000, 1000], [30, 30, 30]),  # two-dimensional
        (["a", "b", "c"], [1, 2, 3], [1000, 1000, 1000], [30, 30, 30]),  # not numeric
    ],
)
def test_day_level_rejects_unusable_input(args):
    with pytest.raises(ValueError):
        day_level_test(*args)


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
@pytest.mark.parametrize("position", range(4))
def test_day_level_rejects_nonfinite_values_before_calculating(bad, position):
    """Previously NaN input returned NaN estimates and p-values, serialised as null."""
    arrays = [[1000.0, 1000, 1000], [30.0, 30, 30], [1000.0, 1000, 1000], [30.0, 40, 35]]
    arrays[position][1] = bad
    with pytest.raises(ValueError, match="finite"):
        day_level_test(*arrays)


def test_day_level_example_from_review_is_rejected():
    with pytest.raises(ValueError, match="finite"):
        day_level_test([1000, np.nan], [30, 30], [1000, 1000], [30, 40])


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 2.5, "x", None])
def test_counts_must_be_finite_whole_numbers(bad):
    with pytest.raises(ValueError):
        two_proportion_test(bad, 100, 5, 100)
    with pytest.raises(ValueError):
        two_proportion_test(5, bad, 5, 100)
    with pytest.raises(ValueError):
        fisher_exact_p(bad, 100, 5, 100)
    with pytest.raises(ValueError):
        fisher_exact_p(5, 100, 5, bad)


def test_integral_floats_are_accepted_as_counts():
    assert two_proportion_test(5.0, 100.0, 7.0, 100.0).count_post == 7
    assert 0 <= fisher_exact_p(5.0, 100.0, 7.0, 100.0) <= 1


@settings(max_examples=60, deadline=None, derandomize=True)
@given(
    seed=st.integers(0, 2**31 - 1),
    d0=st.integers(3, 12),
    d1=st.integers(3, 12),
    zeros=st.integers(0, 6),
    base=st.integers(1, 5000),
    rate=st.floats(0.0, 1.0),
)
def test_day_level_results_are_always_finite_and_never_degenerate(seed, d0, d1, zeros, base, rate):
    rng = np.random.default_rng(seed)
    s0, s1 = rng.integers(1, base + 2, d0), rng.integers(1, base + 2, d1)
    o0, o1 = rng.binomial(s0, rate), rng.binomial(s1, rate)
    pad = np.zeros(zeros)
    r = day_level_test(np.r_[s0, pad], np.r_[o0, pad], np.r_[pad, s1], np.r_[pad, o1])
    assert (r.n_days_pre, r.n_days_post) == (d0, d1)  # zero-session days never counted
    assert r.std_error_pp > 0 and np.isfinite(r.std_error_pp)
    assert r.change_pp.lower < r.change_pp.estimate < r.change_pp.upper or (
        r.change_pp.lower < r.change_pp.upper and r.change_pp.lower <= r.change_pp.estimate <= r.change_pp.upper
    )
    assert 0.0 <= r.p_value <= 1.0 and np.isfinite(r.degrees_of_freedom) and r.degrees_of_freedom >= 1
    if r.p_value == 0.0:
        assert abs(r.t_statistic) > 30  # certainty only from an enormous, well-supported effect
    text = r.model_dump_json().lower()
    assert "nan" not in text and "infinity" not in text


def _simulate_null(days: int, overdispersion: float, n: int, seed: int):
    """n independent two-window comparisons with identical true rates; returns day-level arrays."""
    rng = np.random.default_rng(seed)

    def arm():
        sessions = rng.poisson(5000, size=(n, days))
        rate = 0.03 * np.clip(1 + rng.normal(0, overdispersion, size=(n, days)), 0.2, 3)
        return sessions.astype(float), rng.binomial(sessions, rate).astype(float)

    return (*arm(), *arm())


@pytest.mark.parametrize("days", [3, 7, 14])
@pytest.mark.parametrize("overdispersion", [0.0, 0.15, 0.4])
def test_day_level_test_is_calibrated_under_the_null(days, overdispersion):
    """Previously a quasi-binomial test with an estimated, then trusted, dispersion rejected 7.2% at a nominal
    5% with seven days per window. Measured: 2.3-5.6% rejections and 94.4-97.7% interval coverage across 3 to 28
    days and no to heavy overdispersion (Monte Carlo error about 0.3 points). Short windows and clean binomial
    data are conservative, which is the safe side, so only the liberal side is bounded tightly."""
    s0, o0, s1, o1 = _simulate_null(days, overdispersion, n=4000, seed=100 * days + int(100 * overdispersion))
    core = _day_level_core(s0, o0, s1, o1, 0.95)
    rejection = float(np.mean(core["p"] < 0.05))
    coverage = float(np.mean((core["lower"] <= 0) & (core["upper"] >= 0)))
    assert 0.015 <= rejection <= 0.062, rejection
    assert 0.938 <= coverage <= 0.985, coverage


def test_the_plain_binomial_test_is_not_calibrated_when_days_vary(overdispersion=0.4):
    """Negative control: the same simulations through the plain z-test reject far too often, which is why the
    day-level test is the default."""
    s0, o0, s1, o1 = _simulate_null(7, overdispersion, n=1500, seed=77)
    rejected = [
        two_proportion_test(int(a.sum()), int(b.sum()), int(c.sum()), int(d.sum())).p_value < 0.05
        for a, b, c, d in zip(o0, s0, o1, s1, strict=True)
    ]
    assert np.mean(rejected) > 0.15


def test_unequal_window_lengths_are_calibrated_too():
    """The real use: a long before-window and a short after-window."""
    rng = np.random.default_rng(5)
    n = 4000

    def arm(days):
        sessions = rng.poisson(5000, size=(n, days))
        rate = 0.03 * np.clip(1 + rng.normal(0, 0.15, size=(n, days)), 0.2, 3)
        return sessions.astype(float), rng.binomial(sessions, rate).astype(float)

    s0, o0 = arm(60)
    s1, o1 = arm(7)
    core = _day_level_core(s0, o0, s1, o1, 0.95)
    assert 0.015 <= float(np.mean(core["p"] < 0.05)) <= 0.062
