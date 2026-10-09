"""Champion / challenger promotion rule (ADR 072, amended by Amendment 1), applied by code on
FORWARD days only.

The rule's numbers live in ``RULE`` and are frozen: ``RULE_SHA256`` is the hash of their canonical
JSON, recorded in docs/adr/072-champion-challenger-promotion-rule.md, and tests/test_promotion.py
fails if either moves without the other. Changing the rule is GG's decision (a new ADR), never a
tuning step.

Rule version 3 (Amendment 2, 2026-10-08) changes only the horizon unit: 180 DECISION days
(days the champion issued a live forecast), not 180 calendar days. Everything below is as in
version 2.

Rule version 2 (Amendment 1, 2026-10-08) replaces the fixed-sample test of version 1, which needed
about 400 forward days, with an anytime-valid test: a one-sided normal-mixture confidence sequence
(Waudby-Smith et al. 2021, asymptotic form) for the mean of

    e_t = (1 - min_gain) * loss_champion_t - loss_challenger_t

on the days both models issued live. mean(e) > 0 is exactly "the challenger's mean error is more than
``min_gain`` below the champion's", so the 5% gain is built into the series and no ratio (and so no
plug-in denominator) enters the decision. The bound is valid at EVERY look, so the rule can look
each day from ``min_forward_days`` on without inflating false promotions.

What this module does (pure functions, no network):
  * ``compare``: one challenger vs the champion on the days both issued a forecast live;
  * ``cs_lower_bounds``: the confidence-sequence lower bound after each forward day;
  * ``decide``: promote at most one challenger; alpha is split across the registered challengers
    (Bonferroni on the confidence-sequence level), retired challengers never promote;
  * ``load_champion`` / ``save_champion``: the one-file state that says which model is live.

Marking: every number returned is computed here from the per-day records handed in (VERIFIED when
those records are the committed data files); nothing is copied from an earlier report.

Loss = absolute error of the forecast fix in Rs./g, the repo's own ``|pm1 - pm0 * exp(ret)|``.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

# --- the frozen rule (ADR 072, version 2 = Amendment 1) ------------------------------------------
RULE: dict[str, Any] = {
    "version": 5,  # 1 = fixed-n DM + BH; 2 = CS; 3 = horizon unit; 4 = roll60 held back; 5 = calibrated sizes
    "design": "one-sided normal-mixture confidence sequence on the mean of "
    "e = (1 - min_gain) * loss_champion - loss_challenger; promote when the lower bound > 0",
    "min_gain": 0.05,  # challenger mean |error| at least 5% below the champion's (ratio of means)
    "alpha": 0.05,  # family-wise error over all registered live-capable challengers
    "family_size": 3,  # challengers per champion (len(LIVE_CAPABLE) - 1); Bonferroni: alpha / 3 each
    "hac_lags": 4,  # Newey-West lags for the long-run variance plug-in
    # Measured, not assumed (scripts/simulate_sequential_promotion.py): the plain Newey-West plug-in
    # is anti-conservative at 20-40 days (false-promotion rate 7-10% per challenger at the 5%-gain
    # boundary). The variance used is max(Newey-West, plain sample variance) x variance_inflation.
    "variance_floor_iid": True,
    "variance_inflation": 1.5,
    # ADR 072 Amendment 4: a challenger listed here uses its own calibrated inflation instead.
    # p3_roll60: smallest value on a 0.25 grid that keeps its wrongful-promotion rate at the 5%
    # boundary at or under 1.67% on every calibration resampler, proven on held-out ones
    # (scripts/calibrate_challenger_size.py, reports/challenger_size_calibration.md).
    "variance_inflation_by_challenger": {"p3_roll60": 3.0},
    "mix_sd": 0.3,  # sd of the normal mixing distribution over the tilt, in units of 1/sigma
    "min_forward_days": 20,  # no look before this many days both models issued live
    "horizon_days": 180,  # decision days since registration; at or after it: retired, not promoted
    "horizon_unit": "decision_days",  # days the champion issued a live forecast (v2: calendar days)
    # Evaluated and rejected (ADR 072 Amendment 2): a control variate on the hold forecast's
    # same-day error. Frozen here so adopting it later is a visible, hashed change.
    "control_variate": "none",
    # ADR 072 Amendment 3: a challenger listed here is still scored and reported but can never be
    # promoted. p3_roll60 is promoted at the 5% boundary in 8-9% of resampled paths against its
    # 1.67% allowance (reports/promotion_v3_simulation.md, block 20 and 40) until its size is fixed.
    "promotion_blocked": {"p3_monday": "size proof failed on a held-out resampler (Amendment 4)"},
    "coverage_nominal": 0.80,  # challenger's own 80% range; promotion needs it not below target
    "coverage_alpha": 0.05,  # exact one-sided binomial: coverage significantly below 0.80 blocks
    "direction_rule": "challenger direction hit-rate >= champion's on the same days",
    "common_start": "2026-10-07",  # first decision day P3 was live: no earlier day is compared
    "registered": {  # day each challenger started counting; its 180-day clock starts here
        "ensemble": "2026-10-07",
        "p3": "2026-10-07",  # a challenger only after another model has been promoted
        "p3_roll60": "2026-10-07",
        "p3_monday": "2026-10-07",
    },
    # not live-capable, so never promotable: registered on the first day they have a forward record
    "registered_on_first_record": ["hourly", "p3_hourly"],
}
RULE_SHA256 = "ce9e1eeeb49cbbf03e4d0b254f09b282285ecdb9318509039dcfd15289615836"

CHAMPION_FILE = "champion_state.json"
DEFAULT_CHAMPION = "p3"
SCHEMA = 1

# Models that have a live predictor in ml/nextfix.py: only these can be switched on automatically.
# Others (the hourly world-price model, an ensemble of P3 + hourly) are scored and reported, and
# need a code PR with a live predictor before they can be switched on (ADR 072).
LIVE_CAPABLE = ("p3", "p3_roll60", "p3_monday", "ensemble")


def rule_sha256(rule: dict[str, Any] | None = None) -> str:
    blob = json.dumps(RULE if rule is None else rule, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


# --- per-day alignment ---------------------------------------------------------------------------
def _loss(f: dict) -> float:
    return abs(f["pm1"] - f["pm0"] * math.exp(f["ret"]))


def forward_days(folds: list[dict], since: str) -> dict[str, dict]:
    """Per-day folds this model issued live: decision day >= ``since`` and not flagged retro."""
    return {f["d0"]: f for f in folds if f["d0"] >= since and f.get("retro") is not True}


def range_hits(folds: list[dict]) -> dict[str, bool]:
    """Walk-forward conformal 80% range hit per decision day (each range uses earlier folds only)."""
    from ml.nextfix import MIN_CONFORMAL, conformal_q

    out: dict[str, bool] = {}
    ordered = sorted(folds, key=lambda f: f["d0"])
    for i in range(MIN_CONFORMAL, len(ordered)):
        q = conformal_q(ordered[:i])
        if q is None:
            continue
        f = ordered[i]
        half = q * f["vol"] * f["pm0"]
        out[f["d0"]] = abs(f["pm1"] - f["pm0"] * math.exp(f["ret"])) <= half
    return out


def _long_run_var(d: np.ndarray, lags: int) -> float:
    n = len(d)
    dc = d - d.mean()
    var = float(dc @ dc) / n
    for lag in range(1, min(lags, n - 1) + 1):
        var += 2.0 * (1.0 - lag / (lags + 1)) * float(dc[lag:] @ dc[:-lag]) / n
    return var


def running_long_run_var(e: np.ndarray, lags: int) -> np.ndarray:
    """Newey-West long-run variance of ``e[..., :n]`` for every n (last axis), Bartlett weights.

    Same estimator as ``_long_run_var`` (mean-centred, divided by n, lags capped at n - 1) but for
    all prefixes at once through cumulative sums, so a simulation can look after every day. Entries
    are NaN for n < 2. Accepts any leading shape.
    """
    e = np.asarray(e, dtype=float)
    t_len = e.shape[-1]
    zero = np.zeros((*e.shape[:-1], 1))
    c1 = np.concatenate([zero, np.cumsum(e, axis=-1)], axis=-1)  # c1[..., n] = sum of first n
    n = np.arange(1, t_len + 1)
    m = c1[..., 1:] / n
    var = (np.concatenate([zero, np.cumsum(e * e, axis=-1)], axis=-1)[..., 1:]) - n * m * m
    for lag in range(1, lags + 1):
        if lag >= t_len:
            break
        prod = np.zeros_like(e)
        prod[..., lag:] = e[..., lag:] * e[..., :-lag]
        q = np.cumsum(prod, axis=-1)
        # sum over t > lag of (e_t - m)(e_{t-lag} - m), expanded around the running mean m
        s_hi = c1[..., 1:] - c1[..., [lag]]
        idx = np.clip(n - lag, 0, t_len)
        s_lo = np.take_along_axis(c1, np.broadcast_to(idx, c1[..., 1:].shape).copy(), axis=-1)
        lag_sum = q - m * (s_hi + s_lo) + np.maximum(n - lag, 0) * m * m
        w = np.where(n > lag, 2.0 * (1.0 - lag / (lags + 1)), 0.0)
        var = var + w * lag_sum
    out = var / n
    out[..., :1] = np.nan
    return np.maximum(out, 0.0)


@lru_cache(maxsize=4096)
def mixture_boundary(t: int, level: float, mix_sd: float) -> float:
    """``s*`` such that the one-sided normal-mixture martingale equals 1 / ``level`` after t days.

    With S the running sum of (e_t - m) in units of sigma, V = t, and a tilt lambda ~ N(0, mix_sd^2)
    restricted to lambda > 0 (the one-sided mixture), the martingale is
        M = 2 / (mix_sd * sqrt(a)) * exp(S^2 / (2a)) * Phi(S / sqrt(a)),   a = t + 1 / mix_sd^2.
    By Ville's inequality P(M_t >= 1 / level for some t) <= level; ``s*`` is the S where M = 1/level
    (M increases in S, so it is unique). The lower bound on the mean is mean - sigma * s* / t.
    """
    from scipy.optimize import brentq
    from scipy.special import log_ndtr

    a = t + 1.0 / mix_sd**2
    target = math.log(1.0 / level)

    def f(s: float) -> float:
        return (
            math.log(2.0 / mix_sd)
            - 0.5 * math.log(a)
            + s * s / (2 * a)
            + float(log_ndtr(s / math.sqrt(a)))
        ) - target

    return float(brentq(f, 0.0, 60.0 * math.sqrt(a)))


def cs_level(rule: dict[str, Any] = RULE) -> float:
    """Per-challenger confidence-sequence level: the family-wise alpha split across the family."""
    return float(rule["alpha"]) / int(rule["family_size"])


def cs_lower_bounds(
    e: np.ndarray, rule: dict[str, Any] = RULE, cid: str | None = None
) -> np.ndarray:
    """Lower confidence-sequence bound on mean(e) after each day (last axis); NaN before the first
    look (``min_forward_days``) and wherever the long-run variance is zero (fail closed: no bound).

    ``e`` is (1 - min_gain) * loss_champion - loss_challenger per forward day. The bound is
    mean_n - sqrt(inflation * NW_n) * s*(n) / n and is valid at every n at once.
    """
    e = np.asarray(e, dtype=float)
    t_len = e.shape[-1]
    n = np.arange(1, t_len + 1)
    mean = np.cumsum(e, axis=-1) / n
    var = running_long_run_var(e, int(rule["hac_lags"]))
    if rule["variance_floor_iid"]:
        mean_sq = np.cumsum(e * e, axis=-1) / n
        with np.errstate(invalid="ignore"):
            var = np.fmax(var, np.maximum(mean_sq - mean * mean, 0.0))
    infl = rule.get("variance_inflation_by_challenger", {}).get(cid, rule["variance_inflation"])
    var = var * float(infl)
    s_star = np.array(
        [mixture_boundary(int(k), cs_level(rule), float(rule["mix_sd"])) for k in n], dtype=float
    )
    with np.errstate(invalid="ignore"):
        lower = mean - np.sqrt(var) * s_star / n
    lower = np.where(var > 0, lower, np.nan)
    lower[..., : int(rule["min_forward_days"]) - 1] = np.nan
    return lower


def first_reachable(
    n_have: int, n_need: int | None, last_day: str, per_day: float = 5 / 7
) -> str | None:
    """Calendar date the window is first long enough, at ``per_day`` decision days per calendar day
    (the observed rate is passed in when known; 5/7 is the IBJA business-day rate)."""
    if n_need is None or per_day <= 0:
        return None
    left = max(0, n_need - n_have)
    return (date.fromisoformat(last_day) + timedelta(days=math.ceil(left / per_day))).isoformat()


# --- one comparison ------------------------------------------------------------------------------
def registration_date(
    cid: str | None, first_day: str | None, rule: dict[str, Any] = RULE
) -> str | None:
    """The day challenger ``cid`` started counting: the frozen registry, or for models registered
    "on the day they get a record" (and any unregistered id) its first forward day."""
    reg = rule["registered"].get(cid) if cid else None
    return reg if reg else first_day


def compare(
    champion: list[dict],
    challenger: list[dict],
    since: str | None = None,
    rule: dict[str, Any] = RULE,
    *,
    cid: str | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Challenger vs champion on the forward days both issued. Keys documented in ADR 072.

    ``cid`` selects the registration date (horizon clock); ``as_of`` is the date the horizon is
    measured at (default: the last day both models issued, so the clock is the data's own, never
    the wall clock).
    """
    since = since or rule["common_start"]
    a, b = forward_days(champion, since), forward_days(challenger, since)
    days = sorted(set(a) & set(b))
    n = len(days)
    out: dict[str, Any] = {"since": since, "n": n, "first_day": days[0] if days else None}
    out["last_day"] = days[-1] if days else None
    first_look = int(rule["min_forward_days"])
    reg = registration_date(cid, out["first_day"], rule)
    clock = as_of or out["last_day"]
    # Horizon clock: decision days (days the CHAMPION issued a live forecast) after the registration
    # day, up to ``clock``. Counted on the champion's record, so a day the challenger missed still
    # uses up its horizon. Registration day = 0.
    since_reg = sum(1 for d in a if reg < d <= clock) if reg and clock else 0
    horizon = int(rule["horizon_days"])
    base: dict[str, Any] = {
        "first_look_day": first_look,
        "looks_started": False,
        "lower_bound": None,
        "gain_estimate": None,
        "registered": reg,
        "days_since_registration": since_reg,
        "horizon_left": max(0, horizon - since_reg),
        "retired": since_reg >= horizon,
    }
    if n < 2:
        return {**out, **base, "status": "too early"}
    la = np.array([_loss(a[d]) for d in days])
    lb = np.array([_loss(b[d]) for d in days])
    gain = 1.0 - float(lb.mean() / la.mean()) if la.mean() > 0 else 0.0
    # e > 0 on average  <=>  challenger mean error more than min_gain below the champion's
    e = (1.0 - rule["min_gain"]) * la - lb
    lower_e = float(cs_lower_bounds(e, rule, cid)[-1])
    looking = n >= first_look and not math.isnan(lower_e)
    if looking and la.mean() > 0:
        # the same bound on the gain scale (champion's mean loss as the unit; display only: the
        # decision is lower_e > 0, which is the same as this exceeding min_gain)
        base["lower_bound"] = round(rule["min_gain"] + lower_e / float(la.mean()), 4)
    base["looks_started"] = bool(looking)
    base["gain_estimate"] = round(gain, 4) if n >= first_look else None
    base["lower_bound_clears"] = bool(looking and lower_e > 0)
    hits_b = range_hits(challenger)
    cov = [hits_b[d] for d in days if d in hits_b]
    cov_k, cov_n = int(sum(cov)), len(cov)
    if cov_n:
        from scipy.stats import binom

        cov_p = float(binom.cdf(cov_k, cov_n, rule["coverage_nominal"]))
        cov_ok = cov_p >= rule["coverage_alpha"]
    else:
        cov_p, cov_ok = None, False

    def _hit(f: dict) -> bool | None:
        return None if f["pm1"] == f["pm0"] else (f["p_up"] > 0.5) == (f["pm1"] > f["pm0"])

    moved = [d for d in days if _hit(a[d]) is not None]
    dir_a = float(np.mean([_hit(a[d]) for d in moved])) if moved else None
    dir_b = float(np.mean([_hit(b[d]) for d in moved])) if moved else None
    dir_ok = bool(moved) and dir_b is not None and dir_a is not None and dir_b >= dir_a
    return {
        **out,
        **base,
        "mae_champion": round(float(la.mean()), 2),
        "mae_challenger": round(float(lb.mean()), 2),
        "gain": round(gain, 4),
        "coverage_n": cov_n,
        "coverage": round(cov_k / cov_n, 3) if cov_n else None,
        "coverage_p_below": cov_p,
        "coverage_ok": bool(cov_ok),
        "direction_champion": dir_a,
        "direction_challenger": dir_b,
        "direction_ok": bool(dir_ok),
        "status": "scored",
    }


def decide(
    champion_id: str,
    records: dict[str, list[dict]],
    rule: dict[str, Any] = RULE,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Compare every challenger in ``records`` with the champion and say who, if anyone, is promoted.

    A challenger is promotable only when ALL hold: it has a live predictor (LIVE_CAPABLE); it is not
    listed in ``rule['promotion_blocked']`` (ADR 072 Amendments 3-4; its bound uses its own
    ``variance_inflation_by_challenger`` entry, if any); it is not
    retired (180 decision days since registration, ``horizon_left`` 0); at least ``min_forward_days``
    forward days; the confidence-sequence lower bound of its error gain clears ``min_gain`` at the
    per-challenger level alpha / family_size; coverage not below target; direction not worse. At
    most one is promoted: the lowest mean error. ``as_of`` (default: the champion's last forward
    day) is the date the horizon clock is read at.
    """
    champ = records[champion_id]
    if as_of is None:
        mine = forward_days(champ, rule["common_start"])
        as_of = max(mine) if mine else None
    rows: dict[str, dict[str, Any]] = {}
    for cid, folds in records.items():
        if cid == champion_id:
            continue
        rows[cid] = compare(champ, folds, rule=rule, cid=cid, as_of=as_of)
    winners = []
    for cid, r in rows.items():
        r["live_capable"] = cid in LIVE_CAPABLE
        held = rule.get("promotion_blocked", {})
        r["blocked_reason"] = held.get(cid) if cid in held else None
        r["promotable"] = bool(
            r["status"] == "scored"
            and r["live_capable"]
            and cid not in held
            and not r["retired"]
            and r["looks_started"]
            and r["lower_bound_clears"]
            and r["coverage_ok"]
            and r["direction_ok"]
        )
        if r["promotable"]:
            winners.append(cid)
    best = min(winners, key=lambda c: rows[c]["mae_challenger"]) if winners else None
    return {
        "champion": champion_id,
        "rule_sha256": rule_sha256(rule),
        "challengers": rows,
        "promote": best,
    }


# --- champion state (the one-file switch) --------------------------------------------------------
def empty_champion() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA,
        "champion": DEFAULT_CHAMPION,
        "since": None,
        "history": [],
        "pinned": False,  # True after a rollback: the rule does nothing until a person unpins
    }


def load_champion(path: Path) -> dict[str, Any]:
    """Missing file: the default champion (P3). Unreadable, unknown id or a malformed ``history``
    (not a list of objects): FAIL CLOSED to P3 and say so (``unreadable`` True), never to an
    arbitrary challenger (rule 98a). A ``pinned`` that is not a bool reads as pinned True."""
    if not path.exists():
        return empty_champion()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ok = isinstance(data, dict) and data.get("champion") in LIVE_CAPABLE
        if ok:
            hist = data.get("history", [])
            ok = isinstance(hist, list) and all(isinstance(e, dict) for e in hist)
    except (OSError, ValueError):
        data, ok = None, False
    if not ok:
        return {**empty_champion(), "unreadable": True}
    state = {**empty_champion(), **data}
    if not isinstance(state["pinned"], bool):
        state["pinned"] = True  # fail closed: a garbled pin must not let the rule run
    return state


def save_champion(path: Path, state: dict[str, Any]) -> None:
    # atomic (temp file + os.replace): a torn write would read as an unreadable champion file
    from ml.demotion import atomic_write_text

    atomic_write_text(path, json.dumps(state, indent=1) + "\n")


def promote(
    state: dict[str, Any], to: str, evidence: dict[str, Any], now: datetime | None = None
) -> dict[str, Any]:
    """New state with ``to`` as champion and the event (with its evidence) appended to history."""
    if to not in LIVE_CAPABLE:
        raise ValueError(f"{to!r} has no live predictor; add one in a PR first (ADR 072)")
    at = (now or datetime.now(UTC)).isoformat()
    event = {
        "at": at,
        "event": "promoted",
        "from": state["champion"],
        "to": to,
        "evidence": evidence,
    }
    return {**state, "champion": to, "since": at, "history": [*state["history"], event]}


def rollback(state: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """The one-command undo: the previous champion is live again, logged as a rollback, and the
    state is PINNED so the rule cannot re-promote the same challenger from the same evidence on the
    next run. Raises ValueError (nothing written by the caller) unless the latest champion change
    is a promotion that is still in force."""
    changes = [e for e in state["history"] if e.get("event") in ("promoted", "rolled_back")]
    if not changes or changes[-1].get("event") != "promoted":
        raise ValueError("nothing to roll back: no promotion after the last rollback")
    last = changes[-1]
    if state["champion"] != last.get("to") or last.get("from") not in LIVE_CAPABLE:
        raise ValueError("nothing to roll back: the champion is not the last promoted model")
    at = (now or datetime.now(UTC)).isoformat()
    prev = last["from"]
    event = {
        "at": at,
        "event": "rolled_back",
        "from": state["champion"],
        "to": prev,
        "pinned": True,
    }
    return {
        **state,
        "champion": prev,
        "since": at,
        "pinned": True,
        "history": [*state["history"], event],
    }


def unpin(state: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """A person's explicit act: let the rule run again. Logs an ``unpinned`` event."""
    if state.get("pinned") is not True:
        raise ValueError("not pinned")
    at = (now or datetime.now(UTC)).isoformat()
    event = {"at": at, "event": "unpinned", "champion": state["champion"]}
    return {**state, "pinned": False, "history": [*state["history"], event]}


def main(argv: list[str] | None = None) -> int:
    """``python -m ml.promotion rollback|unpin|show`` (ADR 072). rollback and unpin are a person's
    explicit acts; a refused one (ValueError, unreadable file) writes nothing and exits 1."""
    import argparse

    ap = argparse.ArgumentParser(prog="python -m ml.promotion")
    ap.add_argument("action", choices=["rollback", "unpin", "show"])
    ap.add_argument(
        "--data-dir", type=Path, default=Path(__file__).resolve().parent.parent / "data"
    )
    args = ap.parse_args(argv)
    path = args.data_dir / CHAMPION_FILE
    state = load_champion(path)
    if args.action == "show":
        print(json.dumps(state, indent=1))
        print(f"pinned: {state['pinned']}")
        return 0
    if state.get("unreadable"):
        print(f"refused: {path} is unreadable; repair it first")
        return 1
    try:
        new = rollback(state) if args.action == "rollback" else unpin(state)
    except ValueError as exc:
        print(f"refused: {exc}")
        return 1
    save_champion(path, new)
    loaded = load_champion(path)
    print(f"champion is now {loaded['champion']} (pinned: {loaded['pinned']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
