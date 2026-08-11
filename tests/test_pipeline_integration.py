"""End-to-end pipeline integration tests: calibration → inference → notifications.

Prevents re-occurrence of four historically silent bugs:
1. calibration refit never called in CI (weeks)
2. commentary consumer miss (8 PRs)
3. IBJA PM-fix write-once (silent)
4. ntfy delivery never verified

Two tests cover the two critical branching paths:
- probe succeeded → calibration math applied → no T5 fire
- probe failed → model_fallback=True → T5 fires
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import ml.calibration as cal
import ml.inference as inf
import ml.sources.grt as grt_mod
import ml.sources.kalyan as kalyan_mod
import ml.sources.malabar as malabar_mod
import pandas as pd
import pytest
from ml.notifications import NotificationState, check_triggers
from ml.sources.base import SourceNetworkError

IST = ZoneInfo("Asia/Kolkata")


def _raise_network(*_a, **_k) -> None:
    raise SourceNetworkError("test: network disabled")


def _disable_fusion(monkeypatch) -> None:
    """Never let a unit test hit the real network via the tier-3 fusion fallback."""
    monkeypatch.setattr(grt_mod, "fetch_grt", _raise_network)
    monkeypatch.setattr(malabar_mod, "fetch_malabar", _raise_network)
    monkeypatch.setattr(kalyan_mod, "fetch_kalyan_city", _raise_network)


# ---------------------------------------------------------------------------
# Shared fixture builders (mirrors patterns in test_calibration_unlock_chain.py)
# ---------------------------------------------------------------------------


def _iso_dates(n: int, start: str = "2026-01-02") -> list[str]:
    base = pd.Timestamp(start)
    return [(base + pd.Timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]


def _write_ibja_parquet(path, dates: list[str]) -> None:
    """Write ibja_rates.parquet with pm_916 in Rs/10g.

    ibja_per_g = 14000 + i*10  →  pm_916 stored as (ibja_per_g * 10).
    """
    ibja_per_g = [14000.0 + i * 10 for i in range(len(dates))]
    df = pd.DataFrame({"date": dates, "pm_916": [v * 10 for v in ibja_per_g]})
    df.to_parquet(path, index=False)


def _write_prices_json(path, dates: list[str]) -> None:
    """Write prices.json with tanishq_22k ≈ 1.02 * ibja_per_g + 100.

    Non-trivial linear relationship ensures slope≠1 and intercept≠0, so
    horizon_p50 values change after calibration (Link 3 math check).
    """
    readings = [
        {
            "timestamp": f"{d}T12:00:00.000Z",
            "22k": round(1.02 * (14000.0 + i * 10) + 100.0, 2),
            "24k": 15500.0,
            "18k": 12500.0,
            "source": "integration-test",
        }
        for i, d in enumerate(dates)
    ]
    path.write_text(json.dumps(readings))


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
    return {
        "n_folds": n_folds,
        "dir_acc_5d_chronos": 0.6,
        "dir_acc_5d_naive": 0.5,
        "mae_5d_avg_naive": 249.5,
        "folds": folds,
    }


def _make_probe(status: str = "success") -> dict:
    """Build chronos_probe.json fixture matching schema_version=2."""
    if status != "success":
        return {
            "status": status,
            "model_version": "amazon/chronos-bolt-tiny@a0e552de",
            "schema_version": 2,
        }
    return {
        "status": "success",
        "ibja_forecast": [
            {"day": d, "p10": 14200.0, "p50": 14500.0 + d * 20, "p90": 14900.0} for d in range(1, 6)
        ],
        "ibja_last_value": 14450.0,
        "model_version": "test",
        "majority_direction": "up",
        "direction_consensus": 1.0,
        "schema_version": 2,
        "num_samples": 5,
        "sample_directions": ["up", "up", "up", "up", "up"],
    }


# ---------------------------------------------------------------------------
# Test 1: calibration applied path — no T5 fire, all consumer fields present
# ---------------------------------------------------------------------------


def test_pipeline_wiring_calibration_applied(tmp_path, monkeypatch):
    """Full pipeline chain: refit → inference → notifications.

    THE WIRING ASSERTION (why this test exists):
    Before Phi5, run_refit_if_needed() was wired to CI but forecast.json never
    reflected calibration_applied=True — the math ran but the flag was never
    written (silent bug for weeks).  This test guards the closed loop:

        run_refit_if_needed() flips calibration.json valid=True
        → inference.main() reads the updated calibration.json
        → forecast.json.chronos_companion.calibration_applied == True
        → horizon_p50[0] ≠ raw ibja p50 (math, not just the flag)
        → check_triggers() sees a healthy probe → no T5 fires
    """
    # -- Setup: 30 days of aligned IBJA + Tanishq data --
    dates_30 = _iso_dates(30)
    _write_ibja_parquet(tmp_path / "ibja_rates.parquet", dates_30)
    _write_prices_json(tmp_path / "prices.json", dates_30)

    # Stub calibration with valid=False, n_observations=21 — will be refitted
    (tmp_path / "calibration.json").write_text(
        json.dumps(
            {
                "valid": False,
                "n_observations": 21,
                "slope": None,
                "intercept": None,
                "residual_std": None,
                "r_squared": None,
                "fit_date": "2026-01-01",
                "huber_epsilon": 1.35,
                "schema_version": 1,
            }
        )
    )
    (tmp_path / "backtest.json").write_text(json.dumps(_make_backtest(35)))
    probe_dict = _make_probe("success")
    (tmp_path / "chronos_probe.json").write_text(json.dumps(probe_dict))

    # -- Step 1: Refit calibration --
    result = cal.run_refit_if_needed(data_dir=tmp_path)
    assert result is True, "run_refit_if_needed() must return True (refit performed)"

    cal_dict = json.loads((tmp_path / "calibration.json").read_text())
    assert cal_dict["valid"] is True, "calibration.json must be valid=True after refit"
    assert cal_dict["slope"] is not None, "calibration.json slope must be set after refit"
    assert cal_dict["intercept"] is not None, "calibration.json intercept must be set after refit"

    # -- Step 2: Run inference with fresh calibration --
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)
    monkeypatch.setattr("ml.notifications.STATE_PATH", tmp_path / "notification_state.json")
    _disable_fusion(monkeypatch)  # fixture IBJA dates read as stale vs wall-clock "now"
    inf.main()

    fc = json.loads((tmp_path / "forecast.json").read_text())
    cc = fc["chronos_companion"]

    # THE KEY ASSERTION — this is the exact bit the Phi5 wiring gap broke:
    assert cc["calibration_applied"] is True, (
        "forecast.json.chronos_companion.calibration_applied must be True after refit. "
        "If this fails, run_refit_if_needed() ran but inference.py did not read the updated "
        "calibration.json — the same silent bug that went undetected for weeks in Phi5."
    )

    # horizon_p50[0] must differ from raw ibja p50 day 1 (math ran, not just flag)
    raw_ibja_p50_day1 = probe_dict["ibja_forecast"][0]["p50"]  # 14520.0 (14500 + 1*20)
    assert cc["horizon_p50"] is not None and len(cc["horizon_p50"]) == 5
    assert cc["horizon_p50"][0] != pytest.approx(raw_ibja_p50_day1, abs=1.0), (
        f"horizon_p50[0]={cc['horizon_p50'][0]} must differ from raw ibja p50={raw_ibja_p50_day1} "
        "— calibration math must transform the values, not just set the flag."
    )

    assert cc["status"] == "success"
    assert cc["direction_consensus"] == pytest.approx(1.0)
    assert cc["majority_direction"] == "up"

    # -- Consumer field presence assertions --
    # app.js's renderHero()/renderMethodology() read (ml/commentary.py, the other
    # former consumer of these same fields, was retired 2026-08-10 — no remaining
    # reader depends on this specific assertion group beyond app.js):
    assert "predicted_22k" in fc, "app.js reads fc.headline?.predicted_22k ?? fc.predicted_22k"
    assert "lower" in fc, "app.js reads fc.headline?.lower ?? fc.lower"
    assert "upper" in fc, "app.js reads fc.headline?.upper ?? fc.upper"
    for field in [
        "status",
        "lean_direction",
        "lean_strength_pct",
        "direction_acc_30f",
        "majority_direction",
        "direction_consensus",
    ]:
        assert field in cc, f"app.js consumer field missing from chronos_companion: {field!r}"

    # app.js reads (lines 509-806):
    for field in [
        "lean_direction",
        "direction_acc_30f",
        "direction_consensus",
        "calibration_applied",
        "model_version",
    ]:
        assert field in cc, f"app.js consumer field missing from chronos_companion: {field!r}"

    # notifications.py reads (lines 455, 603-605):
    assert "model_fallback" in fc, "notifications.py reads fc.get('model_fallback')"
    assert "chronos_companion" in fc, "notifications.py reads fc.get('chronos_companion')"

    # -- Step 3: check_triggers — no T5 should fire (probe was successful) --
    prices_list = json.loads((tmp_path / "prices.json").read_text())
    backtest_dict = json.loads((tmp_path / "backtest.json").read_text())
    state = NotificationState()
    now_ist = datetime(2026, 6, 2, 14, 0, tzinfo=IST)
    alerts = check_triggers(
        forecast=fc,
        probe=probe_dict,
        prices=prices_list,
        backtest=backtest_dict,
        state=state,
        now_ist=now_ist,
        calibration=cal_dict,
    )
    t5_alerts = [a for a in alerts if a.trigger_id == "T5"]
    assert len(t5_alerts) == 0, (
        f"T5 must not fire when probe is successful; got {len(t5_alerts)} T5 alert(s). "
        "Check notifications.py _check_t5 — fires when model_fallback=True OR probe not ok."
    )


# ---------------------------------------------------------------------------
# Test 2: probe failed path — model_fallback=True → T5 fires
# ---------------------------------------------------------------------------


def test_pipeline_wiring_probe_failed_triggers_t5(tmp_path, monkeypatch):
    """Pipeline with failed probe: model_fallback=True in forecast.json, T5 fires.

    Verifies that the inference → notifications contract is correctly wired
    for the degraded path: a failed probe must propagate through forecast.json
    and trigger a T5 alert to notify the operator.
    """
    # -- Setup: minimal prices (inference only needs 30+ folds in backtest) --
    start = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    prices = [
        {
            "timestamp": (start + timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "22k": 14400 + (i % 7) * 20,
            "24k": 15700,
            "18k": 12500,
            "source": "integration-test",
        }
        for i in range(20)
    ]
    (tmp_path / "prices.json").write_text(json.dumps(prices))
    (tmp_path / "backtest.json").write_text(json.dumps(_make_backtest(35)))

    # Probe failed — write stub calibration (NOT refitted this time)
    failed_probe = _make_probe("failed")
    (tmp_path / "chronos_probe.json").write_text(json.dumps(failed_probe))
    cal_stub = {"valid": False, "n_observations": 21, "schema_version": 1}
    (tmp_path / "calibration.json").write_text(json.dumps(cal_stub))

    # -- Step 2: Run inference with failed probe --
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)
    monkeypatch.setattr("ml.notifications.STATE_PATH", tmp_path / "notification_state.json")
    _disable_fusion(monkeypatch)  # calibration invalid -> tier 3 would otherwise be attempted
    inf.main()

    fc = json.loads((tmp_path / "forecast.json").read_text())

    assert fc["model_fallback"] is True, "model_fallback must be True when probe failed"

    cc = fc["chronos_companion"]
    assert cc["status"] == "failed", (
        f"chronos_companion.status must be 'failed', got {cc['status']!r}"
    )
    assert cc["lean_direction"] == "neutral", (
        f"lean_direction must be 'neutral' for failed probe, got {cc['lean_direction']!r}"
    )
    assert cc["direction_acc_30f"] is None, (
        f"direction_acc_30f must be None for failed probe, got {cc['direction_acc_30f']!r}"
    )

    # PI bands must still be present — headline works without probe
    assert fc["predicted_22k"] > 0
    assert fc["lower"] is not None and fc["lower"] > 0
    assert fc["upper"] is not None and fc["upper"] > fc["lower"]

    # -- Step 3: check_triggers — T5 must fire (probe failed) --
    state = NotificationState()
    now_ist = datetime(2026, 6, 2, 14, 0, tzinfo=IST)
    alerts = check_triggers(
        forecast=fc,
        probe=failed_probe,
        prices=prices,
        backtest=_make_backtest(35),
        state=state,
        now_ist=now_ist,
        calibration=cal_stub,
    )
    assert any(a.trigger_id == "T5" for a in alerts), (
        "T5 must fire when probe.status != 'success'. "
        "If this fails, check notifications.py _check_t5 wiring — "
        "the operator will not be alerted when the Chronos probe fails."
    )
