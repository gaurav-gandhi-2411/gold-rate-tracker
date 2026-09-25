"""ml.duty_schedule — single reader for data/duty_cbic.json (D2 migration, 2026-09-24).

data/duty_cbic.json is the ONLY file in this repo that defines India gold import duty
rates and change dates. It has two keys:

  "rows"                 — CBIC-notification-cited rows, 2019-07-06 onward. Each row:
                            effective_date, bcd_pct, aidc_pct, sws_pct, total_duty_pct,
                            notification, effective_date_basis, source, status.
  "unverified_pre_2019"  — {"rows": [...]}, press-sourced-only 2013 current-account-
                            deficit-crisis hikes (month-level precision, no CBIC
                            notification number found), BCD-only. Read ONLY by
                            ml.inr_proxy's long-history pretraining proxy via
                            load_all_rows_including_unverified() below — never by
                            premium/research code or by the live feature pipeline,
                            both of which must stay CBIC-cited-only and call
                            load_verified_rows()/get_duty_change_dates() instead.

data/duty_events.json (the old file) is retired; nothing in this repo should read it.

Duty CHANGE EVENTS (for "how many days since a duty change" proximity features,
ml.calendar_events.get_duty_event_proximity / ml.feature_store / ml.feature_store_backfill)
are dates where total_duty_pct actually moved between consecutive verified rows.
2023-02-02 is deliberately EXCLUDED: BCD 12.5%->10%, AIDC 2.5%->5%, SWS 0%->0%,
total_duty_pct unchanged at 15.0 -- a real CBIC re-notification (kept in `rows` for the
premium/proxy series) but not a rate change a shopper would feel, so it is not a
"duty change event" for proximity features.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent.parent / "data"
DUTY_TABLE_PATH = DATA_DIR / "duty_cbic.json"

DUTY_EVENT_RECENCY_DAYS = 30


def load_duty_table(path: Path = DUTY_TABLE_PATH) -> dict[str, Any]:
    """Raw parsed contents of data/duty_cbic.json."""
    return json.loads(path.read_text(encoding="utf-8"))


def load_verified_rows(path: Path = DUTY_TABLE_PATH) -> list[dict[str, Any]]:
    """CBIC-notification-cited rows only (the `rows` key), sorted by effective_date.

    This is what every live-pipeline and research consumer should use — the
    unverified 2013 segment is excluded.
    """
    table = load_duty_table(path)
    return sorted(table["rows"], key=lambda r: r["effective_date"])


def load_all_rows_including_unverified(path: Path = DUTY_TABLE_PATH) -> list[dict[str, Any]]:
    """Verified rows + the unverified pre-2019 legacy segment, sorted by effective_date.

    ONLY for ml.inr_proxy's long-history pretraining proxy, which needs duty
    coverage back to 2013-01-01. Every other consumer must use load_verified_rows().
    """
    table = load_duty_table(path)
    unverified = table.get("unverified_pre_2019", {}).get("rows", [])
    rows = list(table["rows"]) + list(unverified)
    return sorted(rows, key=lambda r: r["effective_date"])


def get_duty_change_dates(path: Path = DUTY_TABLE_PATH) -> list[str]:
    """ISO dates (verified rows only) where total_duty_pct actually changed vs the
    immediately-preceding row -- i.e. real duty-rate-change events. Composition-only
    re-notifications (total_duty_pct unchanged, e.g. 2023-02-02) are excluded; see
    module docstring."""
    rows = load_verified_rows(path)
    dates: list[str] = []
    prev_total: float | None = None
    for row in rows:
        total = row["total_duty_pct"]
        if prev_total is None or total != prev_total:
            dates.append(row["effective_date"])
        prev_total = total
    return dates


def duty_change_proximity(
    query_date: date, change_dates: list[str], recency_days: int = DUTY_EVENT_RECENCY_DAYS
) -> tuple[bool, int]:
    """(is_recent, days_since) for the most recent change_dates entry on or before
    query_date. days_since is 9999 and is_recent is False if none exists."""
    past = [date.fromisoformat(d) for d in change_dates if date.fromisoformat(d) <= query_date]
    if not past:
        return False, 9999
    days_since = (query_date - max(past)).days
    return days_since <= recency_days, days_since
