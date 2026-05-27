"""Tests for ml/notifications.py — trigger logic, state management, and ntfy dispatch."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from ml.notifications import (
    NotificationState,
    PendingAlert,
    _is_quiet_hours,
    _release_queued,
    check_triggers,
    compute_chronos_lean,
    compute_dir_acc_30f,
    load_state,
    queue_for_quiet_hours,
    save_state,
    send_pending,
)

IST = ZoneInfo("Asia/Kolkata")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ist(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=IST)


def _probe(status: str = "success", last: float = 14000.0, p50_list: list | None = None) -> dict:
    if p50_list is None:
        p50_list = [14000.0] * 5
    return {
        "status": status,
        "ibja_last_value": last,
        "ibja_forecast": [
            {"day": i + 1, "p10": v * 0.98, "p50": v, "p90": v * 1.02}
            for i, v in enumerate(p50_list)
        ],
    }


def _probe_down(last: float = 14000.0, strength_pct: float = 1.0) -> dict:
    p50 = last * (1 - strength_pct / 100)
    return _probe(last=last, p50_list=[p50] * 5)


def _probe_up(last: float = 14000.0, strength_pct: float = 1.0) -> dict:
    p50 = last * (1 + strength_pct / 100)
    return _probe(last=last, p50_list=[p50] * 5)


def _prices_down(n: int = 10, base: int = 14500, step: int = 100) -> list[dict]:
    base_ts = datetime(2026, 5, 10, 10, 0, 0, tzinfo=UTC)
    return [
        {
            "timestamp": (base_ts + timedelta(days=i)).isoformat().replace("+00:00", "Z"),
            "22k": base - i * step,
            "24k": base - i * step + 500,
            "18k": base - i * step - 500,
            "source": "test",
        }
        for i in range(n)
    ]


def _prices_up(n: int = 10, base: int = 13500, step: int = 100) -> list[dict]:
    base_ts = datetime(2026, 5, 10, 10, 0, 0, tzinfo=UTC)
    return [
        {
            "timestamp": (base_ts + timedelta(days=i)).isoformat().replace("+00:00", "Z"),
            "22k": base + i * step,
            "24k": base + i * step + 500,
            "18k": base + i * step - 500,
            "source": "test",
        }
        for i in range(n)
    ]


def _prices_flat(n: int = 3, base: int = 14000) -> list[dict]:
    base_ts = datetime(2026, 5, 10, 10, 0, 0, tzinfo=UTC)
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
    """All folds: Chronos correctly predicts 'up' direction."""
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


def _backtest_inaccurate(n_folds: int = 30) -> dict:
    """All folds: Chronos predicts 'down' but actual is 'up' — 0% direction accuracy."""
    folds = [
        {
            "naive": [14000.0] * 5,
            "chronos_p50": [13950.0, 13900.0, 13870.0, 13860.0, 13850.0],
            "actuals": [14050.0, 14080.0, 14100.0, 14120.0, 14200.0],
        }
        for _ in range(n_folds)
    ]
    return {
        "n_folds": n_folds,
        "mae_5d_avg_chronos": 300.0,
        "mae_5d_avg_naive": 249.0,
        "folds": folds,
    }


def _forecast(warmup: bool = False, model_fallback: bool = False) -> dict:
    return {"warmup": warmup, "model_fallback": model_fallback, "predicted_22k": 14420}


# ---------------------------------------------------------------------------
# compute_chronos_lean
# ---------------------------------------------------------------------------


def test_lean_down():
    direction, strength = compute_chronos_lean(_probe_down(last=14000.0, strength_pct=1.0))
    assert direction == "down"
    assert abs(strength - 1.0) < 0.01


def test_lean_up():
    direction, strength = compute_chronos_lean(_probe_up(last=14000.0, strength_pct=1.5))
    assert direction == "up"
    assert abs(strength - 1.5) < 0.01


def test_lean_flat():
    direction, strength = compute_chronos_lean(_probe(last=14000.0, p50_list=[14000.0] * 5))
    assert direction == "flat"
    assert strength == 0.0


# ---------------------------------------------------------------------------
# compute_dir_acc_30f
# ---------------------------------------------------------------------------


def test_dir_acc_perfect():
    assert compute_dir_acc_30f(_backtest_accurate(30)) == 1.0


def test_dir_acc_zero():
    assert compute_dir_acc_30f(_backtest_inaccurate(30)) == 0.0


def test_dir_acc_empty():
    assert compute_dir_acc_30f({}) == 0.0


def test_dir_acc_uses_last_30_folds():
    # 40 folds: first 10 inaccurate, last 30 accurate — result should be 1.0
    bad = _backtest_inaccurate(10)["folds"]
    good = _backtest_accurate(30)["folds"]
    bt = {"folds": bad + good, "n_folds": 40}
    assert compute_dir_acc_30f(bt) == 1.0


# ---------------------------------------------------------------------------
# T1 — Predicted drop
# ---------------------------------------------------------------------------


def test_t1_fires():
    alerts = check_triggers(
        _forecast(warmup=False),
        _probe_down(strength_pct=1.0),
        _prices_down(),
        _backtest_accurate(30),
        NotificationState(),
        _ist(2026, 5, 19, 14, 0),
    )
    ids = [a.trigger_id for a in alerts]
    assert "T1" in ids
    t1 = next(a for a in alerts if a.trigger_id == "T1")
    assert "₹" not in t1.title
    assert "₹" not in t1.body
    assert "Rs." in t1.body


def test_t1_blocked_insufficient_folds():
    # n_folds < 30 blocks T1 regardless of other conditions — post-PR-H safe gate
    alerts = check_triggers(
        _forecast(warmup=False),
        _probe_down(strength_pct=1.0),
        _prices_down(),
        _backtest_accurate(n_folds=10),  # only 10 folds — below gate
        NotificationState(),
        _ist(2026, 5, 19, 14, 0),
    )
    assert all(a.trigger_id != "T1" for a in alerts)


def test_t1_blocked_probe_failure():
    probe = _probe_down(strength_pct=1.0)
    probe["status"] = "failed"
    alerts = check_triggers(
        _forecast(warmup=False),
        probe,
        _prices_down(),
        _backtest_accurate(30),
        NotificationState(),
        _ist(2026, 5, 19, 14, 0),
    )
    assert all(a.trigger_id != "T1" for a in alerts)


def test_t1_blocked_dir_acc_gate():
    alerts = check_triggers(
        _forecast(warmup=False),
        _probe_down(strength_pct=1.0),
        _prices_down(),
        _backtest_inaccurate(30),  # dir_acc = 0.0 < 0.55
        NotificationState(),
        _ist(2026, 5, 19, 14, 0),
    )
    assert all(a.trigger_id != "T1" for a in alerts)


def test_t1_cooldown_blocks_second_call():
    forecast = _forecast(warmup=False)
    probe = _probe_down(strength_pct=1.0)
    prices = _prices_down()
    backtest = _backtest_accurate(30)
    now_ist = _ist(2026, 5, 19, 14, 0)

    state = NotificationState()
    alerts1 = check_triggers(forecast, probe, prices, backtest, state, now_ist)
    assert any(a.trigger_id == "T1" for a in alerts1)

    # Simulate successful send
    state.last_sent["T1"] = datetime.now(UTC).isoformat()
    state.sent_today.append({"trigger_id": "T1", "sent_at": datetime.now(UTC).isoformat()})

    alerts2 = check_triggers(forecast, probe, prices, backtest, state, now_ist)
    assert all(a.trigger_id != "T1" for a in alerts2)


# ---------------------------------------------------------------------------
# T2 — Predicted rise
# ---------------------------------------------------------------------------


def test_t2_fires():
    alerts = check_triggers(
        _forecast(warmup=False),
        _probe_up(strength_pct=1.0),
        _prices_up(),
        _backtest_accurate(30),
        NotificationState(),
        _ist(2026, 5, 19, 14, 0),
    )
    ids = [a.trigger_id for a in alerts]
    assert "T2" in ids
    t2 = next(a for a in alerts if a.trigger_id == "T2")
    assert "₹" not in t2.title
    assert "Rs." in t2.body


def test_t2_blocked_dir_acc_gate():
    alerts = check_triggers(
        _forecast(warmup=False),
        _probe_up(strength_pct=1.0),
        _prices_up(),
        _backtest_inaccurate(30),
        NotificationState(),
        _ist(2026, 5, 19, 14, 0),
    )
    assert all(a.trigger_id != "T2" for a in alerts)


def test_t2_blocked_probe_failure():
    probe = _probe_up(strength_pct=1.0)
    probe["status"] = "error"
    alerts = check_triggers(
        _forecast(warmup=False),
        probe,
        _prices_up(),
        _backtest_accurate(30),
        NotificationState(),
        _ist(2026, 5, 19, 14, 0),
    )
    assert all(a.trigger_id != "T2" for a in alerts)


# ---------------------------------------------------------------------------
# T3 — Large actual move
# ---------------------------------------------------------------------------


def test_t3_fires_large_move():
    base_ts = datetime(2026, 5, 19, 6, 0, 0, tzinfo=UTC)
    prices = [
        {
            "timestamp": base_ts.isoformat().replace("+00:00", "Z"),
            "22k": 14000,
            "24k": 14500,
            "18k": 13500,
            "source": "test",
        },
        {
            "timestamp": (base_ts + timedelta(hours=6)).isoformat().replace("+00:00", "Z"),
            "22k": 14200,
            "24k": 14700,
            "18k": 13700,
            "source": "test",
        },
    ]
    alerts = check_triggers(
        _forecast(),
        _probe(),
        prices,
        _backtest_accurate(),
        NotificationState(),
        _ist(2026, 5, 19, 14, 0),
    )
    ids = [a.trigger_id for a in alerts]
    assert "T3" in ids
    t3 = next(a for a in alerts if a.trigger_id == "T3")
    assert "Rs.200" in t3.title


def test_t3_no_fire_small_move():
    base_ts = datetime(2026, 5, 19, 6, 0, 0, tzinfo=UTC)
    prices = [
        {
            "timestamp": base_ts.isoformat().replace("+00:00", "Z"),
            "22k": 14000,
            "24k": 14500,
            "18k": 13500,
            "source": "test",
        },
        {
            "timestamp": (base_ts + timedelta(hours=6)).isoformat().replace("+00:00", "Z"),
            "22k": 14100,
            "24k": 14600,
            "18k": 13600,
            "source": "test",
        },
    ]
    alerts = check_triggers(
        _forecast(),
        _probe(),
        prices,
        _backtest_accurate(),
        NotificationState(),
        _ist(2026, 5, 19, 14, 0),
    )
    assert all(a.trigger_id != "T3" for a in alerts)


# ---------------------------------------------------------------------------
# T4 — Weekly digest
# ---------------------------------------------------------------------------


def test_t4_fires_sunday_1800():
    # 2026-05-17 is a Sunday
    now_ist = _ist(2026, 5, 17, 18, 15)
    assert now_ist.weekday() == 6

    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_flat(),
        _backtest_accurate(),
        NotificationState(),
        now_ist,
    )
    ids = [a.trigger_id for a in alerts]
    assert "T4" in ids
    t4 = next(a for a in alerts if a.trigger_id == "T4")
    assert t4.bypass_quiet is True


def test_t4_no_fire_non_sunday():
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_flat(),
        _backtest_accurate(),
        NotificationState(),
        _ist(2026, 5, 19, 18, 15),
    )
    assert all(a.trigger_id != "T4" for a in alerts)


def test_t4_fires_sunday_late_run():
    # Sunday 20:00 IST — old code blocked this (outside ±30 min window), new code fires it
    now_ist = _ist(2026, 5, 17, 20, 0)
    assert now_ist.weekday() == 6
    alerts = check_triggers(
        _forecast(), _probe(), _prices_flat(), _backtest_accurate(), NotificationState(), now_ist
    )
    ids = [a.trigger_id for a in alerts]
    assert "T4" in ids


def test_t4_no_fire_sunday_before_1700():
    now_ist = _ist(2026, 5, 17, 16, 59)
    assert now_ist.weekday() == 6
    alerts = check_triggers(
        _forecast(), _probe(), _prices_flat(), _backtest_accurate(), NotificationState(), now_ist
    )
    assert all(a.trigger_id != "T4" for a in alerts)


def test_t4_no_fire_sunday_already_fired_today():
    state = NotificationState(last_t4_fired_ist_date="2026-05-17")
    now_ist = _ist(2026, 5, 17, 19, 0)
    assert now_ist.weekday() == 6
    alerts = check_triggers(
        _forecast(), _probe(), _prices_flat(), _backtest_accurate(), state, now_ist
    )
    assert all(a.trigger_id != "T4" for a in alerts)


def test_t4_monday_recovery_fires():
    # Monday 08:00 IST, T4 never fired on prior Sunday
    state = NotificationState(last_t4_fired_ist_date="")
    now_ist = _ist(2026, 5, 18, 8, 0)  # 2026-05-18 is Monday (day after May 17 Sunday)
    assert now_ist.weekday() == 0
    alerts = check_triggers(
        _forecast(), _probe(), _prices_flat(), _backtest_accurate(), state, now_ist
    )
    t4 = [a for a in alerts if a.trigger_id == "T4"]
    assert len(t4) == 1
    assert t4[0].title.startswith("[Delayed]")


def test_t4_monday_recovery_skips_if_sunday_fired():
    # Monday 08:00 IST, but Sunday already got a T4
    state = NotificationState(last_t4_fired_ist_date="2026-05-17")  # prior Sunday
    now_ist = _ist(2026, 5, 18, 8, 0)  # Monday
    assert now_ist.weekday() == 0
    alerts = check_triggers(
        _forecast(), _probe(), _prices_flat(), _backtest_accurate(), state, now_ist
    )
    assert all(a.trigger_id != "T4" for a in alerts)


def test_send_pending_sets_last_t4_fired_ist_date(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: mock_resp)
    monkeypatch.setenv("NTFY_TOPIC", "test-gold-topic")

    now_ist = _ist(2026, 5, 17, 18, 0)
    alert = PendingAlert(
        trigger_id="T4",
        title="Gold Weekly: Model beating naive (22K: Rs.14420)",
        body="22K spot: Rs.14420. Backtest (30 folds): Chronos MAE Rs.275 vs Naive Rs.249. See dashboard.",
        priority=2,
        tags=["newspaper", "white_flower"],
        click_url="https://gaurav-gandhi-2411.github.io/gold-rate-tracker/",
        queued_at=now_ist.isoformat(),
        bypass_quiet=True,
    )
    state = NotificationState()
    assert state.last_t4_fired_ist_date == ""

    send_pending([alert], state, now_ist)

    assert state.last_t4_fired_ist_date == "2026-05-17"


# ---------------------------------------------------------------------------
# T7 — System-alive floor
# ---------------------------------------------------------------------------


def test_t7_fires_first_ever_run():
    # last_t7_fired_ist_date = "" → fires immediately (never fired before)
    state = NotificationState()
    alerts = check_triggers(
        _forecast(),
        _probe_up(),
        _prices_up(),
        _backtest_accurate(),
        state,
        _ist(2026, 5, 17, 14, 0),
    )
    assert any(a.trigger_id == "T7" for a in alerts)


def test_t7_fires_after_3_days():
    state = NotificationState(last_t7_fired_ist_date="2026-05-14")
    now_ist = _ist(2026, 5, 17, 14, 0)  # 3 days after May 14
    alerts = check_triggers(
        _forecast(), _probe_up(), _prices_up(), _backtest_accurate(), state, now_ist
    )
    assert any(a.trigger_id == "T7" for a in alerts)


def test_t7_no_fire_day_1():
    state = NotificationState(last_t7_fired_ist_date="2026-05-17")
    now_ist = _ist(2026, 5, 18, 14, 0)  # 1 day after
    alerts = check_triggers(
        _forecast(), _probe_up(), _prices_up(), _backtest_accurate(), state, now_ist
    )
    assert all(a.trigger_id != "T7" for a in alerts)


def test_t7_no_fire_day_2():
    state = NotificationState(last_t7_fired_ist_date="2026-05-15")
    now_ist = _ist(2026, 5, 17, 14, 0)  # 2 days after
    alerts = check_triggers(
        _forecast(), _probe_up(), _prices_up(), _backtest_accurate(), state, now_ist
    )
    assert all(a.trigger_id != "T7" for a in alerts)


def test_t7_no_fire_probe_failed():
    state = NotificationState()
    alerts = check_triggers(
        _forecast(),
        _probe(status="failed"),
        _prices_flat(),
        _backtest_accurate(),
        state,
        _ist(2026, 5, 17, 14, 0),
    )
    assert all(a.trigger_id != "T7" for a in alerts)


def test_t7_no_fire_already_fired_today():
    state = NotificationState(last_t7_fired_ist_date="2026-05-17")
    now_ist = _ist(2026, 5, 17, 18, 0)  # same IST date
    alerts = check_triggers(
        _forecast(), _probe_up(), _prices_up(), _backtest_accurate(), state, now_ist
    )
    assert all(a.trigger_id != "T7" for a in alerts)


def test_t7_priority_2():
    state = NotificationState()
    alerts = check_triggers(
        _forecast(),
        _probe_up(),
        _prices_up(),
        _backtest_accurate(),
        state,
        _ist(2026, 5, 17, 14, 0),
    )
    t7 = [a for a in alerts if a.trigger_id == "T7"]
    assert len(t7) == 1
    assert t7[0].priority == 2


def test_t7_body_contains_expected_fields():
    state = NotificationState()
    alerts = check_triggers(
        _forecast(),
        _probe_up(strength_pct=1.0),
        _prices_up(),
        _backtest_accurate(),
        state,
        _ist(2026, 5, 17, 14, 0),
    )
    t7 = next(a for a in alerts if a.trigger_id == "T7")
    assert "Rs." in t7.body
    assert "trend:" in t7.body
    assert "lean:" in t7.body
    assert "dir acc" in t7.body
    assert "System healthy" in t7.body


def test_t7_not_counted_in_t123_cap():
    # T1+T2+T3 combined cap is already at max; T7 should still fire
    state = NotificationState()
    # Fill up the sent_today cap with 3 T1/T2/T3 entries
    now_utc = datetime.now(UTC).isoformat()
    state.sent_today = [
        {"trigger_id": "T1", "sent_at": now_utc},
        {"trigger_id": "T2", "sent_at": now_utc},
        {"trigger_id": "T3", "sent_at": now_utc},
    ]
    # Also set cooldowns so T1/T2/T3 themselves don't re-fire
    state.last_sent["T1"] = now_utc
    state.last_sent["T2"] = now_utc
    state.last_sent["T3"] = now_utc

    alerts = check_triggers(
        _forecast(),
        _probe_up(strength_pct=1.0),
        _prices_up(),
        _backtest_accurate(),
        state,
        _ist(2026, 5, 17, 14, 0),
    )
    assert any(a.trigger_id == "T7" for a in alerts), "T7 must fire even when T1/T2/T3 cap is full"


def test_send_pending_sets_last_t7_fired_ist_date(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: mock_resp)
    monkeypatch.setenv("NTFY_TOPIC", "test-gold-topic")

    now_ist = _ist(2026, 5, 17, 14, 0)
    alert = PendingAlert(
        trigger_id="T7",
        title="Gold daily check: 22K Rs.14420",
        body="22K spot Rs.14420. 7d trend: +0.7%. Model lean: up (dir acc 100% on recent folds). System healthy.",
        priority=2,
        tags=["robot", "white_check_mark"],
        click_url="https://gaurav-gandhi-2411.github.io/gold-rate-tracker/",
        queued_at=now_ist.isoformat(),
        bypass_quiet=False,
    )
    state = NotificationState()
    assert state.last_t7_fired_ist_date == ""

    send_pending([alert], state, now_ist)

    assert state.last_t7_fired_ist_date == "2026-05-17"


def test_state_round_trip_new_t4_t7_fields(tmp_path: Path):
    state = NotificationState(
        last_t4_fired_ist_date="2026-05-17",
        last_t7_fired_ist_date="2026-05-15",
    )
    path = tmp_path / "notification_state.json"
    save_state(state, path)

    loaded = load_state(path)
    assert loaded.last_t4_fired_ist_date == "2026-05-17"
    assert loaded.last_t7_fired_ist_date == "2026-05-15"
    assert loaded.schema_version == 1


# ---------------------------------------------------------------------------
# T5 — Model degraded
# ---------------------------------------------------------------------------


def test_t5_fires_model_fallback():
    alerts = check_triggers(
        _forecast(model_fallback=True),
        _probe(),
        _prices_flat(),
        _backtest_accurate(),
        NotificationState(),
        _ist(2026, 5, 19, 14, 0),
    )
    assert any(a.trigger_id == "T5" for a in alerts)


def test_t5_fires_probe_failed():
    alerts = check_triggers(
        _forecast(),
        _probe(status="failed"),
        _prices_flat(),
        _backtest_accurate(),
        NotificationState(),
        _ist(2026, 5, 19, 14, 0),
    )
    assert any(a.trigger_id == "T5" for a in alerts)


def test_t5_once_per_ist_day():
    state = NotificationState(last_t5_ist_date="2026-05-19")
    alerts = check_triggers(
        _forecast(model_fallback=True),
        _probe(),
        _prices_flat(),
        _backtest_accurate(),
        state,
        _ist(2026, 5, 19, 14, 0),
    )
    assert all(a.trigger_id != "T5" for a in alerts)


# ---------------------------------------------------------------------------
# Quiet hours queuing and release
# ---------------------------------------------------------------------------


def test_quiet_hours_detected():
    assert _is_quiet_hours(_ist(2026, 5, 19, 23, 30))
    assert _is_quiet_hours(_ist(2026, 5, 19, 0, 0))
    assert _is_quiet_hours(_ist(2026, 5, 19, 6, 59))
    assert not _is_quiet_hours(_ist(2026, 5, 19, 7, 0))
    assert not _is_quiet_hours(_ist(2026, 5, 19, 14, 0))


def test_quiet_hours_queues_t1():
    state = NotificationState()
    now_ist = _ist(2026, 5, 19, 23, 30)
    assert _is_quiet_hours(now_ist)

    alerts = check_triggers(
        _forecast(warmup=False),
        _probe_down(strength_pct=1.0),
        _prices_down(),
        _backtest_accurate(30),
        state,
        now_ist,
    )
    t1_alerts = [a for a in alerts if a.trigger_id == "T1"]
    assert len(t1_alerts) == 1

    queue_for_quiet_hours(t1_alerts, state)
    assert len(state.queued) == 1
    assert state.queued[0]["trigger_id"] == "T1"


def test_release_queued_returns_and_clears():
    state = NotificationState()
    now_ist = _ist(2026, 5, 19, 23, 30)
    alert = PendingAlert(
        trigger_id="T1",
        title="Gold: Model and momentum both lean DOWN over next 5d",
        body="Test body",
        priority=4,
        tags=["decline"],
        click_url="https://example.com",
        queued_at=now_ist.isoformat(),
        bypass_quiet=False,
    )
    state.queued = [alert.to_dict()]

    released = _release_queued(state)
    assert len(released) == 1
    assert released[0].trigger_id == "T1"
    assert state.queued == []


# ---------------------------------------------------------------------------
# ntfy POST mocked — ASCII-safe headers, Rs. in body
# ---------------------------------------------------------------------------


def test_ntfy_headers_ascii_and_rs(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)

    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["req"] = req
        return mock_resp

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setenv("NTFY_TOPIC", "test-gold-topic")

    now_ist = _ist(2026, 5, 19, 14, 0)
    alert = PendingAlert(
        trigger_id="T1",
        title="Gold: Model and momentum both lean DOWN over next 5d",
        body="22K spot Rs.14420. Last 7d trend: -1.0%. Chronos lean: 1.0% over 5d. Directional signal only.",
        priority=4,
        tags=["decline", "chart_with_downwards_trend"],
        click_url="https://gaurav-gandhi-2411.github.io/gold-rate-tracker/",
        queued_at=now_ist.isoformat(),
        bypass_quiet=False,
    )
    state = NotificationState()
    sent = send_pending([alert], state, now_ist)

    assert len(sent) == 1
    assert sent[0].success is True

    req = captured["req"]
    title = req.get_header("Title")
    assert title is not None
    assert title.isascii(), f"Title must be ASCII-only, got: {title!r}"
    assert "₹" not in title

    body_text = req.data.decode("utf-8")
    assert "Rs." in body_text
    assert "₹" not in body_text

    assert "test-gold-topic" in req.full_url

    # State updated
    assert "T1" in state.last_sent
    assert any(s["trigger_id"] == "T1" for s in state.sent_today)


def test_ntfy_no_send_without_topic(monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    now_ist = _ist(2026, 5, 19, 14, 0)
    alert = PendingAlert(
        trigger_id="T3",
        title="Gold: Rs.200 up detected (+1.4%)",
        body="Test",
        priority=4,
        tags=["warning"],
        click_url="https://example.com",
        queued_at=now_ist.isoformat(),
        bypass_quiet=False,
    )
    state = NotificationState()
    sent = send_pending([alert], state, now_ist)
    # No NTFY_TOPIC → skipped silently; sent list is empty
    assert sent == []


# ---------------------------------------------------------------------------
# State save/load round-trip
# ---------------------------------------------------------------------------


def test_state_round_trip(tmp_path: Path):
    state = NotificationState(
        last_sent={"T1": "2026-05-19T10:00:00+00:00", "T4": "2026-05-17T18:00:00+05:30"},
        queued=[
            {
                "trigger_id": "T3",
                "title": "Test",
                "body": "B",
                "priority": 4,
                "tags": ["warning"],
                "click_url": "https://x.com",
                "queued_at": "2026-05-19T22:30:00+05:30",
                "bypass_quiet": False,
            }
        ],
        sent_today=[{"trigger_id": "T1", "sent_at": "2026-05-19T10:00:00+00:00"}],
        last_t5_ist_date="2026-05-19",
    )
    path = tmp_path / "notification_state.json"
    save_state(state, path)

    loaded = load_state(path)
    assert loaded.schema_version == 1
    assert loaded.last_sent == state.last_sent
    assert loaded.queued == state.queued
    assert loaded.sent_today == state.sent_today
    assert loaded.last_t5_ist_date == state.last_t5_ist_date


def test_load_state_missing_file(tmp_path: Path):
    state = load_state(tmp_path / "nonexistent.json")
    assert state.last_sent == {}
    assert state.queued == []
    assert state.sent_today == []
    assert state.last_t5_ist_date == ""
    assert state.schema_version == 1


def test_load_state_corrupt_json(tmp_path: Path):
    path = tmp_path / "corrupt.json"
    path.write_text("not valid json", encoding="utf-8")
    state = load_state(path)
    assert state.last_sent == {}
