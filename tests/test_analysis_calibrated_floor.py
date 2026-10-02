"""Tests for scripts/analysis_calibrated_floor.py (item 6, ADR 045). No COMEX data."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "analysis_calibrated_floor.py"
_spec = importlib.util.spec_from_file_location("analysis_calibrated_floor", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["analysis_calibrated_floor"] = mod
_spec.loader.exec_module(mod)


def test_shards_cover_every_learner_and_q_and_parse_back() -> None:
    assert len(mod.SHARDS) == len(mod.LEARNERS) * len(mod.GRID) == 25
    for key in mod.SHARDS:
        learner, q = key.rsplit("_q", 1)
        assert learner in mod.LEARNERS and float(q) in mod.GRID
    assert mod.SEEDS[:20] == [42 + i for i in range(20)]  # #1992's seeds come first


def test_calibration_split_is_chronological_with_embargo() -> None:
    elig = np.arange(300)
    as_of = np.array([f"d{i:04d}" for i in range(300)])
    # label known 3 rows later: the last base-fit rows must mature before the first cal day
    label_date = np.array([f"d{i + 3:04d}" for i in range(300)])
    fit, cal = mod.split_calibration(elig, as_of, label_date)
    assert len(cal) == 60 and cal[0] == 240 and cal[-1] == 299
    assert fit.max() < cal.min()
    assert all(label_date[fit] < as_of[cal[0]])
    assert fit.max() == 236  # rows 237-239 had not matured by day 240


def test_calibration_split_respects_min_cal() -> None:
    elig = np.arange(100)
    days = np.array([f"d{i:04d}" for i in range(100)])
    _, cal = mod.split_calibration(elig, days, days)
    assert len(cal) == mod.MIN_CAL


@pytest.mark.parametrize("method", ["platt", "isotonic", "temperature"])
def test_calibrators_fix_an_overconfident_score(method: str) -> None:
    rng = np.random.default_rng(42)
    z_true = rng.normal(0, 0.3, 4000)
    y = (rng.random(4000) < 1 / (1 + np.exp(-z_true))).astype(int)
    z = 5 * z_true  # overconfident by a factor of 5
    raw = 1 / (1 + np.exp(-z[2000:]))
    cal = mod.calibrate(method, z[:2000], y[:2000], z[2000:])
    y_te = y[2000:]
    assert np.mean((cal - y_te) ** 2) < np.mean((raw - y_te) ** 2)
    assert cal.min() >= 0 and cal.max() <= 1


def test_temperature_recovers_the_scale() -> None:
    rng = np.random.default_rng(42)
    z = rng.normal(0, 1, 20000)
    y = (rng.random(20000) < 1 / (1 + np.exp(-z))).astype(int)
    assert mod.fit_temperature(3 * z, y) == pytest.approx(3.0, rel=0.1)


@pytest.mark.parametrize("learner", mod.CALIBRATED)
def test_calibrated_walk_forward_runs_forward_only_on_synthetic_data(learner: str) -> None:
    rng = np.random.default_rng(42)
    n = 400
    X = rng.normal(size=(n, 3))
    y = (rng.random(n) < 1 / (1 + np.exp(-1.5 * X[:, 0]))).astype(int)
    days = np.array([f"2020-{i // 28 + 1:02d}-{i % 28 + 1:02d}" for i in range(n)])
    d = {
        "X": X,
        "as_of": days,
        "label_date": np.r_[days[1:], ["9999"]],
        "min_train": 250,
        "block": 21,
    }
    wf = mod.walk_forward_calibrated(d, learner, y)
    assert wf["idx"][0] == 250 and len(wf["idx"]) == n - 250
    assert ((wf["p"] >= 0) & (wf["p"] <= 1)).all()
    # a real signal is learned: better than climatology out of sample
    y_t = y[wf["idx"]]
    assert np.mean((wf["p"] - y_t) ** 2) < np.mean((wf["p_clim"] - y_t) ** 2)


def test_exact_sign_p_and_wilson() -> None:
    assert mod.exact_sign_p(0, 0) == 1.0
    assert mod.exact_sign_p(7, 0) == pytest.approx(0.5**7)
    assert mod.exact_sign_p(3, 3) > 0.5
    lo, hi = mod.wilson(80, 100)
    assert lo < 0.8 < hi and hi - lo < 0.2


def _cell(learner: str, q: float, acc: list[bool], brier: list[bool]) -> dict:
    return {"learner": learner, "q": q, "p_accuracy_dm_detected": acc, "p_brier_dm_detected": brier}


def test_verdict_requires_a_lower_floor_low_false_positives_and_a_paired_win() -> None:
    n = 100
    no, yes = [False] * n, [True] * n
    cells = []
    for q in mod.GRID:
        # control: detects only from q = 0.20
        cells.append(_cell("logit", q, yes if q >= 0.2 else no, no))
        # platt: Brier test detects from q = 0.10, never at q = 0
        cells.append(_cell("logit_platt", q, no, yes if q >= 0.1 else no))
        # isotonic: lower floor but 20% false positives at q = 0
        iso = yes if q >= 0.1 else ([True] * 20 + [False] * 80 if q == 0 else no)
        cells.append(_cell("logit_isotonic", q, iso, no))
    v = mod.verdict(cells)
    assert v["control_floor_q"] == 0.2
    assert v["cells_that_lower_the_floor"] == ["logit_platt/p_brier_dm"]
    assert v["floor_lowered"] is True
    assert v["table"]["logit_platt/p_brier_dm"]["paired_vs_control_at_floor"]["b"] == n


def test_verdict_is_negative_when_nothing_beats_the_control() -> None:
    n = 100
    no, yes = [False] * n, [True] * n
    cells = [_cell(lr, q, yes if q >= 0.2 else no, no) for lr in mod.LEARNERS for q in mod.GRID]
    v = mod.verdict(cells)
    assert v["floor_lowered"] is False and v["cells_that_lower_the_floor"] == []
