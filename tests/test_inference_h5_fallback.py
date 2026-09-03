"""Tests for Phi22 H5 IBJA-calibrated fallback safety gate.

Tests 1–2 were RED regression tests; they go GREEN once inference.py emits
`price_source`.  Tests 3–8 cover the full fallback logic.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import ml.inference as inf
import ml.sources.grt as grt_mod
import ml.sources.kalyan as kalyan_mod
import ml.sources.malabar as malabar_mod
import pandas as pd
import pytest
from ml.sources.base import SourceNetworkError, SourceReading

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


def _raise_network(*_a: object, **_k: object) -> None:
    raise SourceNetworkError("test: network disabled")


def _disable_fusion(monkeypatch: object) -> None:
    """Monkeypatch all three tier-3 fusion fetchers to fail — never hit the real
    network from a unit test. Tests that want tier 3 to succeed instead patch
    individual fetchers with their own valid-reading stubs.
    """
    monkeypatch.setattr(grt_mod, "fetch_grt", _raise_network)
    monkeypatch.setattr(malabar_mod, "fetch_malabar", _raise_network)
    monkeypatch.setattr(kalyan_mod, "fetch_kalyan_city", _raise_network)


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
    _disable_fusion(monkeypatch)  # calibration.valid=False + stale scrape reaches tier 3

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
    _disable_fusion(monkeypatch)  # calibration.valid=False + stale scrape reaches tier 3

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
    # G1d: no residual_abs_quantiles and only 1 IBJA row (on-the-fly fit needs
    # >= 30 overlap pairs) -> band suppressed, not the old Gaussian residual_std
    # substitute this session measured at 45.3% coverage against 68.3% nominal.
    assert fc["est_low"] is None and fc["est_high"] is None
    assert fc["band_unavailable_reason"] is not None
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
    # G1d: band suppressed (no residual_abs_quantiles, insufficient overlap for
    # an on-the-fly fit) -- the price estimate still activates independently.
    assert fc["est_low"] is None and fc["est_high"] is None
    assert fc["band_unavailable_reason"] is not None


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
    _disable_fusion(monkeypatch)  # tier 3 must also fail for this to stay a true noop

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
    _disable_fusion(monkeypatch)  # tier 3 must also fail for this to stay a true noop

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
    _disable_fusion(monkeypatch)  # tier 3 must also fail for this to stay a true noop

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
    """G1d superseded this test's original premise: residual_std is no longer
    used to size the displayed band at all (that Gaussian-substitution path was
    removed -- session-measured at 45.3% coverage against its own 68.3% nominal
    claim). With no residual_abs_quantiles and only 1 IBJA row (an on-the-fly
    fit needs >= 30 overlap pairs), the correct current behavior is suppression,
    not a residual_std-scaled band -- verifying that instead."""
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
    assert fc["est_low"] is None and fc["est_high"] is None
    assert fc["band_unavailable_reason"] is not None


@pytest.mark.smoke
def test_band_prefers_residual_std_oos_when_present(tmp_path: object, monkeypatch: object) -> None:
    """SUPERSEDED by G1d: ADR 027's residual_std_oos-over-residual_std preference
    applied to a Gaussian-substitute band this session measured at 45.3%
    coverage against its own 68.3% nominal claim -- that whole substitution
    path is now removed, so neither field sizes the band anymore regardless of
    which is present. With no residual_abs_quantiles and only 1 IBJA row (an
    on-the-fly fit needs >= 30 overlap pairs), the correct current behavior is
    suppression -- verifying that instead of the old OOS-preference assertion."""
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)

    now = datetime(2026, 3, 15, 13, 30, tzinfo=UTC)
    last_ts = datetime(2026, 3, 15, 4, 0, tzinfo=UTC)
    prices = _make_prices_with_last_ts(40, last_ts, last_22k=14000)

    (tmp_path / "prices.json").write_text(json.dumps(prices))
    (tmp_path / "backtest.json").write_text(json.dumps(_make_backtest(35)))
    (tmp_path / "chronos_probe.json").write_text(json.dumps(_make_probe("success")))
    (tmp_path / "calibration.json").write_text(
        json.dumps(
            {
                "valid": True,
                "slope": 1.0,
                "intercept": 0.0,
                "residual_std": 100.0,  # neither this nor OOS below sizes the band anymore
                "residual_std_oos": 65.0,
            }
        )
    )
    _make_ibja_parquet(tmp_path, [{"date": "2026-03-15", "pm_916": 145000.0}])

    inf.main(now=now)

    fc = json.loads((tmp_path / "forecast.json").read_text())

    assert fc["current_22k"] == 14500
    assert fc["est_low"] is None and fc["est_high"] is None
    assert fc["band_unavailable_reason"] is not None


# ---------------------------------------------------------------------------
# G1d: fallback priority order when residual_abs_quantiles is absent
# ---------------------------------------------------------------------------


def _make_ibja_parquet_aligned(n: int) -> list[dict]:
    """n daily IBJA rows on the same 2026-01-01-based date sequence
    _make_prices_with_last_ts/_make_prices use, so overlap_count == n."""
    start = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    return [
        {
            "date": (start + timedelta(days=i)).strftime("%Y-%m-%d"),
            "pm_916": 144000.0 + (i % 7) * 200.0,
        }
        for i in range(n)
    ]


@pytest.mark.smoke
def test_no_residual_abs_quantiles_never_uses_residual_std_oos_path(
    tmp_path: object, monkeypatch: object
) -> None:
    """G1d, the required regression test: a calibration file lacking
    residual_abs_quantiles must NEVER produce a band via the old
    residual_std_oos path, regardless of how large residual_std_oos is or
    whether an on-the-fly fit is possible. Two sub-cases in one test:
    insufficient overlap data (suppression) and, separately, band_method is
    asserted to never equal "residual_std_oos" in either case."""
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)

    now = datetime(2026, 3, 15, 13, 30, tzinfo=UTC)
    last_ts = datetime(2026, 3, 15, 4, 0, tzinfo=UTC)  # stale
    prices = _make_prices_with_last_ts(40, last_ts, last_22k=14000)
    calibration = {
        "valid": True,
        "slope": 1.0,
        "intercept": 0.0,
        "residual_std": 100.0,
        "residual_std_oos": 65.0,  # present, large enough to notice if it leaked through
        # residual_abs_quantiles deliberately absent
    }

    (tmp_path / "prices.json").write_text(json.dumps(prices))
    (tmp_path / "backtest.json").write_text(json.dumps(_make_backtest(35)))
    (tmp_path / "chronos_probe.json").write_text(json.dumps(_make_probe("success")))
    (tmp_path / "calibration.json").write_text(json.dumps(calibration))
    _make_ibja_parquet(tmp_path, [{"date": "2026-03-15", "pm_916": 145000.0}])  # only 1 row

    inf.main(now=now)
    fc = json.loads((tmp_path / "forecast.json").read_text())

    assert fc["band_method"] != "residual_std_oos"
    assert fc["band_method"] != "residual_std_in_sample"
    assert fc["est_low"] is None and fc["est_high"] is None
    assert fc["band_unavailable_reason"] is not None
    assert (
        fc["nominal_coverage"] is None
    )  # priority 3: never a band with nominal_coverage set to a lie


@pytest.mark.smoke
def test_on_the_fly_fit_used_when_residual_abs_quantiles_absent_but_overlap_data_sufficient(
    tmp_path: object, monkeypatch: object
) -> None:
    """G1d priority 1: calibration.json lacks residual_abs_quantiles, but the raw
    ibja_rates.parquet + prices.json overlap has >= 30 pairs -- an on-the-fly fit
    must be used instead of suppression, and must never fall back to
    residual_std_oos either."""
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)

    now = datetime(2026, 2, 10, 13, 30, tzinfo=UTC)
    last_ts = datetime(2026, 2, 10, 4, 0, tzinfo=UTC)  # stale
    prices = _make_prices_with_last_ts(40, last_ts, last_22k=14000)
    ibja_rows = _make_ibja_parquet_aligned(35)  # >= 30 overlap pairs with prices above

    (tmp_path / "prices.json").write_text(json.dumps(prices))
    (tmp_path / "backtest.json").write_text(json.dumps(_make_backtest(35)))
    (tmp_path / "chronos_probe.json").write_text(json.dumps(_make_probe("success")))
    (tmp_path / "calibration.json").write_text(
        json.dumps(
            {
                "valid": True,
                "slope": 1.0,
                "intercept": 0.0,
                "residual_std": 100.0,
                "residual_std_oos": 65.0,
                # residual_abs_quantiles deliberately absent -- forces the fallback
            }
        )
    )
    _make_ibja_parquet(tmp_path, ibja_rows)

    inf.main(now=now)
    fc = json.loads((tmp_path / "forecast.json").read_text())

    assert fc["price_source"] == "ibja_calibrated"
    assert fc["band_method"] == "empirical_quantile_on_the_fly"
    assert fc["band_method"] != "residual_std_oos"
    assert fc["nominal_coverage"] is not None
    assert fc["est_low"] is not None and fc["est_high"] is not None
    assert fc["est_low"] < fc["current_22k"] < fc["est_high"]
    assert fc["band_unavailable_reason"] is None
    assert "on_the_fly" in fc["residual_quantile_source"]
    # ml.calibration.CALIBRATION_JSON must NOT have been written by this
    # read-path fit -- the on-the-fly fit is read-only.
    on_disk_calibration = json.loads((tmp_path / "calibration.json").read_text())
    assert "residual_abs_quantiles" not in on_disk_calibration


# schema_version 1: pre-ADR-027, no half_life/OOS fields at all.
_SCHEMA_1_CALIBRATION = {
    "slope": 1.0,
    "intercept": 0.0,
    "fit_date": "2026-03-01",
    "n_observations": 40,
    "residual_std": 90.0,
    "r_squared": 0.95,
    "huber_epsilon": 1.35,
    "valid": True,
    "schema_version": 1,
}
# schema_version 2: ADR 027 (half_life + OOS fields), pre-#1223 -- no
# residual_abs_quantiles. This is what data/calibration.json ACTUALLY
# contains on master right now (fit_date 2026-08-24) -- the exact scenario
# the new empirical-quantile band code will first run unattended against.
_SCHEMA_2_CALIBRATION = {
    "slope": 1.0,
    "intercept": 0.0,
    "fit_date": "2026-08-24",
    "n_observations": 72,
    "residual_std": 78.75,
    "r_squared": 0.975,
    "huber_epsilon": 1.35,
    "half_life": 10.0,
    "r_squared_oos": 0.99,
    "residual_std_oos": 65.0,
    "mae_oos": 36.22,
    "n_oos": 42,
    "oos_method": "expanding_window_walk_forward_recency_weighted",
    "valid": True,
    "schema_version": 2,
}
# schema_version 3: #1223 -- adds residual_abs_quantiles, the new band's
# preferred (empirical_quantile) source.
_SCHEMA_3_CALIBRATION = {
    **_SCHEMA_2_CALIBRATION,
    "residual_abs_quantiles": {"68": 60.0, "80": 80.0, "90": 100.0},
    "schema_version": 3,
}


@pytest.mark.smoke
@pytest.mark.parametrize(
    "schema_calibration,expect_band",
    [
        (_SCHEMA_1_CALIBRATION, False),
        (_SCHEMA_2_CALIBRATION, False),
        (_SCHEMA_3_CALIBRATION, True),
    ],
    ids=["schema_v1_legacy", "schema_v2_current_on_disk", "schema_v3_post_refit"],
)
def test_tier2_band_complete_for_every_reachable_schema_version(
    tmp_path: object, monkeypatch: object, schema_calibration: dict, expect_band: bool
) -> None:
    """F1d/G1d (session dated 2026-08-28): the tier-2 PRICE ESTIMATE must
    activate against every calibration.json schema currently reachable on
    master -- but whether a BAND accompanies it now correctly depends on
    whether an empirically-validated one can be produced.

    Originally (F1d) this test asserted every schema produces a non-None
    band, including via the old residual_std_in_sample/residual_std_oos
    Gaussian substitution for schema_version 1/2. G1d found that
    substitution measured 45.3% empirical coverage against its own 68.3%
    nominal claim and removed it. With this fixture's minimal 1-row IBJA
    parquet (insufficient for G1d's on-the-fly-fit fallback, which needs
    >= 30 overlap pairs), schema 1/2 now correctly SUPPRESS the band rather
    than substitute an unreliable one -- schema 3 (has
    residual_abs_quantiles) still produces a real empirical_quantile band.
    The price estimate itself (current_22k) activates in every case
    regardless -- band availability is a separate concern from price
    availability (see app.js's isEstimateTier/hasBand split, #1237)."""
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)

    now = datetime(2026, 3, 15, 13, 30, tzinfo=UTC)
    last_ts = datetime(2026, 3, 15, 4, 0, tzinfo=UTC)  # stale -- forces tier 2
    prices = _make_prices_with_last_ts(40, last_ts, last_22k=14000)

    (tmp_path / "prices.json").write_text(json.dumps(prices))
    (tmp_path / "backtest.json").write_text(json.dumps(_make_backtest(35)))
    (tmp_path / "chronos_probe.json").write_text(json.dumps(_make_probe("success")))
    (tmp_path / "calibration.json").write_text(json.dumps(schema_calibration))
    _make_ibja_parquet(tmp_path, [{"date": "2026-03-15", "pm_916": 145000.0}])

    inf.main(now=now)

    fc = json.loads((tmp_path / "forecast.json").read_text())

    assert fc["price_source"] == "ibja_calibrated"
    assert fc["current_22k"] is not None  # the price estimate always activates
    assert fc["ibja_asof"] is not None
    assert fc["freshness_stratum"] is not None

    if expect_band:
        assert fc["est_low"] is not None
        assert fc["est_high"] is not None
        assert fc["est_low"] < fc["current_22k"] < fc["est_high"]
        assert fc["band_method"] == "empirical_quantile"
        assert fc["nominal_coverage"] is not None
        assert fc["residual_quantile_source"] is not None
        assert fc["band_unavailable_reason"] is None
    else:
        assert fc["est_low"] is None
        assert fc["est_high"] is None
        assert fc["band_method"] is None
        assert fc["nominal_coverage"] is None
        assert fc["band_unavailable_reason"] is not None


# ---------------------------------------------------------------------------
# Tier 3: fusion-consensus fallback (feat/fusion-fallback-tier)
# ---------------------------------------------------------------------------


def _fake_kalyan_raw(rate_22k: float) -> object:
    """Build a stand-in for KalyanRawReading — only `.reading` is consumed."""
    import types

    return types.SimpleNamespace(
        reading=SourceReading(
            source="kalyan",
            city="Bangalore",
            rate_22k=rate_22k,
            observed_at=datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
            attribution="Kalyan Jewellers — BENGALURU board rate",
        )
    )


def _fake_national_reading(source: str, rate_22k: float) -> SourceReading:
    return SourceReading(
        source=source,
        city=None,
        rate_22k=rate_22k,
        observed_at=datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
        attribution=f"{source} — national board rate (test fixture)",
    )


@pytest.mark.smoke
def test_fusion_fallback_fires_when_ibja_also_fails(tmp_path: object, monkeypatch: object) -> None:
    """Tanishq stale + IBJA parquet missing + all 3 fusion sources succeed →
    tier 3 fusion_consensus serves a sensible, wider-banded estimate."""
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
    # ibja_rates.parquet intentionally absent — tier 2 must fail so tier 3 fires.

    monkeypatch.setattr(grt_mod, "fetch_grt", lambda: _fake_national_reading("grt", 14000.0))
    monkeypatch.setattr(
        malabar_mod, "fetch_malabar", lambda: _fake_national_reading("malabar", 14100.0)
    )
    monkeypatch.setattr(kalyan_mod, "fetch_kalyan_city", lambda _city: _fake_kalyan_raw(14080.0))

    from ml.fusion import fuse_city_price, fuse_national_benchmark

    national = fuse_national_benchmark(
        [_fake_national_reading("grt", 14000.0), _fake_national_reading("malabar", 14100.0)]
    )
    city_fused = fuse_city_price(_fake_kalyan_raw(14080.0).reading, national, city="Bangalore")
    expected_current = round(city_fused.value)
    expected_low = round(city_fused.value - city_fused.band_half_width)
    expected_high = round(city_fused.value + city_fused.band_half_width)

    inf.main(now=now)

    fc = json.loads((tmp_path / "forecast.json").read_text())

    assert fc["price_source"] == "fusion_consensus"
    assert fc["current_22k"] == expected_current
    assert fc["fusion_sources"] == ["grt", "malabar", "kalyan"]
    assert fc["est_low"] < fc["current_22k"] < fc["est_high"]
    assert fc["est_low"] == expected_low
    assert fc["est_high"] == expected_high
    # BASE_BAND_PCT=0.01 on a ~14000 price ≈ ±140 -- comfortably wider than a
    # typical in-sample IBJA residual_std (~50-160 observed in this repo's
    # calibration history); a concrete, unambiguous width floor.
    assert fc["est_high"] - fc["est_low"] > 100


@pytest.mark.smoke
def test_fusion_fallback_partial_sources(tmp_path: object, monkeypatch: object) -> None:
    """Only GRT succeeds; Malabar and Kalyan fail → still serves fusion_consensus,
    fusion_sources reflects exactly what actually contributed."""
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)

    now = datetime(2026, 3, 15, 13, 30, tzinfo=UTC)
    last_ts = datetime(2026, 3, 15, 4, 0, tzinfo=UTC)
    prices = _make_prices_with_last_ts(40, last_ts, last_22k=14320)

    (tmp_path / "prices.json").write_text(json.dumps(prices))
    (tmp_path / "backtest.json").write_text(json.dumps(_make_backtest(35)))
    (tmp_path / "chronos_probe.json").write_text(json.dumps(_make_probe("success")))
    (tmp_path / "calibration.json").write_text(
        json.dumps({"valid": True, "slope": 1.0, "intercept": 100.0, "residual_std": 50.0})
    )
    # ibja_rates.parquet intentionally absent.

    monkeypatch.setattr(grt_mod, "fetch_grt", lambda: _fake_national_reading("grt", 14000.0))
    monkeypatch.setattr(malabar_mod, "fetch_malabar", _raise_network)
    monkeypatch.setattr(kalyan_mod, "fetch_kalyan_city", _raise_network)

    inf.main(now=now)

    fc = json.loads((tmp_path / "forecast.json").read_text())

    assert fc["price_source"] == "fusion_consensus"
    assert fc["fusion_sources"] == ["grt"]
    assert fc["current_22k"] == 14000


@pytest.mark.smoke
def test_fusion_fallback_all_sources_fail_last_resort(
    tmp_path: object, monkeypatch: object
) -> None:
    """IBJA AND all 3 fusion sources fail → true last resort, unchanged behaviour:
    serves the last-known Tanishq price with fusion_sources=None."""
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)
    _disable_fusion(monkeypatch)

    now = datetime(2026, 3, 15, 13, 30, tzinfo=UTC)
    last_ts = datetime(2026, 3, 15, 4, 0, tzinfo=UTC)
    prices = _make_prices_with_last_ts(40, last_ts, last_22k=14320)

    (tmp_path / "prices.json").write_text(json.dumps(prices))
    (tmp_path / "backtest.json").write_text(json.dumps(_make_backtest(35)))
    (tmp_path / "chronos_probe.json").write_text(json.dumps(_make_probe("success")))
    (tmp_path / "calibration.json").write_text(
        json.dumps({"valid": True, "slope": 1.0, "intercept": 100.0, "residual_std": 50.0})
    )
    # ibja_rates.parquet intentionally absent.

    inf.main(now=now)

    fc = json.loads((tmp_path / "forecast.json").read_text())

    assert fc["price_source"] == "tanishq_scrape"
    assert fc.get("fusion_sources") is None


@pytest.mark.smoke
def test_tier1_fresh_scrape_never_calls_fusion(tmp_path: object, monkeypatch: object) -> None:
    """Fresh Tanishq scrape → tier 3 must never even be attempted (lazy/conditional,
    not fetched every cycle)."""
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)

    def _fail_if_called(*_a: object, **_k: object) -> None:
        pytest.fail("fusion fetcher must not be called when Tanishq scrape is fresh")

    monkeypatch.setattr(grt_mod, "fetch_grt", _fail_if_called)
    monkeypatch.setattr(malabar_mod, "fetch_malabar", _fail_if_called)
    monkeypatch.setattr(kalyan_mod, "fetch_kalyan_city", _fail_if_called)

    now = datetime(2026, 3, 15, 13, 30, tzinfo=UTC)
    last_ts = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)  # 1.5h before now → fresh
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
