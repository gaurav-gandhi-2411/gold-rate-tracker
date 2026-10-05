"""Synthetic-data tests for scripts/analysis_nextfix_parity.py (ADR 067). No real data is read."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import analysis_nextfix_parity as ap


def _folds(n: int = 80, rule_noise: float = 0.0, ens_noise: float = 0.002) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    y = rng.normal(0, 0.01, n)
    pm0 = np.full(n, 13500.0)
    pm1 = pm0 * np.exp(y)
    ens = y + rng.normal(0, ens_noise, n)
    rule = y + rng.normal(0, rule_noise or 0.01, n)
    return pd.DataFrame(
        {
            "pm0": pm0,
            "pm1": pm1,
            "ret": ens,
            "p_up": 1 / (1 + np.exp(-ens * 300)),
            "P1": rule,
            "P2": rule,
            "P3": rule,
        }
    )


def test_effective_n_never_exceeds_n_and_shrinks_with_autocorrelation() -> None:
    rng = np.random.default_rng(0)
    iid = rng.normal(size=200)
    ar = (
        np.cumsum(rng.normal(size=200)) * 0.0
        + np.convolve(rng.normal(size=230), np.ones(30) / 30)[:200]
    )
    assert ap.effective_n(iid) <= 200
    assert ap.effective_n(ar) < ap.effective_n(iid)


def test_bh_adjust_is_monotone_and_bounded() -> None:
    adj = ap.bh_adjust([0.01, 0.04, 0.03])
    assert all(0 <= a <= 1 for a in adj)
    assert adj[0] <= adj[2] <= adj[1] + 1e-12


def test_mcnemar_symmetric_and_empty() -> None:
    assert ap.mcnemar_exact_p(0, 0) == 1.0
    assert ap.mcnemar_exact_p(10, 2) == ap.mcnemar_exact_p(2, 10)


def test_much_better_ensemble_clearly_beats_rule() -> None:
    res = ap.summarise(_folds(120, rule_noise=0.01, ens_noise=0.001))
    assert res["results"][0]["clearly_beats"] is True
    assert res["decision"].startswith("keep the ensemble")


def test_equal_models_do_not_clearly_beat_and_simplest_rule_is_recommended() -> None:
    f = _folds(120, ens_noise=0.01, rule_noise=0.01)
    f["ret"] = f["P1"]  # identical forecasts: no difference at all
    res = ap.summarise(f)
    assert not any(r["clearly_beats"] for r in res["results"])
    assert res["decision"].endswith("P1")


def test_rule_forecasts_p3_uses_only_resolved_pairs() -> None:
    d0 = pd.date_range("2026-01-01", periods=100, freq="D")
    rng = np.random.default_rng(1)
    x = rng.normal(0, 0.01, 100)
    y = 0.5 * x + rng.normal(0, 0.001, 100)
    pairs = pd.DataFrame(
        {"d0": d0, "d1": d0 + pd.Timedelta(days=1), "x_glob": x, "bdev": x, "y": y}
    )
    out = ap.rule_forecasts(pairs)
    assert out["P3_b"].iloc[:60].isna().all()
    assert abs(out["P3_b"].iloc[-1] - 0.5) < 0.1
    # row 80 may only use pairs with d1 <= d0[80], i.e. rows 0..79
    tr = pairs.iloc[:80]
    b80 = float(tr["x_glob"] @ tr["y"] / (tr["x_glob"] @ tr["x_glob"]))
    assert abs(out["P3_b"].iloc[80] - b80) < 1e-12
