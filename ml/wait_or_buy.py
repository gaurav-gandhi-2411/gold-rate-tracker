"""ml.wait_or_buy -- F2 "buy now or wait?" (ADR 049). Pure, no look-ahead.

The card states the gamble, not a prediction:

    "Waiting N days: prices are about equally likely to go up or down. This week
    they're moving more than usual -- up to about Rs.X/g either way."

Three independently-measured components, for N in (1, 2, 7):
  (a) probability the price is LOWER after N than today ("about equally likely"),
  (b) a calibrated ENDPOINT price range ("up to about Rs.X either way"),
  (c) today's realised volatility vs its long-run distribution ("moving more than
      usual" / "calmer than usual" / "about as usual").
Every method, threshold and copy rule here is frozen by ADR 049 before any of it
was run on real data. See that ADR for the design rationale; this module is the
implementation of exactly what it specifies, nothing more.

No expected cost of waiting is computed anywhere in this module (ADR 049 (d);
ADR 039/R3 already found no wait rule saves money reliably -- adding an expected-
saving number here would silently reintroduce that signal).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ml.direction.dataset import MAX_WEEKDAYS_PER_STEP
from ml.direction.evaluate_reframed import diebold_mariano_test
from ml.range_forecast.data import MAX_GAP_DAYS, dense_segments
from ml.range_forecast.metrics import wilson_ci
from ml.weekly_range import (
    base_range,
    base_range_endpoint,
    complete_windows,
    times_out_of_ten,
    walk_forward,
    walk_forward_endpoint,
)

N_VALUES: tuple[int, ...] = (1, 2, 7)
NON_OVERLAP_STRIDE: dict[int, int] = {1: 1, 2: 2, 7: 5}  # ADR 049 (a): stride N, or 5 for "week"

REALIZED_VOL_WINDOW = 20  # consecutive IBJA days (ADR 049 (c))
VOL_LOW_PCT = 0.25
VOL_HIGH_PCT = 0.75

ALPHA = 0.05
BONFERRONI_FAMILY = len(N_VALUES) * 2  # 3 horizons x 2 datasets (ADR 049 (a))


# ---------------------------------------------------------------------------
# (a) "About equally likely": outcome windows and the P=0.5 test
# ---------------------------------------------------------------------------


def _consecutive(a: pd.Timestamp, b: pd.Timestamp) -> bool:
    return int(np.busday_count(a.date(), b.date())) <= MAX_WEEKDAYS_PER_STEP


@dataclass(frozen=True)
class Outcome:
    """One as-of day, its N-ahead target day, and both prices."""

    as_of: pd.Timestamp
    target: pd.Timestamp
    price_t: float
    price_target: float

    @property
    def lower(self) -> int:
        """1 if the target price is strictly LOWER than today's, else 0 (a tie
        counts as not-lower, matching ml.direction.dataset.make_label's `>` tie
        convention for label_binary)."""
        return int(self.price_target < self.price_t)


def trading_day_windows(price: pd.Series, n: int) -> list[Outcome]:
    """Every as-of day whose n-trading-day-ahead target is reachable across a chain
    of n consecutive publication-day steps (ADR 042's rule -- at most one holiday
    skipped per step, never a hole). n counted in real publication-day steps
    within `price`'s own index, not calendar days."""
    dates = list(price.index)
    vals = price.to_numpy(dtype=float)
    out: list[Outcome] = []
    for i, t in enumerate(dates):
        j = i + n
        if j >= len(dates):
            continue
        if not all(_consecutive(dates[m], dates[m + 1]) for m in range(i, j)):
            continue
        out.append(
            Outcome(as_of=t, target=dates[j], price_t=float(vals[i]), price_target=float(vals[j]))
        )
    return out


def week_windows(price: pd.Series) -> list[Outcome]:
    """N = 7 calendar days: reuses ml.weekly_range.complete_windows's "week" rule
    unchanged. The target is the last real publication day inside (t, t+7]."""
    out: list[Outcome] = []
    for w in complete_windows(price, "week"):
        out.append(
            Outcome(
                as_of=w.as_of,
                target=w.days[-1],
                price_t=w.price,
                price_target=float(price.loc[w.days[-1]]),
            )
        )
    return out


def outcome_windows(price: pd.Series, n: int) -> list[Outcome]:
    """Dispatches to trading_day_windows (n in {1, 2}) or week_windows (n == 7),
    the only N values ADR 049 registers."""
    if n == 7:
        return week_windows(price)
    return trading_day_windows(price, n)


def outcomes_frame(price: pd.Series, n: int) -> pd.DataFrame:
    rows = outcome_windows(price, n)
    return pd.DataFrame(
        {
            "as_of": [r.as_of for r in rows],
            "target": [r.target for r in rows],
            "price_t": [r.price_t for r in rows],
            "price_target": [r.price_target for r in rows],
            "lower": [r.lower for r in rows],
        }
    )


def prob_lower_stats(df: pd.DataFrame, n: int) -> dict:
    """ADR 049 (a): naive Wilson, overlap-corrected (effective-n) Wilson (the
    interval the copy rule reads), the non-overlapping-stride check, and the
    two-sided HAC test of H0: P = 0.5."""
    indicator = df["lower"].to_numpy(dtype=float)
    n_obs = len(indicator)
    if n_obs == 0:
        return {"n": 0}
    p_hat = float(indicator.mean())
    naive_lo, naive_hi = wilson_ci(int(indicator.sum()), n_obs)

    dm = diebold_mariano_test(indicator.tolist(), [0.5] * n_obs, horizon=n, alternative="two-sided")
    eff_n = dm["effective_n"]
    if eff_n and eff_n > 0:
        eff_n_int = max(1, round(eff_n))
        eff_successes = max(0, min(eff_n_int, round(p_hat * eff_n_int)))
        eff_lo, eff_hi = wilson_ci(eff_successes, eff_n_int)
    else:
        eff_n_int, eff_lo, eff_hi = None, None, None

    stride = NON_OVERLAP_STRIDE[n]
    sub = indicator[::stride]
    sub_lo, sub_hi = wilson_ci(int(sub.sum()), len(sub)) if len(sub) else (None, None)

    return {
        "n": n_obs,
        "p_hat": p_hat,
        "naive_wilson_95": [naive_lo, naive_hi],
        "effective_n": eff_n,
        "effective_n_wilson_95": [eff_lo, eff_hi],
        "non_overlap_n": len(sub),
        "non_overlap_stride": stride,
        "non_overlap_wilson_95": [sub_lo, sub_hi],
        "dm_stat": dm["dm_stat"],
        "p_value_two_sided": dm["p_value"],
        "mean_diff": dm["mean_diff"],
        "gamma_0": dm["gamma_0"],
        "long_run_var": dm["long_run_var"],
        "about_equally_likely": (
            eff_lo is not None and eff_hi is not None and eff_lo <= 0.5 <= eff_hi
        ),
        "times_out_of_10": times_out_of_ten(p_hat),
    }


# ---------------------------------------------------------------------------
# (b) "Up to about Rs.X": calibrated endpoint range
# ---------------------------------------------------------------------------


def endpoint_walk_forward(proxy: pd.Series, ibja: pd.Series, n: int) -> pd.DataFrame:
    """ADR 049 (b): N=1 and N=7 reuse ml.weekly_range's own "1d"/"week" walk_forward
    unchanged (a 1-day path IS a 1-day endpoint; the 7-day path range is a valid
    conservative bound on the 7-day endpoint range -- see the ADR). N=2 uses the
    new endpoint-only machinery."""
    if n == 1:
        return walk_forward(proxy, ibja, "1d")
    if n == 7:
        return walk_forward(proxy, ibja, "week")
    return walk_forward_endpoint(proxy, ibja, n)


def endpoint_base_range(
    proxy: pd.Series, as_of: pd.Timestamp, n: int
) -> tuple[float, float] | None:
    """The single base range ml.wait_or_buy_today would issue today for horizon n --
    same dispatch as endpoint_walk_forward, for computing "today's" range (no
    scoring, since today's window hasn't matured)."""
    if n == 1:
        return base_range(proxy, as_of, 1)
    if n == 7:
        return base_range(proxy, as_of, 5)  # ml.weekly_range.issue_days("week")
    return base_range_endpoint(proxy, as_of, n)


def rs_bounds(price_t: float, scale: float, lo: float, hi: float) -> tuple[float, float, float]:
    """(lo_rs, hi_rs, X): the range in real rupees at today's price, and X = the
    larger absolute side (ADR 049 (b))."""
    lo_rs = price_t * (math.exp(scale * lo) - 1.0)
    hi_rs = price_t * (math.exp(scale * hi) - 1.0)
    return lo_rs, hi_rs, max(abs(lo_rs), abs(hi_rs))


# ---------------------------------------------------------------------------
# (c) "Moving more than usual": realised volatility vs its long-run distribution
# ---------------------------------------------------------------------------


def rolling_realized_vol(
    price: pd.Series, window: int = REALIZED_VOL_WINDOW, max_gap_days: int = MAX_GAP_DAYS
) -> pd.Series:
    """Trailing `window`-day std of daily log returns, computed only within a run
    of consecutive days (gap <= max_gap_days); NaN elsewhere and for the first
    `window` days of any run. dense_segments (ml.range_forecast.data) is the same
    "consecutive" definition ADR 043/039/040 already use for this file."""
    out = pd.Series(index=price.index, dtype=float)
    for seg in dense_segments(price, max_gap_days):
        if len(seg) < 2:
            continue
        rets = pd.Series(np.log(seg / seg.shift(1)), index=seg.index)
        out.loc[seg.index] = rets.rolling(window, min_periods=window).std()
    return out


def realized_vol_today(price: pd.Series, as_of: pd.Timestamp) -> float | None:
    """Today's realised volatility: rolling_realized_vol's value at `as_of`, using
    only rows <= as_of (no look-ahead -- rolling() is already causal, and the
    series is never evaluated past as_of by any caller here)."""
    vol = rolling_realized_vol(price[price.index <= as_of])
    if as_of not in vol.index or pd.isna(vol.loc[as_of]):
        return None
    return float(vol.loc[as_of])


def vol_percentile(reference_vol: pd.Series, as_of: pd.Timestamp, value: float) -> float | None:
    """Fraction of `reference_vol` values known STRICTLY BEFORE as_of that are <=
    value -- a plain historical percentile rank, no look-ahead."""
    prior = reference_vol[(reference_vol.index < as_of) & reference_vol.notna()]
    if prior.empty:
        return None
    return float((prior <= value).mean())


def vol_category(pctile: float | None) -> str | None:
    """Fixed in code, per ADR 049 (c): below the 25th percentile is calmer than
    usual, above the 75th is moving more than usual, else about as usual."""
    if pctile is None:
        return None
    if pctile < VOL_LOW_PCT:
        return "calmer_than_usual"
    if pctile > VOL_HIGH_PCT:
        return "moving_more_than_usual"
    return "about_as_usual"


# ---------------------------------------------------------------------------
# Copy: the plain-language sentence, built only from computed values
# ---------------------------------------------------------------------------

_VOL_CLAUSE = {
    "calmer_than_usual": "This week they've been calmer than usual.",
    "about_as_usual": "This week they've been moving about as usual.",
    "moving_more_than_usual": "This week they're moving more than usual — up to about ₹{x}/g either way.",
}


def build_sentence(n: int, prob: dict, x_rs: float, category: str | None, years_span: float) -> str:
    """ADR 049's exact template, built only from `prob`/`x_rs`/`category` -- no
    hand-typed numbers. `prob` is prob_lower_stats's output for this n."""
    day_word = "day" if n == 1 else "days"
    if prob.get("about_equally_likely"):
        clause = "prices are about equally likely to go up or down."
    else:
        clause = (
            f"prices were lower after {n} {day_word} about {prob['times_out_of_10']} times out of 10 "
            f"(based on the last {round(years_span)} years)."
        )
    parts = [f"Waiting {n} {day_word}: {clause}"]
    if category is not None:
        vol_text = _VOL_CLAUSE[category]
        if category == "moving_more_than_usual":
            vol_text = vol_text.format(x=round(x_rs))
        parts.append(vol_text)
    return " ".join(parts)
