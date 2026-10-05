"""Tests for ml.stale_day_estimate (ADR 048) -- S1/S2/S3 stale-IBJA-day scoring. Synthetic only,
mirroring tests/test_stratified_band.py's pattern for the same production scoring set."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from ml.calibration import _MIN_FIT_OBSERVATIONS, evaluate_empirical_band_coverage
from ml.stale_day_estimate import (
    MAX_CARRY_FORWARD_AGE_DAYS,
    build_scoring_table,
    fusion_benchmark_per_day,
)

_SEED = 42  # hardcoded, per the repo's determinism rule


# ---------------------------------------------------------------------------
# Synthetic data builders
# ---------------------------------------------------------------------------


def _synthetic(
    n_days: int = 110, weekend_extra_sd: float = 70.0, weekend_drift_sd: float = 25.0
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """IBJA publishes Mon-Fri only; Tanishq is quoted every day. Same generator as
    tests/test_stratified_band.py's `_synthetic` -- kept local (no cross-test-file import) per this
    repo's convention of each test file owning its own fixtures."""
    rng = np.random.default_rng(_SEED)
    days = pd.date_range("2026-03-02", periods=n_days, freq="D")  # 2026-03-02 is a Monday
    ibja_rows, tq_rows = [], []
    ibja_per_g = 13000.0
    for d in days:
        ibja_per_g += rng.normal(0, 25 if d.weekday() < 5 else weekend_drift_sd)
        if d.weekday() < 5:
            ibja_rows.append({"date": d.strftime("%Y-%m-%d"), "pm_916": ibja_per_g * 10.0})
            tq = 1.05 * ibja_per_g + 200 + rng.normal(0, 8)
        else:
            tq = 1.05 * ibja_per_g + 200 + rng.normal(0, weekend_extra_sd)
        tq_rows.append({"date": d.strftime("%Y-%m-%d"), "22k": float(tq)})
    return pd.DataFrame(ibja_rows), pd.DataFrame(tq_rows)


def _deterministic(n_weeks: int = 10) -> tuple[pd.DataFrame, pd.DataFrame]:
    """No noise: IBJA steps up by a fixed amount each weekday, Tanishq = 1.05*ibja + 100 exactly
    on weekdays, and a weekend/holiday carries FRIDAY's Tanishq value forward UNCHANGED. S2's
    carry-forward estimate is then exactly predictable (and exactly correct -- zero drift)."""
    base = pd.Timestamp("2026-01-05")  # a Monday
    ibja_rows, tq_rows = [], []
    ibja_per_g = 13000.0
    last_tq = None
    for i in range(n_weeks * 7):
        d = base + pd.Timedelta(days=i)
        if d.dayofweek < 5:
            ibja_per_g += 10.0
            tq = 1.05 * ibja_per_g + 100.0
            ibja_rows.append({"date": str(d.date()), "pm_916": ibja_per_g * 10.0})
            tq_rows.append({"date": str(d.date()), "22k": tq})
            last_tq = tq
        else:
            assert last_tq is not None
            tq_rows.append({"date": str(d.date()), "22k": last_tq})
    return pd.DataFrame(ibja_rows), pd.DataFrame(tq_rows)


def _snapshot_row(
    as_of: str, source: str, rate: float, hour: int = 12, city: str | None = None
) -> dict:
    return {
        "capture_utc": f"{as_of}T{hour:02d}:00:00Z",
        "as_of_date": as_of,
        "schema_version": 1,
        "source": source,
        "city": city,
        "rate_22k": rate,
        "observed_at": f"{as_of}T{hour:02d}:00:00+00:00",
        "attribution": f"{source} test",
    }


# ---------------------------------------------------------------------------
# build_scoring_table -- scoring-set parity with the production scorer
# ---------------------------------------------------------------------------


def test_scoring_set_matches_production_scorer_parity():
    """ADR 048's scoring set is explicitly pinned to evaluate_empirical_band_coverage's own days."""
    ibja, tq = _synthetic()
    prod = evaluate_empirical_band_coverage(ibja, tq, level=80)
    results = build_scoring_table(ibja, tq)
    assert len(results) == prod["n"] > 0


def test_scoring_set_skips_below_min_train_warmup():
    ibja, tq = _synthetic(n_days=_MIN_FIT_OBSERVATIONS + 5)
    results = build_scoring_table(ibja, tq)
    assert all(pd.Timestamp(r.date) >= pd.Timestamp("2026-03-02") for r in results)


# ---------------------------------------------------------------------------
# S2 == S1 on ibja days
# ---------------------------------------------------------------------------


def test_s2_equals_s1_exactly_on_ibja_days():
    ibja, tq = _synthetic()
    results = build_scoring_table(ibja, tq)
    ibja_days = [r for r in results if not r.stale]
    assert len(ibja_days) > 0
    for r in ibja_days:
        assert r.s2_estimate == r.s1_estimate
        assert r.s2_half_width == r.s1_half_width
        assert r.s2_estimate_fallback is False
        assert r.s2_band_fallback is False


# ---------------------------------------------------------------------------
# S2 carry-forward estimate
# ---------------------------------------------------------------------------


def test_s2_carry_forward_matches_the_last_actual_tanishq_reading():
    ibja, tq = _deterministic()
    results = build_scoring_table(ibja, tq)
    stale_days = [r for r in results if r.stale]
    assert len(stale_days) > 0
    for r in stale_days:
        # deterministic fixture: the weekend/holiday value IS the prior day's Tanishq reading,
        # so a resolved (non-fallback) carry-forward estimate must recover it exactly.
        if not r.s2_estimate_fallback:
            assert r.s2_estimate == r.actual


def test_s2_estimate_falls_back_to_s1_when_carry_forward_too_old():
    ibja, tq = _deterministic(n_weeks=10)
    # Delete the four Tanishq rows right before a target stale day, leaving the nearest surviving
    # reading 5 calendar days old -- one more than MAX_CARRY_FORWARD_AGE_DAYS.
    target = "2026-02-28"  # a Saturday, inside the scored (post-warmup) window
    drop_from, drop_to = "2026-02-24", "2026-02-27"
    tq_gapped = tq[~tq["date"].between(drop_from, drop_to)].reset_index(drop=True)
    results = build_scoring_table(ibja, tq_gapped)
    row = next(r for r in results if r.date == target)
    assert row.stale is True
    assert row.s2_estimate_fallback is True
    assert row.s2_estimate == row.s1_estimate


def test_max_carry_forward_age_is_four_days():
    assert MAX_CARRY_FORWARD_AGE_DAYS == 4


# ---------------------------------------------------------------------------
# S2 band fallback / graduation
# ---------------------------------------------------------------------------


def test_s2_band_falls_back_below_minimum_history():
    ibja, tq = _synthetic()
    results = build_scoring_table(ibja, tq, min_carry_residuals=10_000)  # impossible to reach
    stale_days = [r for r in results if r.stale]
    assert len(stale_days) > 0
    assert all(r.s2_band_fallback for r in stale_days)
    assert all(r.s2_half_width == r.s1_half_width for r in stale_days)


def test_s2_band_stops_falling_back_once_enough_history_accrues():
    ibja, tq = _synthetic()
    results = build_scoring_table(ibja, tq, min_carry_residuals=2)
    stale_days = [r for r in results if r.stale]
    assert any(not r.s2_band_fallback for r in stale_days)


# ---------------------------------------------------------------------------
# No look-ahead
# ---------------------------------------------------------------------------


def test_no_look_ahead_mutating_a_later_day_leaves_earlier_days_unchanged():
    ibja, tq = _synthetic()
    before = build_scoring_table(ibja, tq)

    tq_mutated = tq.copy()
    tq_mutated.loc[tq_mutated.index[-1], "22k"] += 5000.0  # blow up only the last day's own reading
    after = build_scoring_table(ibja, tq_mutated)

    assert len(after) == len(before)
    # every day except (at most) the very last one must be bit-for-bit identical
    for a, b in zip(before[:-1], after[:-1], strict=True):
        assert a == b


def test_no_look_ahead_mutating_a_later_day_does_not_change_earlier_fusion_history():
    ibja, tq = _deterministic(n_weeks=8)
    bench = pd.Series(
        {pd.Timestamp(d): v * 0.98 for d, v in zip(tq["date"], tq["22k"], strict=True)}
    )
    before = build_scoring_table(ibja, tq, bench)

    bench_mutated = bench.copy()
    bench_mutated.iloc[-1] += 5000.0
    after = build_scoring_table(ibja, tq, bench_mutated)

    for a, b in zip(before[:-1], after[:-1], strict=True):
        assert a == b


# ---------------------------------------------------------------------------
# fusion_benchmark_per_day
# ---------------------------------------------------------------------------


def test_fusion_benchmark_per_day_averages_grt_and_malabar():
    snaps = pd.DataFrame(
        [
            _snapshot_row("2026-05-01", "grt", 13000.0),
            _snapshot_row("2026-05-01", "malabar", 13100.0),
            _snapshot_row("2026-05-01", "ibja", 13050.0),  # must be excluded
            _snapshot_row("2026-05-01", "kalyan", 13200.0, city="BANGALORE"),  # city row, excluded
        ]
    )
    bench = fusion_benchmark_per_day(snaps)
    # DEFAULT_WEIGHTS gives grt and malabar equal weight (0.7 each) -> plain average of the two.
    assert list(bench.index) == [pd.Timestamp("2026-05-01")]
    assert bench.iloc[0] == pytest.approx(13050.0)


def test_fusion_benchmark_per_day_uses_the_latest_capture_per_source():
    snaps = pd.DataFrame(
        [
            _snapshot_row("2026-05-01", "grt", 13000.0, hour=6),
            _snapshot_row("2026-05-01", "grt", 13500.0, hour=18),  # later capture wins
            _snapshot_row("2026-05-01", "malabar", 13500.0, hour=6),
        ]
    )
    bench = fusion_benchmark_per_day(snaps)
    assert bench.loc[pd.Timestamp("2026-05-01")] == pytest.approx(13500.0)


def test_fusion_benchmark_per_day_empty_inputs():
    assert fusion_benchmark_per_day(pd.DataFrame()).empty
    only_ibja = pd.DataFrame([_snapshot_row("2026-05-01", "ibja", 13000.0)])
    assert fusion_benchmark_per_day(only_ibja).empty


# ---------------------------------------------------------------------------
# S3
# ---------------------------------------------------------------------------


def test_s3_is_none_when_no_benchmark_for_that_day():
    ibja, tq = _deterministic()
    bench = pd.Series(dtype=float)  # no fusion history at all
    results = build_scoring_table(ibja, tq, bench)
    stale_days = [r for r in results if r.stale]
    assert stale_days
    assert all(r.s3_estimate is None for r in stale_days)
    assert all(r.s3_estimate_fallback is None for r in stale_days)


def test_s3_falls_back_to_s2_below_minimum_ratio_pairs():
    ibja, tq = _deterministic()
    bench = pd.Series(
        {pd.Timestamp(d): v / 1.05 for d, v in zip(tq["date"], tq["22k"], strict=True)}
    )
    results = build_scoring_table(ibja, tq, bench, min_fusion_pairs=10_000)  # unreachable
    stale_days = [r for r in results if r.stale]
    assert stale_days
    for r in stale_days:
        assert r.s3_estimate_fallback is True
        assert r.s3_estimate == r.s2_estimate


def test_s3_uses_the_weighted_ratio_once_enough_pairs_exist():
    ibja, tq = _deterministic(n_weeks=10)
    # benchmark is EXACTLY tanishq / 1.05 on every day -> the ratio is always 1.05, so S3's
    # estimate (ratio * benchmark) must recover the actual reading exactly once resolved.
    bench = pd.Series(
        {pd.Timestamp(d): v / 1.05 for d, v in zip(tq["date"], tq["22k"], strict=True)}
    )
    results = build_scoring_table(ibja, tq, bench, min_fusion_pairs=8)
    stale_days = [r for r in results if r.stale]
    resolved = [r for r in stale_days if r.s3_estimate_fallback is False]
    assert resolved
    for r in resolved:
        assert r.s3_estimate == pytest.approx(r.actual, abs=1e-6)


def test_s3_band_falls_back_to_s2_band_below_minimum_history():
    ibja, tq = _deterministic(n_weeks=10)
    bench = pd.Series(
        {pd.Timestamp(d): v / 1.05 for d, v in zip(tq["date"], tq["22k"], strict=True)}
    )
    results = build_scoring_table(ibja, tq, bench, min_fusion_pairs=8, min_carry_residuals=10_000)
    resolved = [r for r in results if r.stale and r.s3_estimate_fallback is False]
    assert resolved
    for r in resolved:
        assert r.s3_band_fallback is True
        assert r.s3_half_width == r.s2_half_width
