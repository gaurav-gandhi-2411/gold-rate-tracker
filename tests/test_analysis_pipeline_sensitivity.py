"""Tests for scripts/analysis_pipeline_sensitivity.py (item 7c). No data, no models."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "analysis_pipeline_sensitivity.py"
_spec = importlib.util.spec_from_file_location("analysis_pipeline_sensitivity", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["analysis_pipeline_sensitivity"] = mod
_spec.loader.exec_module(mod)


def _cell(kind: str, q: float, det: float) -> dict:
    keys = ("learned", "oracle")
    tests = ("p_accuracy_dm", "p_brier_dm")
    return {"dataset": kind, "q": q, **{f"{k}_{t}_detection": det for k in keys for t in tests}}


def test_floor_is_smallest_positive_q_with_80pct_detection() -> None:
    cells = [_cell("comex", 0.0, 0.05), _cell("comex", 0.1, 0.5), _cell("comex", 0.15, 0.8)]
    f = mod.floors(cells)["comex/oracle_p_brier_dm"]
    assert f["floor_q"] == 0.15
    assert f["floor_oracle_accuracy"] == 0.575
    assert f["false_positive_rate_q0"] == 0.05


def test_no_floor_when_never_detected() -> None:
    f = mod.floors([_cell("inr", 0.6, 0.4)])["inr/learned_p_accuracy_dm"]
    assert f["floor_q"] is None


def test_brier_test_uses_probabilities_accuracy_test_does_not() -> None:
    rng = np.random.default_rng(42)
    y = rng.integers(0, 2, 2000)
    clim = np.full(2000, 0.5)
    sharp = np.where(y == 1, 0.55, 0.45)  # right side of 0.5 every time
    r = mod.tests(y, sharp, clim, horizon=1)
    assert r["acc"] == 1.0
    assert r["p_accuracy_dm"] < 0.05 and r["p_brier_dm"] < 0.05
