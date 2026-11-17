from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from frames import day, segmented, to_frame
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
    assert a.test.two_proportion.p_value == pytest.approx(1.0)


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
    ci = part(ref, "device × browser").segments[0].test.two_proportion.change_pp
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
    tested = [(p.name, s) for p in ref.partitions for s in p.segments if s.test is not None]
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


def test_dispersion_adjustment_is_recomputed_independently_for_one_segment(reference, ref):
    metrics, _ = reference
    d = metrics.assign(date=pd.to_datetime(metrics["date"]))
    d = d[(d["device"] == "mobile") & (d["browser"] == "safari") & (d["date"] != "2026-09-20")]
    daily = d.groupby("date")[["sessions", "orders"]].sum()
    post = daily.loc["2026-10-14":"2026-10-31"]
    p = post["orders"].sum() / post["sessions"].sum()
    chi2 = (((post["orders"] - post["sessions"] * p) ** 2) / (post["sessions"] * p * (1 - p))).sum()
    expected = max(1.0, chi2 / (len(post) - 1))
    t = seg(ref, "device × browser", "device=mobile, browser=safari").test.two_proportion
    assert t.dispersion_post == pytest.approx(expected) and t.dispersion_post > 1
    assert t.p_value >= t.p_value_binomial
    for pr in ref.partitions:
        for s in pr.segments:
            if s.test is not None:
                assert s.test.two_proportion.dispersion_pre >= 1 and s.test.two_proportion.dispersion_post >= 1


def test_without_dispersion_adjustment_the_plain_binomial_test_is_used(reference):
    metrics, events = reference
    r = decompose_change(metrics, PRE, POST, events, dispersion="none")
    for p in r.partitions:
        for s in p.segments:
            if s.test is not None:
                tp = s.test.two_proportion
                assert tp.p_value == tp.p_value_binomial and tp.dispersion_pre == tp.dispersion_post == 1.0
    assert "no dispersion adjustment" in r.summary and any("optimistic" in a for a in r.assumptions)


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
        ({"dispersion": "robust"}, "dispersion"),
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
