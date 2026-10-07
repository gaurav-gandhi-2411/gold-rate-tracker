"""Champion / challenger promotion rule (ADR 072), applied by code on FORWARD days only.

The rule's numbers live in ``RULE`` and are frozen: ``RULE_SHA256`` is the hash of their canonical
JSON, recorded in docs/adr/072-champion-challenger-promotion-rule.md, and tests/test_promotion.py
fails if either moves without the other. Changing the rule is GG's decision (a new ADR), never a
tuning step.

What this module does (pure functions, no network):
  * ``compare``: one challenger vs the champion on the days both issued a forecast live;
  * ``required_n``: forward days needed for 80% power at a 5% error difference, from the observed
    long-run variance of the day-to-day error difference;
  * ``decide``: promote at most one challenger, Benjamini-Hochberg across all eligible ones;
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
from pathlib import Path
from typing import Any

import numpy as np

# --- the frozen rule (ADR 072) -------------------------------------------------------------------
RULE: dict[str, Any] = {
    "min_gain": 0.05,  # challenger mean |error| at least 5% below the champion's
    "alpha": 0.05,  # one-sided HAC Diebold-Mariano, then Benjamini-Hochberg across challengers
    "hac_lags": 4,  # Newey-West lags (same as ml.demotion and ml.nextfix)
    "power": 0.80,  # power used to size the forward window
    "effect": 0.05,  # the difference the window must be able to detect (fraction of champion MAE)
    "min_days": 40,  # never fewer forward days than ADR 071's frozen floor
    "coverage_nominal": 0.80,  # challenger's own 80% range; promotion needs it not below target
    "coverage_alpha": 0.05,  # exact one-sided binomial: coverage significantly below 0.80 blocks
    "direction_rule": "challenger direction hit-rate >= champion's on the same days",
    "common_start": "2026-10-07",  # first decision day P3 was live: no earlier day is compared
}
RULE_SHA256 = "0782301d8890788287be983c63d3d4e9bbd9eb0d8cd5d50d213960207dc44902"

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


def _dm_p_better(d: np.ndarray, lags: int) -> float:
    """One-sided p that mean(d) < 0, i.e. the challenger (first argument of d) has lower loss."""
    from scipy.stats import norm

    n = len(d)
    if n < 2:
        return 1.0
    var = _long_run_var(d, lags)
    if var <= 0:
        return 0.0 if d.mean() < 0 else 1.0
    return float(norm.cdf(d.mean() / math.sqrt(var / n)))


def required_n(d: np.ndarray, champion_mae: float, rule: dict[str, Any] = RULE) -> int | None:
    """Forward days for ``power`` at an ``effect`` x champion-MAE difference, one-sided ``alpha``.

    n = ((z_alpha + z_power) * sigma_LR / delta)^2, sigma_LR the long-run sd of the daily loss
    difference d (Newey-West), delta = effect x champion MAE. None when d cannot be estimated.
    """
    from scipy.stats import norm

    if len(d) < 10 or champion_mae <= 0:
        return None
    var = _long_run_var(d, rule["hac_lags"])
    if var <= 0:
        return None
    delta = rule["effect"] * champion_mae
    z = norm.ppf(1 - rule["alpha"]) + norm.ppf(rule["power"])
    return max(rule["min_days"], math.ceil((z * math.sqrt(var) / delta) ** 2))


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
def compare(
    champion: list[dict],
    challenger: list[dict],
    since: str | None = None,
    rule: dict[str, Any] = RULE,
) -> dict[str, Any]:
    """Challenger vs champion on the forward days both issued. Keys documented in ADR 072."""
    since = since or rule["common_start"]
    a, b = forward_days(champion, since), forward_days(challenger, since)
    days = sorted(set(a) & set(b))
    n = len(days)
    out: dict[str, Any] = {"since": since, "n": n, "first_day": days[0] if days else None}
    out["last_day"] = days[-1] if days else None
    if n < 2:
        return {**out, "status": "too early", "p_better": None}
    la = np.array([_loss(a[d]) for d in days])
    lb = np.array([_loss(b[d]) for d in days])
    diff = lb - la  # negative: the challenger is better
    gain = 1.0 - float(lb.mean() / la.mean()) if la.mean() > 0 else 0.0
    p = _dm_p_better(diff, rule["hac_lags"])
    n_need = required_n(diff, float(la.mean()), rule)
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
        "mae_champion": round(float(la.mean()), 2),
        "mae_challenger": round(float(lb.mean()), 2),
        "gain": round(gain, 4),
        "p_better": p,
        "n_required": n_need,
        "coverage_n": cov_n,
        "coverage": round(cov_k / cov_n, 3) if cov_n else None,
        "coverage_p_below": cov_p,
        "coverage_ok": bool(cov_ok),
        "direction_champion": dir_a,
        "direction_challenger": dir_b,
        "direction_ok": bool(dir_ok),
        "status": "scored",
    }


def bh_reject(pvals: list[float], alpha: float) -> list[bool]:
    """Benjamini-Hochberg: which hypotheses are rejected at FDR ``alpha``."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    cutoff = -1
    for rank, i in enumerate(order, start=1):
        if pvals[i] <= alpha * rank / m:
            cutoff = rank
    rej = [False] * m
    for rank, i in enumerate(order, start=1):
        rej[i] = rank <= cutoff
    return rej


def decide(
    champion_id: str,
    records: dict[str, list[dict]],
    rule: dict[str, Any] = RULE,
) -> dict[str, Any]:
    """Compare every challenger in ``records`` with the champion and say who, if anyone, is promoted.

    A challenger is promotable only when ALL hold: forward n >= max(min_days, n_required); mean
    error at least ``min_gain`` below the champion's; BH-adjusted one-sided HAC DM significant
    across every challenger that has enough days; coverage not below target; direction not worse;
    and it has a live predictor (LIVE_CAPABLE). At most one is promoted: the lowest error.
    """
    champ = records[champion_id]
    rows: dict[str, dict[str, Any]] = {}
    for cid, folds in records.items():
        if cid == champion_id:
            continue
        rows[cid] = compare(champ, folds, rule=rule)
    ripe = [
        cid
        for cid, r in rows.items()
        if r["status"] == "scored" and r["n_required"] is not None and r["n"] >= r["n_required"]
    ]
    rejected = dict(
        zip(ripe, bh_reject([rows[c]["p_better"] for c in ripe], rule["alpha"]), strict=True)
    )
    winners = []
    for cid, r in rows.items():
        r["bh_significant"] = bool(rejected.get(cid, False))
        r["live_capable"] = cid in LIVE_CAPABLE
        r["promotable"] = bool(
            cid in ripe
            and r["bh_significant"]
            and r["gain"] >= rule["min_gain"]
            and r["coverage_ok"]
            and r["direction_ok"]
            and r["live_capable"]
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
    return {"schema_version": SCHEMA, "champion": DEFAULT_CHAMPION, "since": None, "history": []}


def load_champion(path: Path) -> dict[str, Any]:
    """Missing file: the default champion (P3). Unreadable or unknown id: FAIL CLOSED to P3 and say
    so (``unreadable`` True), never to an arbitrary challenger (rule 98a)."""
    if not path.exists():
        return empty_champion()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ok = isinstance(data, dict) and data.get("champion") in LIVE_CAPABLE
    except (OSError, ValueError):
        data, ok = None, False
    if not ok:
        return {**empty_champion(), "unreadable": True}
    return {**empty_champion(), **data}


def save_champion(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, indent=1) + "\n", encoding="utf-8")


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
    """The one-command undo: the previous champion is live again, logged as a rollback."""
    promos = [e for e in state["history"] if e["event"] == "promoted"]
    if not promos:
        raise ValueError("nothing to roll back")
    at = (now or datetime.now(UTC)).isoformat()
    prev = promos[-1]["from"]
    event = {"at": at, "event": "rolled_back", "from": state["champion"], "to": prev}
    return {**state, "champion": prev, "since": at, "history": [*state["history"], event]}


def main(argv: list[str] | None = None) -> int:
    """``python -m ml.promotion rollback`` : the one-command undo (ADR 072)."""
    import argparse

    ap = argparse.ArgumentParser(prog="python -m ml.promotion")
    ap.add_argument("action", choices=["rollback", "show"])
    ap.add_argument(
        "--data-dir", type=Path, default=Path(__file__).resolve().parent.parent / "data"
    )
    args = ap.parse_args(argv)
    path = args.data_dir / CHAMPION_FILE
    state = load_champion(path)
    if args.action == "show":
        print(json.dumps(state, indent=1))
        return 0
    save_champion(path, rollback(state))
    print(f"champion is now {load_champion(path)['champion']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
