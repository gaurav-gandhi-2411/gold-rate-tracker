"""ml.range_forecast.metrics -- interval-forecast evaluation primitives.

kupiec_pof_test, christoffersen_independence_test -- standard VaR-backtesting
likelihood-ratio tests (Kupiec 1995, Christoffersen 1998), chi2(1) under H0.
winkler_score -- the proper scoring rule for a central prediction interval
(Winkler 1972; Gneiting & Raftery 2007 call it the "interval score").
qlike -- the QLIKE loss for variance forecasts (Patton 2011), asymmetric and
robust to the choice of volatility proxy under mild conditions.
wilson_ci -- binomial proportion CI, used to check "empirical coverage within
the binomial 95% interval" per the task brief.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import chi2, norm


def wilson_ci(successes: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    """95%-by-default Wilson score interval for a binomial proportion."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = successes / n
    denom = 1 + z**2 / n
    centre = p + z**2 / (2 * n)
    margin = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))
    return (float((centre - margin) / denom), float((centre + margin) / denom))


def kupiec_pof_test(hits: np.ndarray, p: float) -> dict:
    """Unconditional-coverage likelihood-ratio test.

    hits: boolean/0-1 array, 1 = actual fell outside the interval (an
    "exceedance"). p: the NOMINAL exceedance probability (1 - level).
    H0: true exceedance probability equals p. LR ~ chi2(1) under H0.
    """
    hits = np.asarray(hits, dtype=float)
    n = len(hits)
    if n == 0:
        return {"n": 0, "x": 0, "p_hat": None, "lr_stat": None, "p_value": None}
    x = int(hits.sum())
    p_hat = x / n
    # log-likelihood terms; clip at the boundaries (x=0 or x=n) where the
    # naive formula has a 0*log(0) term that is mathematically 0 in the limit.
    eps = 1e-12
    p_c = min(max(p, eps), 1 - eps)
    p_hat_c = min(max(p_hat, eps), 1 - eps)
    log_l_null = (n - x) * np.log1p(-p_c) + x * np.log(p_c)
    log_l_alt = (n - x) * np.log1p(-p_hat_c) + x * np.log(p_hat_c)
    lr_stat = float(-2 * (log_l_null - log_l_alt))
    lr_stat = max(lr_stat, 0.0)
    p_value = float(1 - chi2.cdf(lr_stat, df=1))
    return {"n": n, "x": x, "p_hat": p_hat, "lr_stat": lr_stat, "p_value": p_value}


def christoffersen_independence_test(hits: np.ndarray) -> dict:
    """Markov-independence likelihood-ratio test on the hit sequence.

    H0: exceedances are independent across time (transition probability from
    a hit does not depend on whether the prior day was also a hit). LR ~
    chi2(1) under H0. Degenerate transition counts (e.g. no 0->1 transitions
    observed at all) return p_value=None -- the test is undefined, not "1.0".
    """
    hits = np.asarray(hits, dtype=int)
    n = len(hits)
    if n < 2:
        return {
            "n": n,
            "n00": 0,
            "n01": 0,
            "n10": 0,
            "n11": 0,
            "lr_stat": None,
            "p_value": None,
        }
    prev = hits[:-1]
    curr = hits[1:]
    n00 = int(np.sum((prev == 0) & (curr == 0)))
    n01 = int(np.sum((prev == 0) & (curr == 1)))
    n10 = int(np.sum((prev == 1) & (curr == 0)))
    n11 = int(np.sum((prev == 1) & (curr == 1)))

    n0_ = n00 + n01
    n1_ = n10 + n11
    if n0_ == 0 or n1_ == 0:
        return {
            "n": n,
            "n00": n00,
            "n01": n01,
            "n10": n10,
            "n11": n11,
            "lr_stat": None,
            "p_value": None,
        }

    pi01 = n01 / n0_
    pi11 = n11 / n1_
    pi = (n01 + n11) / (n0_ + n1_)
    eps = 1e-12

    def _ll(p01: float, p11: float) -> float:
        p01c, p11c = min(max(p01, eps), 1 - eps), min(max(p11, eps), 1 - eps)
        return (
            n00 * np.log1p(-p01c)
            + n01 * np.log(p01c)
            + n10 * np.log1p(-p11c)
            + n11 * np.log(p11c)
        )

    pic = min(max(pi, eps), 1 - eps)
    log_l_null = (n0_ + n1_ - n01 - n11) * np.log1p(-pic) + (n01 + n11) * np.log(pic)
    log_l_alt = _ll(pi01, pi11)
    lr_stat = float(-2 * (log_l_null - log_l_alt))
    lr_stat = max(lr_stat, 0.0)
    p_value = float(1 - chi2.cdf(lr_stat, df=1))
    return {
        "n": n,
        "n00": n00,
        "n01": n01,
        "n10": n10,
        "n11": n11,
        "pi01": pi01,
        "pi11": pi11,
        "lr_stat": lr_stat,
        "p_value": p_value,
    }


def stride_subsample(values: np.ndarray, stride: int) -> np.ndarray:
    """Every `stride`-th element, starting at 0 -- de-overlaps h-step-ahead
    forecasts so consecutive kept observations share no outcome days."""
    stride = max(1, stride)
    return np.asarray(values)[::stride]


def winkler_score(actual: np.ndarray, lo: np.ndarray, hi: np.ndarray, level: float) -> np.ndarray:
    """Winkler (interval) score, lower is better. `level` is the NOMINAL
    central-interval probability (e.g. 0.90); alpha = 1 - level."""
    actual, lo, hi = np.asarray(actual, dtype=float), np.asarray(lo, dtype=float), np.asarray(
        hi, dtype=float
    )
    alpha = 1.0 - level
    width = hi - lo
    score = width.copy()
    below = actual < lo
    above = actual > hi
    score = np.where(below, width + (2.0 / alpha) * (lo - actual), score)
    score = np.where(above, width + (2.0 / alpha) * (actual - hi), score)
    return score


def qlike(actual_var: np.ndarray, forecast_var: np.ndarray) -> np.ndarray:
    """QLIKE loss (Patton 2011): log(h_hat) + actual/h_hat. Lower is better.
    forecast_var must be strictly positive; values <= 0 are clipped to a
    small epsilon (should not occur for a well-formed variance forecast)."""
    actual_var = np.asarray(actual_var, dtype=float)
    forecast_var = np.clip(np.asarray(forecast_var, dtype=float), 1e-12, None)
    return np.log(forecast_var) + actual_var / forecast_var


def normal_quantile_bounds(scale: np.ndarray, level: float, mean: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """Central `level` interval bounds for N(mean, scale^2)."""
    z = float(norm.ppf(0.5 + level / 2.0))
    scale = np.asarray(scale, dtype=float)
    return mean - z * scale, mean + z * scale


def implied_normal_scale(lo90: np.ndarray, hi90: np.ndarray) -> np.ndarray:
    """Back out an implied N(0, scale^2) scale from a 90% interval -- used
    for QLIKE on models (quantile_gbm, chronos, historical_simulation) whose
    native output is quantiles rather than a fitted scale parameter."""
    z90 = float(norm.ppf(0.95))
    return (np.asarray(hi90, dtype=float) - np.asarray(lo90, dtype=float)) / (2.0 * z90)
