"""Tests for ml/forecast.py — _target_time() and load_combined_history()."""

import json
import random
from datetime import datetime, timedelta, timezone

import pytest

import ml.forecast as fc
from ml.forecast import _target_time, load_combined_history


# ---------------------------------------------------------------------------
# _target_time tests
# ---------------------------------------------------------------------------

def test_target_time_always_future():
    """target_time() must be strictly future for 100 random 'now' timestamps."""
    rng = random.Random(42)
    for _ in range(100):
        hour = rng.randint(0, 23)
        minute = rng.randint(0, 59)
        second = rng.randint(0, 59)
        now = datetime(2026, 5, 9, hour, minute, second, tzinfo=timezone.utc)
        target = _target_time(now)
        assert target > now, f"target_time {target} not after now {now}"


def test_target_time_is_midnight():
    now = datetime(2026, 5, 9, 14, 30, 45, tzinfo=timezone.utc)
    target = _target_time(now)
    assert target.hour == 0
    assert target.minute == 0
    assert target.second == 0
    assert target.microsecond == 0


def test_target_time_is_next_day():
    now = datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)
    target = _target_time(now)
    assert target.date() == (now + timedelta(days=1)).date()


def test_target_time_midnight_edge():
    """Even at 23:59:59 the target is the following day's midnight."""
    now = datetime(2026, 5, 9, 23, 59, 59, tzinfo=timezone.utc)
    target = _target_time(now)
    assert target > now
    assert target.date() == datetime(2026, 5, 10).date()


def test_target_time_default_uses_utc_now():
    before = datetime.now(timezone.utc)
    target = _target_time()
    after = datetime.now(timezone.utc)
    # target should be after 'before' and after 'after' (it's tomorrow midnight)
    assert target > before
    assert target > after


# ---------------------------------------------------------------------------
# load_combined_history tests
# ---------------------------------------------------------------------------

def _entry(ts: str, price: int, source: str = "test") -> dict:
    return {
        "timestamp": ts,
        "22k": price,
        "24k": int(round(price * 24 / 22)),
        "18k": int(round(price * 18 / 22)),
        "source": source,
    }


def test_four_readings_same_day_gives_one_row(tmp_path, monkeypatch):
    """4 live readings on one day should collapse to 1 row (the last)."""
    prices = [
        _entry("2026-05-09T00:00:00.000Z", 9000),
        _entry("2026-05-09T06:00:00.000Z", 9010),
        _entry("2026-05-09T12:00:00.000Z", 9020),
        _entry("2026-05-09T18:00:00.000Z", 9030),
    ]
    monkeypatch.setattr(fc, "DATA_DIR", tmp_path)
    (tmp_path / "prices.json").write_text(json.dumps(prices))
    (tmp_path / "history_seed.json").write_text("[]")

    df = load_combined_history()
    assert len(df) == 1
    assert df.iloc[0]["22k"] == 9030


def test_last_reading_of_day_wins(tmp_path, monkeypatch):
    """Resampling picks the last reading, not the first or middle."""
    prices = [
        _entry("2026-05-09T00:00:00.000Z", 9000),
        _entry("2026-05-09T18:00:00.000Z", 9999),
        _entry("2026-05-09T12:00:00.000Z", 9500),
    ]
    monkeypatch.setattr(fc, "DATA_DIR", tmp_path)
    (tmp_path / "prices.json").write_text(json.dumps(prices))
    (tmp_path / "history_seed.json").write_text("[]")

    df = load_combined_history()
    assert df.iloc[0]["22k"] == 9999


def test_overlap_live_wins_over_seed(tmp_path, monkeypatch):
    """When seed and prices.json share a date, prices.json wins."""
    seed = [_entry("2026-05-09T00:00:00.000Z", 9000, "seed")]
    prices = [_entry("2026-05-09T18:00:00.000Z", 9100, "live")]

    monkeypatch.setattr(fc, "DATA_DIR", tmp_path)
    (tmp_path / "history_seed.json").write_text(json.dumps(seed))
    (tmp_path / "prices.json").write_text(json.dumps(prices))

    df = load_combined_history()
    assert len(df) == 1
    assert df.iloc[0]["22k"] == 9100


def test_non_overlapping_dates_concatenated(tmp_path, monkeypatch):
    """Distinct dates from seed and live are both present."""
    seed = [_entry("2026-05-07T00:00:00.000Z", 9000, "seed")]
    prices = [_entry("2026-05-09T18:00:00.000Z", 9100, "live")]

    monkeypatch.setattr(fc, "DATA_DIR", tmp_path)
    (tmp_path / "history_seed.json").write_text(json.dumps(seed))
    (tmp_path / "prices.json").write_text(json.dumps(prices))

    df = load_combined_history()
    assert len(df) == 2
    assert list(df["22k"]) == [9000, 9100]


def test_no_data_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(fc, "DATA_DIR", tmp_path)
    (tmp_path / "history_seed.json").write_text("[]")
    (tmp_path / "prices.json").write_text("[]")

    with pytest.raises(RuntimeError, match="No data found"):
        load_combined_history()


def test_seed_only_works(tmp_path, monkeypatch):
    """Works when prices.json is absent."""
    seed = [_entry("2026-05-09T00:00:00.000Z", 9000, "seed")]

    monkeypatch.setattr(fc, "DATA_DIR", tmp_path)
    (tmp_path / "history_seed.json").write_text(json.dumps(seed))
    # prices.json absent

    df = load_combined_history()
    assert len(df) == 1
    assert df.iloc[0]["22k"] == 9000


def test_result_sorted_by_date(tmp_path, monkeypatch):
    """Returned DataFrame is sorted ascending by date."""
    seed = [
        _entry("2026-05-07T00:00:00.000Z", 9000, "seed"),
        _entry("2026-05-05T00:00:00.000Z", 8900, "seed"),
    ]
    monkeypatch.setattr(fc, "DATA_DIR", tmp_path)
    (tmp_path / "history_seed.json").write_text(json.dumps(seed))
    (tmp_path / "prices.json").write_text("[]")

    df = load_combined_history()
    assert list(df["22k"]) == [8900, 9000]
