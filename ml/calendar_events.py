from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from ml.duty_schedule import DUTY_TABLE_PATH, duty_change_proximity, get_duty_change_dates

# ±3-day window applies to single-day festivals (Akshaya Tritiya, Dhanteras, Diwali).
FESTIVAL_WINDOW_DAYS_BEFORE: int = 3
FESTIVAL_WINDOW_DAYS_AFTER: int = 3

# Navratri spans 9 nights, so window_before=0, window_after=9 (anchor = first night).
_NAVRATRI_WINDOW_BEFORE: int = 0
_NAVRATRI_WINDOW_AFTER: int = 9

ALL_FESTIVALS: list[dict] = [
    {
        "name": "Akshaya Tritiya",
        "anchor_dates": [
            date(2022, 5, 3),
            date(2023, 4, 22),
            date(2024, 5, 10),
            date(2025, 4, 30),
            date(2026, 5, 19),
            date(2027, 5, 9),
        ],
        "window_before": FESTIVAL_WINDOW_DAYS_BEFORE,
        "window_after": FESTIVAL_WINDOW_DAYS_AFTER,
    },
    {
        "name": "Dhanteras",
        "anchor_dates": [
            date(2022, 10, 22),
            date(2023, 11, 10),
            date(2024, 10, 29),
            date(2025, 10, 20),
            date(2026, 11, 7),
            date(2027, 10, 28),
        ],
        "window_before": FESTIVAL_WINDOW_DAYS_BEFORE,
        "window_after": FESTIVAL_WINDOW_DAYS_AFTER,
    },
    {
        "name": "Diwali",
        "anchor_dates": [
            date(2022, 10, 24),
            date(2023, 11, 12),
            date(2024, 11, 1),
            date(2025, 10, 20),
            date(2026, 11, 8),
            date(2027, 10, 29),
        ],
        "window_before": FESTIVAL_WINDOW_DAYS_BEFORE,
        "window_after": FESTIVAL_WINDOW_DAYS_AFTER,
    },
    {
        "name": "Navratri",
        "anchor_dates": [
            date(2022, 10, 2),
            date(2023, 10, 15),
            date(2024, 10, 3),
            date(2025, 9, 29),
            date(2026, 10, 20),
            date(2027, 10, 10),
        ],
        "window_before": _NAVRATRI_WINDOW_BEFORE,
        "window_after": _NAVRATRI_WINDOW_AFTER,
    },
]


def _in_window(query: date, anchor: date, before: int, after: int) -> bool:
    return (anchor - timedelta(days=before)) <= query <= (anchor + timedelta(days=after))


def get_festival_info(query_date: date) -> dict[str, object]:
    """
    Returns festival proximity info for a given calendar date.

    Keys in the returned dict:
      is_festival_window: bool
      festival_name: str | None   — first matching festival name; None if not in any window
      days_to_next_festival: int  — 0 if currently in a window; else days to nearest
                                    upcoming anchor date on or after query_date
    """
    matched_name: str | None = None
    for festival in ALL_FESTIVALS:
        name: str = festival["name"]  # type: ignore[assignment]
        anchors: list[date] = festival["anchor_dates"]  # type: ignore[assignment]
        before: int = festival["window_before"]  # type: ignore[assignment]
        after: int = festival["window_after"]  # type: ignore[assignment]
        for anchor in anchors:
            if _in_window(query_date, anchor, before, after):
                matched_name = name
                break
        if matched_name is not None:
            break

    if matched_name is not None:
        return {
            "is_festival_window": True,
            "festival_name": matched_name,
            "days_to_next_festival": 0,
        }

    # Not in any window — find the nearest upcoming anchor date.
    min_days: int = 9999
    for festival in ALL_FESTIVALS:
        anchors = festival["anchor_dates"]  # type: ignore[assignment]
        for anchor in anchors:
            if anchor >= query_date:
                delta = (anchor - query_date).days
                if delta < min_days:
                    min_days = delta

    return {
        "is_festival_window": False,
        "festival_name": None,
        "days_to_next_festival": min_days,
    }


# ---------------------------------------------------------------------------
# Wedding season (M1: Indian demand calendar, GG spec 2026-09-23)
#
# Approximate solar-calendar windows for the two broad Indian wedding
# seasons. Real auspicious dates (vivah muhurat) follow the lunar Hindu
# calendar and exclude Kharmas/Malmas and Guru/Shukra Ast periods, which
# shift year to year and require a panchang to compute exactly -- these
# fixed month/day windows are a deliberately broad approximation of demand
# seasonality, not a muhurat calendar. Good enough for a coarse "is this
# roughly wedding season" feature; not precise enough to flag individual
# auspicious dates.
# ---------------------------------------------------------------------------

WEDDING_SEASONS: list[dict] = [
    {"name": "winter", "start": (11, 15), "end": (2, 15)},  # wraps year-end
    {"name": "summer", "start": (4, 14), "end": (7, 15)},
]


def _month_day_in_range(query: date, start_md: tuple[int, int], end_md: tuple[int, int]) -> bool:
    start = date(query.year, *start_md)
    end = date(query.year, *end_md)
    if start <= end:
        return start <= query <= end
    # Range wraps the year boundary (e.g. Nov 15 -> Feb 15).
    return query >= start or query <= end


def get_wedding_season_info(query_date: date) -> dict[str, object]:
    """Returns {"is_wedding_season": bool, "wedding_season_name": str | None}."""
    for season in WEDDING_SEASONS:
        if _month_day_in_range(query_date, season["start"], season["end"]):
            return {"is_wedding_season": True, "wedding_season_name": season["name"]}
    return {"is_wedding_season": False, "wedding_season_name": None}


# ---------------------------------------------------------------------------
# Union Budget window (M1: duty/policy-announcement seasonality)
#
# The Union Budget has been presented on 2026-02-01 every year since the
# 2017 reform (previously the last working day of February). Basic-customs-
# duty changes on gold are announced in the Budget more often than at any
# other time of year (see data/duty_cbic.json: 2019-07-06 and 2021-02-02
# and 2022-07-01 are Budget-adjacent; only 2013's crisis-era hikes and
# 2024/2026 are not). The window below covers pre-Budget speculation and
# post-Budget adjustment, not just the single announcement day.
# ---------------------------------------------------------------------------

_BUDGET_ANCHOR_MONTH_DAY = (2, 1)
_BUDGET_WINDOW_DAYS_BEFORE = 12
_BUDGET_WINDOW_DAYS_AFTER = 14


def get_budget_window_info(query_date: date) -> dict[str, object]:
    """Returns {"is_budget_window": bool}."""
    anchor = date(query_date.year, *_BUDGET_ANCHOR_MONTH_DAY)
    in_window = (
        (anchor - timedelta(days=_BUDGET_WINDOW_DAYS_BEFORE))
        <= query_date
        <= (anchor + timedelta(days=_BUDGET_WINDOW_DAYS_AFTER))
    )
    return {"is_budget_window": in_window}


# ---------------------------------------------------------------------------
# Duty/cess event proximity (data/duty_cbic.json — see ml.duty_schedule)
# ---------------------------------------------------------------------------


def get_duty_event_proximity(query_date: date, path: Path = DUTY_TABLE_PATH) -> dict[str, object]:
    """Returns {"is_duty_event_recent": bool, "days_since_duty_event": int}.

    days_since_duty_event is 9999 if no duty change event (verified rows only,
    composition-only re-notifications like 2023-02-02 excluded — see
    ml.duty_schedule) has occurred on or before query_date. is_duty_event_recent
    is True within ml.duty_schedule.DUTY_EVENT_RECENCY_DAYS of the most recent one.
    """
    change_dates = get_duty_change_dates(path)
    is_recent, days_since = duty_change_proximity(query_date, change_dates)
    return {"is_duty_event_recent": is_recent, "days_since_duty_event": days_since}


def get_demand_calendar_features(
    query_date: date, duty_events_path: Path = DUTY_TABLE_PATH
) -> dict[str, object]:
    """Single entry point combining festival + wedding-season + budget-window
    + duty-event-proximity flags for feature construction (M1)."""
    features: dict[str, object] = {}
    features.update(get_festival_info(query_date))
    features.update(get_wedding_season_info(query_date))
    features.update(get_budget_window_info(query_date))
    features.update(get_duty_event_proximity(query_date, path=duty_events_path))
    return features
