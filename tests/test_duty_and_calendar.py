from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from ml.calendar_events import ALL_FESTIVALS, get_festival_info

# ---------------------------------------------------------------------------
# Helper defined in test file (not a production export)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent


def duty_change_active(query_date: date, events: list[dict]) -> tuple[bool, int]:
    """
    Computes duty-change proximity for a given date against an event list.

    Returns:
      (active, days_since) where active=True if any event fell within the past
      30 days (inclusive), and days_since is the number of days since the most
      recent event on or before query_date (9999 if none exists).
    """
    past_events = [e for e in events if date.fromisoformat(e["date"]) <= query_date]
    if not past_events:
        return False, 9999

    most_recent = max(past_events, key=lambda e: date.fromisoformat(e["date"]))
    days_since = (query_date - date.fromisoformat(most_recent["date"])).days
    active = days_since <= 30
    return active, days_since


# ---------------------------------------------------------------------------
# TestFestivalWindows
# ---------------------------------------------------------------------------


class TestFestivalWindows:
    def test_akshaya_tritiya_2025_in_window(self) -> None:
        result = get_festival_info(date(2025, 4, 30))
        assert result["is_festival_window"] is True
        assert "Akshaya" in str(result["festival_name"])

    def test_three_days_before_dhanteras_2024_in_window(self) -> None:
        # 2024-10-29 - 3 days = 2024-10-26
        result = get_festival_info(date(2024, 10, 26))
        assert result["is_festival_window"] is True
        assert "Dhanteras" in str(result["festival_name"])

    def test_four_days_before_festival_not_in_window(self) -> None:
        # 2024-10-29 - 4 days = 2024-10-25; outside the ±3-day window
        result = get_festival_info(date(2024, 10, 25))
        assert result["is_festival_window"] is False

    def test_navratri_2024_start_in_window(self) -> None:
        result = get_festival_info(date(2024, 10, 3))
        assert result["is_festival_window"] is True
        assert "Navratri" in str(result["festival_name"])

    def test_navratri_2024_day9_in_window(self) -> None:
        # anchor 2024-10-03 + 9 days = 2024-10-12 (last day of window)
        result = get_festival_info(date(2024, 10, 12))
        assert result["is_festival_window"] is True

    def test_navratri_2024_day10_not_in_window(self) -> None:
        # anchor 2024-10-03 + 10 days = 2024-10-13; outside window_after=9
        result = get_festival_info(date(2024, 10, 13))
        assert result["is_festival_window"] is False

    def test_non_festival_date_returns_false(self) -> None:
        result = get_festival_info(date(2026, 3, 1))
        assert result["is_festival_window"] is False
        assert result["festival_name"] is None

    def test_days_to_next_festival_is_zero_when_in_window(self) -> None:
        result = get_festival_info(date(2025, 4, 30))
        assert result["days_to_next_festival"] == 0

    def test_days_to_next_festival_positive_outside_window(self) -> None:
        result = get_festival_info(date(2026, 1, 1))
        assert result["days_to_next_festival"] > 0

    def test_all_festivals_has_required_keys(self) -> None:
        required = {"name", "anchor_dates", "window_before", "window_after"}
        for entry in ALL_FESTIVALS:
            assert required.issubset(entry.keys()), f"Missing keys in entry: {entry}"


# ---------------------------------------------------------------------------
# TestDutyEventsJson
# ---------------------------------------------------------------------------


class TestDutyEventsJson:
    _path: Path = _REPO_ROOT / "data" / "duty_events.json"

    def _load(self) -> list[dict]:
        return json.loads(self._path.read_text(encoding="utf-8"))

    def test_duty_events_json_is_valid_json(self) -> None:
        data = self._load()
        assert isinstance(data, list)

    def test_duty_events_json_has_at_least_one_entry(self) -> None:
        data = self._load()
        assert len(data) >= 1

    def test_duty_events_each_entry_has_required_fields(self) -> None:
        required = {"date", "event_type", "direction", "magnitude_pct", "note", "source"}
        for entry in self._load():
            assert required.issubset(entry.keys()), f"Missing keys in entry: {entry}"

    def test_duty_events_date_is_valid_iso_format(self) -> None:
        for entry in self._load():
            date.fromisoformat(entry["date"])  # raises ValueError if invalid

    def test_duty_events_direction_is_valid_enum(self) -> None:
        valid = {"cut", "increase"}
        for entry in self._load():
            assert entry["direction"] in valid, f"Invalid direction: {entry['direction']}"

    def test_duty_2024_entry_present(self) -> None:
        data = self._load()
        assert any(e["date"] == "2024-07-23" and e["direction"] == "cut" for e in data)


# ---------------------------------------------------------------------------
# TestDutyJoinLogic
# ---------------------------------------------------------------------------

_SAMPLE_EVENTS: list[dict] = [
    {
        "date": "2024-07-23",
        "event_type": "duty_change",
        "direction": "cut",
        "magnitude_pct": None,
        "note": "",
        "source": "",
    }
]


class TestDutyJoinLogic:
    def test_duty_active_30_days_after_cut(self) -> None:
        # 2024-07-23 + 9 days = 2024-08-01
        active, days_since = duty_change_active(date(2024, 8, 1), _SAMPLE_EVENTS)
        assert active is True
        assert days_since == 9

    def test_duty_inactive_31_days_after_cut(self) -> None:
        # 2024-07-23 + 32 days = 2024-08-24
        active, days_since = duty_change_active(date(2024, 8, 24), _SAMPLE_EVENTS)
        assert active is False
        assert days_since == 32

    def test_duty_no_events_before_date(self) -> None:
        active, days_since = duty_change_active(date(2020, 1, 1), _SAMPLE_EVENTS)
        assert active is False
        assert days_since == 9999
