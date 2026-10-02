from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from ml.calendar_events import (
    ALL_FESTIVALS,
    get_budget_window_info,
    get_demand_calendar_features,
    get_duty_event_proximity,
    get_festival_info,
    get_wedding_season_info,
)
from ml.duty_schedule import (
    DUTY_TABLE_PATH,
    get_duty_change_dates,
    load_all_rows_including_unverified,
    load_verified_rows,
)

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
# TestDutyCbicJson — data/duty_cbic.json is the single source of truth (D2)
# ---------------------------------------------------------------------------


class TestDutyCbicJson:
    _path: Path = DUTY_TABLE_PATH

    def _table(self) -> dict:
        return json.loads(self._path.read_text(encoding="utf-8"))

    def test_duty_cbic_json_is_valid_json_with_rows(self) -> None:
        table = self._table()
        assert isinstance(table["rows"], list)
        assert len(table["rows"]) >= 1

    def test_verified_rows_have_required_fields(self) -> None:
        required = {
            "effective_date",
            "bcd_pct",
            "aidc_pct",
            "sws_pct",
            "total_duty_pct",
            "notification",
            "source",
            "status",
        }
        for row in load_verified_rows():
            assert required.issubset(row.keys()), f"Missing keys in row: {row}"

    def test_verified_rows_effective_date_is_valid_iso_format(self) -> None:
        for row in load_verified_rows():
            date.fromisoformat(row["effective_date"])  # raises ValueError if invalid

    def test_2024_duty_change_effective_date_is_07_24(self) -> None:
        # The retired duty_events.json dated this event 2024-07-23 (announcement day);
        # the CBIC notification itself says "shall come into force on the 24th day of
        # July, 2024" — duty_cbic.json/D2 use the correct in-force date.
        row = next(r for r in load_verified_rows() if r["effective_date"].startswith("2024-07"))
        assert row["effective_date"] == "2024-07-24"

    def test_2023_02_02_row_present_but_not_a_change_event(self) -> None:
        # Composition-only re-notification (BCD 12.5%->10%, AIDC 2.5%->5%, total
        # unchanged at 15.0%) -- present in `rows` (verified, real notification)
        # but excluded from get_duty_change_dates (not a rate change).
        rows = load_verified_rows()
        row = next(r for r in rows if r["effective_date"] == "2023-02-02")
        assert row["total_duty_pct"] == 15.0
        assert "2023-02-02" not in get_duty_change_dates()

    def test_unverified_pre_2019_rows_excluded_from_verified_loader(self) -> None:
        verified_dates = {r["effective_date"] for r in load_verified_rows()}
        assert "2013-01-01" not in verified_dates
        all_dates = {r["effective_date"] for r in load_all_rows_including_unverified()}
        assert "2013-01-01" in all_dates

    def test_no_other_data_json_file_defines_duty_rows(self) -> None:
        """D2: data/duty_cbic.json is the ONLY file allowed to define duty rows."""

        def _has_duty_rows(obj: object) -> bool:
            if isinstance(obj, dict):
                if "effective_date" in obj and "total_duty_pct" in obj:
                    return True
                return any(_has_duty_rows(v) for v in obj.values())
            if isinstance(obj, list):
                return any(_has_duty_rows(v) for v in obj)
            return False

        offenders: list[str] = []
        for path in (_REPO_ROOT / "data").rglob("*.json"):
            if path == self._path:
                continue
            try:
                obj = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if _has_duty_rows(obj):
                offenders.append(str(path.relative_to(_REPO_ROOT)))
        assert offenders == [], f"duty rows found outside duty_cbic.json: {offenders}"

    def test_duty_events_json_no_longer_exists(self) -> None:
        assert not (_REPO_ROOT / "data" / "duty_events.json").exists()


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


# ---------------------------------------------------------------------------
# TestWeddingSeason (M1: Indian demand calendar, GG spec 2026-09-23)
# ---------------------------------------------------------------------------


class TestWeddingSeason:
    def test_mid_december_is_winter_wedding_season(self) -> None:
        result = get_wedding_season_info(date(2025, 12, 15))
        assert result["is_wedding_season"] is True
        assert result["wedding_season_name"] == "winter"

    def test_winter_season_wraps_year_boundary(self) -> None:
        result = get_wedding_season_info(date(2026, 1, 20))
        assert result["is_wedding_season"] is True
        assert result["wedding_season_name"] == "winter"

    def test_june_is_summer_wedding_season(self) -> None:
        result = get_wedding_season_info(date(2025, 6, 1))
        assert result["is_wedding_season"] is True
        assert result["wedding_season_name"] == "summer"

    def test_august_is_not_wedding_season(self) -> None:
        result = get_wedding_season_info(date(2025, 8, 15))
        assert result["is_wedding_season"] is False
        assert result["wedding_season_name"] is None

    def test_march_is_not_wedding_season(self) -> None:
        result = get_wedding_season_info(date(2025, 3, 10))
        assert result["is_wedding_season"] is False


# ---------------------------------------------------------------------------
# TestBudgetWindow
# ---------------------------------------------------------------------------


class TestBudgetWindow:
    def test_budget_day_itself_in_window(self) -> None:
        assert get_budget_window_info(date(2026, 2, 1))["is_budget_window"] is True

    def test_twelve_days_before_budget_in_window(self) -> None:
        assert get_budget_window_info(date(2026, 1, 20))["is_budget_window"] is True

    def test_thirteen_days_before_budget_not_in_window(self) -> None:
        assert get_budget_window_info(date(2026, 1, 19))["is_budget_window"] is False

    def test_fourteen_days_after_budget_in_window(self) -> None:
        assert get_budget_window_info(date(2026, 2, 15))["is_budget_window"] is True

    def test_fifteen_days_after_budget_not_in_window(self) -> None:
        assert get_budget_window_info(date(2026, 2, 16))["is_budget_window"] is False

    def test_mid_year_not_in_window(self) -> None:
        assert get_budget_window_info(date(2026, 7, 1))["is_budget_window"] is False


# ---------------------------------------------------------------------------
# TestDutyEventProximityProduction (production version of duty_change_active)
# ---------------------------------------------------------------------------


def _write_duty_table(path: Path, rows: list[dict]) -> None:
    """Write a minimal duty_cbic.json-shaped table (verified rows only)."""
    path.write_text(json.dumps({"rows": rows, "unverified_pre_2019": {"rows": []}}))


class TestDutyEventProximityProduction:
    def test_recent_after_2026_hike(self, tmp_path: Path) -> None:
        events_path = tmp_path / "duty_cbic.json"
        _write_duty_table(events_path, [{"effective_date": "2026-05-13", "total_duty_pct": 15.0}])

        result = get_duty_event_proximity(date(2026, 5, 20), path=events_path)
        assert result["is_duty_event_recent"] is True
        assert result["days_since_duty_event"] == 7

    def test_not_recent_60_days_later(self, tmp_path: Path) -> None:
        events_path = tmp_path / "duty_cbic.json"
        _write_duty_table(events_path, [{"effective_date": "2026-05-13", "total_duty_pct": 15.0}])

        result = get_duty_event_proximity(date(2026, 7, 12), path=events_path)
        assert result["is_duty_event_recent"] is False

    def test_no_events_before_query_date(self, tmp_path: Path) -> None:
        events_path = tmp_path / "duty_cbic.json"
        _write_duty_table(events_path, [{"effective_date": "2026-05-13", "total_duty_pct": 15.0}])

        result = get_duty_event_proximity(date(2020, 1, 1), path=events_path)
        assert result["days_since_duty_event"] == 9999
        assert result["is_duty_event_recent"] is False

    def test_composition_only_change_is_not_an_event(self, tmp_path: Path) -> None:
        events_path = tmp_path / "duty_cbic.json"
        _write_duty_table(
            events_path,
            [
                {"effective_date": "2022-07-01", "total_duty_pct": 15.0},
                {"effective_date": "2023-02-02", "total_duty_pct": 15.0},  # composition-only
            ],
        )
        result = get_duty_event_proximity(date(2023, 2, 5), path=events_path)
        # Nearest real change event is still 2022-07-01, not the 2023-02-02 re-notification.
        assert result["days_since_duty_event"] == (date(2023, 2, 5) - date(2022, 7, 1)).days

    def test_matches_real_duty_cbic_json(self) -> None:
        # Sanity check against the real committed file — must not raise.
        result = get_duty_event_proximity(date(2026, 9, 23))
        assert isinstance(result["days_since_duty_event"], int)
        assert result["days_since_duty_event"] < 9999  # 2026-05-13 event is on record


# ---------------------------------------------------------------------------
# TestDemandCalendarFeatures (combined entry point)
# ---------------------------------------------------------------------------


class TestDemandCalendarFeatures:
    def test_combines_all_four_flag_groups(self) -> None:
        result = get_demand_calendar_features(date(2025, 4, 30))  # Akshaya Tritiya 2025
        expected_keys = {
            "is_festival_window",
            "festival_name",
            "days_to_next_festival",
            "is_wedding_season",
            "wedding_season_name",
            "is_budget_window",
            "is_duty_event_recent",
            "days_since_duty_event",
        }
        assert expected_keys.issubset(result.keys())
        assert result["is_festival_window"] is True
