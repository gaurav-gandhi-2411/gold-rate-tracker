"""data/backtest.json is scores only (ADR 060, 2026-10-09): no IBJA price levels, and the numbers the
pipeline and the page derive from it are unchanged."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from ml.backtest import CONFORMAL_FOLDS, CONFORMAL_PCT, scores_only
from ml.inference import _CONFORMAL_FOLDS, _CONFORMAL_PCT, _compute_conformal_pi
from ml.notifications import compute_dir_acc_30f

ROOT = Path(__file__).resolve().parent.parent
LEVEL_KEYS = {
    "actuals",
    "naive",
    "chronos_p10",
    "chronos_p50",
    "chronos_p90",
    "mae_chronos_per_h",
    "mae_naive_per_h",
}


def _levels_result(n: int = 40) -> dict:
    rng = np.random.default_rng(42)
    base = 13000.0 + np.cumsum(rng.normal(0, 40, n + 6))
    folds = []
    for i in range(n):
        last = float(base[i])
        act = [round(float(v), 2) for v in base[i + 1 : i + 6]]
        p50 = [round(last + float(rng.normal(0, 60)), 2) for _ in range(5)]
        folds.append(
            {
                "fold_id": i,
                "context_end_date": f"2026-01-{i + 1:02d}" if i < 28 else f"2026-02-{i - 27:02d}",
                "context_size": 100 + i,
                "actuals": act,
                "chronos_p10": [round(v - 150, 2) for v in p50],
                "chronos_p50": p50,
                "chronos_p90": [round(v + 150, 2) for v in p50],
                "naive": [round(last, 2)] * 5,
                "mae_chronos_per_h": [round(abs(p - a), 2) for p, a in zip(p50, act, strict=True)],
                "mae_naive_per_h": [round(abs(last - a), 2) for a in act],
                "in_pi_80": [abs(p - a) <= 150 for p, a in zip(p50, act, strict=True)],
                "sub_30_context": False,
            }
        )
    return {"n_folds": n, "mae_5d_avg_chronos": 1.0, "dir_acc_5d_chronos": 0.5, "folds": folds}


def test_matching_constants():
    # The scores-only file's aggregates are only used by inference when these agree exactly.
    assert (CONFORMAL_FOLDS, CONFORMAL_PCT) == (_CONFORMAL_FOLDS, _CONFORMAL_PCT)


def test_no_price_level_survives_in_any_fold():
    out = scores_only(_levels_result())
    assert all(not (LEVEL_KEYS & set(f)) for f in out["folds"])
    text = json.dumps(out)
    for k in LEVEL_KEYS:
        assert f'"{k}"' not in text, k


def test_conformal_band_and_direction_accuracy_are_unchanged():
    old = _levels_result()
    new = scores_only(old)
    for idx in range(5):
        assert _compute_conformal_pi(new, idx) == _compute_conformal_pi(old, idx), idx
    assert compute_dir_acc_30f(new) == compute_dir_acc_30f(old)
    # every non-fold field is carried over untouched
    for k, v in old.items():
        if k != "folds":
            assert new[k] == v


def test_idempotent_and_error_values():
    old = _levels_result(8)
    new = scores_only(old)
    assert scores_only(new) is new
    f0, g0 = old["folds"][0], new["folds"][0]
    assert g0["err_chronos_p50"] == [
        round(p - a, 2) for p, a in zip(f0["chronos_p50"], f0["actuals"], strict=True)
    ]
    assert g0["in_pi_80"] == f0["in_pi_80"]


def test_inference_ignores_aggregates_with_a_different_window_or_percentile():
    new = scores_only(_levels_result())
    assert _compute_conformal_pi(new, 0) is not None
    for key, val in (("folds_window", 20), ("pct", 90)):
        bad = {**new, "naive_error_recent": {**new["naive_error_recent"], key: val}}
        # no folds with levels either, so the safe outcome is "not enough history", never a wrong band
        assert _compute_conformal_pi(bad, 0) is None


def test_too_few_recent_folds_gives_no_band():
    assert _compute_conformal_pi(scores_only(_levels_result(12)), 0) is None


def test_per_fold_mae_is_the_same_from_either_shape():
    from ml.metrics import fold_mae_5d

    old = _levels_result(12)
    new = scores_only(old)
    for a, b in zip(old["folds"], new["folds"], strict=True):
        for which in ("chronos", "naive"):
            assert abs(fold_mae_5d(a, which) - fold_mae_5d(b, which)) < 0.006


def test_scorecard_chronos_row_is_the_same_from_either_shape(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "bms", ROOT / "scripts" / "build_model_scorecard.py"
    )
    bms = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bms)
    start = bms.FORWARD_STARTS["chronos"]["date"]
    old = _levels_result(40)
    for f in old["folds"]:
        f["context_end_date"] = "2026-09-01"  # all before the forward start: retrospective block
    assert start > "2026-09-01"
    rows = []
    for name, bt in (("levels", old), ("scores", scores_only(old))):
        d = tmp_path / name
        d.mkdir()
        (d / "backtest.json").write_text(json.dumps(bt), encoding="utf-8")
        rows.append(bms.row_chronos(d))
    # identical row (retrospective means, CI, status) from either file shape; no builder-error row
    assert rows[0] == rows[1]
    assert "builder error" not in json.dumps(rows[1])


def test_the_contract_schema_accepts_both_shapes():
    import pytest

    jsonschema = pytest.importorskip("jsonschema")
    from tests.test_schema_contracts import BACKTEST_SCHEMA

    old = {**_levels_result(8), "dir_acc_5d_chronos": 0.5, "dir_acc_5d_naive": 0.5}
    new = scores_only(old)
    jsonschema.validate(old, {**BACKTEST_SCHEMA, "required": ["n_folds", "folds"]})
    jsonschema.validate(new, {**BACKTEST_SCHEMA, "required": ["n_folds", "folds"]})
