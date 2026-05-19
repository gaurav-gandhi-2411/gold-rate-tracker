"""Smoke tests for ml.inference.main() — naive headline + Chronos companion path."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta

import pytest

import ml.inference as inf


def _make_prices(n: int, base: int = 14400) -> list[dict]:
    start = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    return [
        {
            "timestamp": (start + timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "22k": base + (i % 7) * 20,
            "24k": int(round((base + (i % 7) * 20) * 24 / 22)),
            "18k": int(round((base + (i % 7) * 20) * 18 / 22)),
            "source": "smoke-test",
        }
        for i in range(n)
    ]


def _make_backtest(n_folds: int = 35) -> dict:
    """Synthetic backtest.json with deterministic folds for conformal PI testing."""
    folds = []
    for i in range(n_folds):
        base = 14000.0 + i * 10
        naive_val = [base] * 5
        actuals = [base + (j + 1) * 50 for j in range(5)]
        folds.append({
            "fold_id": i,
            "context_end_date": f"2026-01-{(i % 28) + 1:02d}",
            "context_size": 30 + i,
            "actuals": actuals,
            "chronos_p50": [base + (j + 1) * 60 for j in range(5)],
            "naive": naive_val,
        })
    return {
        "n_folds": n_folds,
        "mae_5d_avg_naive": 249.5,
        "folds": folds,
    }


def _make_probe(status: str = "success") -> dict:
    if status != "success":
        return {"status": status, "model_version": "amazon/chronos-bolt-tiny@a0e552de"}
    return {
        "status": "success",
        "ibja_last_value": 14450.0,
        "ibja_forecast": [
            {"day": d, "p10": 14200.0, "p50": 14600.0 + d * 50, "p90": 14900.0}
            for d in range(1, 6)
        ],
        "model_version": "amazon/chronos-bolt-tiny@a0e552de",
        "schema_version": 1,
    }


@pytest.mark.smoke
def test_inference_main_produces_valid_forecast(tmp_path, monkeypatch):
    """main() writes forecast.json with correct new schema and top-level aliases."""
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)

    (tmp_path / "prices.json").write_text(json.dumps(_make_prices(40)))
    (tmp_path / "backtest.json").write_text(json.dumps(_make_backtest(35)))
    (tmp_path / "chronos_probe.json").write_text(json.dumps(_make_probe("success")))
    (tmp_path / "calibration.json").write_text(json.dumps({"valid": False}))

    inf.main()

    fc = json.loads((tmp_path / "forecast.json").read_text())

    # Top-level aliases
    for key in ("predicted_22k", "lower", "upper", "predicted_at", "target_time",
                "model_status", "model_version", "warmup", "real_readings_count"):
        assert key in fc, f"Missing top-level key: {key}"

    # PI ordering via aliases
    assert fc["lower"] < fc["predicted_22k"] < fc["upper"]

    # Naive flat-hold: predicted == current price
    prices = _make_prices(40)
    current_22k = prices[-1]["22k"]
    assert fc["predicted_22k"] == current_22k

    # Nested headline block
    hl = fc["headline"]
    assert hl["method"] == "naive_flat_hold"
    assert hl["predicted_22k"] == current_22k
    assert hl["lower"] < hl["predicted_22k"] < hl["upper"]
    assert hl["conformal_pi_half"] > 0
    assert hl["naive_mae_recent_30"] > 0

    # PI symmetry (within rounding tolerance of ±1)
    assert abs((hl["upper"] - hl["predicted_22k"]) - (hl["predicted_22k"] - hl["lower"])) <= 1

    # Chronos companion
    cc = fc["chronos_companion"]
    assert cc["status"] == "success"
    assert cc["lean_direction"] in ("up", "down", "neutral")
    assert isinstance(cc["lean_strength_pct"], float)
    assert cc["direction_acc_30f"] is not None
    assert len(cc["horizon_p50"]) == 5

    # No LightGBM artifacts in new schema
    for legacy_key in ("val_mae", "training_rows", "blend_weight_lgbm", "ensemble"):
        assert legacy_key not in fc, f"Legacy key {legacy_key!r} must not appear in new schema"

    # All required values are finite positives
    for key in ("predicted_22k", "lower", "upper"):
        assert math.isfinite(fc[key]) and fc[key] > 0

    assert fc["model_status"] == "naive_headline"
    assert fc["warmup"] is False


@pytest.mark.smoke
def test_inference_probe_failed(tmp_path, monkeypatch):
    """When chronos probe failed, companion block reflects failure and model_fallback=True."""
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)

    (tmp_path / "prices.json").write_text(json.dumps(_make_prices(20)))
    (tmp_path / "backtest.json").write_text(json.dumps(_make_backtest(35)))
    (tmp_path / "chronos_probe.json").write_text(json.dumps(_make_probe("failed")))
    (tmp_path / "calibration.json").write_text(json.dumps({"valid": False}))

    inf.main()

    fc = json.loads((tmp_path / "forecast.json").read_text())
    assert fc["model_fallback"] is True
    cc = fc["chronos_companion"]
    assert cc["status"] == "failed"
    assert cc["lean_direction"] == "neutral"
    assert cc["direction_acc_30f"] is None
    # Headline still valid when probe fails
    assert fc["predicted_22k"] > 0
    assert fc["lower"] < fc["predicted_22k"] < fc["upper"]


@pytest.mark.smoke
def test_inference_no_backtest(tmp_path, monkeypatch):
    """When backtest.json is missing, writes insufficient_backtest_history — no fabricated PI."""
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)

    (tmp_path / "prices.json").write_text(json.dumps(_make_prices(10)))
    (tmp_path / "chronos_probe.json").write_text(json.dumps(_make_probe("failed")))

    inf.main()

    fc = json.loads((tmp_path / "forecast.json").read_text())
    assert fc["model_status"] == "insufficient_backtest_history"
    assert fc["predicted_22k"] > 0
    assert fc["lower"] is None
    assert fc["upper"] is None
    assert "headline" not in fc


@pytest.mark.smoke
def test_inference_calibration_applied(tmp_path, monkeypatch):
    """When calibration.valid=True, companion horizon arrays are calibrated."""
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)

    (tmp_path / "prices.json").write_text(json.dumps(_make_prices(20)))
    (tmp_path / "backtest.json").write_text(json.dumps(_make_backtest(35)))
    (tmp_path / "chronos_probe.json").write_text(json.dumps(_make_probe("success")))
    (tmp_path / "calibration.json").write_text(json.dumps({
        "valid": True,
        "slope": 1.02,
        "intercept": 150.0,
    }))

    inf.main()

    fc = json.loads((tmp_path / "forecast.json").read_text())
    cc = fc["chronos_companion"]
    assert cc["calibration_applied"] is True
    # Verify first p50 value: 1.02 * (14600.0 + 1 * 50) + 150.0
    raw_p50_day1 = 14600.0 + 1 * 50  # from _make_probe: 14600.0 + d * 50, d=1
    expected = round(1.02 * raw_p50_day1 + 150.0, 2)
    assert cc["horizon_p50"][0] == pytest.approx(expected, abs=0.01)


@pytest.mark.smoke
def test_compute_conformal_pi_uses_last_30_folds():
    """_compute_conformal_pi uses only the last _CONFORMAL_FOLDS folds."""
    # Build 50 folds: first 20 have h=5 error=1000, last 30 have error=100
    folds = []
    for i in range(50):
        error = 100.0 if i >= 20 else 1000.0
        base = 14000.0
        folds.append({
            "actuals": [base, base, base, base, base + error],
            "naive": [base, base, base, base, base],
        })
    bt = {"n_folds": 50, "mae_5d_avg_naive": 249.5, "folds": folds}

    pi_half, mae = inf._compute_conformal_pi(bt)
    # All 30 recent folds have error=100 → 80th pct = 100.0
    assert pi_half == pytest.approx(100.0, abs=1.0)
    assert mae == pytest.approx(100.0, abs=1.0)
