"""WS1 — three-layer scraper degradation chain (cross-layer integration).

Failure mode #3 from the orchestrator brief: "Both Worker AND Playwright miss in
a cycle". Per ADR 025, Tanishq being blocked is now the EXPECTED steady state
(sustained Cloudflare block) and IBJA is the PRIMARY source — this file proves
the two downstream layers agree on what's actually alertable:

  - ml.inference      -> H5 IBJA-calibrated estimate (price_source field),
                         active whenever IBJA has a usable reading, not just as
                         an occasional fallback.
  - ml.notifications  -> T9 "IBJA data stale" alert, driven by the IBJA
                         business-day gap (ml.ibja.compute_ibja_gap_business_days),
                         NOT by Tanishq's scrape freshness. A Tanishq-blocked
                         cycle with IBJA fresh (or on its normal weekend
                         carry-forward) must NOT alert — that's the new normal.

These layers have unit tests of their own (test_inference_h5_fallback.py,
test_notifications.py). This file is deliberately an integration test: it runs
the real inference.main() to write forecast.json, then computes the matching
IBJA gap the same way main() does, and feeds both into the real
notifications.check_triggers(), asserting the two layers agree.

Self-contained fixtures (norm: do NOT import helpers from sibling test modules).
"""

from __future__ import annotations

import json
import types
from datetime import UTC, datetime, timedelta, timezone

import ml.inference as inf
import ml.sources.grt as grt_mod
import ml.sources.kalyan as kalyan_mod
import ml.sources.malabar as malabar_mod
import pandas as pd
import pytest
from ml.ibja import compute_ibja_gap_business_days
from ml.notifications import NotificationState, check_triggers
from ml.sources.base import SourceNetworkError, SourceReading

IST = timezone(timedelta(hours=5, minutes=30))


def _raise_network(*_a, **_k) -> None:
    raise SourceNetworkError("test: network disabled")


def _disable_fusion(monkeypatch) -> None:
    """Never let a unit test hit the real network via the tier-3 fusion fallback."""
    monkeypatch.setattr(grt_mod, "fetch_grt", _raise_network)
    monkeypatch.setattr(malabar_mod, "fetch_malabar", _raise_network)
    monkeypatch.setattr(kalyan_mod, "fetch_kalyan_city", _raise_network)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _prices_with_last_ts(n: int, last_ts: datetime, last_22k: int = 14320) -> list[dict]:
    """n price entries; the final entry carries last_ts / last_22k exactly."""
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


def _backtest(n_folds: int = 35) -> dict:
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
    return {
        "n_folds": n_folds,
        "mae_5d_avg_naive": 249.5,
        "mae_5d_avg_chronos": 275.0,
        "folds": folds,
    }


def _probe_flat(last: float = 14000.0) -> dict:
    """A flat probe so notifications' directional triggers (T1/T2) stay silent;
    we are only asserting on T9 here."""
    return {
        "status": "success",
        "ibja_last_value": last,
        "ibja_forecast": [
            {"day": d, "p10": last * 0.98, "p50": last, "p90": last * 1.02} for d in range(1, 6)
        ],
        "majority_direction": "flat",
        "direction_consensus": 1.0,
        "num_samples": 5,
        "model_version": "amazon/chronos-bolt-tiny@test",
    }


def _write_inputs(
    tmp_path, prices: list[dict], calibration: dict, ibja_rows: list[dict] | None
) -> None:
    (tmp_path / "prices.json").write_text(json.dumps(prices))
    (tmp_path / "backtest.json").write_text(json.dumps(_backtest(35)))
    (tmp_path / "chronos_probe.json").write_text(json.dumps(_probe_flat()))
    (tmp_path / "calibration.json").write_text(json.dumps(calibration))
    if ibja_rows is not None:
        pd.DataFrame(ibja_rows).to_parquet(tmp_path / "ibja_rates.parquet", index=False)


_VALID_CAL = {"valid": True, "slope": 1.0, "intercept": 100.0, "residual_std": 50.0}


# ---------------------------------------------------------------------------
# Scenario 1 — Tanishq blocked, IBJA fresh: the new normal (ADR 025).
# H5 fires; T9 must NOT — a Tanishq-blocked cycle with healthy IBJA is expected.
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_tanishq_blocked_ibja_fresh_h5_fires_t9_silent(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)

    now = datetime(2026, 3, 15, 13, 30, tzinfo=UTC)  # IBJA (11:30 UTC) is 2h old -> fresh
    last_ts = datetime(2026, 3, 15, 4, 0, tzinfo=UTC)  # 9.5h before now -> stale (expected)
    prices = _prices_with_last_ts(40, last_ts, last_22k=14320)
    # ibja_per_g = 144000/10 = 14400; calibrated = round(1.0*14400 + 100) = 14500
    ibja_rows = [{"date": "2026-03-15", "pm_916": 144000.0}]
    _write_inputs(tmp_path, prices, _VALID_CAL, ibja_rows)

    # Layer A: inference H5 estimate — now the primary display path.
    inf.main(now=now)
    fc = json.loads((tmp_path / "forecast.json").read_text())

    assert fc["price_source"] == "ibja_calibrated", (
        "H5 must serve the IBJA estimate on a Tanishq-blocked cycle"
    )
    assert fc["current_22k"] == 14500, (
        "user sees the calibrated live estimate, not the stale scrape"
    )
    assert fc["current_22k"] is not None and fc["current_22k"] > 0, "price must never be dead"
    # _VALID_CAL has no residual_abs_quantiles and only 1 IBJA row (an on-the-fly
    # fit needs >= 30 overlap pairs) -- G1d: suppress the band rather than
    # silently substitute the old Gaussian residual_std fallback this session
    # measured at 45.3% coverage against its own 68.3% nominal claim.
    assert fc["est_low"] is None and fc["est_high"] is None
    assert fc["band_unavailable_reason"] is not None

    # Layer B: notifications T9, driven off the IBJA gap (not Tanishq's staleness).
    now_ist = now.astimezone(IST)
    ibja_gap = compute_ibja_gap_business_days(now_ist, tmp_path / "ibja_rates.parquet")
    assert ibja_gap == 0, "IBJA published today — gap must be 0"
    alerts = check_triggers(
        fc,
        _probe_flat(),
        prices,
        _backtest(35),
        NotificationState(),
        now_ist,
        ibja_gap_days=ibja_gap,
    )
    assert all(a.trigger_id != "T9" for a in alerts), (
        "T9 must NOT fire — Tanishq-blocked-with-fresh-IBJA is the expected steady state"
    )


# ---------------------------------------------------------------------------
# Scenario 2 — Tanishq blocked, IBJA on normal weekend carry-forward.
# H5 still fires (dated to Friday); T9 still must NOT fire — the weekend gap is 0.
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_tanishq_blocked_ibja_weekend_carry_forward_t9_silent(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)

    now = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)  # Sunday noon
    last_ts = datetime(2026, 3, 15, 3, 0, tzinfo=UTC)  # 9h before now -> stale (expected)
    scraped_22k = 14320
    prices = _prices_with_last_ts(40, last_ts, last_22k=scraped_22k)
    # IBJA row is Friday 2026-03-13 (asof 11:30 UTC) -> 48.5h old, but well within the
    # 14-day display backstop — this is normal weekend carry-forward, not staleness.
    ibja_rows = [{"date": "2026-03-13", "pm_916": 144000.0}]
    _write_inputs(tmp_path, prices, _VALID_CAL, ibja_rows)

    inf.main(now=now)
    fc = json.loads((tmp_path / "forecast.json").read_text())

    assert fc["price_source"] == "ibja_calibrated", (
        "weekend carry-forward must still serve the IBJA-calibrated estimate"
    )
    assert fc["current_22k"] == 14500
    assert fc["ibja_asof"] == "2026-03-13T11:30:00+00:00", "must carry Friday's date"

    now_ist = now.astimezone(IST)
    ibja_gap = compute_ibja_gap_business_days(now_ist, tmp_path / "ibja_rates.parquet")
    assert ibja_gap == 0, "Fri->Sun is 0 business days — weekends never count toward the gap"
    alerts = check_triggers(
        fc,
        _probe_flat(),
        prices,
        _backtest(35),
        NotificationState(),
        now_ist,
        ibja_gap_days=ibja_gap,
    )
    assert all(a.trigger_id != "T9" for a in alerts), (
        "T9 must NOT fire on a normal weekend carry-forward"
    )


# ---------------------------------------------------------------------------
# Scenario 3 — Tanishq blocked AND IBJA genuinely stale (beyond the 14-day
# backstop): H5 cannot activate, falls to last real price; T9 DOES fire.
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_both_sources_stale_falls_to_last_real_price_t9_fires(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)
    _disable_fusion(monkeypatch)  # tier 3 must also fail for this to stay "everything is dead"

    now = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)
    last_ts = datetime(2026, 3, 15, 3, 0, tzinfo=UTC)  # 9h before now -> stale
    scraped_22k = 14320
    prices = _prices_with_last_ts(40, last_ts, last_22k=scraped_22k)
    # 2026-02-23 -> 20 days before now -> beyond the 14-day backstop -> H5 falls through.
    ibja_rows = [{"date": "2026-02-23", "pm_916": 144000.0}]
    _write_inputs(tmp_path, prices, _VALID_CAL, ibja_rows)

    inf.main(now=now)
    fc = json.loads((tmp_path / "forecast.json").read_text())

    # H5 cannot activate, but the honest fallback is the LAST REAL scraped price —
    # the PWA shows it under a "last confirmed price" banner. It is never null/dead.
    assert fc["price_source"] == "tanishq_scrape"
    assert fc["current_22k"] == scraped_22k, "must serve the last real price, not a dead value"
    assert fc["current_22k"] is not None and fc["current_22k"] > 0
    assert fc.get("est_low") is None and fc.get("est_high") is None

    # T9 fires — a genuine multi-week IBJA outage is exactly what it exists to catch.
    now_ist = now.astimezone(IST)
    ibja_gap = compute_ibja_gap_business_days(now_ist, tmp_path / "ibja_rates.parquet")
    assert ibja_gap is not None and ibja_gap >= 2
    alerts = check_triggers(
        fc,
        _probe_flat(),
        prices,
        _backtest(35),
        NotificationState(),
        now_ist,
        ibja_gap_days=ibja_gap,
    )
    assert any(a.trigger_id == "T9" for a in alerts), (
        "T9 must fire on a genuine multi-week IBJA outage, even though H5 cannot activate"
    )


# ---------------------------------------------------------------------------
# Scenario 4 — everything fresh: negative control, neither layer activates.
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_fresh_scrape_no_h5_no_t9(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)

    now = datetime(2026, 3, 15, 13, 30, tzinfo=UTC)
    last_ts = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)  # 1.5h old -> fresh
    prices = _prices_with_last_ts(40, last_ts, last_22k=14320)
    ibja_rows = [{"date": "2026-03-15", "pm_916": 144000.0}]
    _write_inputs(tmp_path, prices, _VALID_CAL, ibja_rows)

    inf.main(now=now)
    fc = json.loads((tmp_path / "forecast.json").read_text())
    assert fc["price_source"] == "tanishq_scrape"
    assert fc["current_22k"] == 14320

    now_ist = now.astimezone(IST)
    ibja_gap = compute_ibja_gap_business_days(now_ist, tmp_path / "ibja_rates.parquet")
    alerts = check_triggers(
        fc,
        _probe_flat(),
        prices,
        _backtest(35),
        NotificationState(),
        now_ist,
        ibja_gap_days=ibja_gap,
    )
    assert all(a.trigger_id != "T9" for a in alerts), "T9 must not fire when everything is fresh"


# ---------------------------------------------------------------------------
# Scenario 5 — Tanishq stale AND IBJA genuinely stale, but tier-3 fusion
# consensus succeeds: the user sees a reasonable live estimate, and T9 (its
# own IBJA-parquet-gap signal) AND T11 (this-cycle fusion-fallback signal)
# both fire — proving the two alerts are orthogonal, not mutually exclusive.
# ---------------------------------------------------------------------------


def _fake_national_reading(source: str, rate_22k: float) -> SourceReading:
    return SourceReading(
        source=source,
        city=None,
        rate_22k=rate_22k,
        observed_at=datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
        attribution=f"{source} — national board rate (test fixture)",
    )


def _fake_kalyan_raw(rate_22k: float) -> object:
    """Stand-in for KalyanRawReading — only `.reading` is consumed by _try_fusion_fallback."""
    return types.SimpleNamespace(
        reading=SourceReading(
            source="kalyan",
            city="Bangalore",
            rate_22k=rate_22k,
            observed_at=datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
            attribution="Kalyan Jewellers — BENGALURU board rate",
        )
    )


@pytest.mark.smoke
def test_ibja_stale_fusion_succeeds_serves_consensus_t9_still_fires_on_its_own_schedule(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)

    now = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)
    last_ts = datetime(2026, 3, 15, 3, 0, tzinfo=UTC)  # 9h before now -> stale
    scraped_22k = 14320
    prices = _prices_with_last_ts(40, last_ts, last_22k=scraped_22k)
    # Same fixture as scenario 3: 20 days before now -> beyond the 14-day backstop.
    ibja_rows = [{"date": "2026-02-23", "pm_916": 144000.0}]
    _write_inputs(tmp_path, prices, _VALID_CAL, ibja_rows)

    monkeypatch.setattr(grt_mod, "fetch_grt", lambda: _fake_national_reading("grt", 14000.0))
    monkeypatch.setattr(
        malabar_mod, "fetch_malabar", lambda: _fake_national_reading("malabar", 14100.0)
    )
    monkeypatch.setattr(kalyan_mod, "fetch_kalyan_city", lambda _city: _fake_kalyan_raw(14080.0))

    inf.main(now=now)
    fc = json.loads((tmp_path / "forecast.json").read_text())

    assert fc["price_source"] == "fusion_consensus", (
        "tier 3 must serve the live consensus estimate when both Tanishq and IBJA fail"
    )
    assert fc["fusion_sources"] == ["grt", "malabar", "kalyan"]

    # T9 fires — driven purely by IBJA's own parquet gap, independent of which
    # display tier is currently active.
    now_ist = now.astimezone(IST)
    ibja_gap = compute_ibja_gap_business_days(now_ist, tmp_path / "ibja_rates.parquet")
    assert ibja_gap is not None and ibja_gap >= 2
    alerts = check_triggers(
        fc,
        _probe_flat(),
        prices,
        _backtest(35),
        NotificationState(),
        now_ist,
        ibja_gap_days=ibja_gap,
    )
    assert any(a.trigger_id == "T9" for a in alerts), (
        "T9 must still fire on its own IBJA-gap schedule, even though the user "
        "is seeing a reasonable fusion estimate rather than a frozen price"
    )
    assert any(a.trigger_id == "T11" for a in alerts), (
        "T11 must fire because the site is serving fusion_consensus this cycle"
    )
