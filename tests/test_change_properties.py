"""Property tests for ``confirm_change`` and ``detect_change_point``.

Guaranteed properties use **bounded** fixtures (see ``frames.bounded_series``: every value lies in
a known interval, no sampling noise) and hold for every draw. Statistical tests use **binomial**
fixtures and assert explicitly stated rates over fixed seeds, because individual noisy draws can
legitimately go either way.
"""
from __future__ import annotations

import itertools

import numpy as np
import pytest
from frames import binomial_series, bounded_series, day
from hypothesis import given, settings
from hypothesis import strategies as st

from causelens.analytics import confirm_change, detect_change_point

COMMON = {"deadline": None, "derandomize": True}


# ------------------------------------------------------------------ guaranteed properties


@settings(max_examples=40, **COMMON)
@given(
    seed=st.integers(0, 2**31 - 1),
    change_day=st.integers(14, 65),
    step=st.floats(0.15, 0.5),
    sign=st.sampled_from([-1, 1]),
    base_rate=st.floats(0.02, 0.08),
    base_sessions=st.integers(5_000, 30_000),
)
def test_a_large_step_in_bounded_data_is_located_exactly(seed, change_day, step, sign, base_rate, base_sessions):
    """Noise is bounded at +/-1% of the rate and weekday effects at ~5%; a >=15% step dwarfs both, so
    the squared-error minimum sits exactly on the step (misplacing it by a day costs about delta^2
    while the noise cross-terms are bounded far below that)."""
    df = bounded_series(seed, 80, base_rate, base_sessions, change_day, sign * step)
    r = detect_change_point(df)
    assert r.change_detected and r.best_date == day(change_day)
    assert day(change_day) in r.plausible_dates
    assert r.direction == ("increase" if sign > 0 else "decrease")


@settings(max_examples=40, **COMMON)
@given(seed=st.integers(0, 2**31 - 1), change_day=st.integers(14, 65), step=st.floats(-0.4, 0.4))
def test_the_plausible_set_always_contains_the_best_date_and_is_well_formed(seed, change_day, step):
    r = detect_change_point(bounded_series(seed, 80, step_day=change_day, step=step))
    assert r.best_date in r.plausible_dates
    assert r.plausible_dates == sorted(set(r.plausible_dates))
    assert r.plausible_range == (r.plausible_dates[0], r.plausible_dates[-1])
    assert 0.0 <= r.p_value_adjusted <= 1.0
    assert r.n_days_before + r.n_days_after == r.n_days_used == 80


@settings(max_examples=15, **COMMON)
@given(seed=st.integers(0, 2**31 - 1), change_day=st.integers(20, 60), step=st.floats(-0.1, 0.1))
def test_a_higher_plausible_level_never_shrinks_the_set(seed, change_day, step):
    df = bounded_series(seed, 80, step_day=change_day, step=step, rate_noise=0.012)
    sets = [set(detect_change_point(df, plausible_level=lv).plausible_dates) for lv in (0.5, 0.8, 0.95, 0.99)]
    assert all(a <= b for a, b in itertools.pairwise(sets))


@settings(max_examples=30, **COMMON)
@given(
    seed=st.integers(0, 2**31 - 1),
    step=st.floats(-0.3, 0.3),
    split=st.integers(20, 40),
    boot_seed=st.integers(0, 10_000),
)
def test_every_confirm_change_interval_is_ordered_and_consistent(seed, step, split, boot_seed):
    df = bounded_series(seed, 70, step_day=split, step=step)
    r = confirm_change(df, (day(0), day(split - 1)), (day(split), day(69)), n_bootstrap=300, seed=boot_seed)
    assert r.status == "ok"
    for iv in (r.pre.conversion, r.post.conversion, r.abs_change_pp, r.rel_change):
        assert iv.lower <= iv.estimate <= iv.upper
    assert r.pre.n_days_used == split and r.post.n_days_used == 70 - split
    assert r.abs_change_pp.estimate == pytest.approx(100 * (r.post.conversion.estimate - r.pre.conversion.estimate))
    assert r.is_real == (r.noise_test.welch_significant and r.noise_test.bootstrap_excludes_zero)
    assert r.stop_investigation == (not r.is_real)


@settings(max_examples=15, **COMMON)
@given(seed=st.integers(0, 2**31 - 1), step=st.floats(-0.3, 0.3), boot_seed=st.integers(0, 10_000))
def test_results_are_deterministic_given_the_seed_and_ignore_row_order(seed, step, boot_seed):
    df = bounded_series(seed, 70, step_day=35, step=step)
    shuffled = df.sample(frac=1.0, random_state=seed % 1000).reset_index(drop=True)
    kwargs = {"n_bootstrap": 300, "seed": boot_seed}
    a = confirm_change(df, (day(0), day(34)), (day(35), day(69)), **kwargs)
    assert a.model_dump() == confirm_change(shuffled, (day(0), day(34)), (day(35), day(69)), **kwargs).model_dump()
    assert detect_change_point(df).model_dump() == detect_change_point(shuffled).model_dump()


@settings(max_examples=30, **COMMON)
@given(
    seed=st.integers(0, 2**31 - 1),
    factors=st.lists(st.floats(0.3, 1.0), min_size=2, max_size=4).map(lambda xs: sorted(xs, reverse=True)),
)
def test_lowering_post_orders_never_raises_the_estimated_change(seed, factors):
    df = bounded_series(seed, 56, step_day=28, step=-0.05)
    late = df["date"] >= day(28).isoformat()
    estimates = []
    for f in factors:
        scaled = df.copy()
        scaled.loc[late, "orders"] = np.floor(scaled.loc[late, "orders"] * f).astype(int)
        estimates.append(confirm_change(scaled, (day(0), day(27)), (day(28), day(55)), n_bootstrap=200).abs_change_pp.estimate)
    assert all(a >= b for a, b in itertools.pairwise(estimates))


# ------------------------------------------------------------------ statistical tests (fixed seeds)
# Rates measured over 150 seeds when these thresholds were set: null false-positive 4/150 (change
# point) and 4/150 (confirm); detection 150/150; plausible-set coverage 96% at a 15% step and only
# about 87% at an 8% step, which is why coverage is asserted for the larger step alone.

SEEDS = range(70)


def test_null_series_rarely_produce_a_supported_change_point():
    false_positives = sum(detect_change_point(binomial_series(s)).change_detected for s in SEEDS)
    assert false_positives / len(SEEDS) <= 0.08, f"{false_positives}/{len(SEEDS)} null series flagged"


def test_real_steps_in_noisy_data_are_detected():
    for step, minimum in [(-0.15, 0.99), (-0.08, 0.97)]:
        hits = 0
        for s in SEEDS:
            c = int(np.random.default_rng(s).integers(20, 60))
            hits += detect_change_point(binomial_series(s, 80, 0.04, 12_000, c, step)).change_detected
        assert hits / len(SEEDS) >= minimum, f"step {step}: detected {hits}/{len(SEEDS)}"


def test_plausible_set_covers_the_true_date_at_roughly_its_stated_level_for_a_clear_step():
    """Nominal 95%; measured 96% at a 15% step. Asserted with margin at 88%. It undercovers for small
    steps (about 87% at 8%), so the set is an approximate likelihood-ratio set, not an exact interval."""
    covered = 0
    for s in SEEDS:
        c = int(np.random.default_rng(s).integers(20, 60))
        covered += day(c) in detect_change_point(binomial_series(s, 80, 0.04, 12_000, c, -0.15)).plausible_dates
    assert covered / len(SEEDS) >= 0.88, f"covered {covered}/{len(SEEDS)}"


def test_confirm_change_false_positive_and_detection_rates():
    def run(s: int, step: float) -> bool:
        df = binomial_series(s, 56, 0.04, 12_000, 28, step)
        return confirm_change(df, (day(0), day(27)), (day(28), day(55)), n_bootstrap=300, seed=s).is_real

    null = sum(run(s, 0.0) for s in SEEDS)
    assert null / len(SEEDS) <= 0.08, f"{null}/{len(SEEDS)} null comparisons called real"
    power = sum(run(s, -0.10) for s in SEEDS)
    assert power / len(SEEDS) >= 0.97, f"{power}/{len(SEEDS)} 10% drops confirmed"
