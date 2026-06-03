"""
drivers.py — Log-decomposition attribution of recent Indian gold moves.

IBJA ≈ gold_usd × usdinr × premium is MULTIPLICATIVE. Uses the log decomposition:

    Δln(IBJA_916) = Δln(gold_usd) + Δln(usd_inr) + Δln(premium)

which is genuinely additive — terms sum exactly, no cross-term artefact.

HONESTY HARD LINE (ADR 005 + Φ14 spec):
  - DESCRIPTIVE only — attributes an ALREADY-OBSERVED move, NOT a forecast.
  - The premium residual is a sanity check; it is NEVER shown as a "driver".
  - If |premium share| > PREMIUM_THRESHOLD_PCT → attribution_valid = False;
    display degrades to driver-state-only or suppresses (norm #8, no silent fallback).
  - If macro is stale → attribution_valid = False for all windows.

Units (verified from ml/calibration.py and ml/ibja.py):
  ibja pm_916 : INR per 10g (raw integer; ibja_per_g = pm_916 / 10)
  macro gold_usd : USD per troy oz  (GC=F)
  macro usd_inr  : INR per USD      (INR=X)
  tanishq 22k    : INR per gram     (prices.json)
"""

from __future__ import annotations

import json
import logging
import math
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Unit constants
# ---------------------------------------------------------------------------
_TROY_G_PER_OZ: float = 31.1035       # grams per troy ounce
_PURITY_916: float = 0.916             # 916‰ purity (22K)
# Pure-gold troy-oz equivalent in 10g of 916-purity gold
_CONV_10G_916: float = (10.0 / _TROY_G_PER_OZ) * _PURITY_916  # ≈ 0.2945

# ---------------------------------------------------------------------------
# Tuning knobs (all in one place, auditable)
# ---------------------------------------------------------------------------
PREMIUM_THRESHOLD_PCT: float = 15.0    # |premium share| above this → attribution invalid
MACRO_STALE_THRESHOLD_DAYS: float = 14.0  # matches macro.py hard-fail threshold
WINDOWS_DAYS: list[int] = [7, 30]     # attribution windows for 7d headline + 30d context


# ---------------------------------------------------------------------------
# Private loaders
# ---------------------------------------------------------------------------

def _load_ibja(data_dir: Path) -> pd.DataFrame:
    """Load ibja_rates.parquet with parsed date index and ibja_10g column (INR/10g)."""
    path = data_dir / "ibja_rates.parquet"
    if not path.exists():
        return pd.DataFrame()
    ibja = pd.read_parquet(path)
    ibja["date_parsed"] = pd.to_datetime(ibja["date"])
    ibja = ibja.set_index("date_parsed").sort_index()
    # Prefer PM rate (more representative closing fix); fall back to AM
    ibja["ibja_10g"] = ibja["pm_916"].fillna(ibja["am_916"])
    return ibja[["ibja_10g"]].dropna()


def _load_macro(data_dir: Path) -> pd.DataFrame:
    """Load macro_cache.parquet with tz-naive DatetimeIndex."""
    path = data_dir / "macro_cache.parquet"
    if not path.exists():
        return pd.DataFrame()
    macro = pd.read_parquet(path)
    macro.index = pd.to_datetime(macro.index, utc=True)
    macro.index = macro.index.tz_localize(None)
    return macro[["gold_usd", "usd_inr"]].dropna(subset=["gold_usd", "usd_inr"])


def _resolve_macro_staleness(data_dir: Path) -> float | None:
    """Return macro cache age in calendar days; None if unknown.

    Reads macro_status.json (written by macro.py; gitignored) first.
    Falls back to macro_cache.parquet file mtime if status file absent.
    """
    status_path = data_dir / "macro_status.json"
    if status_path.exists():
        try:
            status = json.loads(status_path.read_text())
            return status.get("cache_age_days")
        except Exception:
            pass
    parquet_path = data_dir / "macro_cache.parquet"
    if parquet_path.exists():
        return (time.time() - parquet_path.stat().st_mtime) / 86400
    return None


def _null_window(reason: str) -> dict:
    return {
        "n_obs": 0,
        "t0_date": None,
        "t1_date": None,
        "delta_pct_ibja": None,
        "delta_pct_gold_usd": None,
        "delta_pct_usdinr": None,
        "delta_pct_premium": None,
        "premium_share_pct": None,
        "attribution_valid": False,
        "attribution_valid_reason": reason,
        "total_move_rs_per_g": None,
        "gold_usd_contrib_rs_per_g": None,
        "usdinr_contrib_rs_per_g": None,
        "premium_contrib_rs_per_g": None,
    }


# ---------------------------------------------------------------------------
# Core: single-window log decomposition
# ---------------------------------------------------------------------------

def _decompose_window(
    merged: pd.DataFrame,
    window_days: int,
    tanishq_df: pd.DataFrame | None,
) -> dict:
    """Log decomposition for one [t0, t1] window.

    Parameters
    ----------
    merged : DataFrame with columns ibja_10g, gold_usd, usd_inr,
             ln_ibja, ln_gold_usd, ln_usdinr, ln_premium (pre-computed).
    window_days : calendar days to look back from the most recent row.
    tanishq_df : optional; prices.json as DataFrame with columns ts (UTC) and 22k (INR/g).
                 Used only for display-ready Rs contributions; decomposition is IBJA-based.
    """
    now = merged.index.max()
    w = merged[merged.index >= now - pd.Timedelta(days=window_days)]

    if len(w) < 2:
        return _null_window(
            f"fewer than 2 IBJA/macro rows in past {window_days}d"
        )

    t0, t1 = w.iloc[0], w.iloc[-1]

    dln_ibja = float(t1["ln_ibja"]     - t0["ln_ibja"])
    dln_g    = float(t1["ln_gold_usd"] - t0["ln_gold_usd"])
    dln_r    = float(t1["ln_usdinr"]   - t0["ln_usdinr"])
    dln_p    = float(t1["ln_premium"]  - t0["ln_premium"])

    result: dict = {
        "n_obs": len(w),
        "t0_date": w.index[0].strftime("%Y-%m-%d"),
        "t1_date": w.index[-1].strftime("%Y-%m-%d"),
        "delta_pct_ibja":     round(dln_ibja * 100, 3),
        "delta_pct_gold_usd": round(dln_g * 100, 3),
        "delta_pct_usdinr":   round(dln_r * 100, 3),
        "delta_pct_premium":  round(dln_p * 100, 3),
        "total_move_rs_per_g": None,
        "gold_usd_contrib_rs_per_g": None,
        "usdinr_contrib_rs_per_g": None,
        "premium_contrib_rs_per_g": None,
    }

    if abs(dln_ibja) < 1e-8:
        result["premium_share_pct"] = 0.0
        result["attribution_valid"] = False
        result["attribution_valid_reason"] = "IBJA unchanged over window — no move to attribute"
        return result

    premium_share = dln_p / dln_ibja
    prem_abs_pct = abs(premium_share) * 100.0

    result["premium_share_pct"] = round(prem_abs_pct, 1)

    if prem_abs_pct > PREMIUM_THRESHOLD_PCT:
        result["attribution_valid"] = False
        result["attribution_valid_reason"] = (
            f"premium share {prem_abs_pct:.1f}% exceeds {PREMIUM_THRESHOLD_PCT:.0f}% threshold"
        )
    else:
        result["attribution_valid"] = True
        result["attribution_valid_reason"] = "clean — premium share within threshold"

    # Tanishq total move in Rs/g for display
    total_move: float | None = None
    if tanishq_df is not None and len(tanishq_df) > 1:
        p_now = float(tanishq_df["22k"].iloc[-1])
        cutoff = tanishq_df["ts"].iloc[-1] - pd.Timedelta(days=window_days)
        period = tanishq_df[tanishq_df["ts"] >= cutoff]
        if len(period) > 1:
            total_move = round(p_now - float(period["22k"].iloc[0]), 1)

    result["total_move_rs_per_g"] = total_move

    if total_move is not None:
        sg = dln_g / dln_ibja
        sr = dln_r / dln_ibja
        sp = dln_p / dln_ibja
        result["gold_usd_contrib_rs_per_g"] = round(sg * total_move, 1)
        result["usdinr_contrib_rs_per_g"]   = round(sr * total_move, 1)
        result["premium_contrib_rs_per_g"]  = round(sp * total_move, 1)

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_driver_attribution(
    data_dir: Path = DATA_DIR,
    macro_staleness_days: float | None = None,
) -> dict:
    """Compute log-decomposition driver attribution for configured windows.

    Returns a dict for inclusion in forecast.json as ``driver_context``.
    Never raises — all failures produce attribution_valid=False with a reason string
    (norm #8: no silent fallback).

    Parameters
    ----------
    data_dir : path to the data directory (override for testing).
    macro_staleness_days : optional explicit age override (skips file read).
    """
    if macro_staleness_days is None:
        macro_staleness_days = _resolve_macro_staleness(data_dir)

    macro_fresh = (
        macro_staleness_days is not None
        and macro_staleness_days <= MACRO_STALE_THRESHOLD_DAYS
    )

    ctx: dict = {
        "computed_at": datetime.now(UTC).isoformat(),
        "macro_staleness_days": (
            round(macro_staleness_days, 2) if macro_staleness_days is not None else None
        ),
        "macro_fresh": macro_fresh,
        "premium_threshold_pct": PREMIUM_THRESHOLD_PCT,
        "windows": {},
        "driver_state": None,
    }

    if not macro_fresh:
        stale_msg = (
            f"macro stale ({macro_staleness_days:.1f}d > {MACRO_STALE_THRESHOLD_DAYS:.0f}d)"
            if macro_staleness_days is not None
            else "macro cache missing"
        )
        logger.warning("drivers: %s — attribution_valid=False for all windows", stale_msg)
        for wd in WINDOWS_DAYS:
            ctx["windows"][f"{wd}d"] = _null_window(stale_msg)
        return ctx

    ibja  = _load_ibja(data_dir)
    macro = _load_macro(data_dir)

    if ibja.empty or macro.empty:
        reason = "IBJA or macro data unavailable"
        logger.warning("drivers: %s", reason)
        for wd in WINDOWS_DAYS:
            ctx["windows"][f"{wd}d"] = _null_window(reason)
        return ctx

    # Load Tanishq prices for display-ready Rs contributions (INR/g)
    tanishq_df: pd.DataFrame | None = None
    prices_path = data_dir / "prices.json"
    if prices_path.exists():
        try:
            raw = json.loads(prices_path.read_text())
            tanishq_df = pd.DataFrame(raw)
            tanishq_df["ts"] = pd.to_datetime(tanishq_df["timestamp"])
            tanishq_df = tanishq_df.sort_values("ts")
        except Exception as exc:
            logger.warning("drivers: could not load prices.json: %s", exc)

    # Merge IBJA + macro on date index
    merged = ibja.join(macro, how="inner")
    merged = merged.dropna(subset=["ibja_10g", "gold_usd", "usd_inr"])

    if len(merged) < 2:
        reason = "insufficient merged rows after IBJA/macro join"
        for wd in WINDOWS_DAYS:
            ctx["windows"][f"{wd}d"] = _null_window(reason)
        return ctx

    # Pre-compute log series (done once; shared across all window calls)
    ln_conv = math.log(_CONV_10G_916)
    merged = merged.copy()
    merged["ln_ibja"]     = np.log(merged["ibja_10g"])
    merged["ln_gold_usd"] = np.log(merged["gold_usd"])
    merged["ln_usdinr"]   = np.log(merged["usd_inr"])
    # ln_premium = ln(ibja) − ln(gold_usd) − ln(usd_inr) − ln(conv)
    merged["ln_premium"]  = (
        merged["ln_ibja"]
        - merged["ln_gold_usd"]
        - merged["ln_usdinr"]
        - ln_conv
    )

    # Driver state: 30d raw % changes for the supporting display copy
    now = merged.index.max()
    w30 = merged[merged.index >= now - pd.Timedelta(days=30)]
    if len(w30) >= 2:
        r0, r1 = w30.iloc[0], w30.iloc[-1]
        ctx["driver_state"] = {
            "usd_inr_now":             round(float(r1["usd_inr"]), 3),
            "gold_usd_now":            round(float(r1["gold_usd"]), 1),
            "usd_inr_30d_pct_change":  round(
                (float(r1["usd_inr"]) - float(r0["usd_inr"])) / float(r0["usd_inr"]) * 100, 2
            ),
            "gold_usd_30d_pct_change": round(
                (float(r1["gold_usd"]) - float(r0["gold_usd"])) / float(r0["gold_usd"]) * 100, 2
            ),
        }

    for wd in WINDOWS_DAYS:
        ctx["windows"][f"{wd}d"] = _decompose_window(merged, wd, tanishq_df)

    return ctx
