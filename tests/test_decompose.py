from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from frames import DEFAULT_SEGMENT, day, segmented, to_frame
from statsmodels.stats.multitest import multipletests

from causelens.analytics import (
    DIMENSIONS,
    DataValidationError,
    confirm_change,
    decompose_change,
)

PRE = ("2026-08-01", "2026-10-13")
POST = ("2026-10-14", "2026-10-31")
W_PRE, W_POST = (day(0), day(13)), (day(14), day(27))  # synthetic frames: 14 days each


def seg(result, partition: str, label: str):
    part = next(p for p in result.partitions if p.name == partition)
    return next(s for s in part.segments if s.label == label)


def part(result, name: str):
    return next(p for p in result.partitions if p.name == name)


@pytest.fixture(scope="module")
def ref(reference):
    metrics, events = reference
    return decompose_change(metrics, PRE, POST, events)


# ------------------------------------------------------------------ the arithmetic, by hand


def test_pure_traffic_mix_shift_has_only_mix_effects():
    """A (4%) loses traffic to B (2%) at unchanged segment rates: R falls 3.0% -> 2.5%."""
    df = segmented([
        {"device": "desktop", "sessions": (1000, 500), "rate": (0.04, 0.04)},
        {"device": "mobile", "sessions": (1000, 1500), "rate": (0.02, 0.02)},
    ])
    r = decompose_change(df, W_PRE, W_POST)
    assert r.change_pp == pytest.approx(-0.5)
    d = part(r, "device")
    a, b = seg(r, "device", "device=desktop"), seg(r, "device", "device=mobile")
    assert a.rate_effect_pp == 0 and b.rate_effect_pp == 0
    # mix_A = (0.25 - 0.5) * (0.04 - 0.0275) ; mix_B = (0.75 - 0.5) * (0.02 - 0.0275)
    assert a.mix_effect_pp == pytest.approx(-0.3125) and b.mix_effect_pp == pytest.approx(-0.1875)
    assert d.sum_rate_effects_pp == 0 and d.sum_mix_effects_pp == pytest.approx(-0.5)
    assert d.reconciles and abs(d.residual_pp) < 1e-9
    assert a.share_change_pp == pytest.approx(-25.0) and b.share_change_pp == pytest.approx(25.0)
    # rates did not change, so no segment is flagged and there is no p-value below 1
    assert a.test.significant is False and b.test.significant is False
    assert a.test.p_value == pytest.approx(1.0)


def test_pure_rate_change_has_only_a_rate_effect():
    df = segmented([
        {"device": "desktop", "sessions": (1000, 1000), "rate": (0.04, 0.03)},
        {"device": "mobile", "sessions": (1000, 1000), "rate": (0.02, 0.02)},
    ])
    r = decompose_change(df, W_PRE, W_POST)
    a = seg(r, "device", "device=desktop")
    assert a.rate_effect_pp == pytest.approx(-0.5) and a.mix_effect_pp == pytest.approx(0, abs=1e-12)
    assert a.contribution_pp == pytest.approx(r.change_pp) and a.share_of_change == pytest.approx(1.0)
    assert a.rate_change_pp == pytest.approx(-1.0) and a.test.significant


def test_rate_and_mix_together_with_the_interaction_split_evenly():
    """A: share 50% -> 25%, rate 4% -> 3%; B: share 50% -> 75%, rate 2% steady. Hand-computed."""
    df = segmented([
        {"device": "desktop", "sessions": (1000, 500), "rate": (0.04, 0.03)},
        {"device": "mobile", "sessions": (1000, 1500), "rate": (0.02, 0.02)},
    ])
    r = decompose_change(df, W_PRE, W_POST)
    a, b = seg(r, "device", "device=desktop"), seg(r, "device", "device=mobile")
    assert r.change_pp == pytest.approx(-0.75)
    assert a.rate_effect_pp == pytest.approx(-0.375)  # mean share 0.375 x -1 pp
    assert a.mix_effect_pp == pytest.approx(-0.21875) and b.mix_effect_pp == pytest.approx(-0.15625)
    assert a.contribution_pp + b.contribution_pp == pytest.approx(-0.75)


def test_segment_present_in_one_window_is_all_mix_effect_and_untested():
    df = segmented([
        {"device": "desktop", "sessions": (1000, 1000), "rate": (0.03, 0.03)},
        {"device": "mobile", "sessions": (0, 1000), "rate": (0.0, 0.01)},  # appears after the split
    ])
    r = decompose_change(df, W_PRE, W_POST)
    new = seg(r, "device", "device=mobile")
    assert new.status == "only_post" and new.rate_effect_pp == 0 and new.rate_change_pp is None
    assert new.conversion_pre is None and new.test is None and new.share_pre == 0
    assert new.mix_effect_pp != 0 and part(r, "device").n_tests == 1
    assert part(r, "device").reconciles and r.change_pp == pytest.approx(-1.0)
    assert any("only one window" in a for a in r.assumptions)


def test_segment_that_disappears_is_all_mix_effect():
    df = segmented([
        {"device": "desktop", "sessions": (1000, 1000), "rate": (0.03, 0.03)},
        {"device": "mobile", "sessions": (1000, 0), "rate": (0.01, 0.0)},
    ])
    r = decompose_change(df, W_PRE, W_POST)
    gone = seg(r, "device", "device=mobile")
    assert gone.status == "only_pre" and gone.conversion_post is None and gone.share_post == 0
    assert gone.rate_effect_pp == 0 and part(r, "device").reconciles


def test_dimension_with_one_value_carries_the_whole_change_as_rate_effect():
    df = segmented([{"sessions": (1000, 1000), "rate": (0.04, 0.03)}])
    r = decompose_change(df, W_PRE, W_POST, dimensions=["channel"], include_pairs=False)
    (s,) = part(r, "channel").segments
    assert s.rate_effect_pp == pytest.approx(r.change_pp) and s.mix_effect_pp == pytest.approx(0, abs=1e-15)
    assert s.share_pre == s.share_post == 1.0


def test_offsetting_segments_give_no_net_change_and_no_explaining_set():
    df = segmented([
        {"device": "desktop", "sessions": (1000, 1000), "rate": (0.04, 0.03)},
        {"device": "mobile", "sessions": (1000, 1000), "rate": (0.02, 0.03)},
    ])
    r = decompose_change(df, W_PRE, W_POST)
    assert abs(r.change_pp) < 1e-9
    p = part(r, "device")
    assert p.explaining_set is None and "did not change" in p.explaining_set_unavailable_reason
    assert all(s.share_of_change is None for s in p.segments)
    assert r.localization is None and "No segment can be singled out" in r.summary
    assert r.localization_note and "did not change" in r.localization_note
    assert seg(r, "device", "device=desktop").rate_effect_pp == pytest.approx(-0.5)  # movements still shown


# ------------------------------------------------------------------ ranking and the explaining set


def _three_segments(c_rate=(0.02, 0.02)):
    return segmented([
        {"device": "desktop", "sessions": (1000, 1000), "rate": (0.05, 0.03)},  # -1.0 pp x 1/3
        {"device": "mobile", "sessions": (1000, 1000), "rate": (0.04, 0.03)},  # -1.0 pp x 1/3 (smaller share below)
        {"device": "tablet", "sessions": (1000, 1000), "rate": c_rate},
    ])


def test_segments_are_ranked_by_contribution_in_the_direction_of_the_change():
    df = segmented([
        {"device": "desktop", "sessions": (1000, 1000), "rate": (0.05, 0.03)},
        {"device": "mobile", "sessions": (1000, 1000), "rate": (0.04, 0.035)},
        {"device": "tablet", "sessions": (1000, 1000), "rate": (0.02, 0.03)},  # moves against the change
    ])
    r = decompose_change(df, W_PRE, W_POST)
    labels = [s.label for s in part(r, "device").segments]
    assert labels == ["device=desktop", "device=mobile", "device=tablet"]
    assert [s.rank for s in part(r, "device").segments] == [1, 2, 3]
    assert seg(r, "device", "device=tablet").contribution_pp > 0 > r.change_pp  # opposes the change


def test_explaining_set_is_the_smallest_prefix_reaching_the_threshold():
    r = decompose_change(_three_segments(), W_PRE, W_POST, explain_threshold=0.5)
    es = part(r, "device").explaining_set
    # desktop: -2 pp x 1/3 = -0.667 pp of the -1.0 pp change; mobile: -1 pp x 1/3 = -0.333 pp; tablet: 0
    assert es.k == 1 and es.segments == ["device=desktop"]
    assert es.selected_share_of_change == pytest.approx(2 / 3)
    assert es.rate_effect_pp == pytest.approx(-2 / 3) and es.traffic_share_post == pytest.approx(1 / 3)
    full = decompose_change(_three_segments(), W_PRE, W_POST, explain_threshold=1.0)
    es_full = part(full, "device").explaining_set
    assert es_full.k == 2 and es_full.segments == ["device=desktop", "device=mobile"]
    assert es_full.selected_share_of_change == pytest.approx(1.0)


def test_over_100_percent_is_reported_not_capped_when_other_segments_offset():
    df = segmented([
        {"device": "desktop", "sessions": (1000, 1000), "rate": (0.05, 0.03)},
        {"device": "mobile", "sessions": (1000, 1000), "rate": (0.02, 0.03)},
    ])
    r = decompose_change(df, W_PRE, W_POST)
    es = part(r, "device").explaining_set
    assert es.segments == ["device=desktop"] and es.selected_share_of_change > 1
    assert es.remaining_share_of_change == pytest.approx(1 - es.selected_share_of_change)
    assert es.remaining_share_of_change < 0
    assert "other segments moved the opposite way" in es.note


def test_tied_segments_share_a_rank_and_are_selected_together():
    df = segmented([
        {"device": "desktop", "sessions": (1000, 1000), "rate": (0.04, 0.03)},
        {"device": "mobile", "sessions": (1000, 1000), "rate": (0.04, 0.03)},
        {"device": "tablet", "sessions": (1000, 1000), "rate": (0.04, 0.04)},
    ])
    r = decompose_change(df, W_PRE, W_POST, explain_threshold=0.4)
    ranks = {s.label: s.rank for s in part(r, "device").segments}
    assert ranks["device=desktop"] == ranks["device=mobile"] == 1 and ranks["device=tablet"] == 3
    assert sorted(part(r, "device").explaining_set.segments) == ["device=desktop", "device=mobile"]


def test_significance_of_selected_segments_is_reported_honestly():
    # B's traffic grows (mix only); B's own rate is unchanged, so it is not significant.
    df = segmented([
        {"device": "desktop", "sessions": (1000, 500), "rate": (0.04, 0.04)},
        {"device": "mobile", "sessions": (1000, 1500), "rate": (0.02, 0.02)},
    ])
    r = decompose_change(df, W_PRE, W_POST)
    es = part(r, "device").explaining_set
    assert not es.all_selected_significant and "may reflect a traffic-mix shift" in es.note


def test_a_mix_shift_is_not_localized_to_a_one_value_partition_or_to_all_traffic():
    """With one value for browser, 'browser=chrome' holds all traffic and its pooled rate moved: it
    must not be reported as where the change is concentrated."""
    df = segmented([
        {"device": "desktop", "sessions": (1000, 500), "rate": (0.04, 0.04)},
        {"device": "mobile", "sessions": (1000, 1500), "rate": (0.02, 0.02)},
    ])
    r = decompose_change(df, W_PRE, W_POST)
    chrome = part(r, "browser").explaining_set
    assert chrome.segments == ["browser=chrome"] and chrome.traffic_share_post == pytest.approx(1.0)
    assert r.localization is None
    assert "spread across traffic" in r.localization_note and "broad shift" in r.localization_note
    assert "broad shift" in r.summary


def test_a_uniform_drop_across_every_segment_is_reported_as_broad():
    df = segmented([
        {"device": "desktop", "sessions": (1000, 1000), "rate": (0.04, 0.03)},
        {"device": "mobile", "sessions": (1000, 1000), "rate": (0.04, 0.03)},
        {"device": "tablet", "sessions": (1000, 1000), "rate": (0.04, 0.03)},
    ])
    r = decompose_change(df, W_PRE, W_POST)
    assert r.localization is None and "broad shift" in r.localization_note
    assert all(s.test.significant for s in part(r, "device").segments)  # each moved, none stands out
    assert part(r, "device").explaining_set.k >= 3 - 1  # needs most of the traffic to reach 80%


def test_a_single_local_break_is_reported_as_local_and_the_traffic_limit_is_adjustable():
    df = segmented([
        {"device": "desktop", "sessions": (1000, 1000), "rate": (0.04, 0.04)},
        {"device": "mobile", "sessions": (1000, 1000), "rate": (0.04, 0.04)},
        {"device": "tablet", "sessions": (1000, 1000), "rate": (0.04, 0.02)},
    ])
    r = decompose_change(df, W_PRE, W_POST)
    assert r.localization.segments == ["device=tablet"] and r.localization.traffic_share_post == pytest.approx(1 / 3)
    assert r.localization.partition == "device" and r.localization.all_selected_significant
    strict = decompose_change(df, W_PRE, W_POST, max_localized_traffic_share=0.2)
    assert strict.localization is None and "at most 20%" in strict.localization_note


def test_localization_prefers_significant_then_fewest_then_narrowest():
    """device (k=1, 1/3 of traffic) and device x channel with the same set: ties on k and traffic fall
    back to the partition name, so the result is deterministic."""
    df = segmented([
        {"device": "desktop", "sessions": (1000, 1000), "rate": (0.04, 0.04)},
        {"device": "mobile", "sessions": (1000, 1000), "rate": (0.04, 0.04)},
        {"device": "tablet", "sessions": (1000, 1000), "rate": (0.04, 0.02)},
    ])
    a = decompose_change(df, W_PRE, W_POST)
    b = decompose_change(df.sample(frac=1.0, random_state=1).reset_index(drop=True), W_PRE, W_POST)
    assert a.localization == b.localization


def test_the_traffic_limit_is_validated(reference):
    for bad in (0.0, 1.5):
        with pytest.raises(ValueError, match="max_localized_traffic_share"):
            decompose_change(reference[0], PRE, POST, max_localized_traffic_share=bad)


# ------------------------------------------------------------------ reference data


def test_every_partition_reconciles_to_the_overall_change(ref):
    assert ref.status == "ok" and len(ref.partitions) == 10
    for p in ref.partitions:
        assert p.reconciles and abs(p.residual_pp) <= 1e-9
        assert p.sum_rate_effects_pp + p.sum_mix_effects_pp == pytest.approx(ref.change_pp, abs=1e-9)
        assert sum(s.contribution_pp for s in p.segments) == pytest.approx(ref.change_pp, abs=1e-9)
        assert sum(s.share_pre for s in p.segments) == pytest.approx(1.0, abs=1e-12)
        assert sum(s.share_post for s in p.segments) == pytest.approx(1.0, abs=1e-12)
        assert sum(s.sessions_pre for s in p.segments) == ref.pre.sessions
        assert sum(s.orders_post for s in p.segments) == ref.post.orders


def test_partitions_are_each_dimension_and_each_pair_in_order(ref):
    names = [p.name for p in ref.partitions]
    assert names[:4] == list(DIMENSIONS)
    assert len(names) == 4 + 6 and len(set(names)) == 10
    assert "device × browser" in names and "browser × device" not in names


def test_overall_change_matches_an_independent_pandas_computation(reference, ref):
    metrics, _ = reference
    d = metrics.assign(date=pd.to_datetime(metrics["date"]))
    d = d[d["date"] != pd.Timestamp("2026-09-20")]  # the flagged day, dropped for every segment
    pre = d[d["date"] <= pd.Timestamp("2026-10-13")]
    post = d[d["date"] >= pd.Timestamp("2026-10-14")]
    expected = 100 * (post["orders"].sum() / post["sessions"].sum() - pre["orders"].sum() / pre["sessions"].sum())
    assert ref.change_pp == pytest.approx(expected)


def test_change_equals_the_pooled_change_reported_by_confirm_change(reference, ref):
    metrics, events = reference
    c = confirm_change(metrics, PRE, POST, events)
    assert ref.change_pp == pytest.approx(c.abs_change_pp.estimate)
    assert ref.conversion_pre == pytest.approx(c.pre.conversion.estimate)
    assert ref.pre.n_days_used == c.pre.n_days_used and ref.post.sessions == c.post.sessions


def test_localization_is_computed_and_agrees_with_the_planted_segment(ref, ground_truth):
    """Ground truth is used only to grade: the segments come from the data."""
    planted = ground_truth["planted_cause"]["affected_segment"]
    label = ", ".join(f"{k}={v}" for k, v in planted.items())
    loc = ref.localization
    assert loc is not None and loc.segments == [label] and loc.partition == "device × browser"
    assert loc.all_selected_significant and loc.selected_share_of_change >= 0.8
    top = part(ref, "device × browser").segments[0]
    assert top.label == label and top.rank == 1 and top.test.significant
    assert "concentrated in" in loc.statement and "does not say why" in loc.statement


def test_planted_segment_interval_covers_the_planted_rate_change(ref, ground_truth):
    truth = ground_truth["planted_cause"]["true_effect_in_segment_pp"]
    ci = part(ref, "device × browser").segments[0].test.change_pp
    assert ci.lower <= truth <= ci.upper and ci.level == 0.95


def test_unaffected_segments_are_not_distinguishable_from_noise(ref, ground_truth):
    planted = ground_truth["planted_cause"]["affected_segment"]
    target = ", ".join(f"{k}={v}" for k, v in planted.items())
    others = [s for s in part(ref, "device × browser").segments if s.label != target]
    assert others and all(not s.test.significant for s in others)
    assert all(abs(s.rate_effect_pp) < 0.02 for s in others)  # near-zero contributions


def test_no_unaffected_segment_carries_a_significant_rate_effect(ref):
    # Within device x browser the significant rate effects are just the affected segment's; the rest
    # (not distinguishable from noise) sum to almost nothing.
    p = part(ref, "device × browser")
    assert p.rate_effect_significant_pp == pytest.approx(p.segments[0].rate_effect_pp)
    assert abs(p.rate_effect_not_distinguishable_pp) < 0.03


def test_fdr_info_and_separate_q_values_are_reported(ref):
    assert ref.fdr_family == "scan" and ref.n_tests_scanned == sum(p.n_tests for p in ref.partitions) == 71
    t = seg(ref, "device × browser", "device=mobile, browser=safari").test
    assert t.q_value == t.q_value_scan and t.q_value_partition <= 1 and t.q_value < 0.05
    assert "Benjamini-Hochberg" in ref.summary and "71" in ref.summary


def test_q_values_equal_an_independent_bh_over_the_scan_and_per_partition(ref):
    tested = [(p.name, s) for p in ref.partitions for s in p.segments if s.test is not None and s.test.reliable]
    ps = [s.test.p_value for _, s in tested]
    expected = multipletests(ps, method="fdr_bh")[1]
    assert np.allclose([s.test.q_value_scan for _, s in tested], expected, rtol=1e-9, atol=1e-300)
    for p in ref.partitions:
        pp = [s.test.p_value for s in p.segments if s.test is not None]
        got = [s.test.q_value_partition for s in p.segments if s.test is not None]
        assert np.allclose(got, multipletests(pp, method="fdr_bh")[1], rtol=1e-9, atol=1e-300)


def test_partition_family_switches_the_primary_q_value(reference, ref):
    metrics, events = reference
    r = decompose_change(metrics, PRE, POST, events, fdr_family="partition")
    for p in r.partitions:
        for s in p.segments:
            if s.test is not None:
                assert s.test.q_value == s.test.q_value_partition
    assert r.fdr_family == "partition" and "each partition" in r.summary
    assert ref.change_pp == r.change_pp  # the accounting does not depend on the testing family


def test_segment_test_is_recomputed_independently_from_daily_counts(reference, ref):
    """Days are the sampling units: rebuild one segment's day-level test with plain numpy from the raw rows."""
    from scipy import stats as sp

    metrics, _ = reference
    d = metrics.assign(date=pd.to_datetime(metrics["date"]))
    d = d[(d["device"] == "mobile") & (d["browser"] == "safari")]
    daily = d.groupby("date")[["sessions", "orders"]].sum()

    def window(start, end):
        days = pd.date_range(start, end)
        days = days[days != pd.Timestamp("2026-09-20")]  # the day Task 2 excluded, for every segment
        got = daily.reindex(days).fillna(0.0)
        return got["sessions"].to_numpy(), got["orders"].to_numpy()

    s0, o0 = window(*PRE)
    s1, o1 = window(*POST)
    r0, r1 = o0.sum() / s0.sum(), o1.sum() / s1.sum()
    v0 = len(s0) / (len(s0) - 1) * np.sum((o0 - r0 * s0) ** 2) / s0.sum() ** 2
    v1 = len(s1) / (len(s1) - 1) * np.sum((o1 - r1 * s1) ** 2) / s1.sum() ** 2
    se, df = np.sqrt(v0 + v1), (v0 + v1) ** 2 / (v0**2 / (len(s0) - 1) + v1**2 / (len(s1) - 1))
    t = seg(ref, "device × browser", "device=mobile, browser=safari").test
    assert t.basis == "day_level_welch_t" and t.reliable and t.note is None
    assert t.day_level.t_statistic == pytest.approx((r1 - r0) / se) and t.day_level.degrees_of_freedom == pytest.approx(df)
    assert t.p_value == pytest.approx(2 * sp.t.sf(abs(r1 - r0) / se, df), rel=1e-6)
    crit = sp.t.ppf(0.975, df)
    assert t.change_pp.lower == pytest.approx(100 * (r1 - r0 - crit * se)) and t.change_pp.upper == pytest.approx(100 * (r1 - r0 + crit * se))
    assert (t.day_level.n_days_pre, t.day_level.n_days_post) == (len(s0), len(s1))
    assert t.day_level.design_effect_post > 1  # sessions are not independent trials: the plain test was optimistic


def test_every_reliable_segment_reports_its_basis_and_the_plain_p_value_for_comparison(ref):
    for p in ref.partitions:
        for s in p.segments:
            if s.test is not None:
                assert s.test.reliable and s.test.basis == "day_level_welch_t" and s.test.p_value is not None
                assert 0 <= s.test.binomial.p_value <= 1 and s.test.q_value is not None
                assert s.test.day_level.design_effect_pre is None or s.test.day_level.design_effect_pre > 0


def test_inference_binomial_uses_the_plain_test_and_says_it_is_optimistic(reference, ref):
    metrics, events = reference
    r = decompose_change(metrics, PRE, POST, events, inference="binomial")
    assert r.inference == "binomial"
    for p in r.partitions:
        for s in p.segments:
            if s.test is not None:
                assert s.test.basis == "binomial_z" and s.test.day_level is None
                assert s.test.p_value == s.test.binomial.p_value
                assert s.test.note and "optimistic" in s.test.note
    assert any("optimistic" in a for a in r.assumptions) and "plain binomial" in r.summary
    assert r.change_pp == ref.change_pp  # accounting does not depend on the test


def test_windows_with_too_few_days_withhold_every_day_level_test_and_say_so():
    df = segmented([
        {"device": "desktop", "sessions": (1000, 1000), "rate": (0.04, 0.03)},
        {"device": "mobile", "sessions": (1000, 1000), "rate": (0.02, 0.02)},
    ], n_days=2, split=1)
    r = decompose_change(df, (day(0), day(0)), (day(1), day(1)), min_days_per_window=1)
    for s_ in part(r, "device").segments:
        t = s_.test
        assert not t.reliable and t.unreliable_reason == "too_few_active_days" and t.basis == "none"
        assert t.p_value is None and t.q_value is None and t.significant is False
        assert "Too few days" in t.note and (t.active_days_pre, t.active_days_post) == (1, 1)
    assert r.n_tests_scanned == 0 and r.n_unreliable_tests > 0
    assert any("fewer than 3 days with sessions" in a for a in r.assumptions)
    assert part(r, "device").reconciles  # the accounting does not need a test
    # asking for the plain test is an explicit, labelled choice
    plain = decompose_change(df, (day(0), day(0)), (day(1), day(1)), min_days_per_window=1, inference="binomial")
    t = seg(plain, "device", "device=desktop").test
    assert t.reliable and t.basis == "binomial_z" and "optimistic" in t.note


# ------------------------------------------------------------------ exclusions and windows are disclosed


def test_comparison_windows_and_excluded_dates_are_disclosed(ref):
    assert (ref.pre.start, ref.pre.end, ref.post.start, ref.post.end) == (
        date(2026, 8, 1), date(2026, 10, 13), date(2026, 10, 14), date(2026, 10, 31),
    )
    assert ref.pre.excluded_dates == [date(2026, 9, 20)] and ref.pre.n_days_excluded == 1
    assert ref.post.excluded_dates == [] and ref.pre.n_days_used == 72 + 1 and ref.post.n_days_used == 18
    assert ref.exclusions.applied and [d.date for d in ref.exclusions.excluded_days] == [date(2026, 9, 20)]
    assert ref.exclusions.excluded_days[0].windows == ["pre"]
    assert "2026-09-20 (pre)" in ref.summary
    assert any("Comparison windows: before = 2026-08-01 to 2026-10-13" in a for a in ref.assumptions)


def test_excluding_the_flagged_day_removes_all_segments_that_day_and_changes_the_baseline(reference, ref):
    metrics, events = reference
    off = decompose_change(metrics, PRE, POST, events, apply_exclusions=False)
    assert off.pre.n_days_excluded == 0 and off.pre.sessions > ref.pre.sessions
    assert off.conversion_pre < ref.conversion_pre  # the tracking loss depresses the baseline
    assert "not excluded" in off.summary.lower() and not off.exclusions.applied
    # the outage day lowers desktop's pre-window rate, so its measured post-minus-pre change is larger
    assert seg(off, "device", "device=desktop").rate_change_pp > seg(ref, "device", "device=desktop").rate_change_pp
    assert off.change_pp > ref.change_pp  # the depressed baseline shrinks the apparent drop


def test_weekday_mix_difference_is_reported_and_caveated():
    df = to_frame(np.full(28, 1000), np.full(28, 30))
    r = decompose_change(df, (day(0), day(12)), (day(14), day(27)), dimensions=["device"], include_pairs=False)
    assert r.weekday_mix_difference is not None and r.weekday_mix_difference > 0.0


def test_uncertain_historical_incident_context_is_carried_through():
    df = to_frame(np.full(60, 1000), np.full(60, 30))
    df.loc[30, ["sessions", "orders"]] = 0
    events = pd.DataFrame(
        {"date": [day(30).isoformat()], "type": ["incident"], "name": ["Collector outage"], "scope": ["device=desktop"]}
    )
    r = decompose_change(df, (day(0), day(29)), (day(31), day(59)), events)
    assert r.exclusions.excluded_days[0].incident_context[0].overlap_basis == "historical_only"
    assert any("uncertain context" in a for a in r.assumptions)


# ------------------------------------------------------------------ insufficient data and invalid input


def test_insufficient_data_after_exclusions_is_explicit(reference):
    metrics, events = reference
    r = decompose_change(metrics, ("2026-09-14", "2026-09-20"), ("2026-09-21", "2026-09-27"), events)
    assert r.status == "insufficient_data" and r.partitions == [] and r.change_pp is None
    assert r.localization is None and r.pre.n_days_used == 6 and r.pre.excluded_dates == [date(2026, 9, 20)]
    assert "pre window has 6 usable day(s) of 7 requested (1 excluded as flagged" in r.summary
    assert "No decomposition is reported" in r.summary


def test_window_outside_the_data_is_insufficient_not_an_error(reference):
    r = decompose_change(reference[0], ("2026-06-01", "2026-06-30"), POST)
    assert r.status == "insufficient_data" and r.pre.n_days_used == 0 and r.pre.n_days_missing == 30


def test_dimension_subsets_and_pair_switch(reference):
    metrics, events = reference
    only = decompose_change(metrics, PRE, POST, events, dimensions=["device", "browser"], include_pairs=False)
    assert [p.name for p in only.partitions] == ["device", "browser"]
    pair = decompose_change(metrics, PRE, POST, events, dimensions=["browser", "device"])
    assert [p.name for p in pair.partitions] == ["browser", "device", "browser × device"]


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"dimensions": ["colour"]}, "distinct names"),
        ({"dimensions": ["device", "device"]}, "distinct names"),
        ({"dimensions": []}, "distinct names"),
        ({"alpha": 0.0}, "alpha"),
        ({"alpha": 0.9}, "alpha"),
        ({"level": 1.0}, "level"),
        ({"explain_threshold": 0.0}, "explain_threshold"),
        ({"explain_threshold": 1.5}, "explain_threshold"),
        ({"fdr_family": "global"}, "fdr_family"),
        ({"inference": "robust"}, "inference"),
        ({"min_days_per_window": 0}, "min_days_per_window"),
    ],
)
def test_invalid_arguments_are_rejected(reference, kwargs, match):
    with pytest.raises(ValueError, match=match):
        decompose_change(reference[0], PRE, POST, **kwargs)


@pytest.mark.parametrize(
    ("pre", "post", "match"),
    [
        (("2026-09-10", "2026-09-01"), POST, "starts .* after it ends"),
        (("2026-09-01", "2026-10-20"), POST, "must end before"),
        (("nope", "2026-09-30"), POST, "must be"),
    ],
)
def test_invalid_windows_are_rejected(reference, pre, post, match):
    with pytest.raises(ValueError, match=match):
        decompose_change(reference[0], pre, post)


def test_invalid_metrics_raise_the_typed_error():
    bad = to_frame(np.full(20, 100), np.full(20, 5)).assign(orders=-1)
    with pytest.raises(DataValidationError):
        decompose_change(bad, (day(0), day(9)), (day(10), day(19)))


# ------------------------------------------------------------------ determinism, wording, no hard-coding


def test_result_is_deterministic_and_json_safe(reference, ref):
    metrics, events = reference
    again = decompose_change(metrics, PRE, POST, events)
    assert again.model_dump() == ref.model_dump()
    text = ref.model_dump_json()
    assert "NaN" not in text and "Infinity" not in text
    assert json.loads(text)["partitions"][0]["segments"][0]["labels"]


def test_wording_never_claims_causation(ref):
    texts = [ref.summary, *ref.assumptions, ref.localization.statement]
    texts += [p.explaining_set.note for p in ref.partitions if p.explaining_set]
    joined = " ".join(texts).lower()
    for banned in ("caused by", "because of", "due to", "led to", "is the cause", "responsible for"):
        assert banned not in joined
    assert "not causal effects" in joined and "does not say why" in joined


def test_analytics_source_contains_no_planted_answer():
    """The decomposition must discover the segment; nothing about the scenario may be hard-coded."""
    src = Path(__file__).resolve().parents[1] / "causelens" / "analytics" / "decompose.py"
    code = src.read_text()
    for needle in ("safari", "mobile", "desktop", "tablet", "Checkout", "Homepage", "ground_truth"):
        assert needle.lower() not in code.lower(), needle
    assert not re.search(r"20\d\d-\d\d-\d\d", code)


# ------------------------------------------------------------------ PR #4 review regressions
# Finding 1: a failed greedy selection must not be reported as a broad shift.


def _desktop_ranks_first_but_mobile_is_narrower():
    """Desktop (60% of traffic) has the largest contribution; mobile alone (30%) has 86% of the change;
    tablet moves the other way. Numbers are exact so the contributions can be checked by hand."""
    return segmented([
        {"device": "desktop", "sessions": (600, 600), "rate": (0.05, 0.026)},
        {"device": "mobile", "sessions": (300, 300), "rate": (0.05, 0.01)},
        {"device": "tablet", "sessions": (100, 100), "rate": (0.03, 0.15)},
    ])


def _hand_contributions():
    """Independent shift-share arithmetic with plain Python (no use of decompose_change).

    Orders are whole numbers per day, so desktop's after-rate is 16/600, not the nominal 0.026."""
    pre = {"desktop": (0.6, 30 / 600), "mobile": (0.3, 15 / 300), "tablet": (0.1, 3 / 100)}
    post = {"desktop": (0.6, 16 / 600), "mobile": (0.3, 3 / 300), "tablet": (0.1, 15 / 100)}
    r0 = sum(w * r for w, r in pre.values())
    r1 = sum(w * r for w, r in post.values())
    r_bar = (r0 + r1) / 2
    out = {}
    for name, (w0, a) in pre.items():
        w1, b = post[name]
        out[name] = 100 * ((w0 + w1) / 2 * (b - a) + (w1 - w0) * ((a + b) / 2 - r_bar))
    return 100 * (r1 - r0), out


def test_a_qualifying_combination_is_found_when_the_top_ranked_set_is_too_broad():
    r = decompose_change(_desktop_ranks_first_but_mobile_is_narrower(), W_PRE, W_POST, dimensions=["device"], include_pairs=False)
    change, hand = _hand_contributions()
    p = part(r, "device")
    for s in p.segments:
        assert s.contribution_pp == pytest.approx(hand[s.label.split("=")[1]], abs=1e-9)
    assert r.change_pp == pytest.approx(change)
    ranked = p.explaining_set
    assert ranked.kind == "ranked_prefix" and ranked.segments == ["device=desktop"]
    assert ranked.traffic_share_post == pytest.approx(0.6)  # fails the 50% limit
    local = p.local_set
    assert local.kind == "traffic_limited_search" and local.segments == ["device=mobile"]
    assert local.traffic_share_post == pytest.approx(0.3) and local.selected_share_of_change >= 0.8
    assert p.local_search_exhaustive and p.local_set_unavailable_reason is None
    assert r.localization.segments == ["device=mobile"] and r.localization_note is None
    assert "spread across traffic" not in r.summary and "broad shift" not in r.summary


def test_localization_does_not_depend_on_which_segment_ranks_first():
    base = decompose_change(_desktop_ranks_first_but_mobile_is_narrower(), W_PRE, W_POST, dimensions=["device"], include_pairs=False)
    swapped = segmented([
        {"device": "mobile", "sessions": (600, 600), "rate": (0.05, 0.026)},
        {"device": "desktop", "sessions": (300, 300), "rate": (0.05, 0.01)},
        {"device": "tablet", "sessions": (100, 100), "rate": (0.03, 0.15)},
    ])
    other = decompose_change(swapped, W_PRE, W_POST, dimensions=["device"], include_pairs=False)
    assert base.localization.segments == ["device=mobile"] and other.localization.segments == ["device=desktop"]
    assert base.localization.traffic_share_post == other.localization.traffic_share_post


def test_the_traffic_limit_decides_between_qualifying_combinations():
    df = _desktop_ranks_first_but_mobile_is_narrower()
    loose = decompose_change(df, W_PRE, W_POST, dimensions=["device"], include_pairs=False, max_localized_traffic_share=0.7)
    # desktop (60%) and mobile (30%) now both qualify with one segment; the narrower one is preferred
    assert loose.partitions[0].explaining_set.segments == ["device=desktop"]
    assert loose.partitions[0].local_set.segments == ["device=mobile"]
    tight = decompose_change(df, W_PRE, W_POST, dimensions=["device"], include_pairs=False, max_localized_traffic_share=0.2)
    assert tight.localization is None and "broad shift" in tight.localization_note
    assert "every combination" in tight.localization_note and tight.partitions[0].local_search_exhaustive


def test_broad_shift_is_only_claimed_when_the_search_was_exhaustive():
    df = segmented([
        {"browser": b, "channel": c, "sessions": (1000 + 7 * i, 1000 + 7 * i), "rate": (0.04, 0.03)}
        for i, (b, c) in enumerate((b, c) for b in ["b1", "b2", "b3", "b4"] for c in ["c1", "c2", "c3", "c4"])
    ])
    r = decompose_change(df, W_PRE, W_POST, dimensions=["browser", "channel"])
    pair = part(r, "browser × channel")
    assert pair.n_segments == 16 and not pair.local_search_exhaustive and pair.local_set is None
    assert "largest positive contributors" in pair.local_set_unavailable_reason
    assert r.localization is None
    assert "does not by itself establish a broad shift" in r.localization_note
    assert "consistent with a broad shift" not in r.localization_note


def test_ranked_prefix_is_still_the_smallest_set_without_a_traffic_limit():
    r = decompose_change(_desktop_ranks_first_but_mobile_is_narrower(), W_PRE, W_POST, dimensions=["device"],
                         include_pairs=False, max_localized_traffic_share=1.0)
    p = part(r, "device")
    # with no effective limit the ranked prefix is feasible, so the search cannot need more segments than it
    assert p.explaining_set.k == 1 and p.local_set.k == p.explaining_set.k
    assert p.explaining_set.segments == ["device=desktop"] and p.local_set.traffic_share_post <= p.explaining_set.traffic_share_post


# Finding 3: sparse arms must not be reported as significant.


def _sparse_arm_frame():
    """Mobile: 0 orders in 1 session before, 900 in 1,000 after. Desktop: large and unchanged."""
    desktop = segmented([{"device": "desktop", "sessions": (1000, 1000), "rate": (0.03, 0.03)}])
    pre = pd.DataFrame([{**desktop.iloc[0].to_dict(), "device": "mobile", "sessions": 1, "orders": 0}])
    post_days = pd.date_range(day(14), periods=10).strftime("%Y-%m-%d")
    post = pd.DataFrame([{**desktop.iloc[0].to_dict(), "device": "mobile", "date": d, "sessions": 100, "orders": 90}
                         for d in post_days])
    return pd.concat([desktop, pre, post], ignore_index=True)


def test_a_segment_with_a_sparse_arm_gets_no_significance_claim():
    """Previously small_sample=False and q = 0.0056; Fisher's exact p is 0.10."""
    from scipy import stats as sp

    r = decompose_change(_sparse_arm_frame(), W_PRE, W_POST, dimensions=["device"], include_pairs=False, apply_exclusions=False)
    m = seg(r, "device", "device=mobile")
    assert (m.sessions_pre, m.orders_pre, m.sessions_post, m.orders_post) == (1, 0, 1000, 900)
    t = m.test
    assert t is not None and not t.reliable and t.basis == "none" and t.day_level is None
    assert t.p_value is None and t.q_value is None and t.q_value_scan is None and t.q_value_partition is None
    assert t.significant is False
    assert t.p_value_exact == pytest.approx(sp.fisher_exact([[0, 1], [900, 100]]).pvalue) == pytest.approx(0.100899, abs=1e-5)
    assert t.binomial.small_sample and t.binomial.p_value < 0.01  # the approximation that would have misled
    assert "Too sparse" in t.note and "No significance is claimed" in t.note and "information only" in t.note


def test_sparse_segments_are_left_out_of_the_fdr_family_but_still_counted_in_the_accounting():
    r = decompose_change(_sparse_arm_frame(), W_PRE, W_POST, dimensions=["device"], include_pairs=False, apply_exclusions=False)
    p = part(r, "device")
    assert p.n_unreliable == 1 and p.n_tests == 1 and r.n_unreliable_tests >= 1
    assert r.n_tests_scanned == sum(q.n_tests for q in r.partitions)
    reliable = [s for q in r.partitions for s in q.segments if s.test is not None and s.test.reliable]
    expected = multipletests([s.test.p_value for s in reliable], method="fdr_bh")[1]
    assert np.allclose([s.test.q_value_scan for s in reliable], expected)
    assert p.reconciles and seg(r, "device", "device=mobile").contribution_pp != 0  # its movement is still accounted for
    assert any("cannot be tested reliably" in a for a in r.assumptions)
    assert "segment(s) with too little data were not tested" in r.summary
    assert p.rate_effect_significant_pp == pytest.approx(
        sum(s.rate_effect_pp for s in p.segments if s.test is not None and s.test.significant)
    )


def test_a_sparse_segment_cannot_be_what_makes_a_localization_significant():
    r = decompose_change(_sparse_arm_frame(), W_PRE, W_POST, dimensions=["device"], include_pairs=False,
                         apply_exclusions=False, explain_threshold=0.5)
    loc = r.localization
    if loc is not None and "device=mobile" in loc.segments:
        assert not loc.all_selected_significant
        assert "no reliable test" in r.summary or "traffic-mix shift" in r.summary


@pytest.mark.parametrize(("pre", "post"), [((1, 0), (1000, 900)), ((3, 1), (500, 40)), ((40, 0), (60, 3)), ((0, 0), (200, 0))])
def test_the_reliability_rule_follows_the_smallest_expected_cell(pre, post):
    s0, c0 = pre
    s1, c1 = post
    pooled = (c0 + c1) / (s0 + s1)
    expected_min = min(s0 * pooled, s0 * (1 - pooled), s1 * pooled, s1 * (1 - pooled))
    df = segmented([{"device": "desktop", "sessions": (2000, 2000), "rate": (0.03, 0.03)}])
    extra = []
    for sessions, orders, days in ((s0, c0, [day(0)]), (s1, c1, [day(14)])):
        if sessions:
            extra.append({**df.iloc[0].to_dict(), "device": "tiny", "date": days[0].isoformat(), "sessions": sessions, "orders": orders})
    r = decompose_change(pd.concat([df, pd.DataFrame(extra)], ignore_index=True), W_PRE, W_POST, dimensions=["device"],
                         include_pairs=False, apply_exclusions=False)
    tiny = seg(r, "device", "device=tiny")
    if tiny.status == "both":
        assert tiny.test.reliable == (expected_min >= 5)
        assert tiny.test.binomial.expected_min_count == pytest.approx(expected_min)
    else:
        assert tiny.test is None  # present in one window only: nothing to test


def test_reference_data_has_no_unreliable_segments(ref):
    assert ref.n_unreliable_tests == 0 and all(p.n_unreliable == 0 for p in ref.partitions)


# ------------------------------------------------------------------ PR #4 review round 2
# Finding 1: zero-padded days must not manufacture certainty.


def _one_active_day_per_window_frame():
    """Mobile: 100 sessions / 10 orders on one day before, 100 / 11 on one day after; desktop is large and steady."""
    desktop = segmented([{"device": "desktop", "sessions": (1000, 1000), "rate": (0.03, 0.03)}])
    template = desktop.iloc[0].to_dict()
    rows = [{**template, "device": "mobile", "date": day(0).isoformat(), "sessions": 100, "orders": 10},
            {**template, "device": "mobile", "date": day(14).isoformat(), "sessions": 100, "orders": 11}]
    return pd.concat([desktop, pd.DataFrame(rows)], ignore_index=True)


def test_a_segment_seen_on_one_day_per_window_is_withheld_not_given_p_zero():
    """It used to be reported with 14 days per window, p = q = 0 and the interval [1.0, 1.0]."""
    from scipy import stats as sp

    r = decompose_change(_one_active_day_per_window_frame(), W_PRE, W_POST, dimensions=["device"],
                         include_pairs=False, apply_exclusions=False)
    m = seg(r, "device", "device=mobile")
    t = m.test
    assert (m.sessions_pre, m.orders_pre, m.sessions_post, m.orders_post) == (100, 10, 100, 11)
    assert not t.reliable and t.unreliable_reason == "too_few_active_days" and t.basis == "none"
    assert (t.active_days_pre, t.active_days_post) == (1, 1) and t.day_level is None
    assert t.p_value is None and t.q_value is None and t.q_value_scan is None and t.significant is False
    assert t.p_value_exact == pytest.approx(sp.fisher_exact([[10, 90], [11, 89]]).pvalue)
    assert t.binomial.p_value == pytest.approx(0.818, abs=1e-3)  # shown for information only
    assert "Too few days" in t.note and "at least 3 in each window" in t.note and "information only" in t.note
    assert t.change_pp.lower < t.change_pp.estimate < t.change_pp.upper  # never a zero-width interval
    assert r.n_unreliable_tests == 1 and part(r, "device").n_unreliable == 1 and part(r, "device").reconciles


def test_the_withheld_segment_is_left_out_of_the_fdr_family():
    r = decompose_change(_one_active_day_per_window_frame(), W_PRE, W_POST, dimensions=["device"],
                         include_pairs=False, apply_exclusions=False)
    reliable = [s for p in r.partitions for s in p.segments if s.test is not None and s.test.reliable]
    assert [s.label for s in reliable] == ["device=desktop"] and r.n_tests_scanned == 1
    assert seg(r, "device", "device=desktop").test.q_value == pytest.approx(
        multipletests([seg(r, "device", "device=desktop").test.p_value], method="fdr_bh")[1][0]
    )


@pytest.mark.parametrize(("active", "reliable"), [(1, False), (2, False), (3, True), (5, True)])
def test_a_segment_needs_three_days_with_sessions_in_each_window(active, reliable):
    desktop = segmented([{"device": "desktop", "sessions": (1000, 1000), "rate": (0.03, 0.03)}])
    template = desktop.iloc[0].to_dict()
    rows = []
    for i in range(active):
        rows.append({**template, "device": "mobile", "date": day(i).isoformat(), "sessions": 100, "orders": [8, 10, 12, 9, 11][i]})
        rows.append({**template, "device": "mobile", "date": day(14 + i).isoformat(), "sessions": 100, "orders": [4, 6, 5, 7, 3][i]})
    r = decompose_change(pd.concat([desktop, pd.DataFrame(rows)], ignore_index=True), W_PRE, W_POST,
                         dimensions=["device"], include_pairs=False, apply_exclusions=False)
    t = seg(r, "device", "device=mobile").test
    assert t.reliable is reliable and (t.active_days_pre, t.active_days_post) == (active, active)
    if reliable:
        # the sampling units are the days that had sessions, not the 14 days of the window
        assert (t.day_level.n_days_pre, t.day_level.n_days_post) == (active, active)
        assert t.change_pp.lower < t.change_pp.upper and 0 < t.p_value <= 1 and t.q_value is not None
    else:
        assert t.p_value is None and t.q_value is None and not t.significant


def test_padding_a_segments_absent_days_with_zero_rows_does_not_change_its_test():
    desktop = segmented([{"device": "desktop", "sessions": (1000, 1000), "rate": (0.03, 0.03)}])
    template = desktop.iloc[0].to_dict()
    active = [{**template, "device": "mobile", "date": day(i).isoformat(), "sessions": 100, "orders": o}
              for i, o in [(0, 8), (1, 10), (2, 12), (14, 4), (15, 6), (16, 5)]]
    zeros = [{**template, "device": "mobile", "date": day(i).isoformat(), "sessions": 0, "orders": 0}
             for i in [3, 4, 5, 6, 7, 17, 18, 19, 20]]
    kwargs = {"dimensions": ["device"], "include_pairs": False, "apply_exclusions": False}
    a = decompose_change(pd.concat([desktop, pd.DataFrame(active)], ignore_index=True), W_PRE, W_POST, **kwargs)
    b = decompose_change(pd.concat([desktop, pd.DataFrame(active + zeros)], ignore_index=True), W_PRE, W_POST, **kwargs)
    assert seg(a, "device", "device=mobile").test.model_dump() == seg(b, "device", "device=mobile").test.model_dump()


def test_plain_inference_remains_an_explicit_labelled_choice_for_few_day_segments():
    r = decompose_change(_one_active_day_per_window_frame(), W_PRE, W_POST, dimensions=["device"],
                         include_pairs=False, apply_exclusions=False, inference="binomial")
    t = seg(r, "device", "device=mobile").test
    assert t.reliable and t.basis == "binomial_z" and "optimistic" in t.note and t.p_value == t.binomial.p_value


def test_constant_daily_rates_no_longer_produce_zero_variance():
    """Identical daily rates used to give a variance of exactly zero, p = 0 and a zero-width interval."""
    df = segmented([
        {"device": "desktop", "sessions": (1000, 1000), "rate": (0.04, 0.03)},
        {"device": "mobile", "sessions": (1000, 1000), "rate": (0.02, 0.02)},
    ])
    r = decompose_change(df, W_PRE, W_POST, dimensions=["device"], include_pairs=False)
    for s_ in part(r, "device").segments:
        t = s_.test
        assert t.day_level.std_error_pp > 0 and t.change_pp.lower < t.change_pp.upper
        assert t.day_level.variance_floored_pre and t.day_level.variance_floored_post
    desktop = seg(r, "device", "device=desktop").test
    assert 0 < desktop.p_value < 1e-3 and desktop.significant
    assert seg(r, "device", "device=mobile").test.p_value == pytest.approx(1.0)


# Finding 2: grouping must respect selection-relevant statistical evidence.


def _equal_counts_unequal_evidence(desktop_noisy: bool = True):
    """Mobile and desktop have identical pooled counts (98/2380 before, 49/2380 after), but mobile's days are
    smooth and desktop's are wildly variable. Tablet moves the other way so each of them alone accounts for 87%
    of the change; together they carry 77% of post-window sessions, over the 50% limit."""
    rows = []
    for d in range(28):
        post = d >= 14
        mobile = (3 if d % 2 == 0 else 4) if post else 7
        if desktop_noisy:
            desktop = (0 if d % 2 == 0 else 7) if post else (0 if d % 2 == 0 else 14)
        else:
            desktop = mobile
        for device, orders, sessions in (("mobile", mobile, 170), ("desktop", desktop, 170),
                                         ("tablet", 8 if post else 5, 100)):
            rows.append({**DEFAULT_SEGMENT, "device": device, "date": day(d).isoformat(), "sessions": sessions, "orders": orders})
    return pd.DataFrame(rows)


def test_equal_counts_with_different_significance_are_not_forced_into_one_group():
    """Grouping on counts alone made a 77%-of-traffic atom, rejected both, and called the change broad."""
    r = decompose_change(_equal_counts_unequal_evidence(), W_PRE, W_POST, dimensions=["device"],
                         include_pairs=False, apply_exclusions=False)
    p = part(r, "device")
    mobile, desktop = seg(r, "device", "device=mobile"), seg(r, "device", "device=desktop")
    same = ("sessions_pre", "sessions_post", "orders_pre", "orders_post", "contribution_pp", "share_post")
    assert all(getattr(mobile, f) == getattr(desktop, f) for f in same)  # identical in everything but the days
    assert mobile.test.significant and not desktop.test.significant  # same counts, different daily evidence
    assert mobile.share_post == pytest.approx(0.386, abs=1e-3)
    # The ranked prefix takes tied contributions together, so it holds both and carries 77% of traffic: over the
    # limit. That is the situation that used to be reported as a broad shift.
    assert sorted(p.explaining_set.segments) == ["device=desktop", "device=mobile"]
    assert p.explaining_set.traffic_share_post == pytest.approx(2 * mobile.share_post)
    assert p.explaining_set.traffic_share_post > 0.5
    assert p.local_set is not None and p.local_set.segments == ["device=mobile"]
    assert p.local_set.all_selected_significant and p.local_set.interchangeable_with == []
    assert p.local_set.traffic_share_post == pytest.approx(mobile.share_post)
    assert r.localization is not None and r.localization.segments == ["device=mobile"]
    assert r.localization_note is None and "broad shift" not in r.summary


def test_the_unreliable_twin_cannot_replace_the_significant_one_or_the_other_way_round():
    df = _equal_counts_unequal_evidence()
    swapped = df.assign(device=df["device"].replace({"mobile": "desktop", "desktop": "mobile"}))
    r = decompose_change(swapped, W_PRE, W_POST, dimensions=["device"], include_pairs=False, apply_exclusions=False)
    assert r.localization.segments == ["device=desktop"]  # the smooth one, now named desktop
    assert seg(r, "device", "device=desktop").test.significant and not seg(r, "device", "device=mobile").test.significant


def test_identical_qualifying_segments_are_not_forced_together():
    """Same counts and same outcome: each qualifies alone on 39% of traffic; both together would be 77%."""
    r = decompose_change(_equal_counts_unequal_evidence(desktop_noisy=False), W_PRE, W_POST, dimensions=["device"],
                         include_pairs=False, apply_exclusions=False)
    p = part(r, "device")
    assert seg(r, "device", "device=mobile").test.significant and seg(r, "device", "device=desktop").test.significant
    ls = p.local_set
    assert ls.k == 1 and ls.traffic_share_post == pytest.approx(0.386, abs=1e-3)
    assert ls.segments == ["device=desktop"] and ls.interchangeable_with == ["device=mobile"]  # first by label
    assert "interchangeable" in ls.note or "could take the place" in ls.note
    assert r.localization is not None and r.localization.segments == ["device=desktop"]
    # the ranked prefix takes tied segments together (it has no traffic limit), which is why it differs
    assert sorted(p.explaining_set.segments) == ["device=desktop", "device=mobile"]
    assert p.explaining_set.traffic_share_post > 0.5 > ls.traffic_share_post


def test_localization_numbers_do_not_depend_on_which_interchangeable_segment_is_named():
    df = _equal_counts_unequal_evidence(desktop_noisy=False)
    a = decompose_change(df, W_PRE, W_POST, dimensions=["device"], include_pairs=False, apply_exclusions=False)
    renamed = df.assign(device=df["device"].replace({"desktop": "zzz", "mobile": "aaa", "tablet": "mmm"}))
    b = decompose_change(renamed, W_PRE, W_POST, dimensions=["device"], include_pairs=False, apply_exclusions=False)
    for x, y in ((a.partitions[0].local_set, b.partitions[0].local_set),):
        assert (x.k, x.traffic_share_post, x.selected_share_of_change, x.rate_effect_pp, x.mix_effect_pp) == (
            y.k, y.traffic_share_post, y.selected_share_of_change, y.rate_effect_pp, y.mix_effect_pp)
    assert b.localization.segments == ["device=aaa"] and b.partitions[0].local_set.interchangeable_with == ["device=zzz"]
