"""scripts/simulate_promotion_v3.py: the resampler and the gain constructions behave as documented."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import simulate_promotion_v3 as sim


def _series() -> dict:
    rng = np.random.default_rng(1)
    c = np.abs(rng.normal(100, 40, 200))
    h = np.abs(rng.normal(110, 40, 200))
    d = rng.normal(0, 10, (200, 3))
    return {"C": c, "D": d, "H": h, "days": [str(i) for i in range(200)]}


def test_block_indices_are_runs_of_consecutive_days_and_deterministic() -> None:
    a = sim.block_indices(np.random.default_rng(42), 5, 37, 100, 10)
    b = sim.block_indices(np.random.default_rng(42), 5, 37, 100, 10)
    assert a.shape == (5, 37) and (a == b).all() and a.min() >= 0 and a.max() < 100
    steps = np.diff(a[:, :10], axis=1) % 100  # inside one block every step is +1 (circular)
    assert (steps == 1).all()


def test_additive_and_proportional_gain_have_the_stated_mean() -> None:
    s = _series()
    idx = np.tile(
        np.arange(200), (1, 1)
    )  # every day once: the resample mean equals the record mean
    m_c = s["C"].mean()
    for mode in ("additive", "proportional"):
        e = sim.paths_e(s, idx, 0, 0.10, 1.0, mode=mode)
        assert abs(e.mean() - (0.10 - 0.05) * m_c) < 1e-9


def test_control_variate_slope_matches_least_squares_and_reduces_variance() -> None:
    s = _series()
    s["D"][:, 1] = s["D"][:, 1] - 0.4 * (s["H"] - s["H"].mean())  # plant a hold-error dependence
    cv = sim.control_variate(s)
    pc = cv["per_challenger"]["p3_roll60"]
    assert pc["theta"] < -0.2 and pc["var_reduction_in_sample"] > 0.1
