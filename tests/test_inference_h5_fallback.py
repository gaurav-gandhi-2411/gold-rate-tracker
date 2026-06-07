"""RED regression tests for Phi22 H5 IBJA-calibrated fallback safety gate.

Test 1 (test_valid_false_is_noop_regression) MUST FAIL with current code because
`price_source` does not yet exist in forecast.json output.  It will go GREEN once
inference.py is updated in a later Phi22 step.

Test 2 (test_valid_false_no_crash_without_parquet) verifies that when
calibration.valid=False the run completes without touching ibja_rates.parquet
(which is absent).  This may already PASS with current code.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import ml.inference as inf
import pytest


# ---------------------------------------------------------------------------
# Fixture helpers (self-contained; do NOT import from test_inference_main.py)
# ---------------------------------------------------------------------------


def _make_prices(n: int, base: int = 14000, stale_last: bool = False) -> list[dict]:
    """Return n price entries.

    If stale_last=True the last entry's timestamp is 9 hours ago and its 22k
    value is fixed at 14320 so tests can assert the exact scraped value.
    """
    start = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    entries = [
        {
            "timestamp": (start + timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "22k": base + (i % 7) * 20,
            "24k": round((base + (i % 7) * 20) * 24 / 22),
            "18k": round((base + (i % 7) * 20) * 18 / 22),
            "source": "test",
        }
        for i in range(n - 1)
    ]
    if stale_last:
        stale_ts = (datetime.now(UTC) - timedelta(hours=9)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        entries.append(
            {
                "timestamp": stale_ts,
                "22k": 14320,
                "24k": round(14320 * 24 / 22),
                "18k": round(14320 * 18 / 22),
                "source": "test",
            }
        )
    else:
        entries.append(
            {
                "timestamp": (start + timedelta(days=n - 1)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "22k": base + ((n - 1) % 7) * 20,
                "24k": round((base + ((n - 1) % 7) * 20) * 24 / 22),
                "18k": round((base + ((n - 1) % 7) * 20) * 18 / 22),
                "source": "test",
            }
        )
    return entries


def _make_backtest(n_folds: int = 35) -> dict:
    """Synthetic backtest.json with deterministic folds for conformal PI testing."""
    folds = []
    for i in range(n_folds):
        base = 14000.0 + i * 10
        folds.append(
            {
                "fold_id": i,
                "context_end_date": f"2026-01-{(i % 28) + 1:02d}",
                "context_size": 30 + i,
                "actuals": [base + (j + 1) * 50 for j in range(5)],
                "chronos_p50": [base + (j + 1) * 60 for j in range(5)],
                "naive": [base] * 5,
            }
        )
    return {"n_folds": n_folds, "mae_5d_avg_naive": 249.5, "folds": folds}


def _make_probe(status: str = "success") -> dict:
    """Synthetic chronos_probe.json."""
    if status != "success":
        return {"status": status, "model_version": "amazon/chronos-bolt-tiny@test"}
    return {
        "status": "success",
        "ibja_last_value": 14450.0,
        "ibja_forecast": [
            {"day": d, "p10": 14200.0, "p50": 14600.0 + d * 50, "p90": 14900.0}
            for d in range(1, 6)
        ],
        "model_version": "amazon/chronos-bolt-tiny@test",
        "schema_version": 1,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_valid_false_is_noop_regression(tmp_path: object, monkeypatch: object) -> None:
    """RED test: when calibration.valid=False, forecast.json must carry price_source="tanishq_scrape".

    This FAILS with pre-Phi22 code because `price_source` is not emitted by
    the current inference.py.  The test goes GREEN only after Phi22 adds the
    field.  Its purpose is to prove the safety gate is not silently bypassed.
    """
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)

    prices = _make_prices(40, base=14000, stale_last=True)
    # Capture the exact timestamp that was written so we can assert round-trip.
    expected_scraped_at: str = prices[-1]["timestamp"]

    (tmp_path / "prices.json").write_text(json.dumps(prices))
    (tmp_path / "backtest.json").write_text(json.dumps(_make_backtest(35)))
    (tmp_path / "chronos_probe.json").write_text(json.dumps(_make_probe("success")))
    # calibration.valid=False — no slope/intercept/residual_std
    (tmp_path / "calibration.json").write_text(json.dumps({"valid": False}))
    # ibja_rates.parquet deliberately absent — must NOT be required when valid=False

    inf.main()

    fc = json.loads((tmp_path / "forecast.json").read_text())

    # --- RED assertion (causes test failure with current code) ---
    assert fc.get("price_source") == "tanishq_scrape", (
        "price_source must be 'tanishq_scrape' when calibration.valid=False; "
        f"got: {fc.get('price_source')!r}"
    )

    # --- Remaining invariants (also tested once price_source lands) ---
    assert fc.get("est_low") is None, (
        "est_low must be absent (None/missing) when calibration.valid=False"
    )
    assert fc.get("est_high") is None, (
        "est_high must be absent (None/missing) when calibration.valid=False"
    )
    assert fc["current_22k"] == 14320, (
        f"current_22k must equal the scraped 14320, got {fc.get('current_22k')!r}"
    )
    assert fc["scraped_at"] == expected_scraped_at, (
        f"scraped_at must be the Tanishq timestamp {expected_scraped_at!r}; "
        f"got {fc.get('scraped_at')!r}"
    )
    assert fc["model_status"] == "naive_headline", (
        f"model_status must remain 'naive_headline'; got {fc.get('model_status')!r}"
    )


@pytest.mark.smoke
def test_valid_false_no_crash_without_parquet(tmp_path: object, monkeypatch: object) -> None:
    """When calibration.valid=False and ibja_rates.parquet is absent, inf.main() must not crash.

    This tests the safety gate from the other direction: the valid=False path
    must never attempt to read the parquet file.  If it does, the test fails
    with FileNotFoundError.
    """
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)

    prices = _make_prices(40, base=14000, stale_last=True)
    (tmp_path / "prices.json").write_text(json.dumps(prices))
    (tmp_path / "backtest.json").write_text(json.dumps(_make_backtest(35)))
    (tmp_path / "chronos_probe.json").write_text(json.dumps(_make_probe("success")))
    (tmp_path / "calibration.json").write_text(json.dumps({"valid": False}))
    # ibja_rates.parquet intentionally absent

    # If inference.py tries to read the parquet, this will raise FileNotFoundError
    # or a pandas/pyarrow exception — both would fail the test.
    inf.main()

    assert (tmp_path / "forecast.json").exists(), (
        "forecast.json must be written even when calibration.valid=False and parquet is absent"
    )
