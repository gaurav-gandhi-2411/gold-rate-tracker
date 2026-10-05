"""ml.fhs_ranges -- brief item 5b, ADR 056: adaptive ranges via filtered historical
simulation (FHS).

ADR 047 v2 (ml.next_day_range, reusing ml.weekly_range's historical-simulation shape +
IBJA split-conformal scale) measured 84.1% coverage on the next-day range but was 31%
wider than the live displayed range -- it cleared the coverage bar only by being wider
everywhere, all the time, regardless of whether the market was actually calm or volatile
that day. FHS asks whether an ADAPTIVE shape -- one that is wide on volatile days and
narrow on calm ones -- can match that coverage without paying as much width, by
standardizing historical proxy returns by a conditional-volatility estimate BEFORE taking
empirical quantiles, then rescaling by TODAY's own volatility forecast (not the historical
average).

This module is a drop-in extension of ml.weekly_range, not a rewrite: `fhs_base_range`
returns the exact same (lo, hi) log-return contract as `ml.weekly_range.base_range`, so
`ml.weekly_range.complete_windows` / `score` / `conformal_scale` -- the IBJA split-conformal
calibration layer ADR 043 built and ADR 047 reused verbatim -- apply to FHS unchanged. Only
the proxy-side SHAPE computation changes: from unconditional historical quantiles (weekly_
range.base_range) to volatility-STANDARDIZED ("filtered") ones. No new conformal statistics
are written here.

Two conditional-volatility estimators (`VOL_METHODS`), refit only every REFIT_EVERY trading
days (reusing ml.range_forecast.garch's own refit cadence and MIN_TRAIN_SIZE gate) using
ONLY returns strictly before the current position -- never the as-of day itself or later:

  * "ewma": ml.range_forecast.baselines.ewma_variance_path (reused, not duplicated).
    Lambda is selected from EWMA_LAMBDA_GRID by minimizing 1-step-ahead QLIKE loss
    (ml.range_forecast.metrics.qlike, reused) on an internal held-back TAIL of the training
    returns ("chosen on training folds only" -- the selection never touches a return at or
    after the refit position, and never touches IBJA truth or a coverage outcome).
  * "garch": ml.range_forecast.garch.fit_garch / garch_sigma2_path / garch_h_day_variance
    (reused, not duplicated) -- GARCH(1,1), Student-t innovations, MLE via scipy.optimize on
    a numpy log-likelihood and numpy variance recursion (the same MLE-in-numpy
    implementation already in this codebase; no new GARCH code is written here).

FHS shape, per as-of day t and path length k (1, or the ADR-043 weekly issue_days=5, reused
exactly via ml.weekly_range.issue_days / complete_windows / WEEK_CALENDAR_DAYS):

  1. sigma2_forecast[i] = Var(r_i | F_{i-1}) for every return r_i known before t, aligned to
     the GARCH convention ml.range_forecast.garch.garch_sigma2_path already uses (the
     variance BEFORE seeing r_i). ml.range_forecast.baselines.ewma_variance_path is
     post-return-inclusive by construction (sigma2[i] incorporates r_i itself), so it is
     shifted by one index here to match the same pre-return convention.
  2. Standardized residuals z_i = r_i / sqrt(sigma2_forecast[i]).
  3. Over the trailing HS_WINDOW standardized returns (same window length as ml.weekly_
     range.HS_WINDOW), build every rolling k-day cumulative-standardized-return path --
     identical rolling-path construction to ml.weekly_range.base_range, applied to
     cumsum(z) instead of log(price). lo_q = 10th percentile of path minima, hi_q = 90th
     percentile of path maxima (dimensionless, "z-units", LEVEL reused from ml.weekly_range).
  4. Rescale by TODAY's h-day-ahead volatility FORECAST (not the historical realized vol
     baked into steps 1-3): sigma_h = sqrt(GARCH h-day variance) or sqrt(EWMA
     sigma2_next * h). Returned bounds = (sigma_h * lo_q, sigma_h * hi_q), in the same
     log-return units ml.weekly_range.base_range returns, clipped the same way
     (min(lo, -1e-6), max(hi, 1e-6)) to avoid a zero-division inside `ml.weekly_range.score`.

Refit cadence is managed by `VolCache`, keyed to the NUMBER OF PROXY RETURNS known at a
given as-of day (not calendar time), so it is reusable identically by both evaluation
harnesses in scripts/analysis_fhs_ranges.py: the IBJA-publication-day walk-forward (mirrors
ml.weekly_range.walk_forward) and the displayed-decision-day walk-forward (mirrors ml.
next_day_range.next_day_range_pct).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ml.range_forecast.baselines import ewma_variance_path
from ml.range_forecast.garch import (
    MIN_TRAIN_SIZE,
    REFIT_EVERY,
    fit_garch,
    garch_h_day_variance,
    garch_sigma2_path,
)
from ml.range_forecast.metrics import qlike
from ml.weekly_range import (
    HS_MIN_SAMPLE,
    HS_WINDOW,
    LEVEL,
    complete_windows,
    conformal_scale,
    issue_days,
    score,
)

VOL_METHODS = ("ewma", "garch")
EWMA_LAMBDA_GRID: tuple[float, ...] = (0.90, 0.92, 0.94, 0.96, 0.98)
EWMA_VAL_FRAC = 0.3  # tail fraction of the training returns held back for lambda selection
EWMA_MIN_VAL = 20  # minimum held-back observations; below this, selection is unreliable


@dataclass(frozen=True)
class VolFit:
    """A conditional-volatility model fit on returns strictly before some position.

    `n_train` is the number of returns the fit was made on -- VolCache uses it to decide
    when a refit is due; it carries no information used in any range computation itself.
    """

    method: str  # "ewma" | "garch"
    n_train: int
    lam: float | None = None  # set iff method == "ewma"
    params: dict | None = None  # set iff method == "garch" (omega, alpha, beta, nu, ...)


def select_ewma_lambda(
    r_train: np.ndarray,
    grid: tuple[float, ...] = EWMA_LAMBDA_GRID,
    val_frac: float = EWMA_VAL_FRAC,
    min_val: int = EWMA_MIN_VAL,
) -> float:
    """Grid-search lambda by mean 1-step-ahead QLIKE on the TAIL `val_frac` of `r_train`.

    For each candidate lambda, `ewma_variance_path(r_train, lam)` is computed once over the
    WHOLE of `r_train` (a single causal, left-to-right recursion -- sigma2_path[i] depends
    only on r_train[:i+1], never on anything at or after the held-back tail's own indices),
    then QLIKE is evaluated ONLY on the held-back tail, comparing sigma2_path[i-1] (the
    variance known before seeing r_train[i]) against r_train[i]**2. Because every held-back
    point's forecast depends only on returns strictly before it, this is a legitimate
    training-fold-only selection: it never uses a return at or after `r_train`'s own last
    index (which is itself already strictly before the as-of day the caller is scoring),
    and it never uses IBJA truth or a coverage outcome. Falls back to the grid's median when
    there is not enough data to hold back a reliable tail.
    """
    n = len(r_train)
    n_val = max(min_val, round(n * val_frac))
    n_val = min(n_val, n - min_val)
    if n_val <= 0:
        return float(np.median(grid))

    losses = []
    for lam in grid:
        sigma2_path = ewma_variance_path(r_train, lam=lam)
        idx = np.arange(max(1, n - n_val), n)  # need i-1 >= 0
        forecast_var = sigma2_path[idx - 1]
        actual_sq = r_train[idx] ** 2
        losses.append(float(np.mean(qlike(actual_sq, forecast_var))))
    return float(grid[int(np.argmin(losses))])


def fit_vol_model(r_train: np.ndarray, method: str) -> VolFit | None:
    """Fit/select a conditional-volatility model on `r_train` alone. None if there is not
    yet enough history (MIN_TRAIN_SIZE, reused from ml.range_forecast.garch)."""
    n = len(r_train)
    if n < MIN_TRAIN_SIZE:
        return None
    if method == "ewma":
        lam = select_ewma_lambda(r_train)
        return VolFit(method="ewma", n_train=n, lam=lam)
    if method == "garch":
        params = fit_garch(r_train, dist="student_t")
        return VolFit(method="garch", n_train=n, params=params)
    raise ValueError(f"unknown vol method: {method!r}")


def forecast_variance_path(r_train: np.ndarray, fit: VolFit) -> np.ndarray:
    """sigma2_forecast[i] = Var(r_train[i] | F_{i-1}) for every i, same length as `r_train`.

    GARCH: ml.range_forecast.garch.garch_sigma2_path already returns exactly this
    pre-return convention. EWMA: ml.range_forecast.baselines.ewma_variance_path is
    post-return-inclusive (sigma2[i] incorporates r_train[i] itself), so it is shifted by
    one index here; the first element has no prior return to condition on and is seeded
    with ewma_variance_path's own first (post-return) value, matching that module's own
    seeding convention (EWMA_SEED_WINDOW).
    """
    if fit.method == "garch":
        p = fit.params
        assert p is not None
        return garch_sigma2_path(r_train, p["omega"], p["alpha"], p["beta"])
    assert fit.lam is not None
    sigma2_post = ewma_variance_path(r_train, lam=fit.lam)
    out = np.empty_like(sigma2_post)
    out[0] = sigma2_post[0]
    out[1:] = sigma2_post[:-1]
    return out


def h_day_forecast_stdev(r_train: np.ndarray, fit: VolFit, h: int) -> float:
    """sqrt of the h-day-ahead volatility FORECAST as of the close of `r_train`'s last
    return -- i.e. using only data through the current as-of day, never any historical
    realized vol from inside the quantile window itself."""
    if fit.method == "garch":
        p = fit.params
        assert p is not None
        sigma2_path = garch_sigma2_path(r_train, p["omega"], p["alpha"], p["beta"])
        persistence = p["alpha"] + p["beta"]
        long_run_var = (
            p["omega"] / (1.0 - persistence) if persistence < 1.0 - 1e-8 else sigma2_path[-1]
        )
        sigma2_next = p["omega"] + p["alpha"] * r_train[-1] ** 2 + p["beta"] * sigma2_path[-1]
        h_var = garch_h_day_variance(sigma2_next, long_run_var, persistence, h)
        return math.sqrt(max(h_var, 1e-12))
    assert fit.lam is not None
    sigma2_post = ewma_variance_path(r_train, lam=fit.lam)
    return math.sqrt(max(float(sigma2_post[-1]) * h, 1e-12))


def standardized_quantile_path(
    z: np.ndarray,
    k: int,
    hs_window: int = HS_WINDOW,
    min_sample: int = HS_MIN_SAMPLE,
    level: float = LEVEL,
) -> tuple[float, float] | None:
    """Empirical quantiles of the rolling k-day cumulative-standardized-return path, over
    the trailing `hs_window` standardized returns -- the same rolling-path construction as
    ml.weekly_range.base_range (see that function), applied to cumsum(z) in place of
    log(price). Returns (lo_q, hi_q) in dimensionless z-units, or None below `min_sample`.
    """
    z_tail = z[-(hs_window + k) :] if hs_window + k < len(z) else z
    cum = np.concatenate(([0.0], np.cumsum(z_tail)))
    n = len(cum) - k
    if n < min_sample:
        return None
    steps = np.stack([cum[s : s + n] for s in range(1, k + 1)], axis=1) - cum[:n, None]
    tail = (1.0 - level) / 2.0
    lo_q = float(np.quantile(steps.min(axis=1), tail))
    hi_q = float(np.quantile(steps.max(axis=1), 1.0 - tail))
    return (lo_q, hi_q)


class VolCache:
    """Refits a conditional-volatility model only every `refit_every` proxy returns,
    reusing the cached fit on the (growing) current `r_train` in between -- the same
    expanding-window-with-periodic-refit cadence ml.range_forecast.garch.garch_forecast_set
    already uses, generalized here to also cover EWMA lambda selection. Every fit still
    uses ONLY the `r_train` passed to it (returns strictly before the as-of day being
    scored); the cache only decides HOW OFTEN to recompute that fit, never widens what data
    a fit is allowed to see.
    """

    def __init__(self, method: str, refit_every: int = REFIT_EVERY) -> None:
        self.method = method
        self.refit_every = refit_every
        self._fit: VolFit | None = None

    def get(self, r_train: np.ndarray) -> VolFit | None:
        n = len(r_train)
        if n < MIN_TRAIN_SIZE:
            return None
        if self._fit is None or (n - self._fit.n_train) >= self.refit_every:
            fit = fit_vol_model(r_train, self.method)
            if fit is not None:
                self._fit = fit
        return self._fit


def fhs_base_range(proxy_log: np.ndarray, cache: VolCache, k: int) -> tuple[float, float] | None:
    """Drop-in replacement for ml.weekly_range.base_range(proxy, as_of, k): `proxy_log` is
    log(proxy prices) ALREADY FILTERED to rows known at the as-of day (the caller's
    responsibility, exactly as ml.weekly_range.base_range itself filters `proxy.index <=
    as_of` before this function ever sees the array -- see scripts/analysis_fhs_ranges.py).
    Returns (lo, hi) log-return bounds, or None if there is not yet enough history.
    """
    r_train = np.diff(proxy_log)
    fit = cache.get(r_train)
    if fit is None:
        return None
    sigma2_fc = forecast_variance_path(r_train, fit)
    z = r_train / np.sqrt(np.clip(sigma2_fc, 1e-12, None))
    q = standardized_quantile_path(z, k)
    if q is None:
        return None
    lo_q, hi_q = q
    sigma_h = h_day_forecast_stdev(r_train, fit, k)
    lo, hi = sigma_h * lo_q, sigma_h * hi_q
    return (min(lo, -1e-6), max(hi, 1e-6))


def fhs_walk_forward(proxy: pd.Series, ibja: pd.Series, horizon: str, method: str) -> pd.DataFrame:
    """FHS's own walk-forward -- mirrors ml.weekly_range.walk_forward's exact output columns,
    IBJA split-conformal calibration, and "matured windows only" embargo (a window's score
    counts toward a later window's `scale` only once its OWN outcome day has passed, so
    embargo >= horizon by construction, same as ml.weekly_range). Only the proxy-side shape
    (`fhs_base_range` in place of ml.weekly_range.base_range) differs.

    A single VolCache is reused across the whole pass: `complete_windows` yields windows in
    increasing as_of order, so the number of proxy returns known at each successive window
    only grows, and the cache's "refit every REFIT_EVERY new returns" cadence behaves exactly
    like ml.range_forecast.garch's own expanding-window refit loop.
    """
    windows = complete_windows(ibja, horizon)
    proxy_log = np.log(proxy.to_numpy(dtype=float))
    proxy_dates = proxy.index.to_numpy()
    cache = VolCache(method)
    k = issue_days(horizon)
    rows = []
    matured: list[tuple[pd.Timestamp, float]] = []
    pending: list[tuple[pd.Timestamp, float]] = []
    for w in windows:
        pos = int(np.searchsorted(proxy_dates, np.datetime64(w.as_of), side="right"))
        base = fhs_base_range(proxy_log[:pos], cache, k)
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


def fhs_next_day_range_pct_series(
    proxy: pd.Series, ibja: pd.Series, decision_dates: list[pd.Timestamp], method: str
) -> dict[pd.Timestamp, tuple[float, float] | None]:
    """FHS's 1-day range as (lo_pct, hi_pct) for every `d` in `decision_dates`, mirroring ml.
    next_day_range.next_day_range_pct's contract and its strict `index < d` no-lookahead
    filter exactly (see below for the proof of equivalence).

    Reuses `fhs_walk_forward(proxy, ibja, "1d", method)` rather than recomputing every
    window's score from scratch for every decision day: a window's own `score` value depends
    only on proxy data known at or before the window's OWN as_of (which is always strictly
    before any `d` that could matter for it), so it does not depend on which `d` is being
    scored, and a window with `end < d` is exactly a window that is entirely `< d` (as_of <
    end and every one of w.days <= end), so filtering the precomputed walk-forward by
    `end < d` is equivalent to ml.next_day_range's own `ibja[ibja.index < d]` filter, just
    computed once instead of once per decision day.

    FHS's shape AT `d` itself (as opposed to at any historical window's as_of) is a second,
    separate VolCache fed `d`'s in chronological order -- the same refit cadence, applied to
    a different sequence of as-of points than the windows-based cache inside
    `fhs_walk_forward` uses.

    Caveat (performance/staleness, not a lookahead): because this second cache is shared
    SEQUENTIALLY across every `d` in the call, a later `d`'s shape can reuse a vol-model fit
    refit at an EARLIER `d` (whenever fewer than REFIT_EVERY new proxy returns have
    accumulated since), so calling this function with a larger batch of dates can return a
    (still leakage-free -- the fit was always made on data before that earlier `d`, which is
    also before this later `d`) but slightly staler shape for a given `d` than calling it
    for that `d` alone would. Only the EARLIEST date in a batch is guaranteed to match a
    single-date call, since it is always the first thing either cache fits.
    """
    wf = fhs_walk_forward(proxy, ibja, "1d", method)
    proxy_log_full = np.log(proxy.to_numpy(dtype=float))
    proxy_dates = proxy.index.to_numpy()
    cache = VolCache(method)
    out: dict[pd.Timestamp, tuple[float, float] | None] = {}
    for d in sorted(decision_dates):
        matured = wf[wf["end"] < d] if len(wf) else wf
        s = conformal_scale(matured["score"].tolist()) if len(matured) else None
        if s is None:
            out[d] = None
            continue
        pos_d = int(np.searchsorted(proxy_dates, np.datetime64(d), side="left"))
        base_d = fhs_base_range(proxy_log_full[:pos_d], cache, 1)
        if base_d is None:
            out[d] = None
            continue
        lo_base, hi_base = base_d
        out[d] = (math.exp(s * lo_base) - 1.0, math.exp(s * hi_base) - 1.0)
    return out


__all__ = [
    "EWMA_LAMBDA_GRID",
    "VOL_METHODS",
    "VolCache",
    "VolFit",
    "fhs_base_range",
    "fhs_next_day_range_pct_series",
    "fhs_walk_forward",
    "fit_vol_model",
    "forecast_variance_path",
    "h_day_forecast_stdev",
    "select_ewma_lambda",
    "standardized_quantile_path",
]
