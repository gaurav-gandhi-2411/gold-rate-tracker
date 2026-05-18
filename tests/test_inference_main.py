"""Smoke test for ml.inference.main() — exercises the live CI hot path end-to-end."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta

import pytest

import ml.forecast as fc
import ml.inference as inf


def _make_prices(n_days: int, base_price: int = 9500) -> list[dict]:
    """Synthetic daily price readings — deterministic, no external data."""
    entries = []
    start = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    for i in range(n_days):
        ts = start + timedelta(days=i)
        # Small deterministic variation so not all deltas are zero
        price = base_price + (i % 7) * 10
        entries.append(
            {
                "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "22k": price,
                "24k": int(round(price * 24 / 22)),
                "18k": int(round(price * 18 / 22)),
                "source": "smoke-test",
            }
        )
    return entries


@pytest.mark.smoke
def test_inference_main_produces_valid_forecast(tmp_path, monkeypatch):
    """Smoke: main() runs end-to-end on synthetic data and writes a valid forecast.json.

    Redirects all I/O to tmp_path so the test:
      - never touches data/ or models/production/
      - never calls yfinance, Tanishq scraper, Groq, or ntfy
    """
    # Redirect data read/write paths to tmp_path
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)
    monkeypatch.setattr(fc, "DATA_DIR", tmp_path)

    # Redirect model output path (avoids overwriting models/production/lgbm.txt)
    prod_dir = tmp_path / "models" / "production"
    monkeypatch.setattr(inf, "PROD_DIR", prod_dir)

    # Synthetic data: 35 daily readings gives ~27 valid training rows after NaN filtering
    (tmp_path / "prices.json").write_text(json.dumps(_make_prices(35)))
    (tmp_path / "history_seed.json").write_text("[]")

    # Suppress macro fetch — no yfinance in tests
    monkeypatch.setattr("ml.macro.load_macro_features", lambda *a, **kw: None)

    # Run the live hot path
    inf.main()

    # forecast.json must exist
    forecast_path = tmp_path / "forecast.json"
    assert forecast_path.exists(), "forecast.json was not written by main()"

    result = json.loads(forecast_path.read_text())

    # Required keys
    required = {"predicted_22k", "val_mae", "naive_mae", "model_status", "warmup", "lower", "upper"}
    missing = required - result.keys()
    assert not missing, f"Missing keys in forecast.json: {missing}"

    predicted = result["predicted_22k"]
    lower = result["lower"]
    upper = result["upper"]

    # PI ordering
    assert lower < predicted, f"lower ({lower}) must be < predicted_22k ({predicted})"
    assert predicted < upper, f"predicted_22k ({predicted}) must be < upper ({upper})"

    # PI near-symmetry: conformal PI is symmetric before rounding; each float→int
    # rounding can shift the boundary by ±1, so |diff| ≤ 2 is the correct tolerance.
    half_upper = upper - predicted
    half_lower = predicted - lower
    assert abs(half_upper - half_lower) <= 2, (
        f"PI asymmetric beyond rounding tolerance: "
        f"upper-pred={half_upper}, pred-lower={half_lower}"
    )

    # All output values are positive finite numbers
    for key in ("predicted_22k", "val_mae", "naive_mae", "lower", "upper"):
        val = result[key]
        assert isinstance(val, (int, float)), f"{key} is not numeric: {val!r}"
        assert math.isfinite(val), f"{key} is not finite: {val}"
        assert val > 0, f"{key} must be positive: {val}"

    # model_status must be a recognised value
    assert result["model_status"] in {"beating_naive", "matching_naive", "trailing_naive"}, (
        f"Unknown model_status: {result['model_status']!r}"
    )
