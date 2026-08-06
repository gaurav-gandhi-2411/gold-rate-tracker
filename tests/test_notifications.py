"""Tests for ml/notifications.py — trigger logic, state management, and ntfy dispatch."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from ml.notifications import (
    NotificationState,
    PendingAlert,
    _get_prior_day_price,
    _is_quiet_hours,
    _release_queued,
    _stamp_ist_dedup,
    check_triggers,
    compute_chronos_lean,
    compute_dir_acc_30f,
    compute_selfhosted_consecutive_failures,
    compute_snapshot_gap_days,
    compute_usable_snapshot_gap_days,
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
    probe = _probe(last=last, p50_list=[p50] * 5)
    # v2 schema fields: unanimous down consensus so existing T1 tests keep their semantics.
    probe["majority_direction"] = "down"
    probe["direction_consensus"] = 1.0
    probe["num_samples"] = 5
    probe["sample_directions"] = ["down"] * 5
    return probe


def _probe_up(last: float = 14000.0, strength_pct: float = 1.0) -> dict:
    p50 = last * (1 + strength_pct / 100)
    probe = _probe(last=last, p50_list=[p50] * 5)
    # v2 schema fields: unanimous up consensus so existing T2 tests keep their semantics.
    probe["majority_direction"] = "up"
    probe["direction_consensus"] = 1.0
    probe["num_samples"] = 5
    probe["sample_directions"] = ["up"] * 5
    return probe


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


def test_t1_skips_momentum_up():
    """T1 skips when 7d momentum is up (wrong direction)."""
    alerts = check_triggers(
        _forecast(warmup=False),
        _probe_down(strength_pct=1.0),
        _prices_up(),  # momentum is up ~5%
        _backtest_accurate(30),
        NotificationState(),
        _ist(2026, 5, 19, 14, 0),
    )
    assert all(a.trigger_id != "T1" for a in alerts), "T1 must NOT fire when momentum is up"


def test_t1_skips_momentum_too_small():
    """T1 skips when |mom_pct| < 0.5% (below the meaningful-move threshold)."""
    alerts = check_triggers(
        _forecast(warmup=False),
        _probe_down(strength_pct=1.0),
        _prices_flat(),  # 0% change
        _backtest_accurate(30),
        NotificationState(),
        _ist(2026, 5, 19, 14, 0),
    )
    assert all(a.trigger_id != "T1" for a in alerts), "T1 must NOT fire on flat/sub-0.5% prices"


def test_t1_fires_regardless_of_probe_majority():
    """T1 fires on momentum even when probe majority_direction is 'up' (Chronos gate gone)."""
    probe = _probe_down(strength_pct=1.0)
    probe["majority_direction"] = "up"  # would have blocked T1 under old gate
    probe["direction_consensus"] = 1.0
    alerts = check_triggers(
        _forecast(warmup=False),
        probe,
        _prices_down(),
        _backtest_accurate(30),
        NotificationState(),
        _ist(2026, 5, 19, 14, 0),
    )
    t1 = [a for a in alerts if a.trigger_id == "T1"]
    assert len(t1) == 1, "T1 must fire on momentum regardless of probe majority_direction"


def _prices_down_unordered_float_latest() -> list[dict]:
    """Descending 7d series where the newest reading (by timestamp) is a float and
    is NOT the last element of the array — exercises the sort + int-coerce fix."""
    base_ts = datetime(2026, 5, 10, 10, 0, 0, tzinfo=UTC)
    prices = [
        {
            "timestamp": (base_ts + timedelta(days=i)).isoformat().replace("+00:00", "Z"),
            "22k": 14500 - i * 100,
            "24k": 14500 - i * 100 + 500,
            "18k": 14500 - i * 100 - 500,
            "source": "test",
        }
        for i in range(10)
    ]
    prices[-1]["22k"] = 13600.0  # newest (day 9) is a float
    newest = prices.pop()  # remove day 9 from the end ...
    prices.insert(0, newest)  # ... and put it first, so prices[-1] is NOT the latest
    return prices


def test_t1_current_price_is_int_and_latest_by_timestamp():
    """T1 body shows the int latest-by-timestamp price (Rs.13600), not the unsorted
    array tail (Rs.13700) and not a float (Rs.13600.0)."""
    alerts = check_triggers(
        _forecast(warmup=False),
        _probe(),  # status success, flat — T1 is momentum-driven
        _prices_down_unordered_float_latest(),
        _backtest_accurate(30),
        NotificationState(),
        _ist(2026, 5, 19, 14, 0),
    )
    t1 = [a for a in alerts if a.trigger_id == "T1"]
    assert len(t1) == 1, "T1 must fire on the down momentum"
    assert "Rs.13600.0" not in t1[0].body, "must not render a float (Rs.13600.0)"
    assert "Rs.13600" in t1[0].body, "must show the int latest-by-timestamp price"
    assert "Rs.13700" not in t1[0].body, "must not show the unsorted array-tail reading"


def test_t2_current_price_is_int_and_latest_by_timestamp():
    """T2 mirror of the T1 sort+int regression (ascending series)."""
    base_ts = datetime(2026, 5, 10, 10, 0, 0, tzinfo=UTC)
    prices = [
        {
            "timestamp": (base_ts + timedelta(days=i)).isoformat().replace("+00:00", "Z"),
            "22k": 13500 + i * 100,
            "24k": 13500 + i * 100 + 500,
            "18k": 13500 + i * 100 - 500,
            "source": "test",
        }
        for i in range(10)
    ]
    prices[-1]["22k"] = 14400.0  # newest is a float
    newest = prices.pop()
    prices.insert(0, newest)  # out of order
    alerts = check_triggers(
        _forecast(warmup=False),
        _probe(),
        prices,
        _backtest_accurate(30),
        NotificationState(),
        _ist(2026, 5, 19, 14, 0),
    )
    t2 = [a for a in alerts if a.trigger_id == "T2"]
    assert len(t2) == 1, "T2 must fire on the up momentum"
    assert "Rs.14400.0" not in t2[0].body, "must not render a float (Rs.14400.0)"
    assert "Rs.14400" in t2[0].body, "must show the int latest-by-timestamp price"


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


def test_t2_skips_momentum_down():
    """T2 skips when 7d momentum is down (wrong direction)."""
    alerts = check_triggers(
        _forecast(warmup=False),
        _probe_up(strength_pct=1.0),
        _prices_down(),  # momentum is down ~5%
        _backtest_accurate(30),
        NotificationState(),
        _ist(2026, 5, 19, 14, 0),
    )
    assert all(a.trigger_id != "T2" for a in alerts), "T2 must NOT fire when momentum is down"


def test_t2_skips_momentum_too_small():
    """T2 skips when |mom_pct| < 0.5% (below the meaningful-move threshold)."""
    alerts = check_triggers(
        _forecast(warmup=False),
        _probe_up(strength_pct=1.0),
        _prices_flat(),  # 0% change
        _backtest_accurate(30),
        NotificationState(),
        _ist(2026, 5, 19, 14, 0),
    )
    assert all(a.trigger_id != "T2" for a in alerts), "T2 must NOT fire on flat/sub-0.5% prices"


def test_t2_fires_regardless_of_probe_majority():
    """T2 fires on momentum even when probe majority_direction is 'down' (Chronos gate gone)."""
    probe = _probe_up(strength_pct=1.0)
    probe["majority_direction"] = "down"  # would have blocked T2 under old gate
    probe["direction_consensus"] = 1.0
    alerts = check_triggers(
        _forecast(warmup=False),
        probe,
        _prices_up(),
        _backtest_accurate(30),
        NotificationState(),
        _ist(2026, 5, 19, 14, 0),
    )
    t2 = [a for a in alerts if a.trigger_id == "T2"]
    assert len(t2) == 1, "T2 must fire on momentum regardless of probe majority_direction"


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
        title="Gold Weekly: 22K Rs.14420",
        body="Gold 22K: Rs.14420. Check the app for the latest read.",
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
    assert "this week" in t7.body  # week-trend description
    assert "System working normally" in t7.body
    # Plain language: no jargon in T7 body
    assert "dir acc" not in t7.body
    assert "lean:" not in t7.body
    assert "folds" not in t7.body


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
        title="Gold daily check: Rs.14420",
        body="Gold 22K: Rs.14420. Up 0.7% this week. Prices may edge up a little. System working normally.",
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


def test_stamp_ist_dedup_t7_on_queue(tmp_path):
    """T7 queued during quiet hours must stamp last_t7_fired_ist_date immediately.

    Without the stamp-on-queue fix, a second CI run during quiet hours would see
    an un-stamped state, re-generate T7, and add another copy to the queue.
    """
    state = NotificationState()
    now_ist = _ist(2026, 5, 20, 23, 30)  # 23:30 IST — quiet hours

    assert state.last_t7_fired_ist_date == ""
    _stamp_ist_dedup("T7", state, now_ist)
    assert state.last_t7_fired_ist_date == "2026-05-20"


def test_t7_does_not_accumulate_in_queue_across_quiet_runs():
    """Simulate two consecutive quiet-hours CI runs.  T7 should only appear once
    in the queue even though the dedup stamp was not set by an actual send.

    Before the fix: both runs would generate T7 (un-stamped state) and the queue
    would hold two copies. After the fix: the first run stamps the date; the second
    run sees today's date and returns None from _check_t7.
    """
    state = NotificationState()

    # Run 1: 23:30 IST on Day 1 — T7 eligible (no prior stamp)
    now_ist_r1 = _ist(2026, 5, 20, 23, 30)
    alerts_r1 = check_triggers(
        _forecast(),
        _probe_up(strength_pct=1.0),
        _prices_up(),
        _backtest_accurate(),
        state,
        now_ist_r1,
    )
    t7_r1 = [a for a in alerts_r1 if a.trigger_id == "T7"]
    assert len(t7_r1) == 1, "T7 must fire on first quiet run"

    # Simulate quiet-hours queuing (what main() does) — this stamps the dedup
    for alert in t7_r1:
        state = queue_for_quiet_hours([alert], state)
        _stamp_ist_dedup(alert.trigger_id, state, now_ist_r1)

    assert state.last_t7_fired_ist_date == "2026-05-20"
    assert len(state.queued) == 1

    # Run 2: 05:30 IST on Day 2 — same calendar date IST stamp → T7 must NOT fire again
    now_ist_r2 = _ist(2026, 5, 21, 5, 30)
    alerts_r2 = check_triggers(
        _forecast(),
        _probe_up(strength_pct=1.0),
        _prices_up(),
        _backtest_accurate(),
        state,
        now_ist_r2,
    )
    t7_r2 = [a for a in alerts_r2 if a.trigger_id == "T7"]
    assert len(t7_r2) == 0, "T7 must NOT re-fire on second quiet run — stamp set by first run"

    # Queue must still hold exactly 1 T7 (not accumulating)
    assert len(state.queued) == 1


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


def test_release_queued_returns_and_clears(monkeypatch):
    # Freeze ml.notifications.datetime so the 12-hour cutoff is stable regardless of
    # wall-clock date.  queued_at is 2026-05-19 23:30 IST; freeze "now" to 1h later
    # (2026-05-20 00:30 IST = 2026-05-19 19:00 UTC) so the entry is well within the
    # 12-hour window.
    from datetime import UTC
    from datetime import datetime as _real_datetime

    _frozen_now_utc = _ist(2026, 5, 20, 0, 30).astimezone(UTC)

    class _FakeDatetime(_real_datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return _frozen_now_utc if tz is UTC else _real_datetime.now(tz)

    monkeypatch.setattr("ml.notifications.datetime", _FakeDatetime)

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


def _freeze_now_utc(monkeypatch, frozen_now_utc) -> None:
    """Freeze ml.notifications.datetime.now(UTC) to a fixed instant.

    Mirrors the _FakeDatetime pattern in test_release_queued_returns_and_clears
    so the 12-hour _release_queued cutoff is deterministic.
    """
    from datetime import datetime as _real_datetime

    class _FakeDatetime(_real_datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return frozen_now_utc if tz is UTC else _real_datetime.now(tz)

    monkeypatch.setattr("ml.notifications.datetime", _FakeDatetime)


def test_release_dedupes_repeated_trigger_keeps_most_recent(monkeypatch):
    """Reproduce the production duplicate: 3 quiet-hours runs each queue T1+T3,
    then one post-quiet release run must send exactly ONE T1 and ONE T3 — not 3 each.

    Mirrors the live run sequence 27432315878 -> 27440006193 -> 27446430564
    (three quiet-hours schedule runs, each Queued T1 + Queued T3) -> 27455089566
    (08:52 IST release run that fired 3xT1 + 3xT3). T1/T2/T3 carry no IST-date
    queue-time stamp, so they accumulate; release-dedup is the backstop.
    """
    # Release at 2026-06-13 08:52 IST (= 03:22 UTC), matching run 27455089566.
    _freeze_now_utc(monkeypatch, _ist(2026, 6, 13, 8, 52).astimezone(UTC))

    state = NotificationState()
    # Three quiet-hours runs, increasing queued_at; all within the 12h window.
    run_times_ist = [
        _ist(2026, 6, 12, 23, 5),  # 27432315878
        _ist(2026, 6, 13, 1, 35),  # 27440006193
        _ist(2026, 6, 13, 3, 51),  # 27446430564
    ]
    for i, qt in enumerate(run_times_ist):
        alerts = [
            PendingAlert(
                trigger_id="T1",
                title="Gold: 22K prices are down this week",
                body=f"run{i}",
                priority=4,
                tags=["decline"],
                click_url="https://example.com",
                queued_at=qt.isoformat(),
                bypass_quiet=False,
            ),
            PendingAlert(
                trigger_id="T3",
                title="Gold: Rs.270 up detected (+2.0%)",
                body=f"run{i}",
                priority=4,
                tags=["warning"],
                click_url="https://example.com",
                queued_at=qt.isoformat(),
                bypass_quiet=False,
            ),
        ]
        queue_for_quiet_hours(alerts, state)

    assert len(state.queued) == 6, "precondition: 3 runs x (T1+T3) accumulated"

    released = _release_queued(state)

    by_id = {}
    for a in released:
        by_id.setdefault(a.trigger_id, []).append(a)
    assert sorted(by_id) == ["T1", "T3"]
    assert len(by_id["T1"]) == 1, "exactly one T1 released (not 3)"
    assert len(by_id["T3"]) == 1, "exactly one T3 released (not 3)"
    # Most-recent copy kept (run index 2 = the 03:51 IST run).
    assert by_id["T1"][0].body == "run2"
    assert by_id["T3"][0].body == "run2"
    assert state.queued == []


def test_release_dedup_preserves_distinct_triggers(monkeypatch):
    """Guard: release-dedup must NOT merge or drop distinct queued triggers.

    A queue holding one each of the IST-date-stamped triggers (T5/T7/T8_MORNING/T9)
    must release one of each — confirming dedup-by-trigger_id only collapses
    genuine duplicates and leaves the T4-T9 queue-time-stamping behavior intact.
    """
    _freeze_now_utc(monkeypatch, _ist(2026, 6, 13, 8, 52).astimezone(UTC))

    qt = _ist(2026, 6, 13, 3, 51)
    state = NotificationState()
    for tid in ("T5", "T7", "T8_MORNING", "T9"):
        queue_for_quiet_hours(
            [
                PendingAlert(
                    trigger_id=tid,
                    title=f"{tid} title",
                    body=f"{tid} body",
                    priority=2,
                    tags=["bell"],
                    click_url="https://example.com",
                    queued_at=qt.isoformat(),
                    bypass_quiet=False,
                )
            ],
            state,
        )

    released = _release_queued(state)
    assert sorted(a.trigger_id for a in released) == ["T5", "T7", "T8_MORNING", "T9"]
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


# ---------------------------------------------------------------------------
# T6 — Calibration unlocked
# ---------------------------------------------------------------------------


def test_check_triggers_backward_compat_no_calibration_kwarg():
    """Calling check_triggers without the new calibration kwarg must work identically to before."""
    state = NotificationState()
    now_ist = _ist(2026, 5, 19, 14, 0)
    forecast = {"model_fallback": False}
    probe = {"status": "success"}
    prices = _prices_flat()
    backtest = _backtest_accurate(30)
    # Old-style call (no calibration kwarg)
    alerts_old = check_triggers(forecast, probe, prices, backtest, state, now_ist)
    # New-style call with calibration=None
    alerts_new = check_triggers(forecast, probe, prices, backtest, state, now_ist, calibration=None)
    assert [a.trigger_id for a in alerts_old] == [a.trigger_id for a in alerts_new]


def test_t6_fires_when_calibration_valid_first_time():
    state = NotificationState()  # last_t6_fired_date_ist == ""
    now_ist = _ist(2026, 6, 2, 14, 0)
    calibration = {"valid": True, "n_observations": 30}
    alerts = check_triggers(
        {"model_fallback": False},
        {"status": "success"},
        _prices_flat(),
        _backtest_accurate(30),
        state,
        now_ist,
        calibration=calibration,
    )
    t6 = [a for a in alerts if a.trigger_id == "T6"]
    assert len(t6) == 1
    assert "calibration" in t6[0].title.lower()


def test_t6_skips_when_calibration_invalid():
    state = NotificationState()
    now_ist = _ist(2026, 6, 2, 14, 0)
    calibration = {"valid": False, "n_observations": 21}
    alerts = check_triggers(
        {"model_fallback": False},
        {"status": "success"},
        _prices_flat(),
        _backtest_accurate(30),
        state,
        now_ist,
        calibration=calibration,
    )
    assert not any(a.trigger_id == "T6" for a in alerts)


def test_t6_skips_when_already_fired():
    state = NotificationState(last_t6_fired_date_ist="2026-06-02")
    now_ist = _ist(2026, 6, 3, 14, 0)  # next day, still valid calibration
    calibration = {"valid": True, "n_observations": 31}
    alerts = check_triggers(
        {"model_fallback": False},
        {"status": "success"},
        _prices_flat(),
        _backtest_accurate(30),
        state,
        now_ist,
        calibration=calibration,
    )
    assert not any(a.trigger_id == "T6" for a in alerts)


def test_send_pending_sets_last_t6_fired_date_ist(monkeypatch):
    """send_pending sets last_t6_fired_date_ist when T6 is successfully sent."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: mock_resp)
    monkeypatch.setenv("NTFY_TOPIC", "test-gold-topic")

    now_ist = _ist(2026, 6, 2, 14, 0)
    alert = PendingAlert(
        trigger_id="T6",
        title="Gold forecast: calibration unlocked",
        body="IBJA->Tanishq calibration achieved 30 overlap pairs (>=30). See dashboard.",
        priority=3,
        tags=["unlock", "white_check_mark"],
        click_url="https://gaurav-gandhi-2411.github.io/gold-rate-tracker/",
        queued_at=now_ist.isoformat(),
        bypass_quiet=False,
    )
    state = NotificationState()
    assert state.last_t6_fired_date_ist == ""

    sent = send_pending([alert], state, now_ist)

    assert len(sent) == 1
    assert sent[0].success is True
    assert state.last_t6_fired_date_ist == now_ist.strftime("%Y-%m-%d")


def test_state_round_trip_includes_t6_field(tmp_path: Path):
    """NotificationState with last_t6_fired_date_ist survives a save/load cycle."""
    state = NotificationState(
        last_t5_ist_date="2026-06-01",
        last_t6_fired_date_ist="2026-06-02",
    )
    path = tmp_path / "notification_state.json"
    save_state(state, path)

    loaded = load_state(path)
    assert loaded.last_t6_fired_date_ist == "2026-06-02"
    assert loaded.last_t5_ist_date == "2026-06-01"
    assert loaded.schema_version == 1


# ---------------------------------------------------------------------------
# ADR 020 momentum gate — T1/T2 re-pointed to 7-day realised momentum
# ---------------------------------------------------------------------------
# The old Phi4 consensus gate (direction_consensus >= 0.6) is removed — it was
# always true (model deterministic, consensus always 1.0). T1/T2 now fire on
# N=7d momentum >= 0.5% in the correct direction. Probe majority_direction and
# direction_consensus fields are ignored for gating purposes.
# ---------------------------------------------------------------------------


def test_t1_copy_is_description_not_forecast():
    """T1 body must say 'past 7 days' (past-tense description) and never say 'will' or 'signal'."""
    alerts = check_triggers(
        _forecast(warmup=False),
        _probe_down(strength_pct=1.0),
        _prices_down(),
        _backtest_accurate(30),
        NotificationState(),
        _ist(2026, 5, 19, 14, 0),
    )
    t1 = next((a for a in alerts if a.trigger_id == "T1"), None)
    assert t1 is not None, "T1 must fire"
    assert "past 7 days" in t1.body, "T1 body must anchor move as past"
    assert "will" not in t1.body.lower(), "T1 body must not use 'will'"
    assert "signal" not in t1.body.lower(), "T1 body must not reference a signal"
    assert "Rs." in t1.body


def test_t2_copy_is_description_not_forecast():
    """T2 body must say 'past 7 days' (past-tense description) and never say 'will' or 'signal'."""
    alerts = check_triggers(
        _forecast(warmup=False),
        _probe_up(strength_pct=1.0),
        _prices_up(),
        _backtest_accurate(30),
        NotificationState(),
        _ist(2026, 5, 19, 14, 0),
    )
    t2 = next((a for a in alerts if a.trigger_id == "T2"), None)
    assert t2 is not None, "T2 must fire"
    assert "past 7 days" in t2.body, "T2 body must anchor move as past"
    assert "will" not in t2.body.lower(), "T2 body must not use 'will'"
    assert "signal" not in t2.body.lower(), "T2 body must not reference a signal"
    assert "Rs." in t2.body


# ---------------------------------------------------------------------------
# T8 — Daily plain-language digest (morning + evening)
# ---------------------------------------------------------------------------
# Test now: _ist(2026, 5, 19, ...).  Prices from _prices_up(n=10) / _prices_down(n=10)
# span 2026-05-10 → 2026-05-19 UTC (= IST same date at 15:30).
# At test time 2026-05-19 14:00 IST:
#   today_ist = 2026-05-19
#   current  = reading at 2026-05-19T10:00Z (index 9)
#   prior    = last reading before May 19 IST = 2026-05-18T10:00Z (index 8)


def _forecast_with_companion(lean_direction: str = "up") -> dict:
    return {
        "warmup": False,
        "model_fallback": False,
        "predicted_22k": 14420,
        "chronos_companion": {
            "status": "success",
            "lean_direction": lean_direction,
            "lean_strength_pct": 1.5,
            "direction_acc_30f": 0.633,
        },
    }


def _forecast_companion_failed() -> dict:
    return {
        "warmup": False,
        "model_fallback": False,
        "predicted_22k": 14420,
        "chronos_companion": {"status": "failed"},
    }


# --- T8_MORNING timing ---


def test_t8_morning_fires_at_threshold():
    """T8_MORNING fires on first run at/after 08:00 IST."""
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_up(n=10),
        _backtest_accurate(),
        NotificationState(),
        _ist(2026, 5, 19, 10, 0),  # 10:00 IST ≥ 08:00 threshold
    )
    assert any(a.trigger_id == "T8_MORNING" for a in alerts)


def test_t8_morning_no_fire_before_threshold():
    """T8_MORNING does NOT fire before 08:00 IST (cron-drift safety)."""
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_up(n=10),
        _backtest_accurate(),
        NotificationState(),
        _ist(2026, 5, 19, 4, 0),  # 04:00 IST < 08:00 threshold
    )
    assert all(a.trigger_id != "T8_MORNING" for a in alerts)


def test_t8_morning_fires_even_with_drift():
    """T8_MORNING fires at 11:30 IST (simulated cron drift past 08:00)."""
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_up(n=10),
        _backtest_accurate(),
        NotificationState(),
        _ist(2026, 5, 19, 11, 30),  # drifted run, still fires
    )
    assert any(a.trigger_id == "T8_MORNING" for a in alerts)


def test_t8_morning_upper_bound_blocks_hour22():
    """T8_MORNING does NOT fire at hour >= 14 (upper bound blocks late/night runs)."""
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_up(n=10),
        _backtest_accurate(),
        NotificationState(),  # fresh state — dedup would not block this
        _ist(2026, 5, 19, 22, 0),  # hour=22 satisfies >= 8 lower bound but fails < 14 upper
    )
    assert all(a.trigger_id != "T8_MORNING" for a in alerts)


def test_t8_morning_upper_bound_edge_hour13_fires():
    """T8_MORNING fires at hour=13 (just inside the 8-13 window)."""
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_up(n=10),
        _backtest_accurate(),
        NotificationState(),
        _ist(2026, 5, 19, 13, 0),  # hour=13, last in-window hour
    )
    assert any(a.trigger_id == "T8_MORNING" for a in alerts)


def test_t8_morning_dedup_no_double_fire():
    """T8_MORNING fires at most once per IST day (dedup blocks in-window repeat)."""
    state = NotificationState(last_t8_morning_ist_date="2026-05-19")
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_up(n=10),
        _backtest_accurate(),
        state,
        _ist(2026, 5, 19, 10, 0),  # second in-window run same IST day — dedup blocks it
    )
    assert all(a.trigger_id != "T8_MORNING" for a in alerts)


def test_t8_morning_fires_next_day_after_dedup():
    """T8_MORNING fires again the next IST day."""
    state = NotificationState(last_t8_morning_ist_date="2026-05-19")
    now_ist = _ist(2026, 5, 20, 10, 0)  # next day
    # Need prices covering May 20 as current day
    from datetime import UTC as _UTC

    base_ts = datetime(2026, 5, 10, 10, 0, 0, tzinfo=_UTC)
    prices_11d = [
        {
            "timestamp": (base_ts + timedelta(days=i)).isoformat().replace("+00:00", "Z"),
            "22k": 13500 + i * 100,
            "24k": 14000 + i * 100,
            "18k": 13000 + i * 100,
            "source": "test",
        }
        for i in range(11)  # through May 20
    ]
    alerts = check_triggers(
        _forecast(),
        _probe(),
        prices_11d,
        _backtest_accurate(),
        state,
        now_ist,
    )
    assert any(a.trigger_id == "T8_MORNING" for a in alerts)


# --- T8_EVENING timing ---


def test_t8_evening_fires_at_threshold():
    """T8_EVENING fires on first run at/after 18:00 IST (within the 18-21 window)."""
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_up(n=10),
        _backtest_accurate(),
        NotificationState(),
        _ist(2026, 5, 19, 18, 0),  # 18:00 IST — lower bound, inside window
    )
    assert any(a.trigger_id == "T8_EVENING" for a in alerts)


def test_t8_evening_no_fire_before_threshold():
    """T8_EVENING does NOT fire before 18:00 IST."""
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_up(n=10),
        _backtest_accurate(),
        NotificationState(),
        _ist(2026, 5, 19, 16, 0),  # 16:00 IST < 18:00 threshold
    )
    assert all(a.trigger_id != "T8_EVENING" for a in alerts)


def test_t8_evening_upper_bound_blocks_hour23():
    """T8_EVENING does NOT fire at hour >= 22 (upper bound keeps it outside quiet hours)."""
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_up(n=10),
        _backtest_accurate(),
        NotificationState(),  # fresh state — dedup would not block this
        _ist(2026, 5, 19, 23, 0),  # hour=23 satisfies >= 18 lower bound but fails < 22 upper
    )
    assert all(a.trigger_id != "T8_EVENING" for a in alerts)


def test_t8_evening_upper_bound_edge_hour21_fires():
    """T8_EVENING fires at hour=21 (just inside the 18-21 window)."""
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_up(n=10),
        _backtest_accurate(),
        NotificationState(),
        _ist(2026, 5, 19, 21, 0),  # hour=21, last in-window hour
    )
    assert any(a.trigger_id == "T8_EVENING" for a in alerts)


def test_t8_evening_dedup_no_double_fire():
    """T8_EVENING fires at most once per IST day (dedup blocks in-window repeat)."""
    state = NotificationState(last_t8_evening_ist_date="2026-05-19")
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_up(n=10),
        _backtest_accurate(),
        state,
        _ist(2026, 5, 19, 19, 0),  # in-window second run same IST day — dedup blocks it
    )
    assert all(a.trigger_id != "T8_EVENING" for a in alerts)


def test_t8_evening_bypass_quiet_true():
    """T8_EVENING has bypass_quiet=True — belt-and-suspenders for extreme drift past 22:00.

    The window bound (_T8_EVENING_UPPER_H=22) means T8_EVENING fires in the clean 18-21
    window (outside quiet hours) in normal operation. bypass_quiet=True is a safety net
    for the rare case where the evening cron drifts past 22:00 IST.
    """
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_up(n=10),
        _backtest_accurate(),
        NotificationState(),
        _ist(2026, 5, 19, 19, 0),  # in-window: 19:00 IST, well before quiet hours
    )
    t8e = [a for a in alerts if a.trigger_id == "T8_EVENING"]
    assert len(t8e) == 1
    assert t8e[0].bypass_quiet is True


def test_t8_morning_bypass_quiet_false():
    """T8_MORNING has bypass_quiet=False (10:00 IST cron is outside quiet hours)."""
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_up(n=10),
        _backtest_accurate(),
        NotificationState(),
        _ist(2026, 5, 19, 10, 0),
    )
    t8m = [a for a in alerts if a.trigger_id == "T8_MORNING"]
    assert len(t8m) == 1
    assert t8m[0].bypass_quiet is False


# --- Both morning and evening fire on the same IST day ---


def test_t8_morning_and_evening_both_fire_same_day():
    """T8_MORNING and T8_EVENING both fire on the same day (independent dedup)."""
    # Simulate the UTC-12 cron at ~19:00 IST after morning already ran at ~10:00 IST.
    state = NotificationState(last_t8_morning_ist_date="2026-05-19")  # morning already fired
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_up(n=10),
        _backtest_accurate(),
        state,
        _ist(2026, 5, 19, 19, 0),  # 19:00 IST: morning blocked (dedup), evening fires
    )
    morning_alerts = [a for a in alerts if a.trigger_id == "T8_MORNING"]
    evening_alerts = [a for a in alerts if a.trigger_id == "T8_EVENING"]
    assert len(morning_alerts) == 0, "T8_MORNING already fired today — should be blocked"
    assert len(evening_alerts) == 1, "T8_EVENING should fire on same day as morning"


# --- Three price scenarios ---


def test_t8_scenario_rose():
    """Rose scenario: message contains 'rose' and the up amount. ASCII-safe."""
    # _prices_up(n=10) at _ist(2026,5,19,14): current=14400, prior=14300, delta=+100
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_up(n=10),
        _backtest_accurate(),
        NotificationState(),
        _ist(2026, 5, 19, 10, 0),
    )
    t8m = next(a for a in alerts if a.trigger_id == "T8_MORNING")
    assert "rose" in t8m.body
    assert "up Rs.100" in t8m.body
    assert "₹" not in t8m.body
    assert "Rs." in t8m.body


def test_t8_scenario_dropped():
    """Dropped scenario: message contains 'dropped' and the down amount. ASCII-safe."""
    # _prices_down(n=10) at _ist(2026,5,19,14): current=13600, prior=13700, delta=-100
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_down(n=10),
        _backtest_accurate(),
        NotificationState(),
        _ist(2026, 5, 19, 10, 0),
    )
    t8m = next(a for a in alerts if a.trigger_id == "T8_MORNING")
    assert "dropped" in t8m.body
    assert "down Rs.100" in t8m.body
    assert "₹" not in t8m.body
    assert "Rs." in t8m.body


def test_t8_scenario_flat():
    """Flat scenario: message contains 'held steady'. ASCII-safe."""
    # _prices_flat(n=3): all prices = 14000; at May 19, prior is May 12 = 14000, delta = 0
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_flat(n=3),
        _backtest_accurate(),
        NotificationState(),
        _ist(2026, 5, 19, 10, 0),
    )
    t8m = next(a for a in alerts if a.trigger_id == "T8_MORNING")
    assert "held steady" in t8m.body
    assert "₹" not in t8m.body
    assert "Rs." in t8m.body


# --- Directional hint ---


def test_t8_hint_included_when_companion_success_up():
    """Directional hint appended when chronos_companion status=success and lean=up."""
    alerts = check_triggers(
        _forecast_with_companion(lean_direction="up"),
        _probe(),
        _prices_up(n=10),
        _backtest_accurate(),
        NotificationState(),
        _ist(2026, 5, 19, 10, 0),
    )
    t8m = next(a for a in alerts if a.trigger_id == "T8_MORNING")
    assert "Prices may edge up a little." in t8m.body
    # norm #4: no forecast language — no "will", no probability claim, no time horizon
    assert " will " not in t8m.body
    assert "likely" not in t8m.body
    assert "next few days" not in t8m.body


def test_t8_hint_included_when_companion_success_down():
    """Directional hint appended when chronos_companion status=success and lean=down."""
    alerts = check_triggers(
        _forecast_with_companion(lean_direction="down"),
        _probe(),
        _prices_up(n=10),
        _backtest_accurate(),
        NotificationState(),
        _ist(2026, 5, 19, 10, 0),
    )
    t8m = next(a for a in alerts if a.trigger_id == "T8_MORNING")
    assert "Prices may ease a little." in t8m.body
    assert " will " not in t8m.body
    assert "next few days" not in t8m.body


def test_t8_hint_omitted_when_probe_failed():
    """Directional hint OMITTED when chronos_companion status=failed (no fabrication)."""
    alerts = check_triggers(
        _forecast_companion_failed(),
        _probe(),
        _prices_up(n=10),
        _backtest_accurate(),
        NotificationState(),
        _ist(2026, 5, 19, 10, 0),
    )
    t8m = next(a for a in alerts if a.trigger_id == "T8_MORNING")
    # No directional hint phrases should appear
    assert "edge up" not in t8m.body
    assert "ease" not in t8m.body
    assert "likely" not in t8m.body


def test_t8_hint_omitted_when_no_companion_block():
    """Directional hint OMITTED when forecast has no chronos_companion key."""
    alerts = check_triggers(
        _forecast(),  # no chronos_companion key
        _probe(),
        _prices_up(n=10),
        _backtest_accurate(),
        NotificationState(),
        _ist(2026, 5, 19, 10, 0),
    )
    t8m = next(a for a in alerts if a.trigger_id == "T8_MORNING")
    assert "edge up" not in t8m.body
    assert "ease" not in t8m.body


def test_t8_hint_omitted_when_lean_flat():
    """Directional hint OMITTED when lean_direction=flat (no direction to report)."""
    fc = _forecast_with_companion(lean_direction="flat")
    alerts = check_triggers(
        fc,
        _probe(),
        _prices_up(n=10),
        _backtest_accurate(),
        NotificationState(),
        _ist(2026, 5, 19, 10, 0),
    )
    t8m = next(a for a in alerts if a.trigger_id == "T8_MORNING")
    assert "edge up" not in t8m.body
    assert "ease" not in t8m.body


# --- ASCII-safe ---


def test_t8_ascii_safe_no_rupee_symbol():
    """T8 messages never contain the ₹ symbol (norm #12)."""
    for prices in (_prices_up(n=10), _prices_down(n=10), _prices_flat(n=3)):
        alerts = check_triggers(
            _forecast_with_companion("up"),
            _probe(),
            prices,
            _backtest_accurate(),
            NotificationState(),
            _ist(2026, 5, 19, 10, 0),
        )
        for a in alerts:
            if a.trigger_id.startswith("T8"):
                assert "₹" not in a.title, f"₹ found in {a.trigger_id} title"
                assert "₹" not in a.body, f"₹ found in {a.trigger_id} body"
                assert "Rs." in a.body, f"Rs. missing from {a.trigger_id} body"


# --- Priority ---


def test_t8_priority_2():
    """T8_MORNING and T8_EVENING are priority 2 (informational)."""
    # Morning and evening windows don't overlap; check each trigger's priority separately.
    morning_alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_up(n=10),
        _backtest_accurate(),
        NotificationState(),
        _ist(2026, 5, 19, 10, 0),  # in morning window
    )
    evening_alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_up(n=10),
        _backtest_accurate(),
        NotificationState(),
        _ist(2026, 5, 19, 19, 0),  # in evening window
    )
    t8m = next((a for a in morning_alerts if a.trigger_id == "T8_MORNING"), None)
    t8e = next((a for a in evening_alerts if a.trigger_id == "T8_EVENING"), None)
    assert t8m is not None, "T8_MORNING should fire in morning window"
    assert t8e is not None, "T8_EVENING should fire in evening window"
    assert t8m.priority == 2, "T8_MORNING should be priority 2"
    assert t8e.priority == 2, "T8_EVENING should be priority 2"


# --- T8 does NOT count toward T1+T2+T3 anti-spam cap ---


def test_t8_not_counted_in_t123_cap():
    """T8 fires even when the T1+T2+T3 combined cap is saturated."""
    state = NotificationState()
    now_utc = datetime.now(UTC).isoformat()
    state.sent_today = [
        {"trigger_id": "T1", "sent_at": now_utc},
        {"trigger_id": "T2", "sent_at": now_utc},
        {"trigger_id": "T3", "sent_at": now_utc},
    ]
    state.last_sent["T1"] = now_utc
    state.last_sent["T2"] = now_utc
    state.last_sent["T3"] = now_utc

    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_up(n=10),
        _backtest_accurate(),
        state,
        _ist(2026, 5, 19, 10, 0),
    )
    assert any(a.trigger_id == "T8_MORNING" for a in alerts), (
        "T8_MORNING must fire even when T1/T2/T3 cap is full"
    )


# --- send_pending stamps T8 state dates ---


def test_send_pending_sets_t8_morning_date(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: mock_resp)
    monkeypatch.setenv("NTFY_TOPIC", "test-gold-topic")

    now_ist = _ist(2026, 5, 19, 10, 0)
    alert = PendingAlert(
        trigger_id="T8_MORNING",
        title="Gold morning: Rs.14400 (up Rs.100)",
        body="Gold rose today — Rs.14400 (up Rs.100 from yesterday).",
        priority=2,
        tags=["bell"],
        click_url="https://gaurav-gandhi-2411.github.io/gold-rate-tracker/",
        queued_at=now_ist.isoformat(),
        bypass_quiet=False,
    )
    state = NotificationState()
    assert state.last_t8_morning_ist_date == ""

    send_pending([alert], state, now_ist)

    assert state.last_t8_morning_ist_date == "2026-05-19"


def test_send_pending_sets_t8_evening_date(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: mock_resp)
    monkeypatch.setenv("NTFY_TOPIC", "test-gold-topic")

    now_ist = _ist(2026, 5, 19, 22, 0)
    alert = PendingAlert(
        trigger_id="T8_EVENING",
        title="Gold evening: Rs.14400 (up Rs.100)",
        body="Gold rose today — Rs.14400 (up Rs.100 from yesterday).",
        priority=2,
        tags=["bell"],
        click_url="https://gaurav-gandhi-2411.github.io/gold-rate-tracker/",
        queued_at=now_ist.isoformat(),
        bypass_quiet=True,
    )
    state = NotificationState()
    assert state.last_t8_evening_ist_date == ""

    send_pending([alert], state, now_ist)

    assert state.last_t8_evening_ist_date == "2026-05-19"


# --- Backward-compat: old state without T8 fields loads cleanly ---


def test_t8_backward_compat_old_state_loads(tmp_path: Path):
    """State file without last_t8_* fields loads with empty-string defaults."""
    old_state_json = {
        "schema_version": 1,
        "last_sent": {},
        "queued": [],
        "sent_today": [],
        "last_t5_ist_date": "",
        "last_t6_fired_date_ist": "",
        "last_t4_fired_ist_date": "2026-05-17",
        "last_t7_fired_ist_date": "2026-05-15",
        # deliberately absent: last_t8_morning_ist_date, last_t8_evening_ist_date
    }
    path = tmp_path / "old_notification_state.json"
    import json as _json

    path.write_text(_json.dumps(old_state_json), encoding="utf-8")

    loaded = load_state(path)
    assert loaded.last_t8_morning_ist_date == ""
    assert loaded.last_t8_evening_ist_date == ""
    assert loaded.last_t4_fired_ist_date == "2026-05-17"  # existing fields preserved


def test_t8_state_round_trip(tmp_path: Path):
    """T8 state fields survive a full save/load cycle."""
    state = NotificationState(
        last_t8_morning_ist_date="2026-05-30",
        last_t8_evening_ist_date="2026-05-30",
    )
    path = tmp_path / "notification_state.json"
    save_state(state, path)

    loaded = load_state(path)
    assert loaded.last_t8_morning_ist_date == "2026-05-30"
    assert loaded.last_t8_evening_ist_date == "2026-05-30"


# --- _get_prior_day_price helper ---


def test_get_prior_day_price_returns_most_recent_prior():
    """_get_prior_day_price returns last reading from the day before now_ist."""
    # _prices_up(10) creates readings May 10–19.
    # At May 19 14:00 IST → prior = May 18 reading (base + 8*100 = 14300)
    prices = _prices_up(n=10)
    result = _get_prior_day_price(prices, _ist(2026, 5, 19, 14, 0))
    assert result == 14300  # 13500 + 8 * 100


def test_get_prior_day_price_returns_none_when_no_prior():
    """_get_prior_day_price returns None when all readings are from today or future."""
    from datetime import UTC as _UTC

    # All readings from today (May 19)
    today_ts = datetime(2026, 5, 19, 10, 0, 0, tzinfo=_UTC)  # = 15:30 IST May 19
    prices = [
        {
            "timestamp": today_ts.isoformat().replace("+00:00", "Z"),
            "22k": 14400,
            "24k": 14900,
            "18k": 13900,
            "source": "test",
        }
    ]
    result = _get_prior_day_price(prices, _ist(2026, 5, 19, 14, 0))
    assert result is None


# ---------------------------------------------------------------------------
# T9 — IBJA data feed stale (IST-day deduped) — ADR 025
# ---------------------------------------------------------------------------
# Per ADR 025, IBJA is now the PRIMARY price source and Tanishq an opportunistic
# enrichment. Tanishq scrape staleness is the expected steady state under its
# sustained Cloudflare block and must NOT trip this alert — only a business-day
# gap in IBJA itself (ml.ibja.compute_ibja_gap_business_days) does.
# T9 fires at most once per IST calendar day across all gap sizes >= threshold.
# ---------------------------------------------------------------------------

_T9_NOW_IST = _ist(2026, 6, 7, 14, 0)  # reference time for T9 tests


def _prices_aged(age_h: float, now_ist: datetime = _T9_NOW_IST) -> list[dict]:
    """One-entry prices list where the reading is age_h hours old relative to now_ist."""
    ts = (now_ist.astimezone(UTC) - timedelta(hours=age_h)).isoformat().replace("+00:00", "Z")
    return [{"timestamp": ts, "22k": 14000, "24k": 14500, "18k": 13500, "source": "test"}]


def test_t9_fires_when_ibja_gap_at_threshold():
    """T9 fires when the IBJA business-day gap is >= 2 (the routine threshold)."""
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_aged(9.0),  # Tanishq stale too — expected, must NOT itself trigger anything
        _backtest_accurate(),
        NotificationState(),
        _T9_NOW_IST,
        ibja_gap_days=2,
    )
    t9 = [a for a in alerts if a.trigger_id == "T9"]
    assert len(t9) == 1, "T9 must fire when the IBJA gap is 2 business days"
    assert "₹" not in t9[0].title, "T9 title must be ASCII-safe (no ₹)"
    assert "₹" not in t9[0].body, "T9 body must be ASCII-safe (no ₹)"


def test_t9_no_fire_when_ibja_gap_below_threshold():
    """T9 does not fire when the IBJA gap is 0 or 1 business day (normal weekday lag
    or expected weekend/holiday silence) — regardless of Tanishq's own staleness."""
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_aged(200.0),  # Tanishq badly stale — must be irrelevant to T9 now
        _backtest_accurate(),
        NotificationState(),
        _T9_NOW_IST,
        ibja_gap_days=1,
    )
    assert all(a.trigger_id != "T9" for a in alerts), (
        "T9 must NOT fire on a 1-business-day IBJA gap, however stale Tanishq is"
    )


def test_t9_no_fire_when_ibja_gap_none():
    """T9 does not fire when ibja_gap_days is None (missing/reset IBJA store —
    not a capture failure, same convention as T10)."""
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_aged(9.0),
        _backtest_accurate(),
        NotificationState(),
        _T9_NOW_IST,
        ibja_gap_days=None,
    )
    assert all(a.trigger_id != "T9" for a in alerts)


def test_t9_at_most_one_per_ist_day():
    """Dedup: T9 fires once on first gapped run, not again same IST day.

    Run 1 (10:00 IST): IBJA gap=3 -> T9 fires, IST-date stamped.
    Run 2 (13:00 IST, same day): gap still 3 -> T9 suppressed by dedup.
    """
    now_run1 = _ist(2026, 6, 7, 10, 0)
    now_run2 = _ist(2026, 6, 7, 13, 0)

    state = NotificationState()
    alerts_r1 = check_triggers(
        _forecast(),
        _probe(),
        _prices_aged(1.0, now_run1),
        _backtest_accurate(),
        state,
        now_run1,
        ibja_gap_days=3,
    )
    t9_r1 = [a for a in alerts_r1 if a.trigger_id == "T9"]
    assert len(t9_r1) == 1, "Run 1: T9 must fire (first gapped alert today)"

    # Stamp the IST-date dedup — mirrors what main() does via _stamp_ist_dedup
    _stamp_ist_dedup("T9", state, now_run1)

    alerts_r2 = check_triggers(
        _forecast(),
        _probe(),
        _prices_aged(1.0, now_run2),
        _backtest_accurate(),
        state,
        now_run2,
        ibja_gap_days=3,
    )
    t9_r2 = [a for a in alerts_r2 if a.trigger_id == "T9"]
    assert len(t9_r2) == 0, "Run 2 (same IST day): T9 must NOT fire again — IST-day dedup"


def test_t9_sustained_gap_fires_on_day_2():
    """A sustained IBJA outage spanning two IST days alerts on each day.

    Day 1 (2026-06-07): T9 fired, last_t9_ist_date = "2026-06-07".
    Day 2 (2026-06-08): gap still >= threshold -> T9 must fire again.
    """
    state = NotificationState()
    state.last_t9_ist_date = "2026-06-07"  # simulates Day 1 send

    now_day2 = _ist(2026, 6, 8, 14, 0)
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_aged(1.0, now_day2),
        _backtest_accurate(),
        state,
        now_day2,
        ibja_gap_days=4,
    )
    t9 = [a for a in alerts if a.trigger_id == "T9"]
    assert len(t9) == 1, "T9 must fire on day 2 of a sustained IBJA gap (different IST day)"


def test_t9_not_counted_in_t123_cap():
    """T9 fires even when the T1+T2+T3 combined anti-spam cap is saturated."""
    state = NotificationState()
    now_utc = datetime.now(UTC).isoformat()
    state.sent_today = [
        {"trigger_id": "T1", "sent_at": now_utc},
        {"trigger_id": "T2", "sent_at": now_utc},
        {"trigger_id": "T3", "sent_at": now_utc},
    ]
    state.last_sent["T1"] = now_utc
    state.last_sent["T2"] = now_utc
    state.last_sent["T3"] = now_utc

    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_aged(9.0),
        _backtest_accurate(),
        state,
        _T9_NOW_IST,
        ibja_gap_days=2,
    )
    assert any(a.trigger_id == "T9" for a in alerts), "T9 must fire even when T1/T2/T3 cap is full"


def test_t9_state_round_trip(tmp_path: Path):
    """last_t9_ist_date survives a NotificationState save/load cycle."""
    state = NotificationState(last_t9_ist_date="2026-06-07")
    path = tmp_path / "notification_state.json"
    save_state(state, path)

    loaded = load_state(path)
    assert loaded.last_t9_ist_date == "2026-06-07"


def test_t9_backward_compat_old_state(tmp_path: Path):
    """State file without last_t9_ist_date loads with empty-string default."""
    import json as _json

    old_state = {
        "schema_version": 1,
        "last_sent": {},
        "queued": [],
        "sent_today": [],
        "last_t5_ist_date": "",
        "last_t6_fired_date_ist": "",
        "last_t4_fired_ist_date": "",
        "last_t7_fired_ist_date": "",
        "last_t8_morning_ist_date": "",
        "last_t8_evening_ist_date": "",
        # deliberately absent: last_t9_ist_date
    }
    path = tmp_path / "old_state.json"
    path.write_text(_json.dumps(old_state), encoding="utf-8")

    loaded = load_state(path)
    assert loaded.last_t9_ist_date == ""
    assert loaded.schema_version == 1


def test_send_pending_sets_t9_ist_date(monkeypatch):
    """send_pending stamps last_t9_ist_date when T9 is successfully sent."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: mock_resp)
    monkeypatch.setenv("NTFY_TOPIC", "test-gold-topic")

    now_ist = _ist(2026, 6, 7, 14, 0)
    alert = PendingAlert(
        trigger_id="T9",
        title="Gold Tracker: data stale (9h)",
        body="No new price reading in 9h. Scraper may be failing. Check CI logs.",
        priority=4,
        tags=["warning"],
        click_url="https://gaurav-gandhi-2411.github.io/gold-rate-tracker/",
        queued_at=now_ist.isoformat(),
        bypass_quiet=False,
    )
    state = NotificationState()
    assert state.last_t9_ist_date == ""

    send_pending([alert], state, now_ist)

    assert state.last_t9_ist_date == now_ist.strftime("%Y-%m-%d")


def test_stamp_ist_dedup_t9():
    """_stamp_ist_dedup sets last_t9_ist_date for T9 immediately on queue."""
    state = NotificationState()
    now_ist = _ist(2026, 6, 7, 23, 30)  # quiet hours
    assert state.last_t9_ist_date == ""
    _stamp_ist_dedup("T9", state, now_ist)
    assert state.last_t9_ist_date == "2026-06-07"


# ---------------------------------------------------------------------------
# T9_ESCALATE — Sustained IBJA outage (2x T9 threshold, IST-day deduped)
# ---------------------------------------------------------------------------
# T9 fires once per IST day regardless of how large the IBJA gap gets, so a
# multi-day outage looks identical (priority 4) to a single missed publish.
# This fires a separate, higher-priority alert once the business-day gap
# doubles past the routine T9 threshold, so sustained outages escalate.
# ---------------------------------------------------------------------------


def test_t9_escalate_fires_when_ibja_gap_sustained():
    """T9_ESCALATE fires when the IBJA gap is >= 4 business days."""
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_aged(1.0),
        _backtest_accurate(),
        NotificationState(),
        _T9_NOW_IST,
        ibja_gap_days=4,
    )
    esc = [a for a in alerts if a.trigger_id == "T9_ESCALATE"]
    assert len(esc) == 1, "T9_ESCALATE must fire when the IBJA gap is 4 business days"
    assert esc[0].priority == 5, "T9_ESCALATE must be max priority"
    assert esc[0].bypass_quiet, "T9_ESCALATE must bypass quiet hours — sustained outage is urgent"
    assert "₹" not in esc[0].title
    assert "₹" not in esc[0].body


def test_t9_escalate_no_fire_below_threshold():
    """T9_ESCALATE does not fire when the gap is between the T9 and escalate thresholds."""
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_aged(1.0),
        _backtest_accurate(),
        NotificationState(),
        _T9_NOW_IST,
        ibja_gap_days=2,
    )
    assert all(a.trigger_id != "T9_ESCALATE" for a in alerts), (
        "T9_ESCALATE must NOT fire at gap=2 — only routine T9 should fire"
    )
    assert any(a.trigger_id == "T9" for a in alerts), "T9 should still fire at gap=2"


def test_t9_escalate_fires_alongside_t9():
    """A sustained outage fires both T9 (routine) and T9_ESCALATE (urgent) same run."""
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_aged(1.0),
        _backtest_accurate(),
        NotificationState(),
        _T9_NOW_IST,
        ibja_gap_days=5,
    )
    ids = {a.trigger_id for a in alerts}
    assert {"T9", "T9_ESCALATE"} <= ids, "sustained IBJA gap must fire both T9 and T9_ESCALATE"


def test_t9_escalate_at_most_one_per_ist_day():
    """T9_ESCALATE dedups per IST day, independent of T9's own dedup."""
    now_run1 = _ist(2026, 6, 7, 10, 0)
    now_run2 = _ist(2026, 6, 7, 13, 0)

    state = NotificationState()
    alerts_r1 = check_triggers(
        _forecast(),
        _probe(),
        _prices_aged(1.0, now_run1),
        _backtest_accurate(),
        state,
        now_run1,
        ibja_gap_days=5,
    )
    assert any(a.trigger_id == "T9_ESCALATE" for a in alerts_r1)
    state.last_t9_escalate_ist_date = now_run1.strftime("%Y-%m-%d")

    alerts_r2 = check_triggers(
        _forecast(),
        _probe(),
        _prices_aged(1.0, now_run2),
        _backtest_accurate(),
        state,
        now_run2,
        ibja_gap_days=5,
    )
    assert all(a.trigger_id != "T9_ESCALATE" for a in alerts_r2), (
        "T9_ESCALATE must NOT fire again same IST day"
    )


def test_t9_escalate_state_round_trip(tmp_path: Path):
    """last_t9_escalate_ist_date survives a NotificationState save/load cycle."""
    state = NotificationState(last_t9_escalate_ist_date="2026-06-07")
    path = tmp_path / "notification_state.json"
    save_state(state, path)

    loaded = load_state(path)
    assert loaded.last_t9_escalate_ist_date == "2026-06-07"


def test_t9_escalate_backward_compat_old_state(tmp_path: Path):
    """State file without last_t9_escalate_ist_date loads with empty-string default."""
    import json as _json

    old_state = {
        "schema_version": 1,
        "last_sent": {},
        "queued": [],
        "sent_today": [],
        "last_t9_ist_date": "2026-06-07",
        # deliberately absent: last_t9_escalate_ist_date
    }
    path = tmp_path / "old_state.json"
    path.write_text(_json.dumps(old_state), encoding="utf-8")

    loaded = load_state(path)
    assert loaded.last_t9_escalate_ist_date == ""


def test_send_pending_sets_t9_escalate_ist_date(monkeypatch):
    """send_pending stamps last_t9_escalate_ist_date when T9_ESCALATE is successfully sent."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: mock_resp)
    monkeypatch.setenv("NTFY_TOPIC", "test-gold-topic")

    now_ist = _ist(2026, 6, 7, 14, 0)
    alert = PendingAlert(
        trigger_id="T9_ESCALATE",
        title="Gold Tracker: SUSTAINED outage (17h stale)",
        body="No new price reading in 17h ...",
        priority=5,
        tags=["rotating_light", "warning"],
        click_url="https://gaurav-gandhi-2411.github.io/gold-rate-tracker/",
        queued_at=now_ist.isoformat(),
        bypass_quiet=True,
    )
    state = NotificationState()
    assert state.last_t9_escalate_ist_date == ""

    send_pending([alert], state, now_ist)

    assert state.last_t9_escalate_ist_date == now_ist.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# T10 — Feature-store snapshot capture stalled (IST-day deduped)
# ---------------------------------------------------------------------------
# Protects the direction-model data-accumulation path: a silent bot-pr-sync
# failure (e.g. bug #4) can stop new PIT snapshots landing while price/forecast
# data (T9's concern) stays fresh, so this is checked independently of T9.
# ---------------------------------------------------------------------------

_T10_NOW_IST = _ist(2026, 6, 7, 14, 0)  # reference time for T10 tests


def _write_snapshots_parquet(path: Path, as_of_dates: list[str]) -> None:
    import pandas as pd

    pd.DataFrame({"as_of_date": as_of_dates}).to_parquet(path)


def test_compute_snapshot_gap_days_missing_file(tmp_path: Path):
    """Returns None when the feature store does not exist (not a capture failure)."""
    missing = tmp_path / "does_not_exist.parquet"
    assert compute_snapshot_gap_days(_T10_NOW_IST, path=missing) is None


def test_compute_snapshot_gap_days_fresh(tmp_path: Path):
    """Gap is 0 when the latest snapshot is dated today (IST)."""
    path = tmp_path / "snapshots.parquet"
    _write_snapshots_parquet(path, ["2026-06-05", "2026-06-06", "2026-06-07"])
    assert compute_snapshot_gap_days(_T10_NOW_IST, path=path) == 0


def test_compute_snapshot_gap_days_stale(tmp_path: Path):
    """Gap counts calendar days since the max as_of_date."""
    path = tmp_path / "snapshots.parquet"
    _write_snapshots_parquet(path, ["2026-06-01", "2026-06-02", "2026-06-04"])
    assert compute_snapshot_gap_days(_T10_NOW_IST, path=path) == 3


def test_t10_fires_when_snapshot_gap_exceeds_threshold():
    """T10 fires when snapshot_gap_days >= _T10_GAP_THRESHOLD_DAYS (2)."""
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_aged(1.0, _T10_NOW_IST),
        _backtest_accurate(),
        NotificationState(),
        _T10_NOW_IST,
        snapshot_gap_days=3,
    )
    t10 = [a for a in alerts if a.trigger_id == "T10"]
    assert len(t10) == 1, "T10 must fire when the snapshot gap is 3 days"
    assert "3 days" in t10[0].body
    assert "₹" not in t10[0].title
    assert "₹" not in t10[0].body


def test_t10_no_fire_below_threshold():
    """T10 does not fire when the gap is below the threshold (e.g. 1 day, or None)."""
    for gap in (None, 0, 1):
        alerts = check_triggers(
            _forecast(),
            _probe(),
            _prices_aged(1.0, _T10_NOW_IST),
            _backtest_accurate(),
            NotificationState(),
            _T10_NOW_IST,
            snapshot_gap_days=gap,
        )
        assert all(a.trigger_id != "T10" for a in alerts), f"T10 must not fire at gap={gap}"


def test_t10_at_most_one_per_ist_day():
    """T10 fires once on the first stale run, not again the same IST day."""
    now_run1 = _ist(2026, 6, 7, 10, 0)
    now_run2 = _ist(2026, 6, 7, 13, 0)

    state = NotificationState()
    alerts_r1 = check_triggers(
        _forecast(),
        _probe(),
        _prices_aged(1.0, now_run1),
        _backtest_accurate(),
        state,
        now_run1,
        snapshot_gap_days=3,
    )
    assert len([a for a in alerts_r1 if a.trigger_id == "T10"]) == 1

    _stamp_ist_dedup("T10", state, now_run1)

    alerts_r2 = check_triggers(
        _forecast(),
        _probe(),
        _prices_aged(1.0, now_run2),
        _backtest_accurate(),
        state,
        now_run2,
        snapshot_gap_days=4,  # gap can only grow within the same day
    )
    assert len([a for a in alerts_r2 if a.trigger_id == "T10"]) == 0, (
        "Run 2 (same IST day): T10 must NOT fire again - IST-day dedup"
    )


def test_t10_fires_again_next_ist_day():
    """A sustained gap alerts again on the following IST day (mirrors T9's H4b fix)."""
    state = NotificationState()
    state.last_t10_ist_date = "2026-06-07"

    now_day2 = _ist(2026, 6, 8, 14, 0)
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_aged(1.0, now_day2),
        _backtest_accurate(),
        state,
        now_day2,
        snapshot_gap_days=4,
    )
    assert len([a for a in alerts if a.trigger_id == "T10"]) == 1


def test_t10_state_round_trip(tmp_path: Path):
    """last_t10_ist_date survives a NotificationState save/load cycle."""
    state = NotificationState(last_t10_ist_date="2026-06-07")
    path = tmp_path / "notification_state.json"
    save_state(state, path)

    loaded = load_state(path)
    assert loaded.last_t10_ist_date == "2026-06-07"


def test_t10_backward_compat_old_state(tmp_path: Path):
    """State file without last_t10_ist_date loads with empty-string default."""
    import json as _json

    old_state = {
        "schema_version": 1,
        "last_sent": {},
        "queued": [],
        "sent_today": [],
        "last_t5_ist_date": "",
        "last_t6_fired_date_ist": "",
        "last_t4_fired_ist_date": "",
        "last_t7_fired_ist_date": "",
        "last_t8_morning_ist_date": "",
        "last_t8_evening_ist_date": "",
        "last_t9_ist_date": "",
        # deliberately absent: last_t10_ist_date
    }
    path = tmp_path / "old_state.json"
    path.write_text(_json.dumps(old_state), encoding="utf-8")

    loaded = load_state(path)
    assert loaded.last_t10_ist_date == ""
    assert loaded.schema_version == 1


def test_stamp_ist_dedup_t10():
    """_stamp_ist_dedup sets last_t10_ist_date for T10 immediately on queue."""
    state = NotificationState()
    now_ist = _ist(2026, 6, 7, 23, 30)  # quiet hours
    assert state.last_t10_ist_date == ""
    _stamp_ist_dedup("T10", state, now_ist)
    assert state.last_t10_ist_date == "2026-06-07"


# ---------------------------------------------------------------------------
# T11 — fusion-consensus fallback (both Tanishq and IBJA unavailable this cycle)
# ---------------------------------------------------------------------------

_T11_NOW_IST = _ist(2026, 6, 7, 14, 0)


def _forecast_fusion(fusion_sources: list[str] | None, current_22k: int = 13500) -> dict:
    return {
        "price_source": "fusion_consensus",
        "fusion_sources": fusion_sources,
        "current_22k": current_22k,
        "predicted_22k": current_22k,
    }


def test_t11_fires_when_price_source_is_fusion_consensus():
    """T11 fires when forecast.price_source == 'fusion_consensus'; body names sources."""
    alerts = check_triggers(
        _forecast_fusion(["grt", "malabar"]),
        _probe(),
        _prices_aged(1.0, _T11_NOW_IST),
        _backtest_accurate(),
        NotificationState(),
        _T11_NOW_IST,
    )
    t11 = [a for a in alerts if a.trigger_id == "T11"]
    assert len(t11) == 1, "T11 must fire when price_source is fusion_consensus"
    assert "GRT, Malabar" in t11[0].body
    assert "₹" not in t11[0].title
    assert "₹" not in t11[0].body


def test_t11_silent_when_price_source_is_ibja_calibrated():
    """T11 must not fire on the (healthy) tier-2 IBJA-calibrated path."""
    forecast = {"price_source": "ibja_calibrated", "current_22k": 14500}
    alerts = check_triggers(
        forecast,
        _probe(),
        _prices_aged(1.0, _T11_NOW_IST),
        _backtest_accurate(),
        NotificationState(),
        _T11_NOW_IST,
    )
    assert all(a.trigger_id != "T11" for a in alerts)


def test_t11_silent_when_price_source_is_tanishq_scrape():
    """T11 must not fire on the (healthy) tier-1 fresh-Tanishq-scrape path."""
    forecast = {"price_source": "tanishq_scrape", "current_22k": 14320}
    alerts = check_triggers(
        forecast,
        _probe(),
        _prices_aged(1.0, _T11_NOW_IST),
        _backtest_accurate(),
        NotificationState(),
        _T11_NOW_IST,
    )
    assert all(a.trigger_id != "T11" for a in alerts)


def test_t11_dedup_once_per_ist_day():
    """T11 does not fire again the same IST day it already fired."""
    state = NotificationState(last_t11_ist_date="2026-06-07")
    alerts = check_triggers(
        _forecast_fusion(["grt", "malabar", "kalyan"]),
        _probe(),
        _prices_aged(1.0, _T11_NOW_IST),
        _backtest_accurate(),
        state,
        _T11_NOW_IST,
    )
    assert all(a.trigger_id != "T11" for a in alerts)


def test_t11_fires_alongside_t9_when_both_conditions_met():
    """T9 (IBJA business-day gap) and T11 (this-cycle fusion fallback) are independent
    signals — both must be able to fire in the same check_triggers() call."""
    alerts = check_triggers(
        _forecast_fusion(["grt", "malabar"]),
        _probe(),
        _prices_aged(1.0, _T11_NOW_IST),
        _backtest_accurate(),
        NotificationState(),
        _T11_NOW_IST,
        ibja_gap_days=3,
    )
    assert any(a.trigger_id == "T9" for a in alerts), "T9 must fire (gap >= threshold)"
    assert any(a.trigger_id == "T11" for a in alerts), "T11 must fire (fusion_consensus)"


# ---------------------------------------------------------------------------
# T12 — Tanishq self-hosted job failing repeatedly (runner online, jobs failing)
# ---------------------------------------------------------------------------

_T12_NOW_IST = _ist(2026, 6, 7, 14, 0)


def test_t12_fires_at_three_consecutive_failures():
    """T12 fires once consecutive_job_failures reaches the threshold (3)."""
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_aged(1.0, _T12_NOW_IST),
        _backtest_accurate(),
        NotificationState(),
        _T12_NOW_IST,
        selfhosted_consecutive_failures=3,
    )
    t12 = [a for a in alerts if a.trigger_id == "T12"]
    assert len(t12) == 1, "T12 must fire at 3 consecutive failures"
    assert "3x" in t12[0].title
    assert "3 runs in a row" in t12[0].body


def test_t12_no_fire_below_threshold():
    """T12 does not fire below the threshold, and is silent when the health
    record is missing (None) -- mirrors T9/T10's "missing store isn't a
    failure" convention, since a never-run/reset record isn't itself a signal."""
    for count in (None, 0, 1, 2):
        alerts = check_triggers(
            _forecast(),
            _probe(),
            _prices_aged(1.0, _T12_NOW_IST),
            _backtest_accurate(),
            NotificationState(),
            _T12_NOW_IST,
            selfhosted_consecutive_failures=count,
        )
        assert all(a.trigger_id != "T12" for a in alerts), f"T12 must not fire at count={count}"


def test_t12_dedup_once_per_ist_day():
    """T12 does not fire again the same IST day it already fired."""
    state = NotificationState(last_t12_ist_date="2026-06-07")
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_aged(1.0, _T12_NOW_IST),
        _backtest_accurate(),
        state,
        _T12_NOW_IST,
        selfhosted_consecutive_failures=5,
    )
    assert all(a.trigger_id != "T12" for a in alerts)


def test_t12_fires_again_next_ist_day():
    """A sustained failure streak alerts again on the following IST day."""
    state = NotificationState()
    state.last_t12_ist_date = "2026-06-07"

    now_day2 = _ist(2026, 6, 8, 14, 0)
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_aged(1.0, now_day2),
        _backtest_accurate(),
        state,
        now_day2,
        selfhosted_consecutive_failures=4,
    )
    assert len([a for a in alerts if a.trigger_id == "T12"]) == 1


def test_t12_state_round_trip(tmp_path: Path):
    """last_t12_ist_date survives a NotificationState save/load cycle."""
    state = NotificationState(last_t12_ist_date="2026-06-07")
    path = tmp_path / "notification_state.json"
    save_state(state, path)

    loaded = load_state(path)
    assert loaded.last_t12_ist_date == "2026-06-07"


def test_t12_backward_compat_old_state(tmp_path: Path):
    """State file without last_t12_ist_date loads with empty-string default."""
    import json as _json

    old_state = {
        "schema_version": 1,
        "last_sent": {},
        "queued": [],
        "sent_today": [],
        # deliberately absent: last_t12_ist_date (and everything after T9)
    }
    path = tmp_path / "old_state.json"
    path.write_text(_json.dumps(old_state), encoding="utf-8")

    loaded = load_state(path)
    assert loaded.last_t12_ist_date == ""
    assert loaded.schema_version == 1


def test_stamp_ist_dedup_t12():
    """_stamp_ist_dedup sets last_t12_ist_date for T12 immediately on queue."""
    state = NotificationState()
    now_ist = _ist(2026, 6, 7, 23, 30)  # quiet hours
    assert state.last_t12_ist_date == ""
    _stamp_ist_dedup("T12", state, now_ist)
    assert state.last_t12_ist_date == "2026-06-07"


def test_compute_selfhosted_consecutive_failures_missing_file(tmp_path: Path):
    """Missing health record returns None (not a failure signal)."""
    assert compute_selfhosted_consecutive_failures(tmp_path / "absent.json") is None


def test_compute_selfhosted_consecutive_failures_reads_count(tmp_path: Path):
    """Reads consecutive_job_failures from the health record the self-hosted
    job writes each run."""
    import json as _json

    path = tmp_path / "tanishq_selfhosted_health.json"
    path.write_text(
        _json.dumps({"consecutive_job_failures": 4, "last_job_outcome": "failure"}),
        encoding="utf-8",
    )
    assert compute_selfhosted_consecutive_failures(path) == 4


def test_compute_selfhosted_consecutive_failures_malformed_file(tmp_path: Path):
    """Unparseable health record degrades to None, same as missing."""
    path = tmp_path / "tanishq_selfhosted_health.json"
    path.write_text("not json", encoding="utf-8")
    assert compute_selfhosted_consecutive_failures(path) is None


# ---------------------------------------------------------------------------
# T13 — direction-dataset stall: rows arriving (T10 green) but not usable
# ---------------------------------------------------------------------------
# Regression coverage for the 2026-06-07 -> 2026-08-05 incident: T10 alone
# cannot see this failure mode, since raw rows landed on schedule the whole
# time while every one carried a stale IBJA join.
# ---------------------------------------------------------------------------

_T13_NOW_IST = _ist(2026, 6, 7, 14, 0)


def _write_snapshots_with_ibja_asof(path: Path, rows: list[tuple[str, str | None]]) -> None:
    """rows: list of (as_of_date, ibja_pm_916_asof_date)."""
    import pandas as pd

    pd.DataFrame(
        {
            "as_of_date": [r[0] for r in rows],
            "ibja_pm_916_asof_date": [r[1] for r in rows],
        }
    ).to_parquet(path)


def test_compute_usable_snapshot_gap_days_missing_file(tmp_path: Path):
    """Returns None when the feature store does not exist (not a capture failure)."""
    missing = tmp_path / "does_not_exist.parquet"
    assert compute_usable_snapshot_gap_days(_T13_NOW_IST, path=missing) is None


def test_compute_usable_snapshot_gap_days_all_fresh(tmp_path: Path):
    """Gap is 0 when the latest row's IBJA join is same-day as of today."""
    path = tmp_path / "snapshots.parquet"
    _write_snapshots_with_ibja_asof(
        path,
        [("2026-06-05", "2026-06-05"), ("2026-06-06", "2026-06-06"), ("2026-06-07", "2026-06-07")],
    )
    assert compute_usable_snapshot_gap_days(_T13_NOW_IST, path=path) == 0


def test_compute_usable_snapshot_gap_days_stuck_while_raw_rows_grow(tmp_path: Path):
    """The core regression: raw rows keep landing (max as_of_date == today) but
    every one has a stale IBJA join -- the usable gap must reflect the LAST
    genuinely usable row, not the last row that merely arrived."""
    path = tmp_path / "snapshots.parquet"
    _write_snapshots_with_ibja_asof(
        path,
        [
            ("2026-06-01", "2026-06-01"),  # last usable row
            ("2026-06-05", "2026-06-04"),  # stale: arrived, not usable
            ("2026-06-06", "2026-06-05"),  # stale: arrived, not usable
            ("2026-06-07", "2026-06-06"),  # stale: arrived, not usable (today)
        ],
    )
    # compute_snapshot_gap_days (T10) would report 0 here -- a row landed today.
    assert compute_snapshot_gap_days(_T13_NOW_IST, path=path) == 0
    # compute_usable_snapshot_gap_days (T13) correctly reports the real gap.
    assert compute_usable_snapshot_gap_days(_T13_NOW_IST, path=path) == 6


def test_compute_usable_snapshot_gap_days_ignores_null_ibja_asof(tmp_path: Path):
    """A row with a null ibja_pm_916_asof_date (e.g. IBJA parquet unreadable
    that cycle) must not be misread as usable."""
    path = tmp_path / "snapshots.parquet"
    _write_snapshots_with_ibja_asof(path, [("2026-06-01", "2026-06-01"), ("2026-06-07", None)])
    assert compute_usable_snapshot_gap_days(_T13_NOW_IST, path=path) == 6


def test_compute_usable_snapshot_gap_days_no_usable_row_at_all(tmp_path: Path):
    """Every row stale -> None (not a false 'gap=0' or a crash)."""
    path = tmp_path / "snapshots.parquet"
    _write_snapshots_with_ibja_asof(path, [("2026-06-07", "2026-06-05")])
    assert compute_usable_snapshot_gap_days(_T13_NOW_IST, path=path) is None


def test_t13_fires_when_usable_gap_exceeds_threshold():
    """T13 fires when usable_snapshot_gap_days >= _T13_GAP_THRESHOLD_DAYS (2)."""
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_aged(1.0, _T13_NOW_IST),
        _backtest_accurate(),
        NotificationState(),
        _T13_NOW_IST,
        usable_snapshot_gap_days=6,
    )
    t13 = [a for a in alerts if a.trigger_id == "T13"]
    assert len(t13) == 1, "T13 must fire when the usable gap is 6 days"
    assert "6 days" in t13[0].body
    assert "₹" not in t13[0].title
    assert "₹" not in t13[0].body


def test_t13_no_fire_below_threshold():
    """T13 does not fire when the gap is below the threshold (e.g. 1 day, or None)."""
    for gap in (None, 0, 1):
        alerts = check_triggers(
            _forecast(),
            _probe(),
            _prices_aged(1.0, _T13_NOW_IST),
            _backtest_accurate(),
            NotificationState(),
            _T13_NOW_IST,
            usable_snapshot_gap_days=gap,
        )
        assert all(a.trigger_id != "T13" for a in alerts), f"T13 must not fire at gap={gap}"


def test_t13_independent_of_t10():
    """T13 can fire even when T10 does not -- raw rows arriving (T10 quiet)
    while the usable dataset is stalled (T13 fires) is exactly the bug this
    trigger exists to catch."""
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_aged(1.0, _T13_NOW_IST),
        _backtest_accurate(),
        NotificationState(),
        _T13_NOW_IST,
        snapshot_gap_days=0,  # T10: a row landed today, would not fire
        usable_snapshot_gap_days=6,  # T13: none of them were usable
    )
    assert all(a.trigger_id != "T10" for a in alerts)
    assert len([a for a in alerts if a.trigger_id == "T13"]) == 1


def test_t13_at_most_one_per_ist_day():
    """T13 fires once on the first stale run, not again the same IST day."""
    now_run1 = _ist(2026, 6, 7, 10, 0)
    now_run2 = _ist(2026, 6, 7, 13, 0)

    state = NotificationState()
    alerts_r1 = check_triggers(
        _forecast(),
        _probe(),
        _prices_aged(1.0, now_run1),
        _backtest_accurate(),
        state,
        now_run1,
        usable_snapshot_gap_days=3,
    )
    assert len([a for a in alerts_r1 if a.trigger_id == "T13"]) == 1

    _stamp_ist_dedup("T13", state, now_run1)

    alerts_r2 = check_triggers(
        _forecast(),
        _probe(),
        _prices_aged(1.0, now_run2),
        _backtest_accurate(),
        state,
        now_run2,
        usable_snapshot_gap_days=4,  # gap can only grow within the same day
    )
    assert len([a for a in alerts_r2 if a.trigger_id == "T13"]) == 0, (
        "Run 2 (same IST day): T13 must NOT fire again - IST-day dedup"
    )


def test_t13_fires_again_next_ist_day():
    """A sustained gap alerts again on the following IST day."""
    state = NotificationState()
    state.last_t13_ist_date = "2026-06-07"

    now_day2 = _ist(2026, 6, 8, 14, 0)
    alerts = check_triggers(
        _forecast(),
        _probe(),
        _prices_aged(1.0, now_day2),
        _backtest_accurate(),
        state,
        now_day2,
        usable_snapshot_gap_days=7,
    )
    assert len([a for a in alerts if a.trigger_id == "T13"]) == 1


def test_t13_state_round_trip(tmp_path: Path):
    """last_t13_ist_date survives a NotificationState save/load cycle."""
    state = NotificationState(last_t13_ist_date="2026-06-07")
    path = tmp_path / "notification_state.json"
    save_state(state, path)

    loaded = load_state(path)
    assert loaded.last_t13_ist_date == "2026-06-07"


def test_t13_backward_compat_old_state(tmp_path: Path):
    """State file without last_t13_ist_date loads with empty-string default."""
    import json as _json

    old_state = {
        "schema_version": 1,
        "last_sent": {},
        "queued": [],
        "sent_today": [],
        # deliberately absent: last_t13_ist_date (and everything after T9)
    }
    path = tmp_path / "old_state.json"
    path.write_text(_json.dumps(old_state), encoding="utf-8")

    loaded = load_state(path)
    assert loaded.last_t13_ist_date == ""
    assert loaded.schema_version == 1


def test_stamp_ist_dedup_t13():
    """_stamp_ist_dedup sets last_t13_ist_date for T13 immediately on queue."""
    state = NotificationState()
    now_ist = _ist(2026, 6, 7, 23, 30)  # quiet hours
    assert state.last_t13_ist_date == ""
    _stamp_ist_dedup("T13", state, now_ist)
    assert state.last_t13_ist_date == "2026-06-07"
