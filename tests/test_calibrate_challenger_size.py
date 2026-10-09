"""scripts/calibrate_challenger_size.py: the calibration picks the smallest passing variance inflation and
the synthetic AR(1) series has the stated mean and autocorrelation."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import calibrate_challenger_size as cal


def _series(seed: int = 3, n: int = 145) -> dict:
    rng = np.random.default_rng(seed)
    c = np.abs(rng.normal(100, 40, n))
    d = rng.normal(0, 10, (n, 3))
    return {"C": c, "D": d, "H": np.abs(rng.normal(110, 40, n)), "days": [str(i) for i in range(n)]}


def test_ar1_has_the_requested_autocorrelation_and_boundary_mean(monkeypatch) -> None:
    monkeypatch.setattr(cal, "PATHS", 400)
    s = _series()
    e = cal.e_ar1(s, 0, 0.4, cal.G_BOUNDARY, np.random.default_rng(1))
    assert e.shape == (400, cal.HORIZON)
    assert abs(float(e.mean())) < 0.3  # mean(e) = 0 at the 5% bar
    x = e - e.mean(axis=1, keepdims=True)
    lag1 = float((x[:, 1:] * x[:, :-1]).sum() / (x * x).sum())
    assert 0.3 < lag1 < 0.5


def test_larger_inflation_never_raises_the_promotion_rate(monkeypatch) -> None:
    monkeypatch.setattr(cal, "PATHS", 300)
    s = _series()
    e = cal.e_ar1(s, 0, 0.5, cal.G_BOUNDARY, np.random.default_rng(2))
    rates = [cal.rate(e, k) for k in (1.5, 2.5, 4.0)]
    assert rates[0] >= rates[1] >= rates[2]


def test_calibrate_returns_the_smallest_passing_k_or_none(monkeypatch) -> None:
    monkeypatch.setattr(cal, "PATHS", 300)
    monkeypatch.setattr(cal, "CAL_BLOCKS", (10,))
    s = _series()
    out = cal.calibrate(s, 0)
    ks = list(out["boundary_rate_by_k"])
    if out["k"] is not None:
        assert f"{out['k']:.2f}" == ks[-1]  # the search stops at the first passing k
        assert max(out["boundary_rate_by_k"][ks[-1]].values()) <= cal.ALLOWANCE
        assert all(
            max(v.values()) > cal.ALLOWANCE for v in list(out["boundary_rate_by_k"].values())[:-1]
        )
