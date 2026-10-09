"""scripts/simulate_sequential_promotion.py: the helpers behind the ADR 072 Amendment 1 numbers."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from ml import demotion as dm
from ml import promotion as pr

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "sim_seq", ROOT / "scripts" / "simulate_sequential_promotion.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sim = _load_module()


def test_bootstrap_indices_are_deterministic_and_in_range() -> None:
    a = sim.stationary_indices(np.random.default_rng(42), 50, 30, 145, 6)
    b = sim.stationary_indices(np.random.default_rng(42), 50, 30, 145, 6)
    assert (a == b).all() and a.min() >= 0 and a.max() < 145
    # blocks continue: most steps are +1 (mod 145), about 1 - 1/6 of them
    step = (a[:, 1:] - a[:, :-1]) % 145 == 1
    assert 0.7 < float(step.mean()) < 0.95


def test_horizon_is_180_decision_days_under_rule_v3() -> None:
    n, offs = sim.horizon_days(pr.RULE)
    assert n == len(offs) == pr.RULE["horizon_days"] == 180
    assert offs.max() > 180  # 180 weekday decision days span about 250 calendar days
    start = date.fromisoformat(pr.RULE["common_start"])
    assert all((start + timedelta(days=int(k))).weekday() < 5 for k in offs)


def test_a_v2_rule_still_counts_calendar_days() -> None:
    rule = {k: v for k, v in pr.RULE.items() if k != "horizon_unit"}
    n, offs = sim.horizon_days(rule)
    assert n == len(offs) and 125 <= n <= 130 and offs.max() < rule["horizon_days"]


def test_rolling_error_rule_matches_ml_demotion_estimator() -> None:
    """The vectorised copy of the live error rule fires exactly where ml.demotion's own HAC test
    does (window 40, p < 0.05, model worse, 3 checks in a row)."""
    rng = np.random.default_rng(42)
    path = rng.normal(0.3, 1.0, 90)  # model worse than holding on average
    mine = int(sim._rolling_error_rule(path[None, :])[0])
    breach = []
    for t in range(1, 91):
        w = path[max(0, t - 40) : t]
        ok = len(w) >= 30 and w.mean() > 0 and dm.hac_one_sided_p_worse(w, 4) < 0.05
        breach.append(ok)
    ref = next((t for t in range(2, 90) if all(breach[t - 2 : t + 1])), -1)
    assert mine == ref


def test_wilson_interval_contains_the_estimate() -> None:
    lo, hi = sim.wilson(50, 1000)
    assert lo < 0.05 < hi and lo >= 0.0 and hi <= 1.0
    assert sim.wilson(0, 1000)[0] == 0.0


def test_new_rule_first_finds_the_first_look_that_clears() -> None:
    rule = pr.RULE
    e = np.full((2, 60), 10.0) + np.random.default_rng(1).normal(0, 1.0, (2, 60))
    e[1] = -5.0 + np.random.default_rng(2).normal(0, 1.0, 60)
    first = sim.new_rule_first(e, rule)
    assert first[0] >= rule["min_forward_days"] - 1 and first[1] == -1
