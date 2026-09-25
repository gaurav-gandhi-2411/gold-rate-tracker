"""ml/stale_day_estimate.py -- ADR 048: give stale-IBJA days (weekends, holidays) their own
estimate and their own band, instead of scoring them against a band sized on IBJA days.

Pure functions only -- no file I/O, no side effects, no look-ahead. The caller (scripts/
run_stale_day_shadow.py) loads data/ibja_rates.parquet, data/prices.json and (for S3)
data/fusion_snapshots.parquet and drives this module; nothing here reads a file or knows today's
date. Nothing here changes what the site displays (ml.inference is untouched) -- this is shadow
scoring only, exactly the ADR 048 pre-registration's frozen rules.

Three estimators, scored on ml.calibration.evaluate_empirical_band_coverage's own scoring set (every
Tanishq day, asof-matched backward to the latest IBJA row within its max-age gate):

  S1 (live)      the production IBJA-calibrated estimate and band, exactly as scored today by
                 ml.calibration.evaluate_empirical_band_coverage / evaluate_stratified_band_coverage
                 -- a Huber fit on same-day IBJA/Tanishq pairs strictly before t, band from the
                 recency-weighted empirical |residual| quantile of that fit's own training
                 residuals.

  S2 (proposed)  on an IBJA day (the latest IBJA row is dated t itself) S2 IS S1, exactly. On a
                 stale day (the latest IBJA row is older than t): the estimate is the latest actual
                 Tanishq reading dated strictly before t (raw carry-forward, not run back through
                 the IBJA calibration at all) -- gated at MAX_CARRY_FORWARD_AGE_DAYS calendar days,
                 else it falls back to S1's estimate. The band is the recency-weighted
                 NOMINAL_LEVEL-percent quantile of |carry-forward error| over EARLIER stale days
                 (date < t) whose own S2 estimate was itself not a fallback -- a day that fell back
                 to S1 never had a "carry-forward error" to begin with, so it is excluded from
                 this history rather than silently diluting it with a differently-distributed
                 error. Below MIN_CARRY_RESIDUALS such earlier days, the band falls back to S1's
                 band for this day.

  S3 (secondary) only defined on a stale day where a same-day GRT+Malabar fusion benchmark exists
                 (build one first with fusion_benchmark_per_day). The estimate is that benchmark
                 times the recency-weighted MEDIAN of Tanishq/benchmark over every earlier day
                 (stale or not) where both a Tanishq reading and a benchmark exist -- below
                 MIN_FUSION_RATIO_PAIRS such pairs, or when no benchmark exists for t at all, S3
                 falls back to S2 (not S1). The band follows S2's rule (recency-weighted
                 NOMINAL_LEVEL-percent quantile of |S3 error| over earlier stale days where S3
                 itself resolved, minimum MIN_CARRY_RESIDUALS, else fall back to S2's band).

No look-ahead: every quantity used to score day t (the fit, S1's band, S2's carry-forward reading
and band history, S3's ratio history and band history) is built exclusively from rows strictly
before t, walking the scoring set in date order exactly once. See
tests/test_stale_day_estimate.py's no-look-ahead test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import numpy as np
import pandas as pd

from ml.calibration import (
    _DEFAULT_HALF_LIFE,
    _DEFAULT_HUBER_EPSILON,
    _MIN_FIT_OBSERVATIONS,
    _SCORING_MAX_IBJA_AGE_DAYS,
    _fit_robust,
    _merge_overlap,
    _recency_weights,
    _weighted_percentile,
)
from ml.fusion import default_weight_fn, fuse_national_benchmark
from ml.sources.base import SourceReading

NOMINAL_LEVEL: int = 80  # band level, percent -- matches ml.calibration.NOMINAL_COVERAGE_PCT
MAX_CARRY_FORWARD_AGE_DAYS: int = 4  # S2 estimate: carry-forward Tanishq reading must be this fresh
MIN_CARRY_RESIDUALS: int = 8  # S2/S3 band: minimum earlier resolved stale-day errors to size from
MIN_FUSION_RATIO_PAIRS: int = 8  # S3 estimate: minimum earlier Tanishq/benchmark pairs
_FUSION_SOURCES: tuple[str, ...] = (
    "grt",
    "malabar",
)  # IBJA excluded on purpose -- it's what's stale


@dataclass(frozen=True)
class DayResult:
    """One scored day. s3_* fields are None when no fusion benchmark exists for this date at all
    (S3 was never attempted for this day, as opposed to attempted-and-fell-back)."""

    date: str
    stale: bool
    gap_days: int
    actual: float
    s1_estimate: float
    s1_half_width: float
    s2_estimate: float
    s2_half_width: float
    s2_estimate_fallback: bool
    s2_band_fallback: bool
    s3_estimate: float | None = None
    s3_half_width: float | None = None
    s3_estimate_fallback: bool | None = None
    s3_band_fallback: bool | None = None


def _latest_before(
    series: pd.Series, t: pd.Timestamp, max_age_days: int
) -> tuple[float | None, int | None]:
    """The latest value in `series` (indexed by date, ascending) strictly before `t`.

    Returns (None, None) with no earlier value at all, (None, age) when the latest earlier value
    is older than `max_age_days`, else (value, age).
    """
    earlier = series[series.index < t]
    if earlier.empty:
        return None, None
    d = earlier.index[-1]
    age = int((t - d).days)
    if age > max_age_days:
        return None, age
    return float(earlier.iloc[-1]), age


def _prepare(
    ibja_df: pd.DataFrame, tanishq_df: pd.DataFrame, max_age_days: int
) -> tuple[pd.DataFrame, pd.Series, np.ndarray, np.ndarray, np.ndarray]:
    """Build the exact scoring set ml.calibration.evaluate_empirical_band_coverage scores, plus the
    same-day training arrays its walk-forward fit uses. Duplicated from calibration.py deliberately
    (see the module docstring: ADR 048 is pinned to reproduce that function's scoring set exactly,
    not to refactor it) -- a parity check in tests/test_stale_day_estimate.py pins the two together.
    """
    same_day = _merge_overlap(ibja_df, tanishq_df)
    X = same_day["ibja_per_g"].to_numpy().reshape(-1, 1)
    y = same_day["tanishq_22k"].to_numpy()
    same_day_dates = pd.to_datetime(same_day["date"]).to_numpy()

    ibja_sorted = ibja_df[["date", "pm_916"]].dropna(subset=["pm_916"]).copy()
    ibja_sorted["date_dt"] = pd.to_datetime(ibja_sorted["date"])
    ibja_sorted["ibja_per_g"] = ibja_sorted["pm_916"] / 10.0
    ibja_sorted = ibja_sorted.sort_values("date_dt")

    tanishq_sorted = tanishq_df[["date", "22k"]].copy()
    tanishq_sorted["date_dt"] = pd.to_datetime(tanishq_sorted["date"])
    tanishq_sorted = tanishq_sorted.sort_values("date_dt")

    scoring = pd.merge_asof(
        tanishq_sorted,
        ibja_sorted[["date_dt", "ibja_per_g", "date"]].rename(columns={"date": "ibja_date"}),
        on="date_dt",
        direction="backward",
    )
    scoring = scoring.dropna(subset=["ibja_per_g"])
    scoring["gap_days"] = (scoring["date_dt"] - pd.to_datetime(scoring["ibja_date"])).dt.days
    scoring = (
        scoring[scoring["gap_days"] < max_age_days].sort_values("date_dt").reset_index(drop=True)
    )
    truth_by_date = tanishq_sorted.set_index("date_dt")["22k"]
    return scoring, truth_by_date, X, y, same_day_dates


def fusion_benchmark_per_day(
    snapshots: pd.DataFrame, sources: tuple[str, ...] = _FUSION_SOURCES
) -> pd.Series:
    """The GRT+Malabar-only national fusion benchmark for each day that has at least one of them.

    Filters to national-level snapshots (``city`` is null) from `sources` -- IBJA is excluded on
    purpose, since IBJA being stale/unavailable is exactly the situation S3 is for. Within a day,
    uses the LAST captured reading per source (mirrors ml.shadow_fusion: one reading per source per
    cycle, latest wins) and fuses them with ml.fusion.fuse_national_benchmark under the real
    production DEFAULT_WEIGHTS (via default_weight_fn) -- not a reimplementation. "Before the end of
    day t" is automatic: a day's benchmark is built only from that day's own as_of_date rows, never
    from a later one. A day with neither source present is simply absent from the returned Series
    (never fabricated as 0 or interpolated).

    Returns a float Series indexed by date (midnight Timestamp), sorted ascending.
    """
    if snapshots.empty:
        return pd.Series(dtype=float)
    nat = snapshots[snapshots["city"].isna() & snapshots["source"].isin(sources)].copy()
    if nat.empty:
        return pd.Series(dtype=float)
    nat = nat.sort_values("capture_utc").groupby(["as_of_date", "source"]).last().reset_index()

    out: dict[pd.Timestamp, float] = {}
    for as_of_date, grp in nat.groupby("as_of_date"):
        readings = [
            SourceReading(
                source=str(row["source"]),
                city=None,
                rate_22k=float(row["rate_22k"]),
                observed_at=pd.Timestamp(row["observed_at"]),
                attribution=str(row["attribution"]),
            )
            for _, row in grp.iterrows()
        ]
        try:
            bench = fuse_national_benchmark(readings, weight_fn=default_weight_fn)
        except ValueError:
            continue
        # cast: groupby()'s yielded key is typed as a broad Union across every pandas-stubs
        # groupable dtype (a stub gap, not an actual runtime possibility) -- as_of_date is
        # always a genuine date value here, the same "as_of_date" column grouped on above.
        out[pd.Timestamp(cast(Any, as_of_date))] = bench.value
    return pd.Series(out, dtype=float).sort_index()


def build_scoring_table(
    ibja_df: pd.DataFrame,
    tanishq_df: pd.DataFrame,
    fusion_benchmark: pd.Series | None = None,
    *,
    half_life: float = _DEFAULT_HALF_LIFE,
    huber_epsilon: float = _DEFAULT_HUBER_EPSILON,
    min_train: int = _MIN_FIT_OBSERVATIONS,
    max_age_days: int = _SCORING_MAX_IBJA_AGE_DAYS,
    max_carry_forward_age_days: int = MAX_CARRY_FORWARD_AGE_DAYS,
    min_carry_residuals: int = MIN_CARRY_RESIDUALS,
    min_fusion_pairs: int = MIN_FUSION_RATIO_PAIRS,
    level: int = NOMINAL_LEVEL,
) -> list[DayResult]:
    """Walk the production scoring set once, in date order, resolving S1/S2/S3 for each day from
    data strictly before it only. See the module docstring for the frozen rules (ADR 048)."""
    scoring, truth_by_date, X, y, same_day_dates = _prepare(ibja_df, tanishq_df, max_age_days)

    results: list[DayResult] = []
    # Running histories, built up strictly in date order -- every read here happens before that
    # same day's write, so nothing a day contributes can affect its own score.
    stale_s2_errors: list[float] = []
    stale_s3_errors: list[float] = []
    ratio_pairs: list[float] = []  # tanishq / benchmark, in date order (oldest first)

    for _, srow in scoring.iterrows():
        t = srow["date_dt"]
        actual = float(srow["22k"])
        train_mask = same_day_dates < np.datetime64(t)
        n_train = int(train_mask.sum())
        if n_train < min_train:
            continue

        weights = _recency_weights(n_train, half_life)
        slope, intercept = _fit_robust(
            X[train_mask], y[train_mask], huber_epsilon=huber_epsilon, weights=weights
        )
        train_abs = np.abs(y[train_mask] - (slope * X[train_mask][:, 0] + intercept))
        s1_hw = float(_weighted_percentile(train_abs, weights, level))
        s1_est = float(slope * float(srow["ibja_per_g"]) + intercept)

        stale = int(srow["gap_days"]) >= 1

        if not stale:
            s2_est, s2_hw = s1_est, s1_hw
            s2_est_fb = False
            s2_hw_fb = False
        else:
            carry_val, _age = _latest_before(truth_by_date, t, max_carry_forward_age_days)
            s2_est_fb = carry_val is None
            s2_est = s1_est if carry_val is None else carry_val
            if len(stale_s2_errors) >= min_carry_residuals:
                w2 = _recency_weights(len(stale_s2_errors), half_life)
                s2_hw = float(_weighted_percentile(np.array(stale_s2_errors), w2, level))
                s2_hw_fb = False
            else:
                s2_hw = s1_hw
                s2_hw_fb = True

        s3_est: float | None = None
        s3_hw: float | None = None
        s3_est_fb: bool | None = None
        s3_hw_fb: bool | None = None
        bench_t = None
        if stale and fusion_benchmark is not None and t in fusion_benchmark.index:
            bench_t = float(fusion_benchmark.loc[t])
            if len(ratio_pairs) >= min_fusion_pairs:
                w3 = _recency_weights(len(ratio_pairs), half_life)
                ratio = _weighted_percentile(np.array(ratio_pairs), w3, 50)
                s3_est = float(ratio * bench_t)
                s3_est_fb = False
            else:
                s3_est = s2_est
                s3_est_fb = True
            if len(stale_s3_errors) >= min_carry_residuals:
                w4 = _recency_weights(len(stale_s3_errors), half_life)
                s3_hw = float(_weighted_percentile(np.array(stale_s3_errors), w4, level))
                s3_hw_fb = False
            else:
                s3_hw = s2_hw
                s3_hw_fb = True

        results.append(
            DayResult(
                date=str(t.date()),
                stale=stale,
                gap_days=int(srow["gap_days"]),
                actual=actual,
                s1_estimate=s1_est,
                s1_half_width=s1_hw,
                s2_estimate=s2_est,
                s2_half_width=s2_hw,
                s2_estimate_fallback=s2_est_fb,
                s2_band_fallback=s2_hw_fb,
                s3_estimate=s3_est,
                s3_half_width=s3_hw,
                s3_estimate_fallback=s3_est_fb,
                s3_band_fallback=s3_hw_fb,
            )
        )

        # Update histories AFTER resolving this day -- only later days can see it.
        if stale and not s2_est_fb:
            stale_s2_errors.append(abs(s2_est - actual))
        if stale and s3_est is not None and not s3_est_fb:
            stale_s3_errors.append(abs(s3_est - actual))
        if bench_t is not None:
            ratio_pairs.append(actual / bench_t)

    return results
