"""ml/kalman_nowcast.py -- state-space (Kalman filter) nowcast of the retail 22K price (ADR 055).

Research and forward shadow only. Nothing in the live pipeline imports this module.

Model (daily steps, one step per UTC calendar day):

  state      x_t = [s_t, m_ibja, m_grt, m_malabar, m_comex]
             s_t      log of the "true" retail 22K price, anchored to Tanishq (Tanishq has no
                      markup state: its readings observe s_t directly)
             m_g      each source group's log markup relative to Tanishq, a slow random walk
  transition x_t = x_{t-1} + w_t,  w_t ~ N(0, diag(q_level(t), q_markup, ..., q_markup))
             q_level(t) = q_level_nontrading on Sat/Sun (markets shut), else q_level_trading
  observation  log z = s_t + m_g(source) + e,  e ~ N(0, r_source + q_age * age_days)
             age_days = how old the value was when it was first assimilated (a stale board rate,
             or a COMEX close assimilated the next calendar day). Each distinct reading is
             assimilated ONCE, on the day it first became known; days with no readings just add
             state noise, so the predictive variance grows over weekends and holidays.

A day's non-anchor readings are assimilated first, the posterior of s_t is recorded (that is the
nowcast of day t: it never sees day t's own Tanishq reading), and only then is day t's Tanishq
reading assimilated for later days.

Noise parameters are estimated by maximum likelihood (prediction-error decomposition) on a
training prefix only; `scripts/analysis_kalman_nowcast.py` refits walk-forward.

Pure: numpy/scipy/pandas only, no file or network I/O, no import from the live pipeline.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

ANCHOR = "tanishq"
SOURCE_GROUP: dict[str, str | None] = {
    "tanishq": None,
    "ibja_am": "ibja",
    "ibja_pm": "ibja",
    "grt": "grt",
    "malabar": "malabar",
    "comex": "comex",
}
MARKUP_GROUPS: tuple[str, ...] = ("ibja", "grt", "malabar", "comex")
STATE_DIM = 1 + len(MARKUP_GROUPS)
_GROUP_INDEX = {g: 1 + i for i, g in enumerate(MARKUP_GROUPS)}

PARAM_NAMES: tuple[str, ...] = (
    "q_level_trading",
    "q_level_nontrading",
    "q_markup",
    "q_age",
    "r_tanishq",
    "r_ibja_am",
    "r_ibja_pm",
    "r_grt",
    "r_malabar",
    "r_comex",
)
# Variances are in (log price)^2. Bounds: sd between ~0.003% and ~32% per day / per reading.
LOG_BOUNDS = (math.log(1e-11), math.log(1e-1))

Z80 = 1.2815515655446004  # two-sided 80% normal interval half-width in sd units
BURN_IN_DAYS = 10  # likelihood terms from the first days (diffuse start) are not scored
PURITY_22K = 22.0 / 24.0  # same 24K -> 22K factor as ml/inr_proxy.py


@dataclass(frozen=True)
class Observation:
    source: str
    log_value: float
    age_days: float = 0.0


@dataclass(frozen=True)
class Params:
    values: dict[str, float]

    def __post_init__(self) -> None:
        missing = set(PARAM_NAMES) - set(self.values)
        if missing:
            raise ValueError(f"missing parameters: {sorted(missing)}")
        bad = {k: v for k, v in self.values.items() if not (v > 0 and math.isfinite(v))}
        if bad:
            raise ValueError(f"parameters must be positive and finite: {bad}")

    def __getitem__(self, key: str) -> float:
        return self.values[key]

    def to_log_vector(self) -> np.ndarray:
        return np.log(np.array([self.values[k] for k in PARAM_NAMES], dtype=float))

    @classmethod
    def from_log_vector(cls, v: np.ndarray) -> Params:
        return cls({k: float(math.exp(x)) for k, x in zip(PARAM_NAMES, v, strict=True)})


@dataclass(frozen=True)
class InitialState:
    mean: np.ndarray
    cov: np.ndarray


@dataclass
class FilterResult:
    pre_anchor_mean: np.ndarray  # E[s_t | everything known on day t except day t's Tanishq]
    pre_anchor_var: np.ndarray  # Var of the same
    loglik: float
    n_loglik_terms: int
    final_mean: np.ndarray = field(default_factory=lambda: np.zeros(STATE_DIM))
    final_cov: np.ndarray = field(default_factory=lambda: np.eye(STATE_DIM))


def default_initial_state(first_log_level: float) -> InitialState:
    """Level at the first anchor reading (sd 10%); every markup at 0 with sd 10% (diffuse
    relative to the ~1-2% markups actually seen, so the data, not the prior, sets them)."""
    mean = np.zeros(STATE_DIM)
    mean[0] = first_log_level
    return InitialState(mean=mean, cov=np.eye(STATE_DIM) * 1e-2)


def default_start_params(obs_by_day: Sequence[Sequence[Observation]]) -> Params:
    """Method-of-moments starting point for the MLE: level variance from consecutive-day
    changes of the anchor; observation variances at conventional small values."""
    anchor = [next((o.log_value for o in day if o.source == ANCHOR), np.nan) for day in obs_by_day]
    a = np.asarray(anchor, dtype=float)
    d = np.diff(a)
    d = d[np.isfinite(d)]
    q = float(np.var(d)) if len(d) >= 5 else 1e-4
    q = min(max(q, 1e-7), 1e-2)
    return Params(
        {
            "q_level_trading": q,
            "q_level_nontrading": q / 4,
            "q_markup": 1e-6,
            "q_age": 1e-6,
            "r_tanishq": 1e-6,
            "r_ibja_am": 1e-5,
            "r_ibja_pm": 1e-5,
            "r_grt": 1e-5,
            "r_malabar": 1e-5,
            "r_comex": 1e-4,
        }
    )


def _state_indices(source: str) -> tuple[int, ...]:
    if source not in SOURCE_GROUP:
        raise ValueError(f"unknown source {source!r}")
    g = SOURCE_GROUP[source]
    return (0,) if g is None else (0, _GROUP_INDEX[g])


def _update(
    x: np.ndarray, p: np.ndarray, idx: tuple[int, ...], y: float, r: float
) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Scalar Kalman update for an observation y = sum(x[idx]) + e, Var(e) = r (idx is the level
    alone, or the level plus one markup). P - k ph' = P - ph ph'/f stays exactly symmetric."""
    if len(idx) == 1:
        ph = p[:, 0].copy()
        f = float(ph[0]) + r
        v = y - float(x[0])
    else:
        g = idx[1]
        ph = p[:, 0] + p[:, g]
        f = float(ph[0] + ph[g]) + r
        v = y - float(x[0] + x[g])
    k = ph / f
    return x + k * v, p - np.outer(k, ph), v, f


def run_filter(
    obs_by_day: Sequence[Sequence[Observation]],
    nontrading: Sequence[bool] | np.ndarray,
    params: Params,
    init: InitialState,
    loglik_days: tuple[int, int] | None = None,
) -> FilterResult:
    """Filter every day in order. `loglik_days=(lo, hi)` sums the Gaussian log-likelihood of the
    innovations on days lo <= t < hi (default: BURN_IN_DAYS to the end)."""
    n = len(obs_by_day)
    if len(nontrading) != n:
        raise ValueError("nontrading must have one flag per day")
    lo, hi = loglik_days if loglik_days is not None else (BURN_IN_DAYS, n)
    x = np.array(init.mean, dtype=float).copy()
    p = np.array(init.cov, dtype=float).copy()
    q_markup = params["q_markup"]
    pre_mean = np.full(n, np.nan)
    pre_var = np.full(n, np.nan)
    ll = 0.0
    n_terms = 0
    log2pi = math.log(2 * math.pi)
    for t in range(n):
        if t > 0:
            ql = params["q_level_nontrading"] if nontrading[t] else params["q_level_trading"]
            p[0, 0] += ql
            for j in range(1, STATE_DIM):
                p[j, j] += q_markup
        day = obs_by_day[t]
        ordered = [o for o in day if o.source != ANCHOR] + [o for o in day if o.source == ANCHOR]
        recorded = False
        for o in ordered:
            if o.source == ANCHOR and not recorded:
                pre_mean[t], pre_var[t] = x[0], p[0, 0]
                recorded = True
            idx = _state_indices(o.source)
            r = params[f"r_{o.source}"] + params["q_age"] * max(o.age_days, 0.0)
            x, p, v, f = _update(x, p, idx, o.log_value, r)
            if lo <= t < hi:
                ll += -0.5 * (log2pi + math.log(f) + v * v / f)
                n_terms += 1
        if not recorded:
            pre_mean[t], pre_var[t] = x[0], p[0, 0]
    return FilterResult(pre_mean, pre_var, ll, n_terms, x, p)


def fit_params(
    obs_by_day: Sequence[Sequence[Observation]],
    nontrading: Sequence[bool] | np.ndarray,
    init: InitialState,
    start: Params | None = None,
    maxiter: int = 200,
) -> tuple[Params, dict[str, float | int | bool]]:
    """MLE of the noise parameters on the days given (the caller passes a training prefix
    only). L-BFGS-B on log-variances within LOG_BOUNDS; deterministic."""
    from scipy.optimize import minimize

    s0 = (start or default_start_params(obs_by_day)).to_log_vector()
    s0 = np.clip(s0, LOG_BOUNDS[0] + 1e-6, LOG_BOUNDS[1] - 1e-6)

    def nll(v: np.ndarray) -> float:
        res = run_filter(obs_by_day, nontrading, Params.from_log_vector(v), init)
        return -res.loglik if math.isfinite(res.loglik) else 1e12

    out = minimize(
        nll,
        s0,
        method="L-BFGS-B",
        bounds=[LOG_BOUNDS] * len(PARAM_NAMES),
        options={"maxiter": maxiter},
    )
    info: dict[str, float | int | bool] = {
        "nll": float(out.fun),
        "iterations": int(out.nit),
        "converged": bool(out.success),
    }
    return Params.from_log_vector(np.asarray(out.x)), info


def predictive(
    mean_log: float, var_log: float, params: Params, z: float = Z80
) -> tuple[float, float, float]:
    """Point (the median, exp of the log mean) and central interval of the next Tanishq reading
    in Rs/g: the state's uncertainty plus Tanishq's own reading noise."""
    sd = math.sqrt(max(var_log, 0.0) + params["r_tanishq"])
    return math.exp(mean_log), math.exp(mean_log - z * sd), math.exp(mean_log + z * sd)


def comex_rs_per_g_22k(comex_usd_oz: float, usd_inr: float, duty_rate: float) -> float:
    """Landed 22K parity in Rs/g: COMEX USD/troy oz -> Rs/g 24K (/31.1034768 x USD/INR) x import
    duty in force x 22/24 purity. Same conversion as scripts/analysis_derived_premium.py (per 10 g
    there) and ml/inr_proxy.py (purity)."""
    troy_oz_to_gram = 31.1034768
    return comex_usd_oz / troy_oz_to_gram * usd_inr * (1.0 + duty_rate) * PURITY_22K


def build_observations(
    days: pd.DatetimeIndex,
    tanishq: pd.Series,
    ibja: pd.DataFrame,
    retail: pd.DataFrame,
    comex: pd.Series,
) -> list[list[Observation]]:
    """Assemble one list of readings per day of `days` (a daily UTC calendar).

    tanishq  Rs/g 22K, indexed by UTC date (last reading of the day).
    ibja     indexed by value date, columns `am`, `pm` in Rs/g 22K (916 per 10 g / 10). A value
             identical to the previous published row's same field is a stale re-publication and
             is skipped. AM and PM are assimilated on their value date, age 0.
    retail   columns `day` (UTC date the reading was captured), `source`, `rate_22k`,
             `observed_at_day` (the retailer's own last-update date). One row per source per
             day (the caller picks the last capture). A reading whose observed_at_day equals the
             previously assimilated one for that source is skipped (already used); otherwise
             age = day - observed_at_day.
    comex    landed 22K parity in Rs/g indexed by the COMEX trading date c; assimilated on c + 1
             (a close is final only after the US session, i.e. known on the next UTC day), age 1.
    """
    pos = {d: i for i, d in enumerate(pd.DatetimeIndex(days).normalize())}
    out: list[list[Observation]] = [[] for _ in range(len(days))]

    def put(day: Any, o: Observation) -> None:
        i = pos.get(pd.Timestamp(day).normalize())
        if i is not None and math.isfinite(o.log_value):
            out[i].append(o)

    ib = ibja.sort_index()
    for col, src in (("am", "ibja_am"), ("pm", "ibja_pm")):
        s = ib[col]
        prev = s.shift(1)
        for d, v, pv in zip(s.index, s.to_numpy(), prev.to_numpy(), strict=True):
            if not (np.isfinite(v) and v > 0) or (np.isfinite(pv) and v == pv):
                continue
            put(d, Observation(src, math.log(v), 0.0))
    last_obs: dict[str, pd.Timestamp] = {}
    r = retail.sort_values(["day", "source"])
    for day_raw, src_raw, v_raw, od_raw in zip(
        r["day"], r["source"], r["rate_22k"], r["observed_at_day"], strict=True
    ):
        src = str(src_raw)
        if src not in SOURCE_GROUP or src == ANCHOR:
            continue
        v = float(v_raw)
        od = pd.Timestamp(od_raw).normalize()
        if not (np.isfinite(v) and v > 0) or last_obs.get(src) == od:
            continue
        last_obs[src] = od
        day = pd.Timestamp(day_raw).normalize()
        put(day, Observation(src, math.log(v), max(float((day - od).days), 0.0)))
    for c, v in zip(comex.index, comex.to_numpy(), strict=True):
        if np.isfinite(v) and v > 0:
            put(pd.Timestamp(c) + pd.Timedelta(days=1), Observation("comex", math.log(v), 1.0))
    for d, v in tanishq.items():
        if np.isfinite(v) and v > 0:
            put(d, Observation(ANCHOR, math.log(v), 0.0))
    return out


def nontrading_flags(days: pd.DatetimeIndex) -> np.ndarray:
    """Sat/Sun of the UTC calendar day."""
    return np.asarray(pd.DatetimeIndex(days).dayofweek >= 5)
