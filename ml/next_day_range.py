"""ml.next_day_range -- ADR 047: rebuild the displayed "tomorrow's range" on the ADR 043
(ml.weekly_range) method, applied to the displayed decision day rather than to IBJA's own
publication-day series.

The site shows a naive flat-hold conformal range on `current_22k` (the retail 22K estimate) for
the next reading (`data/metrics_history.json`: decision_date, current_22k, lower, upper,
actual_next_22k). It covers 46/63 = 73.0% against an 80% target (see ADR 047). ADR 043 already
built and measured a better-calibrated 1-day range on real IBJA (85.1% walk-forward, 80.0% held
out) -- this module is that SAME method (ml.weekly_range's historical-simulation shape +
split-conformal scale), reused exactly, evaluated at a displayed decision day `d` instead of an
IBJA publication day, and returned as PERCENTAGES so the caller can apply them to whatever price
is current at `d` (`P_d = current_22k`, not the IBJA price).

next_day_range_pct(proxy, ibja, d) returns (lo_pct, hi_pct): range = [P_d*(1+lo), P_d*(1+hi)].
Computed using ONLY data strictly before `d` -- both the proxy history for the historical-
simulation shape and the IBJA windows for the conformal scale are filtered to `index < d` before
either ml.weekly_range.base_range or ml.weekly_range.complete_windows ever sees them, so a
window's own as_of and outcome (last publication) day are always strictly before `d` too (ADR
043's "no leak" rule, applied at `d` rather than at an IBJA row). This is exactly
scripts/analysis_weekly_range.py's "1d" walk-forward, evaluated at one as-of date instead of over
the whole series: see ml.weekly_range.walk_forward's per-window logic, which this mirrors.

None is returned when there is not yet enough history (ml.weekly_range.HS_MIN_SAMPLE proxy days,
or ml.weekly_range.MIN_CAL matured IBJA windows) -- the same "no forecast yet" condition
ml.weekly_range itself uses.
"""

from __future__ import annotations

import math

import pandas as pd

from ml.weekly_range import base_range, complete_windows, conformal_scale, score

HORIZON = "1d"


def next_day_range_pct(
    proxy: pd.Series, ibja: pd.Series, d: pd.Timestamp
) -> tuple[float, float] | None:
    """The 1-day range as returns relative to whatever price is current at `d`, using only data
    strictly before `d`. Returns (lo_pct, hi_pct), or None if there isn't enough history yet.
    """
    proxy_before = proxy[proxy.index < d]
    ibja_before = ibja[ibja.index < d]

    # Every window built from ibja_before has as_of < d and days[-1] < d by construction (ibja_
    # before contains no row >= d), which is exactly "IBJA windows whose outcome date < d".
    windows = complete_windows(ibja_before, HORIZON)
    scores: list[float] = []
    for w in windows:
        base = base_range(proxy_before, w.as_of, 1)
        if base is not None:
            scores.append(score(w, *base))
    s = conformal_scale(scores)
    if s is None:
        return None

    base = base_range(proxy_before, d, 1)
    if base is None:
        return None
    lo_base, hi_base = base
    return (math.exp(s * lo_base) - 1.0, math.exp(s * hi_base) - 1.0)
