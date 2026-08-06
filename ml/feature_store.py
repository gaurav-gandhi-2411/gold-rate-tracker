from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

SCHEMA_VERSION: int = 3

STORE_PATH: Path = Path(__file__).parent.parent / "data" / "feature_store" / "snapshots.parquet"

_ALL_COLUMNS: list[str] = [
    "capture_utc",
    "as_of_date",
    "schema_version",
    "source",
    "partial",
    # Count of null values across the 8 expected macro series (denominator = 8).
    # n_macro_null == 0 means all macro series are present for this row.
    # Per-series presence is recoverable via each series' own column being null/non-null.
    # partial=True implies n_macro_null=8; partial=False allows 0-8 (individual series may
    # be absent even when the cache loaded, e.g. new tickers not yet in historical cache).
    "n_macro_null",
    "gold_usd",
    "usd_inr",
    "us_10y_yield",
    "dxy",
    "sensex",
    "vix",
    "crude_wti",
    "tips",
    "gold_usd_asof_date",
    "usd_inr_asof_date",
    "us_10y_yield_asof_date",
    "dxy_asof_date",
    "sensex_asof_date",
    "vix_asof_date",
    "crude_wti_asof_date",
    "tips_asof_date",
    "ibja_pm_916",
    "ibja_am_916",
    "tanishq_22k",
    "ibja_pm_916_asof_date",
    "ibja_am_916_asof_date",
    "tanishq_22k_asof_date",
    "dow",
    "dom",
    "month",
    "is_festival_window",
    "festival_name",
    "days_to_next_festival",
    "duty_change_active",
    "days_since_last_duty_change",
]


def append_snapshot(snapshot: dict, store_path: Path = STORE_PATH) -> None:
    """Append a snapshot row to the parquet store if the as_of_date is new.

    Idempotent by default: a second call with the same as_of_date is a no-op.
    One exception — a same-day IBJA upgrade: if the stored row's IBJA reading
    predates as_of_date (captured before IBJA's ~17:00 IST publish that day)
    and the new capture's IBJA reading IS dated as_of_date, the stored row is
    replaced. check-price.yml runs 8x/day; the run that first crosses IST
    midnight captures before IBJA publishes and would otherwise permanently
    lock in the prior day's close for that as_of_date, since every later
    same-day run was silently a no-op. This starved the direction-classifier
    dataset of usable rows for 8 weeks (2026-06-07 -> 2026-08-05, see
    docs/DIRECTION_SIGNAL_STATUS.md) before being caught. Once a same-day-
    fresh row exists, later same-day calls remain no-ops (never downgrades).
    """
    existing = load_snapshots(store_path)

    if len(existing) > 0 and snapshot["as_of_date"] in existing["as_of_date"].values:
        existing_row = existing.loc[existing["as_of_date"] == snapshot["as_of_date"]].iloc[0]
        as_of_date = snapshot["as_of_date"]
        existing_is_fresh = existing_row.get("ibja_pm_916_asof_date") == as_of_date
        new_is_fresh = snapshot.get("ibja_pm_916_asof_date") == as_of_date
        if existing_is_fresh or not new_is_fresh:
            return
        existing = existing.loc[existing["as_of_date"] != as_of_date]

    # Build a single-row DataFrame. Use pd.array with "boolean" dtype for the
    # `partial` column so nullable-bool survives the parquet round-trip cleanly.
    row: dict[str, object] = {col: snapshot.get(col) for col in _ALL_COLUMNS}
    row["partial"] = pd.array([snapshot.get("partial")], dtype="boolean")[0]

    new_df = pd.DataFrame([row], columns=_ALL_COLUMNS)
    combined = pd.concat([existing, new_df], ignore_index=True)

    store_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(store_path, index=False)


def load_snapshots(store_path: Path = STORE_PATH) -> pd.DataFrame:
    """Load all stored snapshots from parquet. Returns an empty DataFrame if the file does not exist."""
    if not store_path.exists():
        return pd.DataFrame()
    return pd.read_parquet(store_path)


_MACRO_SERIES: list[str] = [
    "gold_usd",
    "usd_inr",
    "us_10y_yield",
    "dxy",
    "sensex",
    "vix",
    "crude_wti",
    "tips",
]


def capture_daily_snapshot(
    store_path: Path = STORE_PATH,
    macro_cache_path: Path | None = None,
    ibja_path: Path | None = None,
    duty_events_path: Path | None = None,
    prices_path: Path | None = None,
) -> None:
    """Capture a feature-store snapshot for the current IST date.

    Reads macro cache, IBJA rates, Tanishq prices, and duty events, then
    writes one row to the parquet store via :func:`append_snapshot`.  The call
    is idempotent: a second call on the same IST date is a no-op.

    All data-source reads are individually wrapped in try/except so that a
    single source failure never blocks the snapshot.  ``partial=True`` is set
    only when the macro cache itself cannot be loaded (macro is the primary
    feature source).
    """
    import json
    from datetime import UTC, datetime, timedelta, timezone
    from datetime import date as _date

    from ml.calendar_events import get_festival_info

    # ------------------------------------------------------------------
    # 1. Compute capture timestamps
    # ------------------------------------------------------------------
    now_utc = datetime.now(UTC)
    capture_utc: str = now_utc.isoformat(timespec="seconds").replace("+00:00", "Z")

    # IST = UTC + 5:30
    ist_offset = timezone(timedelta(hours=5, minutes=30))
    now_ist = now_utc.astimezone(ist_offset)
    as_of_date: str = now_ist.strftime("%Y-%m-%d")
    as_of_date_obj = now_ist.date()

    # ------------------------------------------------------------------
    # 2. Load macro cache
    # ------------------------------------------------------------------
    partial: bool = False
    macro_df: pd.DataFrame | None = None

    _macro_path: Path
    if macro_cache_path is not None:
        _macro_path = macro_cache_path
    else:
        from ml.macro import CACHE_PATH as _DEFAULT_MACRO_PATH

        _macro_path = _DEFAULT_MACRO_PATH

    try:
        if not _macro_path.exists():
            raise FileNotFoundError(f"Macro cache not found: {_macro_path}")
        macro_df = pd.read_parquet(_macro_path)
        if macro_df.empty:
            raise ValueError("Macro cache is empty")
    except Exception as exc:
        logger.warning("feature_store: macro cache unavailable — %s", exc)
        partial = True
        macro_df = None

    # ------------------------------------------------------------------
    # 3. Extract per-series macro values and asof dates
    # ------------------------------------------------------------------
    macro_values: dict[str, object] = {}
    macro_asof: dict[str, object] = {}

    for series in _MACRO_SERIES:
        if macro_df is not None and series in macro_df.columns:
            col = macro_df[series].dropna()
            if col.empty:
                macro_values[series] = None
                macro_asof[f"{series}_asof_date"] = None
            else:
                macro_values[series] = float(col.iloc[-1])
                # The index may be a DatetimeIndex or a plain RangeIndex.
                last_idx = col.index[-1]
                if hasattr(last_idx, "date"):
                    macro_asof[f"{series}_asof_date"] = last_idx.date().isoformat()
                else:
                    # Fall back to using the index label as a string
                    macro_asof[f"{series}_asof_date"] = str(last_idx)
        else:
            macro_values[series] = None
            macro_asof[f"{series}_asof_date"] = None

    # ------------------------------------------------------------------
    # 4. Load IBJA rates
    # ------------------------------------------------------------------
    ibja_pm_916: float | None = None
    ibja_am_916: float | None = None
    _ibja_observation_date: str | None = None

    _ibja_path: Path = ibja_path or (Path(__file__).parent.parent / "data" / "ibja_rates.parquet")
    try:
        if not _ibja_path.exists():
            raise FileNotFoundError(f"IBJA parquet not found: {_ibja_path}")
        ibja_df = pd.read_parquet(_ibja_path)
        if not ibja_df.empty:
            last_row = ibja_df.iloc[-1]
            if "date" in ibja_df.columns:
                _ibja_observation_date = str(last_row["date"])
            if "pm_916" in ibja_df.columns and not pd.isna(last_row.get("pm_916")):
                ibja_pm_916 = float(last_row["pm_916"])
            if "am_916" in ibja_df.columns and not pd.isna(last_row.get("am_916")):
                ibja_am_916 = float(last_row["am_916"])
    except Exception as exc:
        logger.warning("feature_store: IBJA load failed — %s", exc)

    ibja_pm_916_asof_date: str | None = _ibja_observation_date if ibja_pm_916 is not None else None
    ibja_am_916_asof_date: str | None = _ibja_observation_date if ibja_am_916 is not None else None

    # ------------------------------------------------------------------
    # 5. Load Tanishq price
    # ------------------------------------------------------------------
    tanishq_22k: float | None = None
    tanishq_22k_asof_date: str | None = None

    _prices_path: Path = prices_path or (Path(__file__).parent.parent / "data" / "prices.json")
    try:
        if not _prices_path.exists():
            raise FileNotFoundError(f"prices.json not found: {_prices_path}")
        with _prices_path.open("r", encoding="utf-8") as fh:
            prices_data = json.load(fh)
        if prices_data:
            last_price_entry = prices_data[-1]
            raw_val = last_price_entry.get("22k")
            if raw_val is not None:
                tanishq_22k = float(raw_val)
                # Convert the entry's UTC timestamp to IST before taking the calendar
                # date — a late-UTC entry (e.g. 19:00Z) is the next calendar day in IST.
                ts_str = last_price_entry.get("timestamp", "")
                if ts_str:
                    ts_utc = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    tanishq_22k_asof_date = ts_utc.astimezone(ist_offset).date().isoformat()
    except Exception as exc:
        logger.warning("feature_store: prices.json load failed — %s", exc)

    # ------------------------------------------------------------------
    # 6. Load duty events
    # ------------------------------------------------------------------
    duty_change_active: bool = False
    days_since_last_duty_change: int = 9999

    _duty_path: Path = duty_events_path or (
        Path(__file__).parent.parent / "data" / "duty_events.json"
    )
    try:
        if not _duty_path.exists():
            raise FileNotFoundError(f"duty_events.json not found: {_duty_path}")
        with _duty_path.open("r", encoding="utf-8") as fh:
            duty_events = json.load(fh)
        # Find the most recent event on or before as_of_date
        past_events = [e for e in duty_events if e.get("date", "") <= as_of_date]
        if past_events:
            latest_event = max(past_events, key=lambda e: e["date"])
            event_date = _date.fromisoformat(latest_event["date"])
            days_delta = (as_of_date_obj - event_date).days
            days_since_last_duty_change = days_delta
            duty_change_active = days_delta <= 30
    except Exception as exc:
        logger.warning("feature_store: duty_events.json load failed — %s", exc)

    # ------------------------------------------------------------------
    # 7. Festival / calendar info
    # ------------------------------------------------------------------
    festival_info = get_festival_info(as_of_date_obj)

    # ------------------------------------------------------------------
    # 8. Build snapshot dict and write
    # ------------------------------------------------------------------
    n_macro_null: int = sum(1 for s in _MACRO_SERIES if macro_values.get(s) is None)

    snapshot: dict[str, object] = {
        "capture_utc": capture_utc,
        "as_of_date": as_of_date,
        "schema_version": SCHEMA_VERSION,
        "source": "live_pit",
        "partial": partial,
        "n_macro_null": n_macro_null,
        **macro_values,
        **macro_asof,
        "ibja_pm_916": ibja_pm_916,
        "ibja_am_916": ibja_am_916,
        "tanishq_22k": tanishq_22k,
        "ibja_pm_916_asof_date": ibja_pm_916_asof_date,
        "ibja_am_916_asof_date": ibja_am_916_asof_date,
        "tanishq_22k_asof_date": tanishq_22k_asof_date,
        "dow": as_of_date_obj.weekday(),
        "dom": as_of_date_obj.day,
        "month": as_of_date_obj.month,
        "is_festival_window": festival_info["is_festival_window"],
        "festival_name": festival_info["festival_name"],
        "days_to_next_festival": festival_info["days_to_next_festival"],
        "duty_change_active": duty_change_active,
        "days_since_last_duty_change": days_since_last_duty_change,
    }

    append_snapshot(snapshot, store_path)
    print(f"Captured snapshot for {as_of_date} (partial={partial})")


if __name__ == "__main__":
    capture_daily_snapshot()
