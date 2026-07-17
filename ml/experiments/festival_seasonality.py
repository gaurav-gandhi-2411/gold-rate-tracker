"""Experiment: is there a detrended, cross-year-consistent festival-window seasonal
effect in gold prices worth surfacing as a descriptive "historical tendency" feature?

Tests Dhanteras, Akshaya Tritiya, and Diwali (2010-2025, 16 years each) against gold_inr
(GC=F x INR=X, an international-spot-in-rupees proxy — avoids extrapolating the
Tanishq<->IBJA markup calibration, which is fit on ~2026 data only and unvalidated across
16 years of duty/GST/regime changes; see the regime-stability section below for exactly
this kind of instability).

Festival dates for 2010-2021 were sourced this session and cross-verified against
drikpanchang.com (6/6 spot checks matched exactly); 2022-2027 reuses
ml/calendar_events.py's existing anchor dates. Dhanteras/Akshaya Tritiya are lunar-
calendar festivals -- NOT fixed Gregorian dates -- so a fixed-date approximation would
have invalidated the whole analysis; the anchor dates below are the actual per-year
Gregorian dates, not an approximation.

Method (detrend BEFORE measuring seasonality, per the pre-registered rigor gate):
  1. 91-day centered rolling mean of log(gold_inr) as the local trend (much longer than
     the 7-day festival window, so it can't absorb the effect it's meant to control for).
  2. Per festival instance: excess = (log_price[window_end] - log_price[window_start])
     - (trend[window_end] - trend[window_start]) -- the return BEYOND what the
     concurrent local trend would predict.
  3. Per festival: mean effect, bootstrap 95% CI (10k resamples), permutation p-value
     (10k draws against a pool of ~6,100 non-festival 7-day windows from the same
     detrended series), cross-year hit rate (sign-consistency).
  4. Benjamini-Hochberg correction across the 3 festivals tested.
  5. Regime-stability: 2010-2015 (n=6) vs 2016-2025 (n=10, "last ~10 years").

Result (2026-07-18): NONE of the three festivals survive. Cross-year hit rates sit at or
near chance (50%, 50%, 62.5%) -- not "firmed in 14 of 19 years", closer to a coin flip.
Bootstrap CIs all span zero. Permutation p-values (0.55, 0.25, 0.74) are nowhere near
0.05 even BEFORE multiple-comparisons correction; none survive BH correction. Where
there's a pooled hint of an effect at all (Akshaya Tritiya, -0.64%), it does not hold
up across the regime split (-0.50% pre-2016 vs -0.73% 2016-2025 -- similar sign here,
but Dhanteras and Diwali both FLIP SIGN across the same split: Dhanteras +1.04% ->
-0.11%, Diwali +1.41% -> -0.56%). Robustness-checked with a 60-day trend window
(TREND_WINDOW=60): results essentially unchanged (all deltas <0.02pp, no festival
crosses into significance). This is exactly the "trend + noise" outcome the rigor gate
was designed to catch -- a naive non-detrended read would likely have shown a
"gold rises into Diwali" story; detrending kills it.

Verdict: kill. Do not surface festival seasonality on the site. Documented here as a
negative result, matching ml/experiments/driver_decomp.py and direction_enrichment.py.

Usage:
    python -m ml.experiments.festival_seasonality
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)

# ---------------------------------------------------------------------------
# Festival anchor dates (actual Gregorian dates per year, not a fixed-date
# approximation -- these festivals move on the Hindu lunisolar calendar).
# 2010-2021 sourced and cross-verified against drikpanchang.com this session;
# 2022-2027 matches ml/calendar_events.py.
# ---------------------------------------------------------------------------
FESTIVALS: dict[str, list[str]] = {
    "Dhanteras": [
        "2010-11-03",
        "2011-10-24",
        "2012-11-11",
        "2013-11-01",
        "2014-10-21",
        "2015-11-08",
        "2016-10-27",
        "2017-10-17",
        "2018-11-05",
        "2019-10-25",
        "2020-11-12",
        "2021-11-02",
        "2022-10-22",
        "2023-11-10",
        "2024-10-29",
        "2025-10-20",
    ],
    "Akshaya Tritiya": [
        "2010-05-16",
        "2011-05-06",
        "2012-04-24",
        "2013-05-13",
        "2014-05-02",
        "2015-04-21",
        "2016-05-09",
        "2017-04-28",
        "2018-04-18",
        "2019-05-07",
        "2020-04-26",
        "2021-05-14",
        "2022-05-03",
        "2023-04-22",
        "2024-05-10",
        "2025-04-30",
    ],
    "Diwali": [
        "2010-11-05",
        "2011-10-26",
        "2012-11-13",
        "2013-11-03",
        "2014-10-23",
        "2015-11-11",
        "2016-10-30",
        "2017-10-18",
        "2018-11-06",
        "2019-10-27",
        "2020-11-14",
        "2021-11-04",
        "2022-10-24",
        "2023-11-12",
        "2024-11-01",
        "2025-10-20",
    ],
}
WINDOW_BEFORE_DAYS = 3
WINDOW_AFTER_DAYS = 3
TREND_WINDOW = 91  # centered rolling window (days) for the local-trend detrender


def fetch_long_history(start: str = "2008-01-01") -> pd.Series:
    """Daily gold_inr = GC=F(USD/oz) x INR=X, forward-filled onto a full calendar.

    Deliberately independent of data/macro_cache.parquet (which only holds the last
    ~2 years) -- this needs 16+ years of history and yfinance daily data for both
    tickers goes back to 2008, so a dedicated fetch is simplest and free.
    """
    import yfinance as yf

    end = (pd.Timestamp.today() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    raw = yf.download(
        ["GC=F", "INR=X"], start=start, end=end, interval="1d", progress=False, threads=False
    )
    raw.columns = ["_".join(c) if isinstance(c, tuple) else c for c in raw.columns]
    gold_usd = raw["Close_GC=F"]
    usd_inr = raw["Close_INR=X"]

    full_idx = pd.date_range(raw.index.min(), raw.index.max(), freq="D")
    gold_usd_ff = gold_usd.reindex(full_idx).ffill()
    usd_inr_ff = usd_inr.reindex(full_idx).ffill()
    return gold_usd_ff * usd_inr_ff


def _nearest_index(series: pd.Series, target: pd.Timestamp) -> pd.Timestamp | None:
    if series.empty:
        return None
    idx = series.index[series.index.get_indexer(pd.DatetimeIndex([target]), method="nearest")]
    return idx[0] if len(idx) else None


def compute_festival_excess_returns(
    log_price: pd.Series,
    trend: pd.Series,
    dates: list[str],
    window_before: int = WINDOW_BEFORE_DAYS,
    window_after: int = WINDOW_AFTER_DAYS,
) -> pd.DataFrame:
    """Detrended excess return per festival instance: raw window return minus the
    concurrent trend-implied return over the same span."""
    rows = []
    for d in dates:
        anchor = pd.Timestamp(d)
        start = _nearest_index(log_price, anchor - pd.Timedelta(days=window_before))
        end = _nearest_index(log_price, anchor + pd.Timedelta(days=window_after))
        if start is None or end is None:
            continue
        vals = [log_price[start], log_price[end], trend[start], trend[end]]
        if any(pd.isna(v) for v in vals):
            continue
        raw_ret = log_price[end] - log_price[start]
        trend_ret = trend[end] - trend[start]
        rows.append({"year": anchor.year, "anchor": anchor.date(), "excess": raw_ret - trend_ret})
    return pd.DataFrame(rows)


def build_permutation_pool(
    log_price: pd.Series,
    trend: pd.Series,
    all_festival_dates: dict[str, list[str]],
    window_before: int = WINDOW_BEFORE_DAYS,
    window_after: int = WINDOW_AFTER_DAYS,
) -> np.ndarray:
    """Detrended excess returns for every non-festival 7-day window in the series --
    the null distribution for the permutation test."""
    excluded_days = set()
    for dates in all_festival_dates.values():
        for d in dates:
            anchor = pd.Timestamp(d)
            for offset in range(-window_before - 2, window_after + 3):
                excluded_days.add((anchor + pd.Timedelta(days=offset)).date())

    span = window_before + window_after
    valid_days = log_price.dropna().index
    pool = []
    for i in range(len(valid_days) - span):
        start = valid_days[i]
        end = start + pd.Timedelta(days=span)
        if start.date() in excluded_days or end.date() in excluded_days:
            continue
        if end not in log_price.index:
            continue
        vals = [log_price[start], log_price[end], trend[start], trend[end]]
        if any(pd.isna(v) for v in vals):
            continue
        pool.append((log_price[end] - log_price[start]) - (trend[end] - trend[start]))
    return np.array(pool)


def bootstrap_ci(
    values: np.ndarray, rng: np.random.Generator, n_boot: int = 10000
) -> tuple[float, float]:
    means = [rng.choice(values, size=len(values), replace=True).mean() for _ in range(n_boot)]
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def permutation_pvalue(
    values: np.ndarray, pool: np.ndarray, rng: np.random.Generator, n_perm: int = 10000
) -> float:
    observed = np.abs(values.mean())
    count = sum(
        1
        for _ in range(n_perm)
        if np.abs(rng.choice(pool, size=len(values), replace=False).mean()) >= observed
    )
    return count / n_perm


def benjamini_hochberg(pvalues: list[float], alpha: float = 0.05) -> list[bool]:
    m = len(pvalues)
    order = np.argsort(pvalues)
    reject = [False] * m
    for rank, idx in enumerate(order, start=1):
        if pvalues[idx] <= alpha * rank / m:
            reject[idx] = True
    return reject


def main() -> None:
    rng = np.random.default_rng(42)

    gold_inr = fetch_long_history()
    log_price = np.log(gold_inr)
    trend = log_price.rolling(TREND_WINDOW, center=True, min_periods=60).mean()
    print(
        f"gold_inr series: {log_price.notna().sum()} days, {log_price.index.min().date()} to {log_price.index.max().date()}"
    )

    pool = build_permutation_pool(log_price, trend, FESTIVALS)
    print(f"Permutation null pool: {len(pool)} non-festival 7-day windows\n")

    summary: list[tuple[str, float]] = []
    for name, dates in FESTIVALS.items():
        res = compute_festival_excess_returns(log_price, trend, dates)
        vals: np.ndarray = res["excess"].to_numpy(dtype=float)
        n = len(vals)
        mean_pct = vals.mean() * 100
        hit_rate = (vals > 0).mean() if mean_pct > 0 else (vals < 0).mean()
        ci_lo, ci_hi = bootstrap_ci(vals, rng)
        p_perm = permutation_pvalue(vals, pool, rng)
        summary.append((name, p_perm))

        print(f"[{name}]  n_years={n}")
        print(f"  mean detrended excess return: {mean_pct:+.3f}%")
        print(
            f"  bootstrap 95% CI: [{ci_lo * 100:+.3f}%, {ci_hi * 100:+.3f}%]  excludes 0: {ci_lo > 0 or ci_hi < 0}"
        )
        print(f"  cross-year hit rate: {hit_rate * 100:.1f}%  ({round(hit_rate * n)}/{n})")
        print(f"  permutation p-value: {p_perm:.4f}")

        early = res[res["year"] <= 2015]["excess"]
        recent = res[res["year"] >= 2016]["excess"]
        print(
            f"  regime check: 2010-2015 mean={early.mean() * 100:+.3f}%  |  2016-2025 mean={recent.mean() * 100:+.3f}%\n"
        )

    pvals = [p for _, p in summary]
    reject = benjamini_hochberg(pvals)
    print("Benjamini-Hochberg correction (alpha=0.05):")
    for (name, p), r in zip(summary, reject, strict=True):
        print(f"  {name}: p={p:.4f}  significant={r}")


if __name__ == "__main__":
    main()
