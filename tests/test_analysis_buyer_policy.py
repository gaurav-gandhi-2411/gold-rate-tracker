"""Tests for scripts/analysis_buyer_policy.py (R3, ADR 039). Toy series only."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "analysis_buyer_policy.py"
_spec = importlib.util.spec_from_file_location("analysis_buyer_policy", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["analysis_buyer_policy"] = mod
_spec.loader.exec_module(mod)


def _series(vals: list[float]) -> pd.Series:
    return pd.Series(vals, index=pd.date_range("2020-01-01", periods=len(vals), freq="B"))


def test_p1_buys_at_first_price_below_limit_else_deadline() -> None:
    p = _series([100.0, 101.0, 98.0, 97.0, 105.0, 106.0])
    f = pd.DataFrame({"sigma": [0.01] * 6, "z": [0.0] * 6}, index=p.index)
    sv = mod.simulate(p, f, 3, "P1", 1.0)  # limit = 99 for t=0
    assert sv[0] == pytest.approx(100.0 - 98.0)  # first day <= 99 is 98
    assert sv[2] == pytest.approx(98.0 - 97.0)  # limit 97.02; 97 on the next day triggers
    assert np.isnan(sv[3])  # t+3 out of range
    q = _series([100.0, 105.0, 106.0])
    g = pd.DataFrame({"sigma": [0.01] * 3, "z": [0.0] * 3}, index=q.index)
    assert mod.simulate(q, g, 2, "P1", 1.0)[0] == pytest.approx(100.0 - 106.0)  # deadline


def test_p2_buys_today_unless_stretched() -> None:
    p = _series([100.0, 99.0, 98.0, 97.0, 96.0])
    z = [2.0, 1.0, -0.5, 0.3, 0.0]
    f = pd.DataFrame({"sigma": [0.01] * 5, "z": z}, index=p.index)
    sv = mod.simulate(p, f, 2, "P2", 1.5)
    assert sv[0] == pytest.approx(100.0 - 98.0)  # stretched; z<=0 on day 2
    assert sv[1] == 0.0  # not stretched -> buy today


def test_p3_waits_only_above_threshold() -> None:
    p = _series([100.0, 99.0, 100.0, 101.0])
    f = pd.DataFrame({"sigma": [0.01] * 4, "z": [0.0] * 4}, index=p.index)
    probs = np.array([0.9, 0.1, np.nan, np.nan])
    sv = mod.simulate(p, f, 2, "P3", 0.6, probs)
    assert sv[0] == pytest.approx(1.0)  # waits, 99 <= 99.5 limit
    assert sv[1] == 0.0


def test_dip_label_uses_only_the_window() -> None:
    p = _series([100.0, 100.0, 99.0, 100.0, 90.0])
    y = mod.dip_label(p, 2)
    assert y[0] == 1.0  # 99 <= 99.5 within t+1..t+2
    assert y[1] == 1.0
    assert y[2] == 1.0  # 90 on t+2
    assert np.isnan(y[3])


def test_features_use_only_past_prices() -> None:
    rng = np.random.default_rng(42)
    p = _series(list(100 + np.cumsum(rng.normal(0, 1, 60))))
    f1 = mod.features(p)
    p2 = p.copy()
    p2.iloc[40:] = p2.iloc[40:] * 1.5  # change the future only
    f2 = mod.features(p2)
    pd.testing.assert_frame_equal(f1.iloc[:40], f2.iloc[:40])


def test_hac_test_positive_mean_gives_small_p() -> None:
    s = np.array([1.0, 2.0, 1.5, 0.5] * 50)
    r = mod.hac_test(s, 5)
    assert r["mean_saving_rs_per_g"] > 0
    assert r["p_one_sided"] < 0.01


def test_p3_training_rows_have_matured_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    rng = np.random.default_rng(1)
    p = _series(list(100 + np.cumsum(rng.normal(0, 1, 400))))
    f = mod.features(p)
    seen: list[int] = []
    import sklearn.pipeline as skp

    real_fit = skp.Pipeline.fit

    def spy(self, X, y):  # type: ignore[no-untyped-def]
        seen.append(len(y))
        return real_fit(self, X, y)

    monkeypatch.setattr(skp.Pipeline, "fit", spy)
    dates = p.index.to_numpy()
    mod.p3_probabilities(p, f, f, dates, 5)
    # First fit is at target index i where last=i; usable rows j <= i-5 minus warm-up NaNs.
    assert seen and all(n <= 400 - 5 for n in seen)


def test_ibja_gaps_split_segments_and_windows_never_span_them() -> None:
    idx = pd.to_datetime(
        [
            *["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"],
            *["2024-02-01", "2024-02-02", "2024-02-05", "2024-02-06"],
        ]
    )
    p = pd.Series([100.0, 90.0, 80.0, 85.0, 200.0, 210.0, 220.0, 230.0], index=idx)
    segs = mod.dense_segments(p)
    assert [len(s) for s in segs] == [4, 4]
    sv = mod.simulate_segmented(p, 2, "P1", 0.0)  # k=0: limit = today's price
    assert np.isnan(sv[0]) and np.isnan(sv[4])  # volatility warm-up: no decision
    assert sv[1] == pytest.approx(90.0 - 80.0)
    assert sv[5] == pytest.approx(210.0 - 230.0)  # deadline inside its own segment
    assert np.isnan(sv[2]) and np.isnan(sv[3])  # t+2 would cross the gap
