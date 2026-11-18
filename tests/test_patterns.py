from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError
from scenarios import POST, PRE, simulate
from scipy import stats

from causelens.analytics import check_data_quality, validate_metrics
from causelens.analytics.patterns import (
    NOT_DECLARED,
    ExpectedPattern,
    check_pattern,
    restricted_test,
    uniform_test,
)

PRE_W = (date(2026, 8, 1), date(2026, 8, 20))  # 20 days
POST_W = (date(2026, 8, 21), date(2026, 8, 30))  # 10 days


def partition_frame(seed: int, rel: dict[str, float], *, base: dict[str, float] | None = None, day_sd: float = 0.1,
                    sessions: int = 4000, shared: bool = True) -> pd.DataFrame:
    """Segments of ``device`` (other dimensions constant); orders are binomial around ``base*(1+rel)`` after day 20,
    with a day effect common to all segments (``shared``)."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(30):
        common = 1 + rng.normal(0, day_sd) if shared else 1.0
        for dev, r in rel.items():
            level = (base or {}).get(dev, 0.04) * (1 + (r if i >= 20 else 0.0)) * (common if shared else 1 + rng.normal(0, day_sd))
            s = int(rng.poisson(sessions))
            rows.append({"date": (pd.Timestamp("2026-08-01") + pd.Timedelta(days=i)).strftime("%Y-%m-%d"), "device": dev,
                         "browser": "chrome", "channel": "organic", "customer_type": "new", "sessions": s,
                         "orders": int(rng.binomial(s, min(level, 0.9)))})
    return validate_metrics(pd.DataFrame(rows))


def all_days(df: pd.DataFrame) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(sorted(df["date"].unique()))


# ------------------------------------------------------------------ the declared pattern object


def test_default_pattern_is_unspecified_and_never_inferred():
    p = ExpectedPattern()
    assert p.kind == "unspecified" and p.scope is None and p.equivalence_margin_pp is None and p.scale == "relative"
    assert NOT_DECLARED.status == "not_declared" and "no expected pattern was declared" in NOT_DECLARED.detail


@pytest.mark.parametrize(
    "kwargs",
    [{"kind": "restricted"}, {"kind": "restricted", "scope": {}}, {"kind": "restricted", "scope": {"colour": "red"}},
     {"kind": "uniform", "scope": {"device": "mobile"}}, {"kind": "restricted", "scope": {"device": "mobile"}, "equivalence_margin_pp": 0.0},
     {"kind": "restricted", "scope": {"device": "mobile"}, "equivalence_margin_pp": -0.1}],
)
def test_invalid_declared_patterns_are_rejected(kwargs):
    with pytest.raises(ValidationError):
        ExpectedPattern(**kwargs)


def test_unspecified_pattern_check_is_not_declared():
    df = partition_frame(1, {"a": 0.0, "b": 0.0})
    assert check_pattern(ExpectedPattern(), df, all_days(df), PRE_W, POST_W) is NOT_DECLARED


# ------------------------------------------------------------------ the omnibus uniform test


def independent_wald(df: pd.DataFrame, scale: str = "relative") -> tuple[float, int, int]:
    """Hotelling-type F for equal change across the values of ``device``, from the formulas, with loops."""
    def window(lo, hi):
        sub = df[(df["date"] >= pd.Timestamp(lo)) & (df["date"] <= pd.Timestamp(hi))]
        days = sorted(sub["date"].unique())
        vals = sorted(sub["device"].unique())
        s = np.array([[sub[(sub.date == dd) & (sub.device == v)]["sessions"].sum() for v in vals] for dd in days], float)
        o = np.array([[sub[(sub.date == dd) & (sub.device == v)]["orders"].sum() for v in vals] for dd in days], float)
        return s, o

    out = []
    for lo, hi in (PRE_W, POST_W):
        s, o = window(lo, hi)
        d, g = s.shape
        S, O = s.sum(0), o.sum(0)
        R = O / S
        cov = np.zeros((g, g))
        for a in range(g):
            for b in range(g):
                cov[a, b] = d / (d - 1) * sum((o[t, a] - R[a] * s[t, a]) * (o[t, b] - R[b] * s[t, b]) for t in range(d)) / (S[a] * S[b])
        out.append((R, cov, d))
    (r0, c0, d0), (r1, c1, d1) = out
    if scale == "relative":
        theta = np.log(r1) - np.log(r0)
        cov = c1 / np.outer(r1, r1) + c0 / np.outer(r0, r0)
    else:
        theta, cov = r1 - r0, c1 + c0
    g = len(theta)
    contrasts = np.array([[(-1.0 if j == 0 else (1.0 if j == i + 1 else 0.0)) for j in range(g)] for i in range(g - 1)])
    diff = contrasts @ theta
    w = float(diff @ np.linalg.inv(contrasts @ cov @ contrasts.T) @ diff)
    nu, p = min(d0, d1) - 1, g - 1
    return w * (nu - p + 1) / (nu * p), p, nu - p + 1


@pytest.mark.parametrize("scale", ["relative", "absolute"])
def test_omnibus_statistic_matches_an_independent_loop_implementation(scale):
    df = partition_frame(3, {"a": -0.3, "b": 0.0, "c": 0.1})
    res = uniform_test(df, all_days(df), PRE_W, POST_W, scale=scale)
    part = next(p for p in res.partitions if p.dimension == "device")
    f, d1, d2 = independent_wald(df, scale)
    assert part.status == "tested" and part.statistic == pytest.approx(f, rel=1e-9) and (part.df1, part.df2) == (d1, d2)
    assert part.p_value == pytest.approx(float(stats.f.sf(f, d1, d2)), rel=1e-9)


def test_heterogeneous_change_is_contradicted_and_equal_change_is_not():
    het = partition_frame(4, {"a": -0.4, "b": 0.0, "c": 0.0})
    r = uniform_test(het, all_days(het), PRE_W, POST_W)
    assert r.status == "contradicted" and "device" in r.detail and "not equal across segments" in r.detail
    assert r.adjustment.startswith("Holm")
    same = partition_frame(4, {"a": -0.2, "b": -0.2, "c": -0.2})
    r = uniform_test(same, all_days(same), PRE_W, POST_W)
    assert r.status == "not_contradicted" and "does not show the effect is uniform" in r.detail


def test_shared_day_effects_cancel_in_the_contrast_so_the_test_keeps_its_level_and_power():
    """With a strong day effect common to all segments, ignoring the covariance would inflate the variance
    of the contrasts. The full-covariance test stays at its nominal level under equality and is powerful
    under inequality."""
    reject_null = reject_alt = 0
    trials = 200
    for s in range(trials):
        null = partition_frame(s, {"a": -0.2, "b": -0.2, "c": -0.2}, day_sd=0.25)
        alt = partition_frame(10_000 + s, {"a": -0.3, "b": -0.1, "c": -0.2}, day_sd=0.25)
        reject_null += uniform_test(null, all_days(null), PRE_W, POST_W).status == "contradicted"
        reject_alt += uniform_test(alt, all_days(alt), PRE_W, POST_W).status == "contradicted"
    assert reject_null / trials <= 0.08, reject_null
    assert reject_alt / trials >= 0.85, reject_alt


def test_a_zero_rate_segment_is_excluded_and_listed_not_logged():
    df = partition_frame(6, {"a": -0.2, "b": -0.2, "dead": 0.0}, base={"dead": 0.0})
    r = uniform_test(df, all_days(df), PRE_W, POST_W)
    part = next(p for p in r.partitions if p.dimension == "device")
    assert part.status == "tested" and part.included == ["a", "b"] and "dead" in part.excluded
    assert "fewer than 5 pooled orders" in part.excluded["dead"]
    assert "nan" not in r.model_dump_json().lower()


def test_a_segment_with_too_few_orders_in_one_window_is_excluded():
    df = partition_frame(7, {"a": -0.2, "b": -0.2, "rare": -0.2}, base={"rare": 0.00004})
    part = next(p for p in uniform_test(df, all_days(df), PRE_W, POST_W).partitions if p.dimension == "device")
    assert "rare" in part.excluded and part.included == ["a", "b"]


def test_a_partition_with_fewer_than_two_testable_segments_is_skipped():
    df = partition_frame(8, {"a": -0.2, "dead": 0.0}, base={"dead": 0.0})
    r = uniform_test(df, all_days(df), PRE_W, POST_W)
    part = next(p for p in r.partitions if p.dimension == "device")
    assert part.status == "skipped" and "fewer than 2 segments" in part.reason and part.p_value is None


def test_when_every_partition_is_skipped_the_pattern_is_untested_and_contributes_nothing():
    df = partition_frame(9, {"only": -0.2})
    r = uniform_test(df, all_days(df), PRE_W, POST_W)
    assert r.status == "untested" and "contributes nothing" in r.detail and all(p.status == "skipped" for p in r.partitions)


def test_too_few_days_makes_the_pattern_untested():
    df = partition_frame(10, {"a": -0.2, "b": -0.2})
    r = uniform_test(df, all_days(df), PRE_W, (date(2026, 8, 21), date(2026, 8, 22)))
    assert r.status == "untested" and "need 3 in each window" in r.detail


def test_holm_adjustment_is_applied_across_the_tested_partitions():
    df = partition_frame(11, {"a": -0.4, "b": 0.0})
    r = uniform_test(df, all_days(df), PRE_W, POST_W)
    tested = [p for p in r.partitions if p.status == "tested"]
    assert tested and all(p.p_value_holm is not None and p.p_value_holm >= p.p_value for p in tested)


# ------------------------------------------------------------------ simulator calibration


@pytest.fixture(scope="module")
def sim_days():
    def run(seed: int, **kw):
        m, e = simulate(seed, **kw)
        q = check_data_quality(m, e)
        d = validate_metrics(m)
        usable = pd.DatetimeIndex(sorted(set(d["date"]) - {pd.Timestamp(x) for x in q.excluded_dates}))
        return d, usable
    return run


PRE_D = (date.fromisoformat(PRE[0]), date.fromisoformat(PRE[1]))
POST_D = (date.fromisoformat(POST[0]), date.fromisoformat(POST[1]))


def test_uniform_effect_is_rarely_contradicted_and_a_localized_one_always_is(sim_days):
    """Measured over 60 seeds: 1 false contradiction at -6% and at -12% (about 1.7%); 30/30 for the localized effect."""
    false_contradictions = 0
    for s in range(1, 21):
        d, u = sim_days(s, uniform=True, rel=-0.12)
        false_contradictions += uniform_test(d, u, PRE_D, POST_D).status == "contradicted"
    assert false_contradictions <= 3, false_contradictions
    for s in range(1, 11):
        d, u = sim_days(s)
        assert uniform_test(d, u, PRE_D, POST_D).status == "contradicted"


# ------------------------------------------------------------------ restricted scope


def test_restricted_pattern_is_contradicted_by_a_change_outside_its_scope(sim_days):
    d, u = sim_days(42)
    wrong = restricted_test(d, u, PRE_D, POST_D, {"device": "desktop"})
    assert wrong.status == "contradicted" and wrong.outside_change_pp.upper < 0 and "excludes 0" in wrong.detail
    right = restricted_test(d, u, PRE_D, POST_D, {"device": "mobile", "browser": "safari"})
    assert right.status == "not_contradicted" and not right.absence_claimed
    assert "no equivalence margin was declared, so absence is not claimed" in right.detail


def test_absence_is_claimed_only_inside_a_declared_margin(sim_days):
    d, u = sim_days(42)
    scope = {"device": "mobile", "browser": "safari"}
    ci = restricted_test(d, u, PRE_D, POST_D, scope).outside_change_pp
    inside = restricted_test(d, u, PRE_D, POST_D, scope, equivalence_margin_pp=1.0)
    assert inside.absence_claimed and inside.status == "not_contradicted" and "within the declared margin" in inside.detail
    tight = restricted_test(d, u, PRE_D, POST_D, scope, equivalence_margin_pp=0.05)
    assert not tight.absence_claimed and "not inside the declared margin" in tight.detail
    assert ci.lower <= 0 <= ci.upper
    assert ci.upper < 1.0 and ci.lower > -1.0


def test_a_contradicted_outside_effect_is_never_called_absent_even_with_a_huge_margin(sim_days):
    d, u = sim_days(42)
    r = restricted_test(d, u, PRE_D, POST_D, {"device": "desktop"}, equivalence_margin_pp=50.0)
    assert r.status == "contradicted" and not r.absence_claimed


def test_restricted_pattern_needs_traffic_outside_and_enough_days(sim_days):
    d, u = sim_days(42)
    everything = restricted_test(d, u, PRE_D, POST_D, {"device": "mobile"})
    assert everything.status in ("contradicted", "not_contradicted")  # traffic exists outside mobile
    no_outside = restricted_test(d.assign(device="mobile"), u, PRE_D, POST_D, {"device": "mobile"})
    assert no_outside.status == "untested" and "no traffic lies outside" in no_outside.detail
    short = restricted_test(d, u, PRE_D, (date(2026, 10, 14), date(2026, 10, 15)), {"device": "mobile"})
    assert short.status == "untested" and "need 3 in each window" in short.detail


def test_check_pattern_dispatches_on_the_declared_kind(sim_days):
    d, u = sim_days(42)
    assert check_pattern(ExpectedPattern(kind="uniform"), d, u, PRE_D, POST_D).kind == "uniform"
    r = check_pattern(ExpectedPattern(kind="restricted", scope={"device": "mobile", "browser": "safari"}, equivalence_margin_pp=1.0), d, u, PRE_D, POST_D)
    assert r.kind == "restricted" and r.absence_claimed
