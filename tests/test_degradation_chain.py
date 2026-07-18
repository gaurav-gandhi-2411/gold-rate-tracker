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
from datetime import UTC, datetime, timedelta, timezone

import ml.inference as inf
import pandas as pd
import pytest
from ml.ibja import compute_ibja_gap_business_days
from ml.notifications import NotificationState, check_triggers

IST = timezone(timedelta(hours=5, minutes=30))


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
    assert fc["est_low"] == 14450 and fc["est_high"] == 14550

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
