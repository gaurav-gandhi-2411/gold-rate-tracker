"""Price-move alerts after 2026-09-28: T3 compares with the last DIFFERENT price, and T15 alerts on
the official benchmark's own move when no shop reading arrives. Plus the estimate's use of a newer
morning benchmark rate (ml.inference._try_ibja_calibrated)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
from ml import inference
from ml.notifications import (
    IbjaMove,
    NotificationState,
    _check_t3,
    _check_t15_ibja_move,
    compute_ibja_move,
)

IST = timedelta(hours=5, minutes=30)
NOW = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)  # 19:30 IST, outside quiet hours
CAL = {
    "valid": True,
    "slope": 1.01,
    "intercept": 3.0,
    "residual_std": 50.0,
    "residual_abs_quantiles": {"80": 77.0},
}


def _row(ts: datetime, v: int) -> dict:
    return {"timestamp": ts.isoformat().replace("+00:00", "Z"), "22k": v, "source": "t"}


def _t3(prices: list[dict], state: NotificationState | None = None):
    return _check_t3({}, {}, prices, {}, state or NotificationState(), NOW.astimezone(UTC))


# --- T3 -----------------------------------------------------------------------------------------


def test_t3_sees_a_drop_even_after_two_scrapes_at_the_new_price():
    """2026-09-28: 14,040 -> 13,710 at 13:45Z and 13,710 again at 14:07Z. The old rule compared the
    last two readings (13,710 vs 13,710) and missed the drop once the second scrape landed."""
    prices = [
        _row(NOW - timedelta(hours=9), 14040),
        _row(NOW - timedelta(minutes=15), 13710),
        _row(NOW - timedelta(minutes=5), 13710),
    ]
    alert = _t3(prices)
    assert alert is not None and "Rs. 330" in alert.title and "down" in alert.title


def test_t3_fires_once_per_change():
    """Change first seen 6 h ago; T3 sent 5 h ago (after the change, and outside the 4 h
    cooldown), so only the once-per-change rule can stop a repeat."""
    prices = [
        _row(NOW - timedelta(hours=9), 14040),
        _row(NOW - timedelta(hours=6), 13710),
        _row(NOW - timedelta(hours=1), 13710),
    ]
    state = NotificationState()
    state.last_sent["T3"] = (NOW - timedelta(hours=5)).isoformat()
    assert _t3(prices, state) is None
    # same prices, T3 last sent BEFORE the change: it fires
    state.last_sent["T3"] = (NOW - timedelta(hours=7)).isoformat()
    assert _t3(prices, state) is not None


def test_t3_ignores_a_change_older_than_24h():
    prices = [_row(NOW - timedelta(hours=40), 14040), _row(NOW - timedelta(hours=30), 13710)]
    assert _t3(prices) is None


def test_t3_small_move_does_not_fire():
    prices = [_row(NOW - timedelta(hours=3), 14040), _row(NOW - timedelta(hours=1), 13940)]
    assert _t3(prices) is None


# --- T15 ----------------------------------------------------------------------------------------


def _ibja(tmp_path: Path, rows: list[tuple[str, float | None, float | None]]) -> Path:
    p = tmp_path / "ibja_rates.parquet"
    pd.DataFrame([{"date": d, "am_916": am, "pm_916": pm} for d, am, pm in rows]).to_parquet(
        p, index=False
    )
    return p


def test_compute_ibja_move_uses_the_latest_two_fixes(tmp_path: Path):
    # 2026-09-28: Friday PM 139,336 -> Monday AM 135,612 per 10 g
    p = _ibja(tmp_path, [("2026-09-25", 138441.0, 139336.0), ("2026-09-28", 135612.0, None)])
    m = compute_ibja_move(p, CAL)
    assert m is not None and m.fix == "am" and m.fix_date == "2026-09-28"
    assert m.published_utc == "2026-09-28T06:30:00+00:00"
    assert m.delta_per_gram == round(1.01 * (135612.0 - 139336.0) / 10)  # -376
    assert compute_ibja_move(p, {"valid": False}) is None


def _move(delta: int, fix: str = "am") -> IbjaMove:
    return IbjaMove("2026-09-28", fix, "2026-09-28T06:30:00+00:00", delta, 13755)


def test_t15_fires_on_a_large_benchmark_move():
    alert = _check_t15_ibja_move(_move(-376), NotificationState(), NOW)
    assert alert is not None and alert.trigger_id == "T15" and alert.priority == 5
    assert "down Rs. 376" in alert.title and "this morning" in alert.body
    assert "IBJA" not in alert.title + alert.body


def test_t15_once_per_fix_and_not_after_a_t3():
    state = NotificationState()
    state.last_sent["T15"] = "2026-09-28T07:00:00+00:00"  # after the 06:30 AM fix
    assert _check_t15_ibja_move(_move(-376), state, NOW) is None
    state = NotificationState()
    state.last_sent["T3"] = datetime.now(UTC).isoformat()  # the shop price already told it
    assert _check_t15_ibja_move(_move(-376), state, NOW) is None


def test_t15_small_move_does_not_fire():
    assert _check_t15_ibja_move(_move(-149), NotificationState(), NOW) is None
    assert _check_t15_ibja_move(None, NotificationState(), NOW) is None


# --- estimate uses a newer morning rate -----------------------------------------------------------


def _write(tmp_path: Path, rows: list[dict]) -> Path:
    pd.DataFrame(rows).to_parquet(tmp_path / "ibja_rates.parquet", index=False)
    (tmp_path / "prices.json").write_text(json.dumps([]), encoding="utf-8")
    return tmp_path


def test_estimate_uses_todays_morning_rate_when_newer_than_the_last_afternoon_rate(tmp_path: Path):
    d = _write(
        tmp_path,
        [
            {"date": "2026-09-25", "am_916": 138441.0, "pm_916": 139336.0},
            {"date": "2026-09-28", "am_916": 135612.0, "pm_916": None},
        ],
    )
    r = inference._try_ibja_calibrated(CAL, d, NOW)
    assert r is not None
    assert r[0] == round(1.01 * 13561.2 + 3.0)
    assert r[4] == "2026-09-28T06:30:00+00:00"
    assert r[7] == "same_day"


def test_estimate_keeps_the_afternoon_rate_when_it_is_the_newest(tmp_path: Path):
    d = _write(tmp_path, [{"date": "2026-09-28", "am_916": 135612.0, "pm_916": 135660.0}])
    r = inference._try_ibja_calibrated(CAL, d, NOW)
    assert r is not None and r[0] == round(1.01 * 13566.0 + 3.0)
    assert r[4] == "2026-09-28T11:30:00+00:00"


def test_estimate_without_a_morning_column_still_works(tmp_path: Path):
    d = _write(tmp_path, [{"date": "2026-09-25", "pm_916": 139336.0}])
    r = inference._try_ibja_calibrated(CAL, d, NOW)
    assert r is not None and r[0] == round(1.01 * 13933.6 + 3.0)
