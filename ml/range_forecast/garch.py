"""ml.range_forecast.garch -- GARCH(1,1) by scipy MLE (normal or Student-t
innovations), refit on an expanding window at least every 21 trading days.

No `arch`/`statsmodels` dependency (not in ml/requirements-inference.lock):
the recursion, negative log-likelihood, and MLE (scipy.optimize.minimize,
SLSQP with the standard alpha+beta<1 stationarity constraint) are all
written here. Multi-step variance forecasting uses the textbook GARCH(1,1)
formula (e.g. Tsay, "Analysis of Financial Time Series"):

    E[sigma2_{t+k} | F_t] = V_L + (alpha+beta)^(k-1) * (sigma2_{t+1} - V_L)

where V_L = omega / (1 - alpha - beta) is the long-run (unconditional)
variance, and h-day variance = sum_{k=1}^{h} E[sigma2_{t+k} | F_t].
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import gammaln
from scipy.stats import norm, t as student_t

from ml.range_forecast.walkforward import (
    assemble_forecast_set,
    log_price_and_returns,
    price_positions_and_dates,
)

REFIT_EVERY = 21
MIN_TRAIN_SIZE = 250
_NU_BOUNDS = (2.1, 60.0)
_ALPHA_BETA_SLACK = 1e-6  # stationarity margin: alpha + beta <= 1 - slack


def _seed_variance(r_used: np.ndarray, omega: float, alpha: float, beta: float) -> float:
    persistence = alpha + beta
    if persistence < 1.0 - 1e-8:
        return omega / (1.0 - persistence)
    return float(np.var(r_used, ddof=1)) if len(r_used) > 1 else float(r_used[0] ** 2)


def garch_sigma2_path(r_used: np.ndarray, omega: float, alpha: float, beta: float) -> np.ndarray:
    """Filtered conditional variance sigma2[i] = Var(r_used[i] | F_{i-1}),
    same length as r_used."""
    n = len(r_used)
    sigma2 = np.empty(n)
    sigma2[0] = _seed_variance(r_used, omega, alpha, beta)
    for i in range(1, n):
        sigma2[i] = omega + alpha * r_used[i - 1] ** 2 + beta * sigma2[i - 1]
    return sigma2


def _neg_log_lik_normal(params: np.ndarray, r_used: np.ndarray) -> float:
    omega, alpha, beta = params
    sigma2 = garch_sigma2_path(r_used, omega, alpha, beta)
    sigma2 = np.clip(sigma2, 1e-12, None)
    ll = -0.5 * np.sum(np.log(2 * np.pi) + np.log(sigma2) + r_used**2 / sigma2)
    return float(-ll)


def _neg_log_lik_student_t(params: np.ndarray, r_used: np.ndarray) -> float:
    omega, alpha, beta, nu = params
    sigma2 = garch_sigma2_path(r_used, omega, alpha, beta)
    sigma2 = np.clip(sigma2, 1e-12, None)
    # Standardized Student-t density scaled to have variance sigma2 (nu > 2).
    log_const = gammaln((nu + 1) / 2) - gammaln(nu / 2) - 0.5 * np.log(np.pi * (nu - 2))
    ll = np.sum(
        log_const
        - 0.5 * np.log(sigma2)
        - ((nu + 1) / 2) * np.log1p(r_used**2 / (sigma2 * (nu - 2)))
    )
    return float(-ll)


def fit_garch(r_used: np.ndarray, dist: str = "student_t", maxiter: int = 200) -> dict:
    """MLE fit of GARCH(1,1) on `r_used` (returns known through the fit
    date). Returns {omega, alpha, beta[, nu], converged, dist}.

    Fits on returns SCALED by their own sample std (a standard numerical
    trick -- e.g. the `arch` package scales by 100 for the same reason):
    omega is quadratic in the return's scale while alpha/beta are
    scale-invariant, so an unscaled fit puts omega ~1e-5 next to alpha/beta
    ~0.05-0.9 in the same parameter vector. SLSQP's finite-difference
    gradient step is a fixed absolute epsilon, so that scale mismatch was
    observed (smoke test) to make the optimizer take a near-zero step on
    alpha/beta and report false convergence after 5 iterations. Scaling
    returns to O(1) variance first, then un-scaling omega by rstd**2
    afterward (alpha/beta need no adjustment -- the recursion is linear in
    sigma2 and r**2, both scaling by rstd**2 together), fixes this.
    """
    rstd = float(np.std(r_used, ddof=1)) if len(r_used) > 1 else 1.0
    rstd = max(rstd, 1e-8)
    r_scaled = r_used / rstd
    var0_scaled = float(np.var(r_scaled, ddof=1)) if len(r_scaled) > 1 else 1.0
    var0_scaled = max(var0_scaled, 1e-6)

    x0_base = [var0_scaled * 0.05, 0.05, 0.90]
    bounds_base = [(1e-10, var0_scaled * 20 + 1e-6), (0.0, 0.4), (0.0, 0.999)]
    constraints = [{"type": "ineq", "fun": lambda p: 1.0 - _ALPHA_BETA_SLACK - p[1] - p[2]}]

    if dist == "student_t":
        x0 = np.array([*x0_base, 8.0])
        bounds = [*bounds_base, _NU_BOUNDS]
        nll = _neg_log_lik_student_t
    else:
        x0 = np.array(x0_base)
        bounds = bounds_base
        nll = _neg_log_lik_normal

    result = minimize(
        nll,
        x0,
        args=(r_scaled,),
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"maxiter": maxiter, "ftol": 1e-10},
    )
    params = result.x
    out = {
        "omega": float(params[0]) * rstd**2,
        "alpha": float(params[1]),
        "beta": float(params[2]),
        "converged": bool(result.success),
        "dist": dist,
    }
    if dist == "student_t":
        out["nu"] = float(params[3])
    return out


def garch_h_day_variance(sigma2_next: float, long_run_var: float, persistence: float, h: int) -> float:
    total = 0.0
    for k in range(1, h + 1):
        e_k = long_run_var + (persistence ** (k - 1)) * (sigma2_next - long_run_var)
        total += max(e_k, 1e-12)
    return total


def _quantile_bounds_for_dist(scale_h: float, level: float, dist: str, nu: float | None) -> tuple[float, float]:
    if dist == "student_t" and nu is not None and nu > 2:
        z = float(student_t.ppf(0.5 + level / 2.0, df=nu))
        adj = np.sqrt((nu - 2) / nu)  # rescale so the interval has variance scale_h^2
        return -z * adj * scale_h, z * adj * scale_h
    z = float(norm.ppf(0.5 + level / 2.0))
    return -z * scale_h, z * scale_h


def garch_forecast_set(
    price: pd.Series,
    dataset: str,
    horizon: int,
    levels: tuple[float, ...] = (0.8, 0.9),
    dist: str = "student_t",
    refit_every: int = REFIT_EVERY,
    min_train_size: int = MIN_TRAIN_SIZE,
) -> dict:
    """GARCH(1,1) walk-forward: expanding-window MLE refit every
    `refit_every` trading days; the conditional-variance recursion and
    forecast use whatever params are currently active on every day in
    between (no look-ahead: a refit at day t uses only r[:t])."""
    log_price, r = log_price_and_returns(price)
    positions, date_strs = price_positions_and_dates(price)
    n_price = len(log_price)

    dates, poss, cur_px, actual, scale = [], [], [], [], []
    level_lo: dict[float, list[float]] = {lv: [] for lv in levels}
    level_hi: dict[float, list[float]] = {lv: [] for lv in levels}

    params: dict | None = None
    last_refit_t = -(10**9)
    fit_count = 0

    for t in range(min_train_size, n_price - horizon):
        r_used = r[:t]
        if params is None or (t - last_refit_t) >= refit_every:
            params = fit_garch(r_used, dist=dist)
            last_refit_t = t
            fit_count += 1

        sigma2_path = garch_sigma2_path(r_used, params["omega"], params["alpha"], params["beta"])
        persistence = params["alpha"] + params["beta"]
        long_run_var = (
            params["omega"] / (1.0 - persistence) if persistence < 1.0 - 1e-8 else sigma2_path[-1]
        )
        sigma2_next = params["omega"] + params["alpha"] * r_used[-1] ** 2 + params["beta"] * sigma2_path[-1]
        h_var = garch_h_day_variance(sigma2_next, long_run_var, persistence, horizon)
        scale_h = float(np.sqrt(max(h_var, 1e-12)))
        act = float(log_price[t + horizon] - log_price[t])

        dates.append(date_strs[t])
        poss.append(positions[t])
        cur_px.append(float(price.iloc[t]))
        actual.append(act)
        scale.append(scale_h)
        for lv in levels:
            lo, hi = _quantile_bounds_for_dist(scale_h, lv, dist, params.get("nu"))
            level_lo[lv].append(lo)
            level_hi[lv].append(hi)

    fs = assemble_forecast_set(
        "garch",
        dataset,
        horizon,
        dates,
        poss,
        cur_px,
        actual,
        scale,
        {lv: (level_lo[lv], level_hi[lv]) for lv in levels},
    )
    fs["n_refits"] = fit_count
    fs["dist"] = dist
    return fs
