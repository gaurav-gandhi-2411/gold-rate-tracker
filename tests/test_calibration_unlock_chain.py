"""End-to-end chain test for the calibration unlock path.

The calibration unlock (~2026-06-12) will be the first time this code path
has ever run in production.  This test walks all six links in a single
sequence, with one assertion per link, to catch silent no-ops before they
hit production.

Chain under test:
  Link 1: run_refit_if_needed() fires when overlap >= 30 → calibration.json
           written with valid=True, real slope/intercept
  Link 2: _build_chronos_companion() receives the calibration dict and sets
           calibration_applied=True
  Link 3: Horizon p50 values are NUMERICALLY TRANSFORMED (slope * v + intercept
           ≠ v when slope≠1 or intercept≠0) — proving the math ran, not just the flag
  Link 4: inference.main() with the refit-produced calibration.json writes
           forecast.json with calibration_applied=True
  Link 5: forecast.json field cc["calibration_applied"] is True — the field
           app.js reads to show "Adjusted to Tanishq prices: Yes"
  Link 6: check_triggers() with the valid calibration fires T6 (once-ever
           "calibration unlocked" notification) when state is fresh

Also tested:
  - calibration_just_unlocked=True appears in forecast.json when inference
    runs on the flip cycle (notification state has no prior T6)
  - T6 is permanently suppressed after first send (lifetime dedup)
  - Quiet-hours edge: T6 queued during quiet hours → stamped (won't re-queue)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import ml.calibration as cal
import ml.inference as inf
from ml.inference import _build_chronos_companion
from ml.notifications import (
    NotificationState,
    _stamp_ist_dedup,
    check_triggers,
    queue_for_quiet_hours,
    send_pending,
)

IST = ZoneInfo("Asia/Kolkata")


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _iso_dates(n: int, start: str = "2026-01-02") -> list[str]:
    base = pd.Timestamp(start)
    return [(base + pd.Timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]


def _write_ibja_parquet(path, dates: list[str]) -> None:
    """Write ibja_rates.parquet with pm_916 in Rs/10g.

    ibja_per_g = 14000 + i*10 → pm_916 stored as (ibja_per_g * 10).
    """
    ibja_per_g = [14000.0 + i * 10 for i in range(len(dates))]
    df = pd.DataFrame({"date": dates, "pm_916": [v * 10 for v in ibja_per_g]})
    df.to_parquet(path, index=False)


def _write_prices_json(path, dates: list[str]) -> None:
    """Write prices.json with tanishq_22k ≈ 1.02 * ibja_per_g + 100.

    Using a non-trivial linear relationship ensures slope≠1 and intercept≠0
    after fitting, so the horizon transform produces values that differ from
    the raw IBJA forecast (Link 3 can assert the math ran).
    """
    readings = [
        {
            "timestamp": f"{d}T12:00:00.000Z",
            "22k": round(1.02 * (14000.0 + i * 10) + 100.0, 2),
            "24k": 15500.0,
            "18k": 12500.0,
            "source": "chain-test",
        }
        for i, d in enumerate(dates)
    ]
    path.write_text(json.dumps(readings))


def _write_stub_calibration(path, valid: bool = False, n_observations: int = 21) -> None:
    path.write_text(
        json.dumps(
            {
                "valid": valid,
                "n_observations": n_observations,
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


def _make_backtest(n_folds: int = 35) -> dict:
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


def _make_probe(ibja_last: float = 14450.0) -> dict:
    """Probe with ibja_forecast p50 values in INR/g — same units as ibja_per_g."""
    return {
        "status": "success",
        "ibja_last_value": ibja_last,
        "ibja_forecast": [
            {"day": d, "p10": ibja_last * 0.98, "p50": ibja_last + d * 30, "p90": ibja_last * 1.02}
            for d in range(1, 6)
        ],
        "model_version": "chronos-bolt-tiny@test",
        "majority_direction": "up",
        "direction_consensus": 0.8,
        "schema_version": 2,
    }


def _ist(year: int, month: int, day: int, hour: int = 14, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=IST)


# ---------------------------------------------------------------------------
# CHAIN TEST — all six links in one sequence
# ---------------------------------------------------------------------------


def test_calibration_unlock_chain_end_to_end(tmp_path, monkeypatch):
    """Walk all six links of the calibration unlock chain in a single test.

    Uses data flowing from one link to the next (refit output → inference input)
    rather than independent mock dicts per link.  Any format mismatch between
    what run_refit_if_needed() writes and what inference.py reads will surface here.
    """
    dates_30 = _iso_dates(30)
    _write_ibja_parquet(tmp_path / "ibja_rates.parquet", dates_30)
    _write_prices_json(tmp_path / "prices.json", dates_30)
    _write_stub_calibration(tmp_path / "calibration.json", valid=False, n_observations=21)
    (tmp_path / "backtest.json").write_text(json.dumps(_make_backtest(35)))
    probe = _make_probe()
    (tmp_path / "chronos_probe.json").write_text(json.dumps(probe))
    # No notification_state.json → load_state returns fresh NotificationState
    # (last_t6_fired_date_ist == "")

    # -- LINK 1: run_refit_if_needed writes calibration.json valid=True --
    result = cal.run_refit_if_needed(data_dir=tmp_path)
    assert result is True, "Link 1 FAIL: run_refit_if_needed() did not report a refit"

    cal_dict = json.loads((tmp_path / "calibration.json").read_text())
    assert cal_dict["valid"] is True, "Link 1 FAIL: calibration.json valid is not True"
    assert cal_dict["slope"] is not None, "Link 1 FAIL: calibration.json slope is None"
    assert cal_dict["intercept"] is not None, "Link 1 FAIL: calibration.json intercept is None"
    assert cal_dict["n_observations"] == 30, "Link 1 FAIL: wrong n_observations"
    slope = cal_dict["slope"]
    intercept = cal_dict["intercept"]

    # -- LINK 2: _build_chronos_companion with the real calibration sets calibration_applied=True --
    state_fresh = NotificationState()  # last_t6_fired_date_ist == ""
    backtest = json.loads((tmp_path / "backtest.json").read_text())
    companion = _build_chronos_companion(probe, backtest, cal_dict, state_fresh)

    assert companion["calibration_applied"] is True, (
        "Link 2 FAIL: _build_chronos_companion did not set calibration_applied=True "
        "with the refit-produced calibration"
    )

    # -- LINK 3: horizon_p50 values are NUMERICALLY TRANSFORMED --
    # raw IBJA p50 day 1: ibja_last + 1*30 = 14480.0
    raw_p50_day1 = probe["ibja_forecast"][0]["p50"]
    transformed_p50_day1 = companion["horizon_p50"][0]
    expected = round(slope * raw_p50_day1 + intercept, 2)
    assert transformed_p50_day1 == pytest.approx(expected, abs=0.05), (
        f"Link 3 FAIL: horizon_p50[0] = {transformed_p50_day1}, "
        f"expected slope*raw+intercept = {expected}"
    )
    assert transformed_p50_day1 != pytest.approx(raw_p50_day1, abs=1.0), (
        "Link 3 FAIL: horizon_p50[0] unchanged — calibration math was a no-op. "
        f"raw={raw_p50_day1}, transformed={transformed_p50_day1}, "
        f"slope={slope}, intercept={intercept}"
    )

    # -- LINK 4: inference.main() with the refit calibration.json writes forecast.json --
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)
    # Redirect STATE_PATH so inference.main() gets a fresh NotificationState
    # (no prior T6 fire → calibration_just_unlocked=True)
    monkeypatch.setattr("ml.notifications.STATE_PATH", tmp_path / "notification_state.json")

    inf.main()

    fc = json.loads((tmp_path / "forecast.json").read_text())
    cc = fc["chronos_companion"]
    assert cc["calibration_applied"] is True, (
        "Link 4 FAIL: forecast.json chronos_companion.calibration_applied is not True"
    )
    assert cc["horizon_p50"] is not None and len(cc["horizon_p50"]) == 5, (
        "Link 4 FAIL: forecast.json chronos_companion.horizon_p50 missing or wrong length"
    )

    # -- LINK 5: app.js reads cc["calibration_applied"] to show "Yes" --
    # JS assertion is not possible in Python; verify the field is present and True
    # (app.js line: cc.calibration_applied ? "Yes" : "Not yet")
    assert "calibration_applied" in cc, (
        'Link 5 FAIL: "calibration_applied" missing from forecast.json chronos_companion — '
        'app.js would show "Not yet" instead of "Yes"'
    )
    assert cc["calibration_applied"] is True, "Link 5 FAIL: calibration_applied is not True"

    # -- BONUS: calibration_just_unlocked appears in forecast.json on the flip cycle --
    assert cc.get("calibration_just_unlocked") is True, (
        "BONUS FAIL: calibration_just_unlocked not True in forecast.json on the flip cycle — "
        "this field is used for process-isolation signalling between inference and T6 trigger"
    )

    # -- LINK 6: T6 fires when check_triggers() sees the valid calibration --
    prices_raw = json.loads((tmp_path / "prices.json").read_text())
    state = NotificationState()  # fresh: last_t6_fired_date_ist == ""
    now_ist = _ist(2026, 6, 12, 10, 0)  # daytime IST, not quiet hours
    alerts = check_triggers(
        forecast={},
        probe=probe,
        prices=prices_raw,
        backtest=backtest,
        state=state,
        now_ist=now_ist,
        calibration=cal_dict,
    )
    t6_alerts = [a for a in alerts if a.trigger_id == "T6"]
    assert len(t6_alerts) == 1, (
        f"Link 6 FAIL: expected exactly 1 T6 alert, got {len(t6_alerts)}. "
        "T6 has never fired in production; if this fails the notification will be silently dropped."
    )
    assert t6_alerts[0].priority == 3, "Link 6 FAIL: T6 priority wrong"
    assert "30" in t6_alerts[0].body, "Link 6 FAIL: n_observations not in T6 body"
    assert "₹" not in t6_alerts[0].body, "Link 6 FAIL: rupee symbol in T6 body (not ASCII-safe)"


# ---------------------------------------------------------------------------
# Link 6 detail: T6 lifetime dedup — does not fire again after send
# ---------------------------------------------------------------------------


def test_t6_lifetime_dedup_after_unlock(monkeypatch):
    """T6 must not fire again on any subsequent run after first send.

    This is the once-ever constraint. If state is persisted correctly, the
    production CI will never send a second T6 — even months later.
    """
    mock_resp = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__ = lambda s: mock_resp
    mock_resp.__exit__ = lambda s, *a: False
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: mock_resp)
    monkeypatch.setenv("NTFY_TOPIC", "chain-test-topic")

    cal_valid = {"valid": True, "n_observations": 30, "slope": 1.02, "intercept": 50.0}
    probe = {"status": "success"}
    prices = [{"timestamp": "2026-06-12T10:00:00.000Z", "22k": 14500, "24k": 15000, "18k": 12000}]
    backtest = {"n_folds": 30, "folds": [], "mae_5d_avg_naive": 249.5}
    state = NotificationState()
    now_ist = _ist(2026, 6, 12)

    # First run: T6 fires
    alerts = check_triggers({}, probe, prices, backtest, state, now_ist, calibration=cal_valid)
    t6 = [a for a in alerts if a.trigger_id == "T6"]
    assert len(t6) == 1, "T6 must fire on first-ever unlock"

    send_pending(t6, state, now_ist)
    assert state.last_t6_fired_date_ist == "2026-06-12"

    # Second run: same day — must not fire
    alerts2 = check_triggers({}, probe, prices, backtest, state, now_ist, calibration=cal_valid)
    assert not any(a.trigger_id == "T6" for a in alerts2), (
        "T6 fired twice same day — lifetime dedup broken"
    )

    # Third run: next day — must still not fire (lifetime, not daily dedup)
    next_day = _ist(2026, 6, 13)
    alerts3 = check_triggers({}, probe, prices, backtest, state, next_day, calibration=cal_valid)
    assert not any(a.trigger_id == "T6" for a in alerts3), (
        "T6 fired on day 2 after initial send — lifetime dedup broken"
    )


# ---------------------------------------------------------------------------
# Link 6 edge: T6 during quiet hours → queued + stamped → does not re-queue
# ---------------------------------------------------------------------------


def test_t6_queued_during_quiet_hours_not_requeued():
    """If the unlock happens during quiet hours, T6 is queued exactly once.

    The stamp-on-queue fix from PR #55 applies to T6 (via _stamp_ist_dedup).
    A second quiet-hours CI run must see the stamp and NOT append another T6.

    Failure here means the once-ever T6 could send twice: once from the queue
    and once from a re-generated alert if the stamp was not set at queue time.
    """
    cal_valid = {"valid": True, "n_observations": 30, "slope": 1.02, "intercept": 50.0}
    probe = {"status": "success"}
    prices = [{"timestamp": "2026-06-12T10:00:00.000Z", "22k": 14500, "24k": 15000, "18k": 12000}]
    backtest = {"n_folds": 30, "folds": [], "mae_5d_avg_naive": 249.5}
    state = NotificationState()
    quiet_ist = _ist(2026, 6, 12, 23, 30)  # 23:30 IST — quiet hours

    # Run 1: quiet hours, T6 eligible
    alerts_r1 = check_triggers({}, probe, prices, backtest, state, quiet_ist, calibration=cal_valid)
    t6_r1 = [a for a in alerts_r1 if a.trigger_id == "T6"]
    assert len(t6_r1) == 1, "T6 must fire on first eligible run (even in quiet hours)"
    assert t6_r1[0].bypass_quiet is False, "T6 must have bypass_quiet=False (queued, not sent)"

    # Simulate what main() does: queue + stamp
    state = queue_for_quiet_hours(t6_r1, state)
    _stamp_ist_dedup("T6", state, quiet_ist)

    assert state.last_t6_fired_date_ist == "2026-06-12", (
        "T6 stamp not set after queue — subsequent quiet-hours run will re-generate T6"
    )
    assert len(state.queued) == 1, "Queue must hold exactly 1 T6"

    # Run 2: still quiet hours — T6 must NOT re-fire (stamp already set)
    quiet_ist_r2 = _ist(2026, 6, 13, 4, 30)  # 04:30 IST next calendar day — still quiet
    alerts_r2 = check_triggers({}, probe, prices, backtest, state, quiet_ist_r2, calibration=cal_valid)
    assert not any(a.trigger_id == "T6" for a in alerts_r2), (
        "T6 re-queued on second quiet-hours run — stamp-on-queue not applied to T6"
    )
    assert len(state.queued) == 1, "Queue must still hold exactly 1 T6 (no duplicate)"


# ---------------------------------------------------------------------------
# Link 3 isolation: calibration math correctness (refit-produced params)
# ---------------------------------------------------------------------------


def test_horizon_values_numerically_transformed_by_refit_calibration(tmp_path):
    """Prove Link 3 in isolation: run_refit_if_needed() params produce non-identity transform.

    Uses the actual slope/intercept produced by fitting real (mock) data, then
    applies them to a probe and asserts the horizon values differ from the raw IBJA values.
    """
    dates_30 = _iso_dates(30)
    _write_ibja_parquet(tmp_path / "ibja_rates.parquet", dates_30)
    _write_prices_json(tmp_path / "prices.json", dates_30)
    _write_stub_calibration(tmp_path / "calibration.json", valid=False)

    cal.run_refit_if_needed(data_dir=tmp_path)
    cal_dict = json.loads((tmp_path / "calibration.json").read_text())

    slope = cal_dict["slope"]
    intercept = cal_dict["intercept"]

    # The test data has tanishq ≈ 1.02*ibja_per_g + 100 → slope≈1.02, intercept≈100
    # These must be non-identity: if slope=1.0 AND intercept=0.0, calibration is a no-op
    assert not (
        pytest.approx(slope, abs=0.001) == 1.0 and pytest.approx(intercept, abs=0.1) == 0.0
    ), (
        "Link 3 data setup error: refit produced identity transform (slope=1, intercept=0). "
        "tanishq and ibja prices are identical in test fixtures — adjust _write_prices_json."
    )

    raw_ibja_p50 = 14450.0  # typical INR/g probe value
    probe = _make_probe(ibja_last=raw_ibja_p50)
    backtest = {"n_folds": 30, "folds": [], "mae_5d_avg_naive": 249.5}
    companion = _build_chronos_companion(probe, backtest, cal_dict, None)

    transformed = companion["horizon_p50"][0]
    raw = probe["ibja_forecast"][0]["p50"]
    expected = round(slope * raw + intercept, 2)

    assert companion["calibration_applied"] is True
    assert transformed == pytest.approx(expected, abs=0.05), (
        f"Transformed value {transformed} != expected {expected} (slope={slope}, intercept={intercept})"
    )
    assert transformed != pytest.approx(raw, abs=1.0), (
        "Calibration produced identity transform in production data — "
        "slope and intercept need investigation"
    )
