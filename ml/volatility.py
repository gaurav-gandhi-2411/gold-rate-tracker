"""
volatility.py — Dynamic 5-day half-width from trailing realized volatility.

Computes "how much has gold been moving lately" as a magnitude-of-movement context
for the good-price section. NOT a forecast. Does not imply direction (ADR 005).

Series: Tanishq 22K from prices.json — the only series with a clean recent
contiguous run (49 days Apr–Jun 2026). IBJA pm_916 has a 14-day gap ending
2026-05-31 leaving only 2 contiguous rows; rejected.

Method (simplest honestly supported):
  trailing realized vol: std of daily log-returns over a 20-day contiguous window,
  scaled to 5-day horizon (× sqrt(5) × current_price). No model fitting.

Floor: 50% of the static conformal PI half-width so a quiet patch never produces
a falsely-tight band. Written as FLOOR_FRACTION to make it auditable.

Degrade: when fewer than MIN_CONTIGUOUS_DAYS contiguous daily readings are
available, falls back to the static conformal PI half-width with is_degraded=True.
This flag is visible in forecast.json — no silent swap (norm #8).
"""

from __future__ import annotations

import contextlib
import math
from typing import TypedDict

# ---------------------------------------------------------------------------
# Constants — all tuning knobs in one place, documented
# ---------------------------------------------------------------------------

# Trailing window for the "recent" vol estimate.
VOL_WINDOW: int = 20

# Minimum contiguous daily readings to produce a dynamic estimate.
# Below this we degrade to the static conformal PI half-width.
MIN_CONTIGUOUS_DAYS: int = 20

# Calendar-day gap tolerance when identifying contiguous runs.
# 3 = Fri→Mon; gold markets close weekends so ≤3 is contiguous.
MAX_GAP_DAYS: int = 3

# Forecast horizon in days.
HORIZON_DAYS: int = 5

# Floor fraction of the static conformal PI half-width.
# Prevents a calm-patch from producing a falsely-tight band.
FLOOR_FRACTION: float = 0.50

# Regime thresholds: recent_std / baseline_std ratio.
# Below CALM_THRESHOLD → "calm"; above ELEVATED_THRESHOLD → "elevated"; else "normal".
CALM_THRESHOLD: float = 0.75
ELEVATED_THRESHOLD: float = 1.35


# ---------------------------------------------------------------------------
# Output type
# ---------------------------------------------------------------------------


class VolContext(TypedDict):
    half_width: int  # floored, nearest integer (Rs.)
    half_width_raw: float  # before floor (Rs.)
    method: str  # always "realized_20d" when not degraded
    window_days: int  # VOL_WINDOW used for the recent estimate
    contiguous_days: int  # length of the most recent contiguous run used
    is_floored: bool  # True when floor_fraction bound the result
    is_degraded: bool  # True when contiguous data was insufficient; reverts to static PI
    floor_fraction: float  # FLOOR_FRACTION constant
    static_pi_half: float  # static conformal PI half-width for reference
    baseline_half_width: int  # full-window baseline (Rs.) used for regime comparison
    regime: str  # "calm" | "normal" | "elevated"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _dedup_daily(prices: list[dict]) -> list[dict]:
    """Return one entry per UTC calendar date (latest timestamp wins)."""
    by_day: dict[str, dict] = {}
    for row in prices:
        day = str(row.get("timestamp", ""))[:10]  # YYYY-MM-DD prefix
        if day not in by_day or row["timestamp"] > by_day[day]["timestamp"]:
            by_day[day] = row
    return sorted(by_day.values(), key=lambda r: r["timestamp"])


def _recent_contiguous_run(daily: list[dict]) -> list[dict]:
    """Return the most recent streak of rows with no gap > MAX_GAP_DAYS.

    Walks backward from the last row to find where the streak breaks.
    """
    if not daily:
        return []
    # Parse dates once (YYYY-MM-DD prefix of timestamp)
    from datetime import date as _date

    dates: list[_date] = []
    for row in daily:
        ts = str(row.get("timestamp", ""))
        try:
            dates.append(_date.fromisoformat(ts[:10]))
        except ValueError:
            dates.append(_date.min)

    # Walk backward until a gap > MAX_GAP_DAYS
    start = len(daily) - 1
    for i in range(len(daily) - 1, 0, -1):
        gap = (dates[i] - dates[i - 1]).days
        if gap > MAX_GAP_DAYS:
            break
        start = i - 1

    return daily[start:]


def _log_returns(prices_rs: list[float]) -> list[float]:
    """Daily log returns; empty if fewer than 2 prices."""
    if len(prices_rs) < 2:
        return []
    return [math.log(prices_rs[i] / prices_rs[i - 1]) for i in range(1, len(prices_rs))]


def _std(values: list[float]) -> float:
    """Sample std dev; 0.0 for fewer than 2 values."""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    return math.sqrt(variance)


def _regime(recent_std: float, baseline_std: float) -> str:
    if baseline_std <= 0:
        return "normal"
    ratio = recent_std / baseline_std
    if ratio < CALM_THRESHOLD:
        return "calm"
    if ratio > ELEVATED_THRESHOLD:
        return "elevated"
    return "normal"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_vol_context(prices: list[dict], static_pi_half: float) -> VolContext:
    """Compute dynamic 5-day half-width from trailing realized volatility.

    Args:
        prices: raw entries from prices.json (may contain multiple readings/day).
        static_pi_half: 80th-pct conformal PI half-width from backtest (fallback + floor anchor).

    Returns:
        VolContext dict — always populated; is_degraded=True signals fallback to static.
    """
    floor_val = FLOOR_FRACTION * static_pi_half

    daily = _dedup_daily(prices)
    contiguous = _recent_contiguous_run(daily)

    # Degrade path: insufficient contiguous data
    if len(contiguous) < MIN_CONTIGUOUS_DAYS:
        return VolContext(
            half_width=round(static_pi_half),
            half_width_raw=static_pi_half,
            method="degraded_static",
            window_days=VOL_WINDOW,
            contiguous_days=len(contiguous),
            is_floored=False,
            is_degraded=True,
            floor_fraction=FLOOR_FRACTION,
            static_pi_half=static_pi_half,
            baseline_half_width=round(static_pi_half),
            regime="normal",
        )

    # Extract 22k price series from the contiguous run
    price_series: list[float] = []
    for row in contiguous:
        with contextlib.suppress(KeyError, ValueError, TypeError):
            price_series.append(float(row["22k"]))

    if len(price_series) < MIN_CONTIGUOUS_DAYS:
        # Price parse failures → degrade
        return VolContext(
            half_width=round(static_pi_half),
            half_width_raw=static_pi_half,
            method="degraded_static",
            window_days=VOL_WINDOW,
            contiguous_days=len(contiguous),
            is_floored=False,
            is_degraded=True,
            floor_fraction=FLOOR_FRACTION,
            static_pi_half=static_pi_half,
            baseline_half_width=round(static_pi_half),
            regime="normal",
        )

    current_price = price_series[-1]

    # Baseline: full contiguous run log-returns
    all_rets = _log_returns(price_series)
    baseline_std = _std(all_rets)
    baseline_hw = baseline_std * math.sqrt(HORIZON_DAYS) * current_price

    # Recent: trailing VOL_WINDOW prices (need VOL_WINDOW+1 prices for VOL_WINDOW returns)
    window_prices = price_series[-(VOL_WINDOW + 1) :]
    recent_rets = _log_returns(window_prices)
    recent_std = _std(recent_rets)
    raw_hw = recent_std * math.sqrt(HORIZON_DAYS) * current_price

    # Apply floor
    floored_hw = max(raw_hw, floor_val)
    is_floored = floored_hw > raw_hw

    return VolContext(
        half_width=round(floored_hw),
        half_width_raw=round(raw_hw, 1),
        method="realized_20d",
        window_days=VOL_WINDOW,
        contiguous_days=len(price_series),
        is_floored=is_floored,
        is_degraded=False,
        floor_fraction=FLOOR_FRACTION,
        static_pi_half=static_pi_half,
        baseline_half_width=round(baseline_hw),
        regime=_regime(recent_std, baseline_std),
    )
