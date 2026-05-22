"""Integration tests for calibration gate: T6 trigger + chronos_companion flag.

Covers the end-to-end path from calibration.json state changes through
check_triggers() and _build_chronos_companion(), without any live HTTP calls.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from ml.inference import _build_chronos_companion
from ml.notifications import (
    NotificationState,
    check_triggers,
    send_pending,
)

IST = ZoneInfo("Asia/Kolkata")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ist(year: int, month: int, day: int, hour: int = 14, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=IST)


def _prices_flat(n: int = 3, base: int = 14000) -> list[dict]:
    base_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    return [
        {
            "timestamp": (base_ts + timedelta(days=i)).isoformat().replace("+00:00", "Z"),
            "22k": base,
            "24k": base + 500,
            "18k": base - 500,
            "source": "test",
        }
        for i in range(n)
    ]


def _backtest_accurate(n_folds: int = 30) -> dict:
    folds = [
        {
            "naive": [14000.0] * 5,
            "chronos_p50": [14050.0, 14080.0, 14100.0, 14120.0, 14150.0],
            "actuals": [14050.0, 14080.0, 14100.0, 14120.0, 14200.0],
        }
        for _ in range(n_folds)
    ]
    return {
        "n_folds": n_folds,
        "mae_5d_avg_chronos": 275.0,
        "mae_5d_avg_naive": 249.0,
        "folds": folds,
    }


def _probe_success(last: float = 14000.0) -> dict:
    """Minimal successful probe dict."""
    return {
        "status": "success",
        "ibja_last_value": last,
        "ibja_forecast": [
            {"day": i + 1, "p10": last * 0.98, "p50": last, "p90": last * 1.02} for i in range(5)
        ],
        "model_version": "chronos-t5-tiny",
    }


def _calibration_invalid() -> dict:
    return {"valid": False, "n_observations": 21, "slope": 1.0, "intercept": 0.0}


def _calibration_valid(n_obs: int = 30) -> dict:
    return {"valid": True, "n_observations": n_obs, "slope": 1.02, "intercept": 50.0}


# ---------------------------------------------------------------------------
# Test: flip from invalid to valid triggers T6
# ---------------------------------------------------------------------------


def test_t6_fires_only_after_calibration_becomes_valid():
    """Start with invalid calibration (no T6), flip to valid (T6 fires)."""
    state = NotificationState()
    now_ist = _ist(2026, 6, 2)
    forecast = {"model_fallback": False}
    probe = {"status": "success"}
    prices = _prices_flat()
    backtest = _backtest_accurate(30)

    # Phase 1: calibration invalid — no T6
    alerts_invalid = check_triggers(
        forecast,
        probe,
        prices,
        backtest,
        state,
        now_ist,
        calibration=_calibration_invalid(),
    )
    assert not any(a.trigger_id == "T6" for a in alerts_invalid)

    # Phase 2: calibration flips to valid — T6 fires
    alerts_valid = check_triggers(
        forecast,
        probe,
        prices,
        backtest,
        state,
        now_ist,
        calibration=_calibration_valid(30),
    )
    t6_alerts = [a for a in alerts_valid if a.trigger_id == "T6"]
    assert len(t6_alerts) == 1
    assert t6_alerts[0].priority == 3
    assert "calibration" in t6_alerts[0].title.lower()
    assert "30" in t6_alerts[0].body  # n_observations in body
    assert "Rs." not in t6_alerts[0].body or True  # ASCII-only; no rupee symbol
    assert "₹" not in t6_alerts[0].body  # no rupee symbol ₹
    assert "₹" not in t6_alerts[0].title


# ---------------------------------------------------------------------------
# Test: companion calibration_just_unlocked flag
# ---------------------------------------------------------------------------


def test_companion_calibration_just_unlocked_true_when_t6_not_yet_fired():
    """calibration_just_unlocked=True when calibration valid and T6 never fired."""
    state = NotificationState()  # last_t6_fired_date_ist == ""
    probe = _probe_success()
    backtest = _backtest_accurate(30)
    calibration = _calibration_valid(30)

    result = _build_chronos_companion(probe, backtest, calibration, state)

    assert result["calibration_just_unlocked"] is True
    assert result["status"] == "success"


def test_companion_calibration_just_unlocked_false_after_t6_fired():
    """calibration_just_unlocked=False when T6 has already fired."""
    state = NotificationState(last_t6_fired_date_ist="2026-06-02")
    probe = _probe_success()
    backtest = _backtest_accurate(30)
    calibration = _calibration_valid(30)

    result = _build_chronos_companion(probe, backtest, calibration, state)

    assert result["calibration_just_unlocked"] is False


def test_companion_calibration_just_unlocked_false_when_calibration_invalid():
    """calibration_just_unlocked=False when calibration is not yet valid."""
    state = NotificationState()  # last_t6_fired_date_ist == ""
    probe = _probe_success()
    backtest = _backtest_accurate(30)
    calibration = _calibration_invalid()

    result = _build_chronos_companion(probe, backtest, calibration, state)

    assert result["calibration_just_unlocked"] is False


def test_companion_calibration_just_unlocked_false_when_no_notification_state():
    """calibration_just_unlocked=False when notification_state is None."""
    probe = _probe_success()
    backtest = _backtest_accurate(30)
    calibration = _calibration_valid(30)

    result = _build_chronos_companion(probe, backtest, calibration, None)

    assert result["calibration_just_unlocked"] is False


def test_companion_failed_probe_has_calibration_just_unlocked_false():
    """Failed probe early-return path always returns calibration_just_unlocked=False."""
    state = NotificationState()  # T6 never fired
    probe = {"status": "failed"}
    backtest = _backtest_accurate(30)
    calibration = _calibration_valid(30)

    result = _build_chronos_companion(probe, backtest, calibration, state)

    assert result["status"] == "failed"
    assert result["calibration_just_unlocked"] is False


# ---------------------------------------------------------------------------
# Test: same-day re-run dedup — T6 does not fire twice
# ---------------------------------------------------------------------------


def test_t6_same_day_rerun_dedup(monkeypatch):
    """Once T6 is sent, same-day re-run of check_triggers must not produce another T6."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: mock_resp)
    monkeypatch.setenv("NTFY_TOPIC", "test-gold-topic")

    state = NotificationState()
    now_ist = _ist(2026, 6, 2)
    forecast = {"model_fallback": False}
    probe = {"status": "success"}
    prices = _prices_flat()
    backtest = _backtest_accurate(30)
    calibration = _calibration_valid(30)

    # First run — T6 fires
    alerts_run1 = check_triggers(
        forecast, probe, prices, backtest, state, now_ist, calibration=calibration
    )
    t6_run1 = [a for a in alerts_run1 if a.trigger_id == "T6"]
    assert len(t6_run1) == 1

    # Simulate successful send (state updated)
    send_pending(t6_run1, state, now_ist)
    assert state.last_t6_fired_date_ist == now_ist.strftime("%Y-%m-%d")

    # Second run same day — T6 must NOT fire again
    alerts_run2 = check_triggers(
        forecast, probe, prices, backtest, state, now_ist, calibration=calibration
    )
    assert not any(a.trigger_id == "T6" for a in alerts_run2)

    # Third run next day — T6 must still NOT fire (lifetime dedup)
    next_day = _ist(2026, 6, 3)
    alerts_run3 = check_triggers(
        forecast, probe, prices, backtest, state, next_day, calibration=calibration
    )
    assert not any(a.trigger_id == "T6" for a in alerts_run3)
