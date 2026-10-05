"""ml.demotion -- pre-registered rules that switch a live model back to "hold" (ADR 068, WIRED).

Wired into ``ml.nextfix.run`` (GG decision D3, 2026-10-05) with GG's settings: direction floor
0.55, persistence 3, windows 40/40/60. Their operating characteristics are in ADR 068
(scripts/simulate_demotion.py). A demotion is sticky: ``apply_status`` keeps a model demoted until a
human edits ``data/model_demotion_state.json``. Pure functions over the model's own
out-of-sample record (``ml.nextfix`` folds: pm0, pm1, ret, p_up, vol), so the rules score exactly
what users were shown, in time order, with nothing from the future.

Why a rolling window: ``ml.direction.gate.decide_direction_signal`` re-checks the CUMULATIVE
record every run. With 143+ days banked, a regime change has to outweigh months of earlier wins
before the cumulative p-value moves, so the signal would keep showing long after it stopped
working. A window forgets old wins.

Three independent rules, each against the baseline users would otherwise see:

  error      last ``err_window`` folds: paired |error| of the model minus |error| of holding the last
             fix. Demote when the model is worse AND the one-sided HAC (Newey-West) test says the
             model is worse at ``err_alpha``.
  direction  last ``dir_window`` folds: accuracy on days the fix moved. Demote when a one-sided exact
             binomial test rejects "true accuracy >= dir_floor" at ``dir_alpha``.
  range      last ``cov_window`` folds: coverage of the 80% range. Demote the range (not the point)
             when a one-sided exact binomial test rejects "true coverage >= nominal - cov_slack" at
             ``cov_alpha``.

A breach only counts after ``persist`` consecutive daily checks, to keep one bad week from flipping
a model that is fine on average (daily re-testing inflates false alarms; the simulation measures it).
Re-promotion is NOT automatic: the existing gates and a human decision re-enable a demoted model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class DemotionParams:
    """GG's approved settings (D3, 2026-10-05; ADR 068). Changing any value is GG's decision."""

    err_window: int = 40
    err_alpha: float = 0.05
    hac_lags: int = 4
    dir_window: int = 40
    dir_floor: float = 0.55  # 0.50 would be nearly inert: a model guessing at 50% is not below 50%
    dir_alpha: float = 0.05
    cov_window: int = 60
    cov_nominal: float = 0.80
    cov_slack: float = 0.05
    cov_alpha: float = 0.05
    persist: int = 3
    min_folds: int = 30


def hac_one_sided_p_worse(diff: np.ndarray, lags: int) -> float:
    """One-sided p that mean(diff) > 0 (the model is worse), Newey-West variance, normal tail."""
    from scipy.stats import norm

    n = len(diff)
    if n < 2:
        return 1.0
    dc = diff - diff.mean()
    var = float(dc @ dc) / n
    for lag in range(1, min(lags, n - 1) + 1):
        var += 2.0 * (1.0 - lag / (lags + 1)) * float(dc[lag:] @ dc[:-lag]) / n
    if var <= 0:
        return 0.0 if diff.mean() > 0 else 1.0
    t = diff.mean() / math.sqrt(var / n)
    return float(1.0 - norm.cdf(t))


def binom_p_lower(k: int, n: int, p0: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p0): the one-sided p that the true rate is >= p0."""
    from scipy.stats import binom

    return float(binom.cdf(k, n, p0)) if n > 0 else 1.0


def _err(folds: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    model = np.array([abs(f["pm1"] - f["pm0"] * math.exp(f["ret"])) for f in folds])
    flat = np.array([abs(f["pm1"] - f["pm0"]) for f in folds])
    return model, flat


def error_breach(folds: list[dict], p: DemotionParams) -> dict:
    w = folds[-p.err_window :]
    if len(w) < p.min_folds:
        return {"breach": False, "n": len(w), "reason": "too few folds"}
    model, flat = _err(w)
    diff = model - flat
    pval = hac_one_sided_p_worse(diff, p.hac_lags)
    return {
        "breach": bool(diff.mean() > 0 and pval < p.err_alpha),
        "n": len(w),
        "mae_model": float(model.mean()),
        "mae_hold": float(flat.mean()),
        "p_model_worse": pval,
    }


def direction_breach(folds: list[dict], p: DemotionParams) -> dict:
    w = [f for f in folds[-p.dir_window :] if f["pm1"] != f["pm0"]]
    if len(w) < p.min_folds:
        return {"breach": False, "n": len(w), "reason": "too few folds"}
    k = sum((f["p_up"] > 0.5) == (f["pm1"] > f["pm0"]) for f in w)
    pval = binom_p_lower(k, len(w), p.dir_floor)
    return {"breach": bool(pval < p.dir_alpha), "n": len(w), "hits": int(k), "p_below_floor": pval}


def range_breach(hits: list[bool], p: DemotionParams) -> dict:
    """``hits``: per-fold True when the realised fix landed inside the range, oldest first."""
    w = hits[-p.cov_window :]
    if len(w) < p.min_folds:
        return {"breach": False, "n": len(w), "reason": "too few folds"}
    k = int(sum(w))
    pval = binom_p_lower(k, len(w), p.cov_nominal - p.cov_slack)
    return {"breach": bool(pval < p.cov_alpha), "n": len(w), "covered": k, "p_below_nominal": pval}


def demotion_status(
    folds: list[dict], hits: list[bool] | None = None, p: DemotionParams | None = None
) -> dict:
    """Which rules have breached on each of the last ``persist`` daily checks (as of each).

    A rule demotes only when it breached on all ``persist`` most recent checks. ``folds`` and
    ``hits`` must contain only forecasts already resolved at the time of the check.
    """
    p = p or DemotionParams()
    out: dict = {"params": p.__dict__, "n_folds": len(folds), "demote": {}}
    # each rule is evaluated "i folds back" (i = 0 is today) so a breach must persist
    rules = {
        "error": lambda i: error_breach(folds[: len(folds) - i], p),
        "direction": lambda i: direction_breach(folds[: len(folds) - i], p),
    }
    if hits is not None:
        rules["range"] = lambda i: range_breach(hits[: len(hits) - i], p)
    for name, fn in rules.items():
        out["demote"][name] = all(fn(i)["breach"] for i in range(p.persist))
        out[name] = fn(0)
    out["demote_any"] = any(out["demote"].values())
    return out


# ── sticky state (data/model_demotion_state.json) ───────────────────────────────────────────────

STATE_SCHEMA = 1


def empty_state(model_version: str) -> dict:
    return {
        "schema_version": STATE_SCHEMA,
        "model_version": model_version,
        "demoted": False,
        "since": None,
        "reasons": [],
        "last_checked": None,
        "history": [],
    }


def load_state(path, model_version: str) -> dict:
    """The persisted state, or a fresh one when missing/unreadable for THIS model version.

    Fails open to "not demoted" only because the rules themselves re-evaluate the record on every
    run: an unreadable file cannot hide a degraded model for longer than one run, and a state
    written for another model version is never applied to this one.
    """
    import json

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty_state(model_version)
    if not isinstance(data, dict) or data.get("model_version") != model_version:
        return empty_state(model_version)
    return {**empty_state(model_version), **data}


def save_state(path, state: dict) -> None:
    import json

    path.write_text(json.dumps(state, indent=1) + "\n", encoding="utf-8")


def _reasons(status: dict) -> list[dict]:
    out = []
    for rule, flag in status["demote"].items():
        if flag:
            keep = {k: v for k, v in status[rule].items() if k != "breach"}
            out.append({"rule": rule, **keep})
    return out


def apply_status(state: dict, status: dict, now_iso: str) -> tuple[dict, bool]:
    """Fold today's ``demotion_status`` into the sticky state. Returns (new state, newly_demoted).

    Once demoted a model stays demoted: nothing here re-promotes it (a human edits the file).
    """
    new = {**state, "last_checked": now_iso, "history": list(state.get("history", []))}
    if state.get("demoted"):
        return new, False
    if status.get("demote_any"):
        reasons = _reasons(status)
        new.update(demoted=True, since=now_iso, reasons=reasons)
        new["history"].append({"at": now_iso, "event": "demoted", "reasons": reasons})
        return new, True
    return new, False
