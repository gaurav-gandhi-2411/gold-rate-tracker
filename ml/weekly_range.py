"""ml.weekly_range -- "the price will very likely stay between Rs X and Rs Y over the next 7 days".

Brief item 5 (2026-09-24). Built on the method that measured best on real IBJA in R1 (ADR 041):
historical simulation. R1 found it over-covers on IBJA (85.6% at nominal 80%, 1 day), because
its return distribution comes from the INR proxy, which moves more day to day than IBJA. This
module keeps historical simulation for the SHAPE of the range and calibrates its SCALE on real
IBJA with split conformal, so the range is as narrow as the measured record allows.

What is forecast (the product claim, exactly):
  * "1d":   the next IBJA publication day's PM price is inside [lo, hi].
  * "week": EVERY IBJA PM price published in the 7 calendar days after the as-of day is inside
            [lo, hi] -- the whole path stays in the range, not just the last day. That is what
            "stay between" says. Usually 5 publication days, 4 in a holiday week.
Weekends and holidays: forecasts are issued only on IBJA publication days. The window is fixed
in calendar days, (t, t + 7], and its end date is part of the forecast, so a Saturday or holiday
viewer sees the last publication day's range with its real end date. Days with no publication
inside the window are not guessed at; a window is scored only when the IBJA record for it is
complete (every step between publication days is consecutive, same rule as
ml.direction.dataset: at most one weekday without a publication, i.e. one holiday).

Method, per as-of day t (all fixed before any result was seen; see ADR 043):
  1. Base range, historical simulation on the INR proxy: over the last HS_WINDOW proxy days known
     at t, the k-day path minimum and maximum of cumulative log returns (k = 1, or 5 for a
     week: the path length known at issue time). lo_base = 10th percentile of the path minima, hi_base = 90th percentile of the
     path maxima.
  2. Conformal scale on IBJA: each earlier IBJA window whose last day is strictly before t (fully
     matured, so no leak) gets a score max(path_min / lo_base, path_max / hi_base); the window is
     inside the base range scaled by s exactly when score <= s. s = the ceil((n+1) * 0.8)/n
     empirical quantile of the latest CAL_WINDOW scores (split conformal). No forecast until
     MIN_CAL windows have matured.
  3. Range = P_t * exp(s * lo_base) .. P_t * exp(s * hi_base).

Coverage is measured on the IBJA PM (916) price. The page shows the range on its retail 22K
estimate by applying the same percentage bounds; that conversion is not separately validated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ml.direction.dataset import MAX_WEEKDAYS_PER_STEP

LEVEL = 0.80
HS_WINDOW = 500  # proxy days; same as R1's historical simulation (ml.range_forecast.baselines)
HS_MIN_SAMPLE = 60
CAL_WINDOW = 250  # most recent matured IBJA windows used for the conformal scale
MIN_CAL = 30  # no forecast until this many IBJA windows have matured
WEEK_CALENDAR_DAYS = 7
# Publication days a weekly range is built for when it is issued. A holiday inside the
# window is not known from the data at issue time, so a 4-day holiday week is scored
# against a 5-day range.
WEEK_ISSUE_DAYS = 5
HORIZONS = ("1d", "week")


@dataclass(frozen=True)
class Window:
    """One IBJA as-of day and the publication days its forecast covers."""

    as_of: pd.Timestamp
    price: float
    end: pd.Timestamp  # last calendar day the forecast covers
    days: tuple[pd.Timestamp, ...]  # publication days inside the window
    path_min: float  # min cumulative log return over `days`
    path_max: float


def _consecutive(a: pd.Timestamp, b: pd.Timestamp) -> bool:
    return int(np.busday_count(a.date(), b.date())) <= MAX_WEEKDAYS_PER_STEP


def issue_days(horizon: str) -> int:
    """Path length the range is built for at issue time (see WEEK_ISSUE_DAYS)."""
    return 1 if horizon == "1d" else WEEK_ISSUE_DAYS


def complete_windows(ibja: pd.Series, horizon: str) -> list[Window]:
    """Every IBJA as-of day whose window is fully observed. For "week" the record must be
    consecutive from t up to the first publication day AFTER t + 7, so that no publication day
    inside the window can be missing."""
    dates = list(ibja.index)
    vals = np.log(ibja.to_numpy(dtype=float))
    out: list[Window] = []
    for i, t in enumerate(dates):
        if horizon == "1d":
            j_last = i + 1
            if j_last >= len(dates) or not _consecutive(t, dates[j_last]):
                continue
            idx = [j_last]
            end = dates[j_last]
        else:
            end = t + pd.Timedelta(days=WEEK_CALENDAR_DAYS)
            j = i + 1
            ok = True
            while j < len(dates) and dates[j] <= end:
                if not _consecutive(dates[j - 1], dates[j]):
                    ok = False
                    break
                j += 1
            # j is the first publication day after the window; it proves the window is complete.
            if not ok or j >= len(dates) or not _consecutive(dates[j - 1], dates[j]):
                continue
            idx = list(range(i + 1, j))
            if not idx:
                continue
        rets = vals[idx] - vals[i]
        out.append(
            Window(
                as_of=t,
                price=float(ibja.iloc[i]),
                end=end,
                days=tuple(dates[k] for k in idx),
                path_min=float(rets.min()),
                path_max=float(rets.max()),
            )
        )
    return out


def base_range(proxy: pd.Series, as_of: pd.Timestamp, k: int) -> tuple[float, float] | None:
    """Historical-simulation path range on the proxy known at `as_of` (dates <= as_of)."""
    hist = np.log(proxy[proxy.index <= as_of].to_numpy(dtype=float))[-(HS_WINDOW + k) :]
    n = len(hist) - k
    if n < HS_MIN_SAMPLE:
        return None
    steps = np.stack([hist[s : s + n] for s in range(1, k + 1)], axis=1) - hist[:n, None]
    tail = (1.0 - LEVEL) / 2.0
    lo = float(np.quantile(steps.min(axis=1), tail))
    hi = float(np.quantile(steps.max(axis=1), 1.0 - tail))
    return (min(lo, -1e-6), max(hi, 1e-6))


def score(w: Window, lo: float, hi: float) -> float:
    """Smallest scale s for which the window's whole path is inside [s*lo, s*hi]."""
    return max(w.path_min / lo, w.path_max / hi, 0.0)


def conformal_scale(scores: list[float]) -> float | None:
    if len(scores) < MIN_CAL:
        return None
    s = np.sort(np.asarray(scores[-CAL_WINDOW:]))
    n = len(s)
    rank = min(n, math.ceil((n + 1) * LEVEL))
    return float(s[rank - 1])


def walk_forward(proxy: pd.Series, ibja: pd.Series, horizon: str) -> pd.DataFrame:
    """Out-of-sample: each window's scale uses only windows that ended strictly before it."""
    windows = complete_windows(ibja, horizon)
    rows = []
    matured: list[tuple[pd.Timestamp, float]] = []  # (window end, score) in end order
    pending: list[tuple[pd.Timestamp, float]] = []
    for w in windows:
        base = base_range(proxy, w.as_of, issue_days(horizon))
        if base is None:
            continue
        lo, hi = base
        pending.sort()
        while pending and pending[0][0] < w.as_of:
            matured.append(pending.pop(0))
        s = conformal_scale([sc for _, sc in matured])
        sc = score(w, lo, hi)
        pending.append((w.days[-1], sc))
        rows.append(
            {
                "as_of": w.as_of,
                "end": w.end,
                "k": len(w.days),
                "price": w.price,
                "base_lo": lo,
                "base_hi": hi,
                "scale": s,
                "score": sc,
                "raw_hit": sc <= 1.0,
                "hit": None if s is None else sc <= s,
                "raw_width_pct": (math.exp(hi) - math.exp(lo)) * 100,
                "width_pct": None if s is None else (math.exp(s * hi) - math.exp(s * lo)) * 100,
                "n_cal": len(matured),
            }
        )
    return pd.DataFrame(rows)


def times_out_of_ten(coverage: float) -> int:
    """User-facing "right about N times out of 10": measured coverage, rounded DOWN."""
    return math.floor(coverage * 10 + 1e-9)
