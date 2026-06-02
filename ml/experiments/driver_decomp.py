"""Experiment Phi10A: driver-decomposition forecast vs flat-naive at h=5.

Two variants:
  driver_decomp_usdinr_drift_gold_rw:
    ibja_hat = premium_hat x gold_usd_t (flat) x usdinr_hat_drift
  driver_decomp_usdinr_drift_gold_drift:
    ibja_hat = premium_hat x gold_usd_hat_drift x usdinr_hat_drift

premium_hat: trailing-30-IBJA-observation median of ibja / (gold_usd * usd_inr).
usdinr_hat: drift model using last-30-calendar-day mean daily change.
gold_usd_hat: flat (RW) or drift model (same window).

Pre-registered gate (ALL must hold for beats_naive=True):
  1. (mae_naive - mae_variant) / mae_naive >= 0.02
  2. Wilcoxon signed-rank p < 0.05
  3. n_folds_ge30ctx >= 30
  4. non_bull_signed_improvement >= -0.02
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ml.backtest import _HORIZON, _MIN_CONTEXT_DAYS
from ml.metrics import compute_wilcoxon_p

_MACRO_CACHE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "macro_cache.parquet"

_PREMIUM_WINDOW = 30  # IBJA observations for trailing premium
_DRIFT_WINDOW_DAYS = 30  # calendar days for drift estimation


def load_macro_series(
    cache_path: Path = _MACRO_CACHE_PATH,
) -> tuple[pd.Series, pd.Series]:
    """Load gold_usd and usd_inr from macro cache as UTC-indexed daily Series.

    Returns (gold_usd, usd_inr). Caller handles alignment to IBJA dates.
    """
    if not cache_path.exists():
        raise FileNotFoundError(
            f"Macro cache not found at {cache_path}. "
            "Run: python ml/macro.py  (or python ml/macro.py --full for cold start)"
        )
    df = pd.read_parquet(cache_path)
    df.index = pd.to_datetime(df.index, utc=True)
    df = df.sort_index()
    gold_usd = df["gold_usd"]
    usd_inr = df["usd_inr"]
    return gold_usd, usd_inr


def compute_trailing_premium(
    context: pd.Series,
    gold_usd: pd.Series,
    usd_inr: pd.Series,
    window: int = _PREMIUM_WINDOW,
) -> float | None:
    """Trailing-window median premium at context end.

    premium_i = ibja_i / (gold_usd_i * usd_inr_i) for each IBJA observation
    in the last `window` observations of context, aligned to macro via ffill.

    Returns None if fewer than 5 valid premium values found.
    """
    recent_ctx = context.iloc[-window:]
    premiums: list[float] = []
    for date, ibja_val in recent_ctx.items():
        ts = pd.Timestamp(str(date), tz="UTC")
        g_slice = gold_usd.loc[:ts]
        u_slice = usd_inr.loc[:ts]
        if len(g_slice) == 0 or len(u_slice) == 0:
            continue
        g_val = float(g_slice.iloc[-1])
        u_val = float(u_slice.iloc[-1])
        if np.isnan(g_val) or np.isnan(u_val) or g_val <= 0 or u_val <= 0:
            continue
        p = ibja_val / (g_val * u_val)
        if not np.isnan(p) and p > 0:
            premiums.append(p)
    if len(premiums) < 5:
        return None
    return float(np.median(premiums))


def compute_daily_drift(
    series: pd.Series,
    before_ts: pd.Timestamp,
    window_days: int = _DRIFT_WINDOW_DAYS,
) -> float:
    """Mean daily change of series over the last window_days before before_ts.

    Uses macro data up to and including before_ts, over a trailing calendar window.
    Returns 0.0 if no data points found in the window.
    """
    slice_ = series.loc[:before_ts]
    cutoff = before_ts - pd.Timedelta(days=window_days)
    window_slice = slice_.loc[cutoff:]
    changes = window_slice.dropna().diff().dropna()
    if len(changes) == 0:
        return 0.0
    return float(changes.mean())


def forecast_driver(
    last_val: float,
    drift_per_day: float,
    calendar_days_ahead: list[int],
    use_drift: bool,
) -> list[float]:
    """Forecast driver values at each calendar day offset.

    Parameters
    ----------
    last_val : current (context-end) value of the driver.
    drift_per_day : estimated mean daily change (used only if use_drift=True).
    calendar_days_ahead : list of calendar-day offsets for each forecast step.
    use_drift : if True, apply drift; if False, return flat random-walk carry.
    """
    if use_drift:
        return [last_val + d * drift_per_day for d in calendar_days_ahead]
    return [last_val] * len(calendar_days_ahead)


def _apply_phi10a_gate(
    mae_variant: float,
    mae_naive: float,
    wilcoxon_p: float | None,
    n_folds_ge30ctx: int,
    non_bull_signed_improvement: float | None,
) -> tuple[bool, float, dict[str, bool]]:
    """Apply the pre-registered Phi10A promotion gate.

    Returns (beats_naive, pct_improvement, gate_details).
    All four sub-gates must pass for beats_naive=True.
    """
    pct = (mae_naive - mae_variant) / mae_naive if mae_naive > 0 else 0.0
    gate_pct = pct >= 0.02
    gate_wilcoxon = wilcoxon_p is not None and wilcoxon_p < 0.05
    gate_n_folds = n_folds_ge30ctx >= 30
    # non-bull check: if no non-bull folds, we cannot verify — treat as passing
    gate_non_bull = non_bull_signed_improvement is None or non_bull_signed_improvement >= -0.02
    beats = gate_pct and gate_wilcoxon and gate_n_folds and gate_non_bull
    gate_details: dict[str, bool] = {
        "pct_ge_2pct": gate_pct,
        "wilcoxon_lt_005": gate_wilcoxon,
        "n_folds_ge30": gate_n_folds,
        "non_bull_not_inverted": gate_non_bull,
    }
    return beats, pct, gate_details


def run_driver_decomp_experiment(
    ibja_series: pd.Series,
    gold_usd: pd.Series,
    usd_inr: pd.Series,
    horizon: int = _HORIZON,
    min_context: int = _MIN_CONTEXT_DAYS,
) -> list[dict[str, Any]]:
    """Run both driver-decomp variants, return list of result dicts.

    Variants:
      1. usdinr=drift, gold_usd=random-walk
      2. usdinr=drift, gold_usd=drift

    Fold logic mirrors yield_folds() exactly (same context_end_idx range).
    Skips folds where context < 30 or macro unavailable at context_end.
    """
    # Define both variants: (name, usdinr_method, gold_usd_method, use_gold_drift)
    variants: list[tuple[str, str, str, bool]] = [
        (
            "driver_decomp_usdinr_drift_gold_rw",
            "drift",
            "random_walk",
            False,
        ),
        (
            "driver_decomp_usdinr_drift_gold_drift",
            "drift",
            "drift",
            True,
        ),
    ]

    # Collect per-fold data across both variants simultaneously
    n = len(ibja_series)

    # Per-variant accumulators
    accumulators: list[dict[str, Any]] = [
        {
            "mae_variant_per_fold": [],
            "mae_naive_per_fold": [],
            "non_bull_mae_variant": [],
            "non_bull_mae_naive": [],
            "n_folds_skipped_no_macro": 0,
            "n_folds_skipped_sub30": 0,
        }
        for _ in variants
    ]

    premium_values: list[float] = []  # for premium_stats (same across variants)
    last_drift_gold: float = 0.0  # last-fold drift for falsifier reporting
    last_drift_usdinr: float = 0.0  # last-fold drift for falsifier reporting

    for context_end_idx in range(min_context - 1, n - horizon):
        context = ibja_series.iloc[: context_end_idx + 1]
        actuals_slice = ibja_series.iloc[context_end_idx + 1 : context_end_idx + 1 + horizon]
        if len(actuals_slice) < horizon:
            break

        actuals = actuals_slice.values.tolist()
        actuals_dates = ibja_series.index[context_end_idx + 1 : context_end_idx + 1 + horizon]
        context_last = float(context.iloc[-1])
        naive_fc = [context_last] * horizon

        # Skip sub-30-context folds
        if len(context) < 30:
            for acc in accumulators:
                acc["n_folds_skipped_sub30"] += 1
            continue

        context_end_date = context.index[-1]
        context_end_ts = pd.Timestamp(context_end_date, tz="UTC")

        # Check macro available at context_end
        gold_slice = gold_usd.loc[:context_end_ts]
        inr_slice = usd_inr.loc[:context_end_ts]
        if len(gold_slice) == 0 or len(inr_slice) == 0:
            for acc in accumulators:
                acc["n_folds_skipped_no_macro"] += 1
            continue

        gold_last = float(gold_slice.iloc[-1])
        inr_last = float(inr_slice.iloc[-1])
        if np.isnan(gold_last) or np.isnan(inr_last) or gold_last <= 0 or inr_last <= 0:
            for acc in accumulators:
                acc["n_folds_skipped_no_macro"] += 1
            continue

        # Compute trailing premium
        prem = compute_trailing_premium(context, gold_usd, usd_inr, window=_PREMIUM_WINDOW)
        if prem is None:
            for acc in accumulators:
                acc["n_folds_skipped_no_macro"] += 1
            continue

        premium_values.append(prem)

        # Compute drifts at context_end
        drift_usdinr = compute_daily_drift(usd_inr, context_end_ts, _DRIFT_WINDOW_DAYS)
        drift_gold = compute_daily_drift(gold_usd, context_end_ts, _DRIFT_WINDOW_DAYS)
        last_drift_gold = drift_gold
        last_drift_usdinr = drift_usdinr

        # Calendar days ahead for each actual
        cal_days_ahead = [
            (pd.Timestamp(actuals_dates[h]) - pd.Timestamp(context_end_date)).days
            for h in range(horizon)
        ]

        # Per-variant forecast
        for vi, (_, _, _, use_gold_drift) in enumerate(variants):
            usdinr_fc = forecast_driver(inr_last, drift_usdinr, cal_days_ahead, use_drift=True)
            gold_fc = forecast_driver(
                gold_last, drift_gold, cal_days_ahead, use_drift=use_gold_drift
            )

            # Compose IBJA forecast: premium_hat * gold_hat * inr_hat
            ibja_fc = [prem * gold_fc[h] * usdinr_fc[h] for h in range(horizon)]

            mae_variant_fold = float(
                np.mean([abs(ibja_fc[h] - actuals[h]) for h in range(horizon)])
            )
            mae_naive_fold = float(np.mean([abs(naive_fc[h] - actuals[h]) for h in range(horizon)]))
            accumulators[vi]["mae_variant_per_fold"].append(mae_variant_fold)
            accumulators[vi]["mae_naive_per_fold"].append(mae_naive_fold)

            # Non-bull tracking: realized h=5 change <= 0
            realized_h5_change = actuals[horizon - 1] - context_last
            if realized_h5_change <= 0:
                accumulators[vi]["non_bull_mae_variant"].append(mae_variant_fold)
                accumulators[vi]["non_bull_mae_naive"].append(mae_naive_fold)

    # Build result dicts
    results: list[dict[str, Any]] = []

    # Premium stats (shared — same folds processed for both variants)
    premium_stats: dict[str, float | None]
    if premium_values:
        prem_arr = np.array(premium_values)
        prem_mean = float(np.mean(prem_arr))
        prem_std = float(np.std(prem_arr))
        prem_cv = prem_std / prem_mean if prem_mean > 0 else float("nan")
        premium_stats = {
            "mean": round(prem_mean, 6),
            "std": round(prem_std, 6),
            "cv": round(prem_cv, 4),
        }
    else:
        premium_stats = {"mean": None, "std": None, "cv": None}

    for vi, (name, usdinr_method, gold_usd_method, _) in enumerate(variants):
        acc = accumulators[vi]
        mv_folds: list[float] = acc["mae_variant_per_fold"]
        mn_folds: list[float] = acc["mae_naive_per_fold"]
        n_folds_ge30ctx = len(mv_folds)

        if n_folds_ge30ctx == 0:
            results.append(
                {
                    "name": name,
                    "experiment": "Phi10A",
                    "horizon": horizon,
                    "usdinr_method": usdinr_method,
                    "gold_usd_method": gold_usd_method,
                    "mae_variant": None,
                    "mae_naive": None,
                    "pct_improvement": None,
                    "wilcoxon_p": None,
                    "n_folds_ge30ctx": 0,
                    "n_folds_skipped_no_macro": acc["n_folds_skipped_no_macro"],
                    "n_folds_skipped_sub30": acc["n_folds_skipped_sub30"],
                    "n_non_bull_folds": 0,
                    "non_bull_signed_improvement": None,
                    "premium_stats": premium_stats,
                    "beats_naive": False,
                    "gate_details": {
                        "pct_ge_2pct": False,
                        "wilcoxon_lt_005": False,
                        "n_folds_ge30": False,
                        "non_bull_not_inverted": False,
                    },
                    "note": "No folds with macro coverage — check macro_cache.parquet",
                }
            )
            continue

        mae_variant = float(np.mean(mv_folds))
        mae_naive = float(np.mean(mn_folds))
        paired_diffs = [v - n for v, n in zip(mv_folds, mn_folds, strict=False)]
        wilcoxon_p = compute_wilcoxon_p(paired_diffs) if n_folds_ge30ctx >= 6 else None

        # Non-bull signed improvement
        nb_v: list[float] = acc["non_bull_mae_variant"]
        nb_n: list[float] = acc["non_bull_mae_naive"]
        n_non_bull = len(nb_v)
        non_bull_signed_improvement: float | None = None
        if n_non_bull > 0:
            nb_mae_v = float(np.mean(nb_v))
            nb_mae_n = float(np.mean(nb_n))
            non_bull_signed_improvement = (nb_mae_n - nb_mae_v) / nb_mae_n if nb_mae_n > 0 else 0.0
            non_bull_signed_improvement = round(non_bull_signed_improvement, 5)

        beats_naive, pct_improvement, gate_details = _apply_phi10a_gate(
            mae_variant,
            mae_naive,
            wilcoxon_p,
            n_folds_ge30ctx,
            non_bull_signed_improvement,
        )

        # Falsifier flags (informational — last-fold drift values used for reporting)
        falsifiers: dict[str, Any] = {}
        if abs(last_drift_gold) < 1e-4:
            falsifiers["gold_drift_near_zero"] = True  # collapses gold forecast to RW
        if abs(last_drift_usdinr) < 1e-6:
            falsifiers["usdinr_drift_near_zero"] = True  # collapses toward flat-naive
        if abs(pct_improvement) < 0.005:
            falsifiers["essentially_tied_with_naive"] = True

        results.append(
            {
                "name": name,
                "experiment": "Phi10A",
                "horizon": horizon,
                "usdinr_method": usdinr_method,
                "gold_usd_method": gold_usd_method,
                "mae_variant": round(mae_variant, 2),
                "mae_naive": round(mae_naive, 2),
                "pct_improvement": round(pct_improvement, 5),
                "wilcoxon_p": wilcoxon_p,
                "n_folds_ge30ctx": n_folds_ge30ctx,
                "n_folds_skipped_no_macro": acc["n_folds_skipped_no_macro"],
                "n_folds_skipped_sub30": acc["n_folds_skipped_sub30"],
                "n_non_bull_folds": n_non_bull,
                "non_bull_signed_improvement": non_bull_signed_improvement,
                "premium_stats": premium_stats,
                "beats_naive": beats_naive,
                "gate_details": gate_details,
            }
        )

    return results
