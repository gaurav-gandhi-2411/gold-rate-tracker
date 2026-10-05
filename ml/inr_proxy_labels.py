"""inr_proxy_labels.py — the LABEL-only counterpart to ml.inr_proxy (M1 follow-up).

ml.inr_proxy.build_proxy_history is correctly leakage-safe for FEATURE use (T-1
lag on every driver, per its own docstring) — but GG's 2026-09-23 follow-up
separates two requirements that build were conflating:

  FEATURES must be leakage-safe: only values known before each IBJA fix.
  LABELS only need to track the real price's moves faithfully; they describe
    something that already happened, so they don't need leakage protection.

Using the T-1-lagged (feature-safe) series as a LABEL source is unnecessarily
conservative and, per the diagnostic below, measurably worse: this module's
`build_label_series` uses SAME-DAY (unlagged) COMEX/FX data — still
roll-adjusted (a data-quality fix, orthogonal to leakage) and still
walk-forward-calibrated (never fit on future data — a genuine leakage
concern for the *calibration parameters*, independent of the lag question)
— to produce the best-tracking available label series.

Diagnostics (`ml.inr_proxy --validate` only reports one overall number; this
module answers WHY it disagrees ~33% of the time and where the disagreement
concentrates):
  - compute_lag_agreement: direction agreement between a proxy candidate
    shifted by each of several day-offsets and real IBJA changes. A peak
    away from lag 0 would mean timing misalignment; see module-level
    RESULTS SUMMARY in the docstring of `python -m ml.inr_proxy_labels
    --diagnose` output / docs/adr/032 for the actual measured shape.
  - compute_magnitude_bucket_agreement: direction agreement bucketed by the
    real IBJA move size (Rs/gram), with Wilson CIs — tests whether large
    moves are tracked reliably even if small ones are noisy (motivating a
    dead-zone label if so).

Usage (from repo root):
    python -m ml.inr_proxy_labels --build      # writes data/history_seed_inr22k_label.parquet
    python -m ml.inr_proxy_labels --diagnose   # prints lag-sweep + magnitude-bucket agreement
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from ml.inr_proxy import (
    DATA_DIR,
    DUTY_TABLE_PATH,
    GRAMS_PER_QUOTE_UNIT,
    IBJA_PARQUET_PATH,
    PROXY_START_DATE,
    PURITY_22K_OF_24K,
    TROY_OZ_TO_GRAM,
    _detect_and_adjust_rolls,
    _fetch_raw_drivers,
    _load_ibja_per_g,
    _walk_forward_premium,
    load_duty_schedule,
)

LABEL_OUTPUT_PATH: Path = DATA_DIR / "history_seed_inr22k_label.parquet"
_FETCH_BUFFER_DAYS = 30


def _wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = successes / n
    denom = 1 + z**2 / n
    centre = p + z**2 / (2 * n)
    margin = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))
    return ((centre - margin) / denom, (centre + margin) / denom)


def _build_raw_unlagged(start: str, end: str) -> pd.Series:
    """Same-day (no T-1 lag) raw pre-duty proxy — see module docstring for
    why this is fine for LABEL use but must never be used as a FEATURE."""
    drivers = _fetch_raw_drivers(start, end)
    gc_adjusted, _ = _detect_and_adjust_rolls(drivers["gold_usd"], drivers["gld"])
    return (
        gc_adjusted
        / TROY_OZ_TO_GRAM
        * drivers["usd_inr"]
        * PURITY_22K_OF_24K
        * GRAMS_PER_QUOTE_UNIT
    )


def build_label_series(
    start: str = PROXY_START_DATE,
    end: str | None = None,
    duty_events_path: Path = DUTY_TABLE_PATH,
    ibja_path: Path = IBJA_PARQUET_PATH,
) -> pd.DataFrame:
    """Build the LABEL-only proxy series: same-day drivers, roll-adjusted,
    duty-adjusted, walk-forward-calibrated against real IBJA. Structurally
    identical to ml.inr_proxy.build_proxy_history except for the missing
    T-1 lag — see that module for the leakage-safe FEATURE version.
    """
    if end is None:
        end = (date.today() + timedelta(days=1)).isoformat()
    fetch_start = (
        datetime.fromisoformat(start).date() - timedelta(days=_FETCH_BUFFER_DAYS)
    ).isoformat()

    raw_unlagged = _build_raw_unlagged(fetch_start, end)
    full_index = pd.DatetimeIndex(raw_unlagged.index)

    duty_pct = load_duty_schedule(path=duty_events_path, index=full_index)
    raw_with_duty = raw_unlagged * (1.0 + duty_pct / 100.0)

    ibja = _load_ibja_per_g(ibja_path)["ibja_22k_per_10g"].reindex(full_index)

    try:
        premium = _walk_forward_premium(raw_with_duty, ibja)
    except ValueError:
        result = pd.DataFrame(
            {
                "raw_pre_duty": raw_unlagged,
                "raw_with_duty": raw_with_duty,
                "label_22k_per_10g": raw_with_duty,
                "is_walk_forward_oos": False,
            }
        )
        return result.loc[start:end]  # type: ignore[misc]

    first_slope, first_intercept = premium["slope"].iloc[0], premium["intercept"].iloc[0]
    label = first_slope * raw_with_duty + first_intercept
    label.loc[premium.index] = premium["predicted"]

    is_oos = pd.Series(False, index=full_index)
    is_oos.loc[premium.index] = premium["is_oos"]

    result = pd.DataFrame(
        {
            "raw_pre_duty": raw_unlagged,
            "raw_with_duty": raw_with_duty,
            "label_22k_per_10g": label,
            "is_walk_forward_oos": is_oos,
        }
    )
    return result.loc[start:end]  # type: ignore[misc]


def save_label_series(df: pd.DataFrame, path: Path = LABEL_OUTPUT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)


# ---------------------------------------------------------------------------
# Diagnostics: (a) lag sweep, (b) magnitude buckets
# ---------------------------------------------------------------------------


def _new_value_direction_pairs(candidate: pd.Series, ibja: pd.Series) -> pd.DataFrame:
    """Align candidate/ibja on date, drop carried-forward (non-new) IBJA
    values, and return a DataFrame of {d_candidate, d_ibja_per_gram} pairs
    for genuine new-value days only."""
    merged = pd.DataFrame({"candidate": candidate, "ibja": ibja}).dropna()
    is_new_value = merged["ibja"].diff().fillna(1.0) != 0
    changes = merged[is_new_value].copy()
    changes["d_candidate"] = changes["candidate"].diff()
    changes["d_ibja"] = changes["ibja"].diff()
    changes["d_ibja_per_gram"] = changes["d_ibja"] / 10.0
    return changes.dropna(subset=["d_candidate", "d_ibja"])


def compute_lag_agreement(
    candidate: pd.Series, ibja: pd.Series, lags: tuple[int, ...] = (-2, -1, 0, 1, 2)
) -> dict:
    """Direction agreement between `candidate` shifted by each lag and real
    IBJA changes. `candidate.shift(-lag)` at lag=+1 tests whether
    `candidate` is running one day BEHIND ibja (using tomorrow's candidate
    value to explain today's real move); lag=-1 tests one day AHEAD.
    """
    results: dict = {}
    for lag in lags:
        shifted = candidate.shift(-lag)
        pairs = _new_value_direction_pairs(shifted, ibja)
        pairs = pairs[pairs["d_ibja"] != 0]
        n = len(pairs)
        if n == 0:
            results[str(lag)] = {"n": 0, "agreement": None, "wilson_ci_95": None}
            continue
        agree = int((np.sign(pairs["d_candidate"]) == np.sign(pairs["d_ibja"])).sum())
        results[str(lag)] = {
            "n": n,
            "agreement": agree / n,
            "wilson_ci_95": list(_wilson_ci(agree, n)),
        }
    return results


_DEFAULT_BUCKETS: tuple[float, ...] = (0, 20, 50, 100, 200, 500, float("inf"))
_DEFAULT_BUCKET_LABELS: tuple[str, ...] = (
    "<20",
    "20-50",
    "50-100",
    "100-200",
    "200-500",
    "500+",
)


def compute_magnitude_bucket_agreement(
    candidate: pd.Series,
    ibja: pd.Series,
    buckets: tuple[float, ...] = _DEFAULT_BUCKETS,
    bucket_labels: tuple[str, ...] = _DEFAULT_BUCKET_LABELS,
) -> dict:
    """Direction agreement bucketed by |real IBJA move| in Rs/gram, with
    Wilson CIs per bucket."""
    pairs = _new_value_direction_pairs(candidate, ibja)
    pairs = pairs[pairs["d_ibja"] != 0].copy()
    pairs["agree"] = np.sign(pairs["d_candidate"]) == np.sign(pairs["d_ibja"])
    pairs["abs_move_per_gram"] = pairs["d_ibja_per_gram"].abs()
    pairs["bucket"] = pd.cut(
        pairs["abs_move_per_gram"], bins=buckets, labels=bucket_labels, right=False
    )

    results: dict = {}
    for label in bucket_labels:
        sub = pairs[pairs["bucket"] == label]
        n = len(sub)
        if n == 0:
            results[label] = {"n": 0, "agreement": None, "wilson_ci_95": None}
            continue
        agree = int(sub["agree"].sum())
        results[label] = {
            "n": n,
            "agreement": agree / n,
            "wilson_ci_95": list(_wilson_ci(agree, n)),
        }
    return results


def run_diagnostics(
    label_path: Path = LABEL_OUTPUT_PATH, ibja_path: Path = IBJA_PARQUET_PATH
) -> dict:
    label_df = pd.read_parquet(label_path)
    ibja = _load_ibja_per_g(ibja_path)["ibja_22k_per_10g"]

    raw_unlagged = label_df["raw_pre_duty"]  # same-day, pre-calibration series
    lag_sweep = compute_lag_agreement(raw_unlagged, ibja)
    bucket_agreement = compute_magnitude_bucket_agreement(raw_unlagged, ibja)

    # Overall summary for the realigned (lag=0) label series, calibrated,
    # OOS-only -- comparable to ml.inr_proxy.validate_against_ibja's report.
    oos = label_df[label_df["is_walk_forward_oos"]]
    merged = pd.DataFrame({"label": oos["label_22k_per_10g"], "ibja": ibja}).dropna()
    residual = merged["label"] - merged["ibja"]
    overall: dict[str, object] = {
        "n_level_overlap_oos": len(merged),
        "mae_rs_per_10g": float(residual.abs().mean()) if len(merged) else None,
        "mae_pct": float((residual.abs() / merged["ibja"]).mean() * 100) if len(merged) else None,
        "correlation_level": float(merged["label"].corr(merged["ibja"])) if len(merged) else None,
    }
    dir_pairs = _new_value_direction_pairs(oos["label_22k_per_10g"], ibja)
    dir_pairs = dir_pairs[dir_pairs["d_ibja"] != 0]
    n_dir = len(dir_pairs)
    if n_dir:
        agree = int((np.sign(dir_pairs["d_candidate"]) == np.sign(dir_pairs["d_ibja"])).sum())
        overall["n_direction_days"] = n_dir
        overall["direction_agreement"] = agree / n_dir
        overall["direction_agreement_wilson_ci_95"] = list(_wilson_ci(agree, n_dir))

    return {
        "lag_sweep": lag_sweep,
        "magnitude_bucket_agreement": bucket_agreement,
        "realigned_label_overall": overall,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--diagnose", action="store_true")
    args = parser.parse_args()

    if args.build or not args.diagnose:
        print("Building label series...")
        df = build_label_series()
        save_label_series(df)
        print(f"Wrote {len(df)} rows to {LABEL_OUTPUT_PATH}")

    if args.diagnose:
        result = run_diagnostics()
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
