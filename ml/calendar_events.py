from __future__ import annotations

from datetime import date, timedelta

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
