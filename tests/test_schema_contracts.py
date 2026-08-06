"""Schema contract tests for load-bearing JSON files.

Any committed data file or freshly-written inference output that violates
its schema fails CI immediately — catching schema drift before it reaches the PWA.

Validation coverage:
  prices.json          → PRICES_SCHEMA
  forecast.json        → FORECAST_SCHEMA
  chronos_probe.json   → CHRONOS_PROBE_SCHEMA
  backtest.json        → BACKTEST_SCHEMA
  calibration.json     → CALIBRATION_SCHEMA

Also validates freshly-written inference output via test_inference_output_validates_forecast_schema.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# jsonschema is a pure-Python test dep — no torch/chronos needed.
try:
    from jsonschema import ValidationError, validate  # noqa: F401

    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

pytestmark = pytest.mark.skipif(not HAS_JSONSCHEMA, reason="jsonschema not installed")

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

# ---------------------------------------------------------------------------
# Schema definitions
# ---------------------------------------------------------------------------

PRICES_SCHEMA: dict = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["timestamp", "22k"],
        "properties": {
            "timestamp": {"type": "string"},
            "22k": {"type": "number"},
        },
    },
}

FORECAST_SCHEMA: dict = {
    "type": "object",
    "required": [
        "predicted_at",
        "headline",
        "chronos_companion",
        "predicted_22k",
        "lower",
        "upper",
    ],
    "properties": {
        "predicted_at": {"type": "string"},
        "predicted_22k": {"type": "number"},
        "lower": {},  # number or null (insufficient_backtest_history path writes null)
        "upper": {},  # number or null
        "headline": {
            "type": "object",
            "required": ["method", "predicted_22k", "lower", "upper", "conformal_pi_half"],
            "properties": {
                "method": {"type": "string"},
                "predicted_22k": {"type": "number"},
                "lower": {"type": "number"},
                "upper": {"type": "number"},
                "conformal_pi_half": {"type": "number"},
            },
        },
        "chronos_companion": {
            "type": "object",
            "required": [
                "status",
                "lean_direction",
                "direction_consensus",
                "calibration_applied",
                "majority_direction",
            ],
            "properties": {
                "status": {
                    "type": "string",
                    "enum": [
                        "success",
                        "failed",
                        "model_load_failed",
                        "forecast_failed",
                        "insufficient_context",
                    ],
                },
                "lean_direction": {
                    "type": "string",
                    "enum": ["up", "down", "neutral", "flat"],
                },
                "direction_consensus": {"type": "number"},
                "calibration_applied": {"type": "boolean"},
                "majority_direction": {"type": "string"},
            },
        },
    },
}

CHRONOS_PROBE_SCHEMA: dict = {
    "type": "object",
    "required": [
        "status",
        "direction_consensus",
        "majority_direction",
        "num_samples",
        "sample_directions",
        "schema_version",
    ],
    "properties": {
        "status": {"type": "string"},
        "direction_consensus": {"type": "number"},
        "majority_direction": {"type": "string"},
        "num_samples": {"type": "integer"},
        "sample_directions": {"type": "array", "items": {"type": "string"}},
        "schema_version": {"type": "integer", "const": 2},
    },
}

BACKTEST_SCHEMA: dict = {
    "type": "object",
    "required": ["n_folds", "dir_acc_5d_chronos", "dir_acc_5d_naive", "folds"],
    "properties": {
        "n_folds": {"type": "integer"},
        "dir_acc_5d_chronos": {"type": "number"},
        "dir_acc_5d_naive": {"type": "number"},
        "folds": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["fold_id", "actuals", "naive", "chronos_p50"],
                "properties": {
                    "fold_id": {"type": "integer"},
                    "actuals": {"type": "array"},
                    "naive": {"type": "array"},
                    "chronos_p50": {"type": "array"},
                },
            },
        },
    },
}

CALIBRATION_SCHEMA: dict = {
    "type": "object",
    "required": ["valid", "schema_version"],
    "properties": {
        "valid": {"type": "boolean"},
        "schema_version": {"type": "integer"},
    },
}


# ---------------------------------------------------------------------------
# Tests against committed data files
# ---------------------------------------------------------------------------


def test_prices_json_schema() -> None:
    """Committed prices.json validates against schema."""
    data = json.loads((DATA_DIR / "prices.json").read_text())
    validate(instance=data, schema=PRICES_SCHEMA)


def test_forecast_json_schema() -> None:
    """Committed forecast.json validates against schema."""
    data = json.loads((DATA_DIR / "forecast.json").read_text())
    validate(instance=data, schema=FORECAST_SCHEMA)


def test_chronos_probe_schema() -> None:
    """Committed chronos_probe.json validates against schema."""
    data = json.loads((DATA_DIR / "chronos_probe.json").read_text())
    validate(instance=data, schema=CHRONOS_PROBE_SCHEMA)


def test_backtest_json_schema() -> None:
    """Committed backtest.json validates against schema."""
    data = json.loads((DATA_DIR / "backtest.json").read_text())
    validate(instance=data, schema=BACKTEST_SCHEMA)


def test_calibration_json_schema() -> None:
    """Committed calibration.json validates against schema."""
    data = json.loads((DATA_DIR / "calibration.json").read_text())
    validate(instance=data, schema=CALIBRATION_SCHEMA)


# ---------------------------------------------------------------------------
# Integration: freshly-written inference output must validate
# ---------------------------------------------------------------------------


def test_inference_output_validates_forecast_schema(tmp_path, monkeypatch) -> None:
    """Freshly-written forecast.json from inference.main() must validate against FORECAST_SCHEMA."""
    import ml.inference as inf

    from tests.test_inference_main import _disable_fusion, _make_backtest, _make_prices, _make_probe

    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)
    monkeypatch.setattr("ml.notifications.STATE_PATH", tmp_path / "notification_state.json")
    _disable_fusion(monkeypatch)  # calibration.valid=False + stale fixture prices reach tier 3

    (tmp_path / "prices.json").write_text(json.dumps(_make_prices(40)))
    (tmp_path / "backtest.json").write_text(json.dumps(_make_backtest(35)))
    (tmp_path / "chronos_probe.json").write_text(json.dumps(_make_probe("success")))
    (tmp_path / "calibration.json").write_text(json.dumps({"valid": False, "schema_version": 1}))

    inf.main()

    data = json.loads((tmp_path / "forecast.json").read_text())
    validate(instance=data, schema=FORECAST_SCHEMA)
