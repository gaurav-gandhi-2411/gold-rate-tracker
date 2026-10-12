"""ADR 075: the live-only monitor's pure functions and its frozen rule. Synthetic records only (the
real record is encrypted); the operating characteristics come from scripts/simulate_live_monitor.py,
not from these tests. The wiring into ml.nextfix and the T18 alert are tested in
tests/test_live_monitor_wired.py."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from ml import live_monitor as lm

START = datetime(2026, 10, 7)


def _fold(day: int, hold_err: float, model_err: float, sign: int, retro: bool | None) -> dict:
    """A fold whose hold error is ``hold_err`` and whose model error is ``model_err`` (Rs./g)."""
    pm0 = 13000.0
    pm1 = pm0 + sign * hold_err
    pred = pm1 + sign * model_err  # |pred - pm1| = model_err
    f = {
        "d0": (START + timedelta(days=day)).strftime("%Y-%m-%d"),
        "pm0": pm0,
        "pm1": pm1,
        "ret": math.log(pred / pm0),
        "p_up": 0.6 if pred > pm0 else 0.4,
        "vol": 0.006,
    }
    if retro is not None:
        f["retro"] = retro
    return f


def _record(n: int, ratio: float, seed: int, retro: bool | None = False, sd: float = 0.35) -> list:
    """n days; the model's error is ``ratio`` x the hold error on average (lognormal spread)."""
    rng = np.random.default_rng(seed)
    hold = np.abs(rng.normal(100.0, 70.0, n)) + 5.0
    fac = np.exp(rng.normal(math.log(ratio) - sd**2 / 2, sd, n))
    sign = rng.choice([-1, 1], n)
    return [
        _fold(i, float(h), float(h * f), int(s), retro)
        for i, (h, f, s) in enumerate(zip(hold, fac, sign, strict=True))
    ]


def test_rule_is_frozen_with_its_hash():
    assert lm.rule_sha256() == lm.LIVE_RULE_SHA256
    changed = {**lm.LIVE_RULE, "variance_inflation": lm.LIVE_RULE["variance_inflation"] + 0.25}
    assert lm.rule_sha256(changed) != lm.LIVE_RULE_SHA256


def test_the_adr_records_the_hash():
    adr = (Path(__file__).resolve().parent.parent / "docs/adr/075-live-only-monitor.md").read_text(
        encoding="utf-8"
    )
    assert lm.LIVE_RULE_SHA256 in adr


def test_the_committed_simulation_is_for_this_rule():
    rep = json.loads(
        (Path(__file__).resolve().parent.parent / "reports/live_monitor_simulation.json").read_text(
            encoding="utf-8"
        )
    )
    assert rep["rule_sha256"] == lm.LIVE_RULE_SHA256
    assert rep["frozen_k_matches_calibration"] is True and rep["coverage_frozen_and_proven"] is True
    assert rep["fresh_confirmation_at_frozen_k"]["all_pass"] is True


def test_frozen_numbers_are_the_simulated_ones():
    r = lm.LIVE_RULE
    assert (r["first_look_days"], r["alpha"], r["family_size"]) == (10, 0.05, 1)
    assert r["error_action"] == "demote" and r["coverage_action"] == "alert"
    # set by scripts/simulate_live_monitor.py (reports/live_monitor_simulation.md), never by hand
    assert (r["variance_inflation"], r["coverage_threshold"]) == (2.25, 150.0)


def test_only_days_issued_live_count():
    folds = [_fold(0, 100, 50, 1, True), _fold(1, 100, 50, 1, None), _fold(2, 100, 50, 1, False)]
    assert [f["d0"] for f in lm.live_days(folds)] == [folds[2]["d0"]]  # retro and unflagged: out


def test_a_record_of_rerun_days_alone_is_never_judged():
    awful = _record(80, ratio=3.0, seed=1, retro=True)
    out = lm.live_status(awful)
    assert (
        out["error"]["n_live"] == 0 and out["error"]["breach"] is False and out["demote"] is False
    )


def test_too_few_live_days_is_not_a_verdict():
    out = lm.error_status(_record(9, ratio=5.0, seed=2))
    assert out["breach"] is False and out["reason"] == "too few live days" and out["n_live"] == 9


@pytest.mark.parametrize("seed", range(6))
def test_a_degraded_live_model_is_flagged_within_a_quarter(seed):
    folds = _record(60, ratio=1.40, seed=seed)  # 40% worse than holding
    fired = [n for n in range(10, 61) if lm.error_status(folds[:n])["breach"]]
    assert fired, "a model 40% worse than hold was never flagged in 60 live days"
    assert fired[0] <= 60


@pytest.mark.parametrize("seed", range(10))
def test_a_healthy_live_model_is_never_flagged(seed):
    folds = _record(150, ratio=0.93, seed=seed)  # P3's backtest effect: about 7% better than hold
    assert not any(lm.error_status(folds[:n])["breach"] for n in range(10, 151, 5))


def test_a_model_exactly_as_good_as_hold_is_rarely_flagged():
    flagged = sum(
        any(lm.error_status(_record(120, 1.0, s)[:n])["breach"] for n in range(10, 121, 10))
        for s in range(40)
    )
    assert flagged <= 4  # the boundary rate is simulated at <= 5%; this is a coarse guard


def test_the_bound_is_in_the_status_for_a_reader():
    out = lm.error_status(_record(30, ratio=1.4, seed=0))
    assert {"n_live", "mean_diff", "mae_model", "mae_hold", "lower_bound", "breach"} <= set(out)


def test_evalue_matches_a_direct_mixture_sum():
    hits = [True] * 18 + [False] * 12
    grid = [0.50 + 0.02 * i for i in range(15)]
    direct = np.mean([(p / 0.8) ** 18 * ((1 - p) / 0.2) ** 12 for p in grid])
    assert lm.coverage_evalue(hits) == pytest.approx(direct, rel=1e-9)


def test_a_range_that_holds_its_target_raises_no_alert():
    alerts = sum(
        lm.coverage_status([bool(x) for x in np.random.default_rng(s).random(180) < 0.8])["breach"]
        for s in range(300)
    )
    assert alerts <= 15  # simulated false-alert rate over 180 days is <= 5%


def test_a_range_far_below_target_raises_the_alert_but_never_demotes():
    folds = _record(80, ratio=0.9, seed=3)
    hits = [True] * 20 + [False] * 20  # 50% coverage on 40 live days
    out = lm.coverage_status(hits)
    assert out["breach"] is True and out["coverage"] == 0.5
    assert lm.coverage_status([True] * 9)["breach"] is False  # no look before 10 days
    # the combined status keeps the two apart: coverage never sets ``demote``
    st = lm.live_status(folds)
    assert st["demote"] is False
