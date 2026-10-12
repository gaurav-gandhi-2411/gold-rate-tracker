"""ml.live_monitor -- the LIVE-ONLY safety monitor (ADR 075), alongside ADR 068's demotion rules.

Why it exists. ADR 068's windows (40/40/60 folds) are filled mostly by retrospective re-run days
until forward days reach the window length (about December 2026), so a model that is worse live than
in its backtest could go unnoticed for about two months. This monitor scores ONLY days the model
issued live (``retro`` is explicitly False) and nothing else.

Two checks, both anytime-valid (valid at every look, so it can look every day):

  error     per live day d_t = |error of the model| - |error of holding the last fix|. A one-sided
            normal-mixture confidence sequence (the same machinery as rule v7, ml/promotion.py) for
            mean(d). Breach (DEMOTE to hold) when its LOWER bound is above zero: the model is
            clearly worse than hold. First look at ``first_look_days`` live days.
  coverage  live hits of the model's own 80% range. An e-process (a mixture of likelihood ratios
            against the target, uniform over true coverages in [prior_lo, prior_hi]) that is a
            supermartingale for every true coverage at or above the target, so by Ville's inequality
            P(ever >= 1/alpha) <= alpha. Breach (ALERT only, never demote) when it reaches
            ``coverage_threshold``. Ville gives 1/alpha for independent hits; live hits are
            clustered, so the threshold is set by simulation instead.

The numbers live in ``LIVE_RULE`` and are FROZEN: ``LIVE_RULE_SHA256`` is the hash of their canonical
JSON, recorded in docs/adr/075-live-only-monitor.md, and tests/test_live_monitor.py fails if either
moves without the other. The variance inflation was SET BY SIMULATION on the model's retrospective
loss series (scripts/simulate_live_monitor.py, reports/live_monitor_simulation.json, ADR 075), never tuned
on the first live days. Changing the rule is GG's decision (a new ADR), not a tuning step.

Fail closed (rule 98a): a record that cannot be scored is reported as not checked and never as a
pass; the caller (``ml.nextfix._demotion``) treats an exception here as a monitor failure.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import numpy as np

LIVE_RULE: dict[str, Any] = {
    "version": 1,
    "design": "error: one-sided normal-mixture confidence sequence on the mean of "
    "(model |error| - hold |error|) over LIVE days, demote when the lower bound > 0; "
    "coverage: mixture likelihood-ratio e-process against the 80% target, alert when >= 1/alpha",
    "live_days": "folds with retro explicitly False",
    "first_look_days": 10,  # no look before this many live days
    "alpha": 0.05,  # one-sided, valid at every look (family of one test)
    "family_size": 1,
    "hac_lags": 4,  # Newey-West lags of the long-run variance plug-in (as rule v7)
    "variance_floor_iid": True,
    "variance_inflation": 2.25,  # SET BY SIMULATION (smallest value on a 0.25 grid, see ADR 075)
    "mix_sd": 0.3,  # as rule v7
    "error_action": "demote",
    "coverage_nominal": 0.80,
    "coverage_alpha": 0.05,  # the false-alert level the threshold is calibrated to
    "coverage_threshold": 150.0,  # SET BY SIMULATION (smallest on a fixed grid), see ADR 075
    "coverage_first_look_days": 10,
    "coverage_prior_lo": 0.50,  # alternatives mixed over: true coverage uniform on [lo, hi] ...
    "coverage_prior_hi": 0.78,  # ... step 0.02 (15 points); fixed before any simulation was run
    "coverage_prior_step": 0.02,
    "coverage_action": "alert",
}
LIVE_RULE_SHA256 = "7326ea844d7e98c597db9a725c27010e9097e9f5fe1accf5dc96bf0f985f42b1"


def rule_sha256(rule: dict[str, Any] | None = None) -> str:
    blob = json.dumps(LIVE_RULE if rule is None else rule, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def cs_rule(rule: dict[str, Any] = LIVE_RULE) -> dict[str, Any]:
    """The keys ``ml.promotion.cs_lower_bounds`` reads, taken from the frozen live rule."""
    return {
        "alpha": rule["alpha"],
        "family_size": rule["family_size"],
        "hac_lags": rule["hac_lags"],
        "variance_floor_iid": rule["variance_floor_iid"],
        "variance_inflation": rule["variance_inflation"],
        "mix_sd": rule["mix_sd"],
        "min_forward_days": rule["first_look_days"],
    }


def live_days(folds: list[dict]) -> list[dict]:
    """Per-day folds this model issued live, oldest first: ``retro`` is explicitly False. A fold
    with no ``retro`` flag is NOT counted (fail closed: it may be a re-run day)."""
    return sorted((f for f in folds if f.get("retro") is False), key=lambda f: f["d0"])


def loss_diff(f: dict) -> float:
    """Model |error| minus hold |error| for one fold, Rs./g (positive = the model is worse)."""
    return abs(f["pm1"] - f["pm0"] * math.exp(f["ret"])) - abs(f["pm1"] - f["pm0"])


def error_status(folds: list[dict], rule: dict[str, Any] = LIVE_RULE) -> dict[str, Any]:
    """The live error check. ``breach`` True means: demote to hold."""
    from ml import promotion

    days = live_days(folds)
    n = len(days)
    out: dict[str, Any] = {"n_live": n, "first_look_days": rule["first_look_days"], "breach": False}
    if n == 0:
        return {**out, "reason": "no live days yet"}
    d = np.array([loss_diff(f) for f in days], dtype=float)
    hold = float(np.mean([abs(f["pm1"] - f["pm0"]) for f in days]))
    out["mean_diff"] = round(float(d.mean()), 2)
    out["mae_model"] = round(hold + float(d.mean()), 2)
    out["mae_hold"] = round(hold, 2)
    if n < rule["first_look_days"]:
        return {**out, "reason": "too few live days"}
    lower = float(promotion.cs_lower_bounds(d, cs_rule(rule))[-1])
    if math.isnan(lower):  # zero variance: no bound, no breach, and say so
        return {**out, "lower_bound": None, "reason": "no bound (zero variance)"}
    out["lower_bound"] = round(lower, 2)
    out["breach"] = bool(lower > 0.0)
    return out


def coverage_evalue(hits: list[bool], rule: dict[str, Any] = LIVE_RULE) -> float:
    """The e-process value after the given live hits (log-space mixture, no overflow)."""
    n, k = len(hits), int(sum(hits))
    if n == 0:
        return 1.0
    p0 = float(rule["coverage_nominal"])
    grid = np.arange(
        rule["coverage_prior_lo"],
        rule["coverage_prior_hi"] + rule["coverage_prior_step"] / 2,
        rule["coverage_prior_step"],
    )
    logs = k * np.log(grid / p0) + (n - k) * np.log((1.0 - grid) / (1.0 - p0))
    m = float(logs.max())
    return float(math.exp(m + math.log(float(np.exp(logs - m).sum()) / len(grid))))


def coverage_status(live_hits: list[bool], rule: dict[str, Any] = LIVE_RULE) -> dict[str, Any]:
    """The live coverage check. ``breach`` True means: ALERT (never demotes)."""
    n = len(live_hits)
    out: dict[str, Any] = {
        "n_live": n,
        "first_look_days": rule["coverage_first_look_days"],
        "breach": False,
        "coverage": round(sum(live_hits) / n, 3) if n else None,
        "target": rule["coverage_nominal"],
    }
    if n < rule["coverage_first_look_days"]:
        return {**out, "reason": "too few live days"}
    e = coverage_evalue(live_hits, rule)
    out["e_value"] = round(e, 3)
    out["breach"] = bool(e >= rule["coverage_threshold"])
    return out


def live_range_hits(folds: list[dict]) -> list[bool]:
    """Walk-forward 80% range hit for each LIVE day (each range uses only earlier folds)."""
    from ml.promotion import range_hits

    hits = range_hits(folds)
    return [hits[f["d0"]] for f in live_days(folds) if f["d0"] in hits]


def live_status(folds: list[dict], rule: dict[str, Any] = LIVE_RULE) -> dict[str, Any]:
    """Both checks for one model's record. ``demote`` follows the error check only."""
    err = error_status(folds, rule)
    cov = coverage_status(live_range_hits(folds), rule)
    return {
        "rule_sha256": rule_sha256(rule),
        "error": err,
        "coverage": cov,
        "demote": bool(err["breach"]),
        "coverage_alert": bool(cov["breach"]),
    }
