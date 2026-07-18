"""Tests for Phi22 H5 IBJA-calibrated fallback safety gate.

Tests 1–2 were RED regression tests; they go GREEN once inference.py emits
`price_source`.  Tests 3–8 cover the full fallback logic.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import ml.inference as inf
import pandas as pd
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
            {"day": d, "p10": 14200.0, "p50": 14600.0 + d * 50, "p90": 14900.0} for d in range(1, 6)
        ],
        "model_version": "amazon/chronos-bolt-tiny@test",
        "schema_version": 1,
    }


def _make_ibja_parquet(tmp_path: object, rows: list[dict]) -> None:
    """Write ibja_rates.parquet with given rows to tmp_path."""
    pd.DataFrame(rows).to_parquet(tmp_path / "ibja_rates.parquet", index=False)


def _make_prices_with_last_ts(n: int, last_ts: datetime, last_22k: int = 14320) -> list[dict]:
    """n price entries where the last entry has a specific timestamp."""
    start = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    entries = [
        {
            "timestamp": (start + timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "22k": 14000 + (i % 7) * 20,
            "24k": round((14000 + (i % 7) * 20) * 24 / 22),
            "18k": round((14000 + (i % 7) * 20) * 18 / 22),
            "source": "test",
        }
        for i in range(n - 1)
    ]
    entries.append(
        {
            "timestamp": last_ts.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "22k": last_22k,
            "24k": round(last_22k * 24 / 22),
            "18k": round(last_22k * 18 / 22),
            "source": "test",
        }
    )
    return entries


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


# ---------------------------------------------------------------------------
# New tests C1–C6: full fallback logic coverage
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_valid_true_stale_scrape_ibja_fresh(tmp_path: object, monkeypatch: object) -> None:
    """All gates pass: stale scrape + valid calibration + fresh IBJA → ibja_calibrated path."""
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)

    now = datetime(2026, 3, 15, 13, 30, tzinfo=UTC)
    # Last scrape at 04:00 UTC = 9.5h before now → stale
    last_ts = datetime(2026, 3, 15, 4, 0, tzinfo=UTC)
    prices = _make_prices_with_last_ts(40, last_ts, last_22k=14320)
    expected_scraped_at = prices[-1]["timestamp"]

    (tmp_path / "prices.json").write_text(json.dumps(prices))
    (tmp_path / "backtest.json").write_text(json.dumps(_make_backtest(35)))
    (tmp_path / "chronos_probe.json").write_text(json.dumps(_make_probe("success")))
    (tmp_path / "calibration.json").write_text(
        json.dumps({"valid": True, "slope": 1.0, "intercept": 100.0, "residual_std": 50.0})
    )
    # ibja_per_g = 144000 / 10 = 14400; ibja_calibrated_22k = round(1.0*14400+100) = 14500
    _make_ibja_parquet(tmp_path, [{"date": "2026-03-15", "pm_916": 144000.0}])

    inf.main(now=now)

    fc = json.loads((tmp_path / "forecast.json").read_text())

    assert fc["price_source"] == "ibja_calibrated"
    assert fc["current_22k"] == round(1.0 * 14400.0 + 100.0)  # 14500
    assert fc["est_low"] == round(14500 - 50.0)  # 14450
    assert fc["est_high"] == round(14500 + 50.0)  # 14550
    assert fc["scraped_at"] == expected_scraped_at, (
        "scraped_at must NOT be overwritten with IBJA time"
    )
    assert fc["ibja_asof"] == "2026-03-15T11:30:00+00:00"
    assert fc["predicted_22k"] == 14500, "naive flat-hold must use the calibrated current price"


@pytest.mark.smoke
def test_valid_true_fresh_scrape_no_override(tmp_path: object, monkeypatch: object) -> None:
    """Fresh scrape (1.5h old) → fallback must NOT override, even with valid calibration."""
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)

    now = datetime(2026, 3, 15, 13, 30, tzinfo=UTC)
    # Last scrape at 12:00 UTC = 1.5h before now → fresh
    last_ts = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)
    prices = _make_prices_with_last_ts(40, last_ts, last_22k=14320)

    (tmp_path / "prices.json").write_text(json.dumps(prices))
    (tmp_path / "backtest.json").write_text(json.dumps(_make_backtest(35)))
    (tmp_path / "chronos_probe.json").write_text(json.dumps(_make_probe("success")))
    (tmp_path / "calibration.json").write_text(
        json.dumps({"valid": True, "slope": 1.0, "intercept": 100.0, "residual_std": 50.0})
    )
    _make_ibja_parquet(tmp_path, [{"date": "2026-03-15", "pm_916": 144000.0}])

    inf.main(now=now)

    fc = json.loads((tmp_path / "forecast.json").read_text())

    assert fc["price_source"] == "tanishq_scrape"
    assert fc["current_22k"] == 14320
    assert fc.get("est_low") is None
    assert fc.get("est_high") is None


@pytest.mark.smoke
def test_valid_true_ibja_24h_old_still_activates(tmp_path: object, monkeypatch: object) -> None:
    """Overnight gap: IBJA ~24h old (well within the 14-day backstop) → H5 still serves
    the estimate. A 1-day-old PM fix is a sound daily estimate under IBJA-primary (ADR 025).
    """
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)

    now = datetime(2026, 3, 16, 10, 0, tzinfo=UTC)  # Monday morning
    last_ts = datetime(2026, 3, 16, 0, 30, tzinfo=UTC)  # 9.5h before now → stale
    prices = _make_prices_with_last_ts(40, last_ts, last_22k=14320)

    (tmp_path / "prices.json").write_text(json.dumps(prices))
    (tmp_path / "backtest.json").write_text(json.dumps(_make_backtest(35)))
    (tmp_path / "chronos_probe.json").write_text(json.dumps(_make_probe("success")))
    (tmp_path / "calibration.json").write_text(
        json.dumps({"valid": True, "slope": 1.0, "intercept": 100.0, "residual_std": 50.0})
    )
    # Sunday 2026-03-15 IBJA: asof 11:30 UTC; age at Mon 10:00 = 22.5h → within 30h
    _make_ibja_parquet(tmp_path, [{"date": "2026-03-15", "pm_916": 144000.0}])

    inf.main(now=now)

    fc = json.loads((tmp_path / "forecast.json").read_text())
    assert fc["price_source"] == "ibja_calibrated", "24h-old IBJA must still serve H5 estimate"
    assert fc["current_22k"] == 14500
    assert fc["est_low"] == 14450 and fc["est_high"] == 14550


@pytest.mark.smoke
def test_valid_true_ibja_weekend_carry_forward_still_activates(
    tmp_path: object, monkeypatch: object
) -> None:
    """ADR 025: Sunday noon, latest IBJA row is Friday's (48.5h old) → IBJA is now
    PRIMARY, so this is expected weekend carry-forward, not staleness. Must still
    serve the calibrated estimate (dated to Friday via ibja_asof), not fall through.
    """
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)

    now = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)  # Sunday noon UTC
    # Last scrape at 03:00 UTC = 9h before now → stale (expected — Tanishq enrichment absent)
    last_ts = datetime(2026, 3, 15, 3, 0, tzinfo=UTC)
    scraped_22k = 14320
    prices = _make_prices_with_last_ts(40, last_ts, last_22k=scraped_22k)

    (tmp_path / "prices.json").write_text(json.dumps(prices))
    (tmp_path / "backtest.json").write_text(json.dumps(_make_backtest(35)))
    (tmp_path / "chronos_probe.json").write_text(json.dumps(_make_probe("success")))
    (tmp_path / "calibration.json").write_text(
        json.dumps({"valid": True, "slope": 1.0, "intercept": 100.0, "residual_std": 50.0})
    )
    # Friday row: ibja_asof = 2026-03-13T11:30 UTC; age at Sunday noon = 48.5h — well
    # within the 14-day backstop, so this is normal weekend carry-forward.
    _make_ibja_parquet(tmp_path, [{"date": "2026-03-13", "pm_916": 144000.0}])

    inf.main(now=now)

    fc = json.loads((tmp_path / "forecast.json").read_text())

    assert fc["price_source"] == "ibja_calibrated", (
        "weekend carry-forward must still serve the IBJA-calibrated estimate"
    )
    assert fc["current_22k"] == round(1.0 * 14400.0 + 100.0)  # 14500
    assert fc["ibja_asof"] == "2026-03-13T11:30:00+00:00", (
        "ibja_asof must carry Friday's date so the frontend can label it honestly"
    )


@pytest.mark.smoke
def test_valid_true_ibja_beyond_backstop_falls_through(
    tmp_path: object, monkeypatch: object
) -> None:
    """IBJA row 20 days old (beyond the 14-day defensive backstop) → falls through
    to the last-confirmed Tanishq price rather than showing an absurdly old estimate.
    """
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)

    now = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)
    last_ts = datetime(2026, 3, 15, 3, 0, tzinfo=UTC)  # 9h before now → stale
    scraped_22k = 14320
    prices = _make_prices_with_last_ts(40, last_ts, last_22k=scraped_22k)

    (tmp_path / "prices.json").write_text(json.dumps(prices))
    (tmp_path / "backtest.json").write_text(json.dumps(_make_backtest(35)))
    (tmp_path / "chronos_probe.json").write_text(json.dumps(_make_probe("success")))
    (tmp_path / "calibration.json").write_text(
        json.dumps({"valid": True, "slope": 1.0, "intercept": 100.0, "residual_std": 50.0})
    )
    # 2026-02-23 row: 20 days before 2026-03-15 → beyond the 14-day backstop
    _make_ibja_parquet(tmp_path, [{"date": "2026-02-23", "pm_916": 144000.0}])

    inf.main(now=now)

    fc = json.loads((tmp_path / "forecast.json").read_text())

    assert fc["price_source"] == "tanishq_scrape"
    assert fc["current_22k"] == scraped_22k
    assert fc.get("est_low") is None
    assert fc.get("est_high") is None
    assert fc.get("ibja_asof") is None


@pytest.mark.smoke
def test_ibja_parquet_missing_noop(tmp_path: object, monkeypatch: object) -> None:
    """No ibja_rates.parquet present → fallback returns noop, inference must not raise."""
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)

    now = datetime(2026, 3, 15, 13, 30, tzinfo=UTC)
    last_ts = datetime(2026, 3, 15, 4, 0, tzinfo=UTC)  # 9.5h before now → stale
    prices = _make_prices_with_last_ts(40, last_ts, last_22k=14320)

    (tmp_path / "prices.json").write_text(json.dumps(prices))
    (tmp_path / "backtest.json").write_text(json.dumps(_make_backtest(35)))
    (tmp_path / "chronos_probe.json").write_text(json.dumps(_make_probe("success")))
    (tmp_path / "calibration.json").write_text(
        json.dumps({"valid": True, "slope": 1.0, "intercept": 100.0, "residual_std": 50.0})
    )
    # ibja_rates.parquet intentionally absent

    inf.main(now=now)  # must not raise

    fc = json.loads((tmp_path / "forecast.json").read_text())

    assert fc["price_source"] == "tanishq_scrape"
    assert fc["current_22k"] == 14320
    assert fc.get("est_low") is None


@pytest.mark.smoke
def test_ibja_parquet_unreadable_noop(tmp_path: object, monkeypatch: object) -> None:
    """Corrupt parquet file → fallback catches exception, inference must not raise."""
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)

    now = datetime(2026, 3, 15, 13, 30, tzinfo=UTC)
    last_ts = datetime(2026, 3, 15, 4, 0, tzinfo=UTC)  # stale
    prices = _make_prices_with_last_ts(40, last_ts, last_22k=14320)

    (tmp_path / "prices.json").write_text(json.dumps(prices))
    (tmp_path / "backtest.json").write_text(json.dumps(_make_backtest(35)))
    (tmp_path / "chronos_probe.json").write_text(json.dumps(_make_probe("success")))
    (tmp_path / "calibration.json").write_text(
        json.dumps({"valid": True, "slope": 1.0, "intercept": 100.0, "residual_std": 50.0})
    )
    (tmp_path / "ibja_rates.parquet").write_bytes(b"not a parquet file")

    inf.main(now=now)  # must not raise

    fc = json.loads((tmp_path / "forecast.json").read_text())

    assert fc["price_source"] == "tanishq_scrape"


@pytest.mark.smoke
def test_band_unit_scaling_correctness(tmp_path: object, monkeypatch: object) -> None:
    """residual_std is applied directly (INR/g at 22k) — no extra scaling factor."""
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)

    now = datetime(2026, 3, 15, 13, 30, tzinfo=UTC)
    last_ts = datetime(2026, 3, 15, 4, 0, tzinfo=UTC)  # 9.5h before now → stale
    prices = _make_prices_with_last_ts(40, last_ts, last_22k=14000)

    (tmp_path / "prices.json").write_text(json.dumps(prices))
    (tmp_path / "backtest.json").write_text(json.dumps(_make_backtest(35)))
    (tmp_path / "chronos_probe.json").write_text(json.dumps(_make_probe("success")))
    (tmp_path / "calibration.json").write_text(
        json.dumps({"valid": True, "slope": 1.0, "intercept": 0.0, "residual_std": 100.0})
    )
    # ibja_per_g = 145000/10 = 14500; ibja_calibrated_22k = round(1.0*14500+0) = 14500
    _make_ibja_parquet(tmp_path, [{"date": "2026-03-15", "pm_916": 145000.0}])

    inf.main(now=now)

    fc = json.loads((tmp_path / "forecast.json").read_text())

    assert fc["current_22k"] == 14500
    assert fc["est_low"] == 14400  # exactly 100 INR/g below
    assert fc["est_high"] == 14600  # exactly 100 INR/g above
    assert fc["est_high"] - fc["est_low"] == 200  # band width = 2 * residual_std
