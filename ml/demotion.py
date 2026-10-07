"""ml.demotion -- pre-registered rules that switch a live model back to "hold" (ADR 068, WIRED).

Wired into ``ml.nextfix.run`` (GG decision D3, 2026-10-05) with GG's settings: direction floor
0.55, persistence 3, windows 40/40/60. Their operating characteristics are in ADR 068
(scripts/simulate_demotion.py). A demotion is sticky: ``apply_status`` keeps a model demoted until a
human edits ``data/model_demotion_state.json``. Pure functions over the model's own
out-of-sample record (``ml.nextfix`` folds: pm0, pm1, ret, p_up, vol), in time order, with nothing
from the future.

What is scored (verifier note, 2026-10-07): the record holds the model's own out-of-sample folds.
Until at least 40 forward decision days exist (P3 forward from 2026-10-07), the 40/40/60 windows
are filled mostly by retrospective re-run folds, not by forecasts users were shown. Only once
forward n >= the window do the rules score purely what was published.

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
  range      last ``cov_window`` folds: coverage of the 80% range. Demote when a one-sided exact
             binomial test rejects "true coverage >= nominal - cov_slack" at ``cov_alpha``.

Any single rule demotes the WHOLE model to holding the last fix (point and range together, ADR
068 and ``ml.nextfix.forecast``); there is no range-only demotion.

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


def _stable_since(path) -> str:
    """A ``since`` that does not change between runs while the same unreadable file sits there.

    ``datetime.now()`` would move every run, so T16 (which fires when its last send is older than
    ``since``) would re-fire every cycle if the file cannot be rewritten. The file's mtime is stable
    until someone touches it; a fixed epoch is the fallback when even stat fails.
    """
    from datetime import UTC, datetime

    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
    except (OSError, OverflowError, ValueError):
        return "1970-01-01T00:00:00+00:00"


def _shape_ok(data: object) -> bool:
    """True when ``data`` is a state object whose fields ``apply_status`` and T16 can use."""
    if not isinstance(data, dict) or not isinstance(data.get("demoted"), bool):
        return False
    if not isinstance(data.get("model_version"), str):  # missing = cannot tell whose state it is
        return False
    if "history" in data and not isinstance(data["history"], list):
        return False
    reasons = data.get("reasons", [])
    return isinstance(reasons, list) and all(isinstance(r, dict) for r in reasons)


def load_state(path, model_version: str) -> dict:
    """The persisted state for THIS model version.

    * file missing: a fresh "not demoted" state (first run);
    * file present but unreadable, not a JSON object, ``demoted`` not a bool, ``model_version``
      missing, or ``history`` / ``reasons`` malformed: FAIL CLOSED, a demoted state with reason
      ``state_unreadable`` (the hold figure is the safe one; T16 alerts and a person repairs the
      file; rule 98a). Never silently re-promotes a demoted model. Its ``since`` is stable across
      runs (see ``_stable_since``);
    * a well-formed state written for ANOTHER model version is not applied to this one: a version
      bump or champion switch starts a fresh state by design (the new model earns its own record).
      The old file is overwritten on the next save;
    * a BOM (Notepad "UTF-8 with BOM") is accepted: it is not corruption.
    """
    import json

    if not path.exists():
        return empty_state(model_version)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        ok = _shape_ok(data)
    except (OSError, ValueError):
        data, ok = None, False
    if not ok:
        now = _stable_since(path)
        reasons = [{"rule": "state_unreadable", "path": str(path.name)}]
        return {
            **empty_state(model_version),
            "demoted": True,
            "since": now,
            "reasons": reasons,
            "history": [{"at": now, "event": "demoted", "reasons": reasons}],
            # tells the caller NOT to write this over the file: the unreadable original is the
            # evidence a person needs to repair it, and the next run fails closed the same way
            "unreadable": True,
        }
    if data["model_version"] != model_version:
        return empty_state(model_version)
    return {**empty_state(model_version), **data}


def atomic_write_text(path, text: str) -> None:
    """Write via a temp file in the same directory, then ``os.replace``: a crash or a full disk
    leaves the old file intact rather than a half-written one that would read as corrupt."""
    import os
    import tempfile
    from pathlib import Path

    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)  # our own half-written temp file
        raise


def save_state(path, state: dict) -> None:
    import json

    atomic_write_text(path, json.dumps(state, indent=1) + "\n")


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
