from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm
from hypothesis import given, settings
from hypothesis import strategies as st
from scipy import stats

from causelens.analytics.timing import (
    MIN_DAYS_AFTER,
    MIN_DAYS_BEFORE,
    _contrast,
    analyze_timing,
    fieller_set,
    fit_steps,
    lag_ambiguity,
    timing_diagnostics,
)

START = pd.Timestamp("2026-08-01")


def days_index(n: int) -> pd.DatetimeIndex:
    return pd.date_range(START, periods=n)


def d(i: int) -> date:
    return (START + pd.Timedelta(days=i)).date()


def series(seed: int, n: int, steps: dict[int, float], sd: float = 0.2, base: float = -0.9) -> np.ndarray:
    rng = np.random.default_rng(seed)
    y = np.full(n, base) + rng.normal(0, sd, n)
    for k, b in steps.items():
        y[k:] += b
    return y


def sm_fit(days: pd.DatetimeIndex, y: np.ndarray, onsets: list[int]):
    X = np.column_stack([np.ones(len(days)), *[(np.arange(len(days)) >= k).astype(float) for k in onsets]])
    return sm.OLS(y, X).fit(), X


# ------------------------------------------------------------------ the step model and its contrasts


def test_pooled_variance_and_degrees_of_freedom_follow_sse_over_n_minus_rank():
    n, ons = 60, {"A": d(30), "B": d(31), "C": d(45)}
    y = series(1, n, {30: -0.3, 31: -0.3, 45: 0.2})
    fit = fit_steps(days_index(n), y, ons)
    ref, X = sm_fit(days_index(n), y, [30, 31, 45])
    assert fit.rank == int(np.linalg.matrix_rank(X)) == 4 and fit.n == n and fit.df == n - 4
    assert fit.sigma2 == pytest.approx(float(ref.resid @ ref.resid) / (n - 4)) and fit.sigma2 == pytest.approx(ref.scale)
    assert np.allclose(fit.beta, ref.params[1:]) and np.allclose(fit.sigma2 * fit.M, ref.cov_params()[1:, 1:])


def test_a_single_contrast_is_a_t_test_on_residual_degrees_of_freedom():
    n, ons = 60, [30, 31]
    y = series(2, n, {30: -0.2, 31: -0.4})
    fit = fit_steps(days_index(n), y, {"A": d(30), "B": d(31)})
    R = np.array([[1.0, 0.0]])
    kind, value, df1, df2, p = _contrast(fit, R)
    ref, _ = sm_fit(days_index(n), y, ons)
    tt = ref.t_test(np.array([[0, 1, 0]]))
    assert kind == "t" and df1 == 1 and df2 == n - 3
    assert value == pytest.approx(float(np.ravel(tt.tvalue)[0])) and p == pytest.approx(float(np.ravel(tt.pvalue)[0]))
    # an arbitrary linear combination, using the covariance between the two coefficients
    kind, value, *_, p = _contrast(fit, np.array([[1.0, 1.0]]))
    tt = ref.t_test(np.array([[0, 1, 1]]))
    assert value == pytest.approx(float(np.ravel(tt.tvalue)[0])) and p == pytest.approx(float(np.ravel(tt.pvalue)[0]))


def test_a_joint_restriction_is_an_f_test_with_q_and_residual_degrees_of_freedom():
    n, ons = 80, [30, 31, 50]
    y = series(3, n, {30: -0.2, 31: -0.3, 50: 0.25})
    fit = fit_steps(days_index(n), y, {"A": d(30), "B": d(31), "C": d(50)})
    kind, value, df1, df2, p = _contrast(fit, np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]))
    ref, _ = sm_fit(days_index(n), y, ons)
    ft = ref.f_test(np.array([[0, 1, 0, 0], [0, 0, 0, 1]]))
    assert kind == "F" and (df1, df2) == (2, n - 4)
    assert value == pytest.approx(float(np.ravel(ft.fvalue)[0])) and p == pytest.approx(float(np.ravel(ft.pvalue)[0]))


def test_covariance_is_used_not_just_the_marginal_standard_errors():
    """Adjacent steps are negatively correlated; testing their sum with marginal SEs would be wrong."""
    n = 60
    y = series(4, n, {30: -0.2, 31: -0.2})
    fit = fit_steps(days_index(n), y, {"A": d(30), "B": d(31)})
    cov = fit.sigma2 * fit.M
    assert cov[0, 1] < 0 and abs(cov[0, 1]) > 0.1 * np.sqrt(cov[0, 0] * cov[1, 1])
    _, t_full, *_ = _contrast(fit, np.array([[1.0, 1.0]]))
    naive = float(fit.beta.sum()) / np.sqrt(cov[0, 0] + cov[1, 1])
    assert abs(t_full) != pytest.approx(abs(naive), rel=0.05)


def test_sandwich_variance_collapses_for_a_single_date_step_and_ours_does_not():
    """Why design.md excludes HC1/cluster for timing contrasts: the one-day step is fitted exactly (residual 0)."""
    n = 70
    y = series(5, n, {40: -0.3, 41: -0.4})
    ref, X = sm_fit(days_index(n), y, [40, 41])
    hc1 = sm.OLS(y, X).fit(cov_type="HC1")
    fit = fit_steps(days_index(n), y, {"A": d(40), "B": d(41)})
    assert abs(ref.resid[40]) < 1e-12  # the hero-only day has zero residual
    our_se = float(np.sqrt(fit.sigma2 * fit.M[0, 0]))
    assert hc1.bse[1] < 0.25 * our_se


@pytest.mark.parametrize("sd", [0.05, 0.3])
def test_sole_source_contrast_is_calibrated_under_the_model(sd):
    """Under iid normal noise and the true model, the contrast rejects at its nominal 5% (and F at 5% too)."""
    rng = np.random.default_rng(10)
    n, trials = 80, 3000
    rej_t = rej_f = 0
    for _ in range(trials):
        y = -0.9 + rng.normal(0, sd, n)
        y[40:] += -0.5  # the only step is the second candidate's (index 41 in the model below is a no-effect step)
        fit = fit_steps(days_index(n), y, {"A": d(39), "B": d(40), "C": d(60)})
        # truth: only B steps (A and C have beta = 0)
        rej_t += _contrast(fit, np.array([[1.0, 0.0, 0.0]]))[4] < 0.05
        rej_f += _contrast(fit, np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]))[4] < 0.05
    assert 0.035 <= rej_t / trials <= 0.065, rej_t / trials
    assert 0.035 <= rej_f / trials <= 0.065, rej_f / trials


def test_a_real_second_step_is_detected():
    n = 70
    y = series(6, n, {30: -0.1, 31: -0.6}, sd=0.15)
    t = analyze_timing(days_index(n), y, {"A": d(30), "B": d(31)})
    assert t.status == "ok" and t.candidates["A"].sole_source.rejected and not t.candidates["B"].sole_source.rejected
    assert t.candidates["B"].step_significant and t.candidates["B"].direction_matches is True


# ------------------------------------------------------------------ identifiability


def test_coincident_onsets_are_unidentifiable_and_nothing_is_invented():
    n = 60
    y = series(7, n, {30: -0.6})
    t = analyze_timing(days_index(n), y, {"A": d(30), "B": d(30), "C": d(45)})
    for name in ("A", "B"):
        c = t.candidates[name]
        assert c.status == "unidentifiable" and "coincides with" in c.unidentifiable_reason
        assert c.step_pp is None and c.step_p is None and c.sole_source is None and not c.step_significant
        assert not c.share_set.available and c.share_set.pieces == [] and c.share_set.reason
    assert t.candidates["A"].coincident_with == ["B"] and t.candidates["B"].coincident_with == ["A"]
    assert t.candidates["C"].status == "ok" and t.rank == 3  # the model has one column for the {A, B} group
    assert "NaN" not in t.model_dump_json()


def test_no_usable_day_between_two_onsets_makes_them_coincide():
    n = 60
    y = series(8, n, {30: -0.2, 31: -0.4})
    keep = np.arange(n) != 30  # the hero-only day was excluded as flagged
    t = analyze_timing(days_index(n)[keep], y[keep], {"A": d(30), "B": d(31)})
    assert all(c.status == "unidentifiable" for c in t.candidates.values())
    assert all(not c.share_set.available for c in t.candidates.values())


def test_onsets_outside_the_data_are_degenerate_not_estimated():
    n = 60
    y = series(9, n, {30: -0.5})
    t = analyze_timing(days_index(n), y, {"A": d(30), "late": d(200), "early": d(-10)})
    assert t.candidates["A"].status == "ok"
    assert "no usable day on or after" in t.candidates["late"].unidentifiable_reason
    assert "every usable day is on or after" in t.candidates["early"].unidentifiable_reason
    assert t.candidates["late"].step_pp is None and not t.candidates["early"].share_set.available


def test_no_residual_degrees_of_freedom_is_not_evaluable():
    n = 3
    t = analyze_timing(days_index(n), np.array([0.0, -1.0, -2.0]), {"A": d(1), "B": d(2)})
    assert t.status == "not_evaluable" and all(c.status == "unidentifiable" for c in t.candidates.values())


def test_a_single_candidate_has_no_sole_source_test_and_a_trivial_share():
    n = 60
    t = analyze_timing(days_index(n), series(11, n, {30: -0.6}), {"A": d(30)})
    c = t.candidates["A"]
    assert c.status == "ok" and c.sole_source is None and c.step_significant
    assert not c.share_set.available and "by construction" in c.share_set.reason


# ------------------------------------------------------------------ Fieller sets


def grid_set(fit, group: int, level: float = 0.95) -> np.ndarray:
    """Brute force: every rho on a fine grid with (beta_g - rho*total)^2 <= t^2 Var(beta_g - rho*total)."""
    rho = np.linspace(-40, 40, 160_001)
    G = len(fit.groups)
    crit2 = stats.t.ppf(1 - (1 - level) / 2, fit.df) ** 2
    e = np.zeros(G)
    e[group] = 1.0
    ones = np.ones(G)
    c = e[None, :] - rho[:, None] * ones[None, :]
    var = fit.sigma2 * np.einsum("ij,jk,ik->i", c, fit.M, c)
    stat = (c @ fit.beta) ** 2
    return rho[stat <= crit2 * var]


def test_bounded_set_matches_a_brute_force_grid_and_contains_the_estimate():
    n = 70
    fit = fit_steps(days_index(n), series(12, n, {30: -0.3, 45: -0.4}, sd=0.15), {"A": d(30), "B": d(45)})
    cs = fieller_set(fit, 0)
    lo, hi = cs.pieces[0]
    g = grid_set(fit, 0)
    assert cs.kind == "bounded" and cs.available and cs.contains(float(fit.beta[0] / fit.beta.sum()))
    assert lo == pytest.approx(g.min(), abs=2e-3) and hi == pytest.approx(g.max(), abs=2e-3)


def test_offsetting_steps_give_a_disconnected_set_that_matches_the_grid():
    """Steps that cancel (total about 0) make the share undefined: the set is two rays, never a finite interval."""
    n = 80
    fit = fit_steps(days_index(n), series(13, n, {30: -0.5, 50: 0.5}, sd=0.25), {"A": d(30), "B": d(50)})
    cs = fieller_set(fit, 0)
    assert cs.kind in ("disconnected", "unbounded") and cs.available
    g = grid_set(fit, 0)
    if cs.kind == "disconnected":
        (a, lo), (hi, b) = cs.pieces
        assert a is None and b is None and lo < hi
        inside = g[(g > lo + 1e-3) & (g < hi - 1e-3)]
        assert inside.size == 0 and g.min() <= -39.9 and g.max() >= 39.9
        assert lo == pytest.approx(g[g <= lo + 1].max(), abs=3e-3)
    else:
        assert cs.pieces == [(None, None)] and g.size == 160_001


def test_a_total_step_that_is_zero_gives_the_whole_line():
    n = 60
    fit = fit_steps(days_index(n), series(14, n, {30: -0.05, 45: 0.05}, sd=1.0), {"A": d(30), "B": d(45)})
    cs = fieller_set(fit, 0)
    assert cs.available and cs.kind == "unbounded" and cs.pieces == [(None, None)]
    assert cs.contains(-1000) and cs.contains(1000)


def test_a_zero_quadratic_coefficient_gives_a_half_line_that_matches_the_grid():
    """Construct a step model whose Fieller quadratic is exactly linear (a = total^2 - T*s = 0, b != 0)."""
    from types import SimpleNamespace

    crit = float(stats.t.ppf(0.975, 60))
    fake = SimpleNamespace(
        groups=[["A"], ["B"]], df=60, sigma2=4.0 / crit**2, beta=np.array([1.0, 1.0]),
        M=np.array([[0.7, 0.05], [0.05, 0.2]]),  # s = 1.0, so T*s = 4 = total^2
    )
    cs = fieller_set(fake, 0)  # type: ignore[arg-type]
    assert cs.available and cs.kind == "half_line" and cs.pieces[0][0] is None
    assert cs.pieces[0][1] == pytest.approx(0.9, abs=1e-6)
    g = grid_set(fake, 0)  # type: ignore[arg-type]
    assert g.max() == pytest.approx(0.9, abs=1e-3) and g.min() <= -39.99
    assert cs.contains(0.5) and not cs.contains(1.0)


def test_a_vanishing_quadratic_and_slope_gives_the_whole_line():
    from types import SimpleNamespace

    crit = float(stats.t.ppf(0.975, 60))
    m = 2.0 / crit**2
    fake = SimpleNamespace(groups=[["A"], ["B"]], df=60, sigma2=1.0, beta=np.array([1.0, 1.0]),
                           M=np.array([[m, 0.0], [0.0, m]]))
    cs = fieller_set(fake, 0)  # type: ignore[arg-type]
    assert cs.available and cs.kind == "unbounded" and cs.pieces == [(None, None)]


def test_fieller_set_covers_the_true_share_at_about_its_nominal_level():
    rng = np.random.default_rng(15)
    n, hits, trials = 70, 0, 1500
    for _ in range(trials):
        y = -0.9 + rng.normal(0, 0.2, n)
        y[30:] += -0.3  # true betas: A = -0.3, B = -0.6 -> the share of A is 1/3
        y[45:] += -0.6
        fit = fit_steps(days_index(n), y, {"A": d(30), "B": d(45)})
        hits += fieller_set(fit, 0).contains(1 / 3)
    assert 0.93 <= hits / trials <= 0.97, hits / trials


def test_one_group_and_coincident_groups_have_no_set():
    n = 60
    y = series(16, n, {30: -0.5})
    fit = fit_steps(days_index(n), y, {"A": d(30)})
    assert not fieller_set(fit, 0).available


@settings(max_examples=40, deadline=None, derandomize=True)
@given(seed=st.integers(0, 10**6), b1=st.floats(-1, 1), b2=st.floats(-1, 1), level=st.sampled_from([0.8, 0.9, 0.95, 0.99]))
def test_property_fieller_set_is_consistent_with_the_estimate_and_widens_with_level(seed, b1, b2, level):
    n = 70
    y = series(seed, n, {25: b1, 45: b2}, sd=0.2)
    fit = fit_steps(days_index(n), y, {"A": d(25), "B": d(45)})
    cs, wide = fieller_set(fit, 0, level), fieller_set(fit, 0, 0.999)
    assert cs.available and wide.available
    total = float(fit.beta.sum())
    if abs(total) > 1e-9:
        assert cs.contains(float(fit.beta[0]) / total)
    for lo, hi in cs.pieces:
        assert lo is None or hi is None or lo <= hi
    probe = np.linspace(-6, 6, 241)
    assert all(wide.contains(float(r)) for r in probe if cs.contains(float(r)))


# ------------------------------------------------------------------ diagnostics


def test_diagnostics_pass_on_iid_noise_at_a_high_rate():
    passed = 0
    for s in range(200):
        n = 70
        fit = fit_steps(days_index(n), series(s, n, {40: -0.3}), {"A": d(40)})
        passed += timing_diagnostics(fit, d(40)).status == "ok"
    assert passed / 200 >= 0.95


def test_autocorrelated_noise_fails_the_diagnostics():
    rng = np.random.default_rng(20)
    n = 100
    e = np.zeros(n)
    for i in range(1, n):
        e[i] = 0.7 * e[i - 1] + rng.normal(0, 0.2)
    y = -0.9 + e
    y[60:] -= 0.3
    fit = fit_steps(days_index(n), y, {"A": d(60)})
    diag = timing_diagnostics(fit, d(60))
    assert diag.status == "failed" and "Ljung-Box" in diag.reason
    t = analyze_timing(days_index(n), y, {"A": d(60), "B": d(70)})
    assert t.status == "assumptions_failed" and "Ljung-Box" in t.reason


def test_unequal_variance_fails_the_diagnostics():
    rng = np.random.default_rng(21)
    n = 100
    y = -0.9 + rng.normal(0, 0.1, n)
    y[60:] = y[60:] - 0.3 + rng.normal(0, 0.4, n - 60)
    fit = fit_steps(days_index(n), y, {"A": d(60)})
    diag = timing_diagnostics(fit, d(60))
    assert diag.status == "failed" and "Levene" in diag.reason


def test_too_few_days_is_insufficient_not_a_pass():
    n = MIN_DAYS_BEFORE + MIN_DAYS_AFTER - 1
    fit = fit_steps(days_index(n), series(22, n, {MIN_DAYS_BEFORE: -0.4}), {"A": d(MIN_DAYS_BEFORE)})
    diag = timing_diagnostics(fit, d(MIN_DAYS_BEFORE))
    assert diag.status == "insufficient_days" and diag.checks == [] and "cannot be checked" in diag.reason
    ok = fit_steps(days_index(n + 1), series(22, n + 1, {MIN_DAYS_BEFORE: -0.4}), {"A": d(MIN_DAYS_BEFORE)})
    assert timing_diagnostics(ok, d(MIN_DAYS_BEFORE)).status in ("ok", "failed")


def test_a_perfect_fit_cannot_have_its_noise_checked():
    n = 40
    y = np.where(np.arange(n) >= 20, -1.0, 0.0)
    fit = fit_steps(days_index(n), y, {"A": d(20)})
    diag = timing_diagnostics(fit, d(20))
    assert diag.status == "not_evaluable" and "no residual variation" in diag.reason


def test_failed_diagnostics_still_report_the_numbers_but_flag_the_status():
    rng = np.random.default_rng(23)
    n = 100
    e = np.zeros(n)
    for i in range(1, n):
        e[i] = 0.7 * e[i - 1] + rng.normal(0, 0.2)
    t = analyze_timing(days_index(n), -0.9 + e, {"A": d(60), "B": d(70)})
    assert t.status == "assumptions_failed" and t.candidates["A"].step_pp is not None and t.diagnostics.status == "failed"


# ------------------------------------------------------------------ lag ambiguity


EV = {"Hero": date(2026, 10, 13), "Checkout": date(2026, 10, 14)}
LAGS = {"Hero": (0, 1), "Checkout": (0, 1)}


def test_earlier_event_at_lag_one_collides_with_the_next_day_event_at_lag_zero():
    pairs = lag_ambiguity(EV, {date(2026, 10, 14)}, LAGS)
    assert [(p.first, p.first_lag, p.second, p.second_lag, p.onset_date) for p in pairs] == [("Hero", 1, "Checkout", 0, date(2026, 10, 14))]


def test_lags_are_compared_independently_not_only_when_equal():
    # Checkout at lag 1 (15 Oct) cannot meet Hero at lag 1 (14 Oct); only the unequal-lag pair collides
    pairs = lag_ambiguity(EV, {date(2026, 10, 14), date(2026, 10, 15)}, LAGS)
    assert {(p.first_lag, p.second_lag) for p in pairs} == {(1, 0)}
    wide = lag_ambiguity(EV, {date(2026, 10, 14)}, {"Hero": (0, 1, 2), "Checkout": (0,)})
    assert [(p.first_lag, p.second_lag) for p in wide] == [(1, 0)]
    both = lag_ambiguity({"A": date(2026, 10, 10), "B": date(2026, 10, 12)}, {date(2026, 10, 12)}, {"A": (0, 1, 2), "B": (0,)})
    assert [(p.first_lag, p.second_lag) for p in both] == [(2, 0)]


def test_a_collision_outside_the_onset_set_is_not_ambiguity():
    assert lag_ambiguity(EV, {date(2026, 10, 20)}, LAGS) == []
    assert lag_ambiguity(EV, set(), LAGS) == []


def test_identical_dates_collide_at_equal_lags():
    pairs = lag_ambiguity({"A": date(2026, 10, 14), "B": date(2026, 10, 14)}, {date(2026, 10, 14)}, {"A": (0,), "B": (0,)})
    assert len(pairs) == 1 and pairs[0].onset_date == date(2026, 10, 14)


def test_a_candidate_is_never_ambiguous_with_itself_and_missing_lags_default_to_zero():
    assert lag_ambiguity({"A": date(2026, 10, 14)}, {date(2026, 10, 14)}, {"A": (0, 1)}) == []
    assert lag_ambiguity({"A": date(2026, 10, 14), "B": date(2026, 10, 14)}, {date(2026, 10, 14)}, {}) != []


def test_timing_dates_arithmetic_matches_timedelta():
    assert date(2026, 10, 13) + timedelta(days=1) == date(2026, 10, 14)
