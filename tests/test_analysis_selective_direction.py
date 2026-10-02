"""Tests for scripts/analysis_selective_direction.py (R4, ADR 039). Synthetic data only."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "analysis_selective_direction.py"
_spec = importlib.util.spec_from_file_location("analysis_selective_direction", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["analysis_selective_direction"] = mod
_spec.loader.exec_module(mod)


def _walk_series(n: int = 1200, seed: int = 42) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(
        100 * np.exp(np.cumsum(rng.normal(0, 0.01, n))),
        index=pd.date_range("2015-01-01", periods=n, freq="B"),
    )


def test_up_label_horizon() -> None:
    p = pd.Series([1.0, 2.0, 1.5, 3.0], index=pd.date_range("2020-01-01", periods=4))
    y = mod.up_label(p, 2)
    assert list(y[:2]) == [1.0, 1.0] and np.isnan(y[2]) and np.isnan(y[3])


def test_fit_at_respects_embargo(monkeypatch: pytest.MonkeyPatch) -> None:
    p = _walk_series()
    X = mod.features(p)[mod.FEATURES].to_numpy(dtype=float)
    y = mod.up_label(p, 5)
    fitted_max: list[int] = []
    real = mod._model

    class Spy:
        def __init__(self) -> None:
            self.m = real()

        def fit(self, Xf, yf):  # type: ignore[no-untyped-def]
            # Recover row indices by matching feature rows (unique floats).
            idx = [int(np.flatnonzero((row == X).all(axis=1))[0]) for row in Xf[-3:]]
            fitted_max.append(max(idx))
            self.m.fit(Xf, yf)
            return self

        def predict_proba(self, Xp):  # type: ignore[no-untyped-def]
            return self.m.predict_proba(Xp)

    monkeypatch.setattr(mod, "_model", Spy)
    m = 1000
    state = mod.fit_at(X, y, m, 5)
    assert state is not None
    inner_start = (m - 1 - 5) - mod.INNER + 1
    assert fitted_max[0] + 5 < inner_start  # inner model: labels matured before inner window
    assert fitted_max[1] + 5 <= m - 1  # full model: labels matured by the decision row


def test_thresholds_monotone_and_full_coverage() -> None:
    p = _walk_series()
    X = mod.features(p)[mod.FEATURES].to_numpy(dtype=float)
    y = mod.up_label(p, 1)
    state = mod.fit_at(X, y, 1100, 1)
    assert state is not None
    _, taus, majority = state
    assert taus[0.10] >= taus[0.25] >= taus[0.50]
    assert taus[1.00] < 0  # kappa=100%: every day selected
    assert majority in (0, 1)


def test_evaluate_compares_on_the_same_selected_days() -> None:
    y = np.array([1, 1, 0, 0, 1, 0, 1, 1] * 10, dtype=float)
    probs = np.array([0.9, 0.9, 0.1, 0.1, 0.5, 0.5, 0.5, 0.5] * 10)
    maj = np.ones(len(y))
    tau = {k: np.full(len(y), 0.3 if k < 1 else -1.0) for k in mod.KAPPAS}
    out = mod.evaluate(y, probs, maj, tau, np.ones(len(y), dtype=bool), 1)
    cell = out["kappa=0.1"]
    assert cell["n_selected"] == 40  # only the confident days
    assert cell["precision"] == pytest.approx(1.0)
    assert cell["majority_same_days"] == pytest.approx(0.5)  # always-up on THOSE days
    assert out["kappa=1.0"]["n_selected"] == 80
