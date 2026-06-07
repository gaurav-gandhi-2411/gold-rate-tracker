from __future__ import annotations

import logging
from datetime import UTC, datetime
from datetime import date as _date
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


def run_backfill(
    ibja_path: Path | None = None,
    store_path: Path | None = None,
    duty_events_path: Path | None = None,
    start_date: str = "2025-01-01",
    macro_df: pd.DataFrame | None = None,
) -> int:
    """Backfill feature-store snapshots for all 2025+ IBJA dates.

    Reconstructs historical feature-store snapshots for dates where IBJA data
    exists but no live-capture row is present. Rows are tagged
    ``source='backfill_yfinance'`` and are NOT true point-in-time; yfinance
    returns revised adjusted closes, not the exact value known on that date.

    The key invariant is that a ``source='live_pit'`` row is NEVER overwritten.
    Any ``as_of_date`` already present in the store (regardless of source) is
    skipped, giving full idempotency.

    Parameters
    ----------
    ibja_path : Path | None
        Path to ibja_rates.parquet. Defaults to data/ibja_rates.parquet.
    store_path : Path | None
        Path to feature store parquet. Defaults to STORE_PATH from feature_store.
    duty_events_path : Path | None
        Path to duty_events.json. Defaults to data/duty_events.json.
    start_date : str
        Earliest date to backfill (ISO, inclusive). Default '2025-01-01'.
    macro_df : pd.DataFrame | None
        Pre-loaded macro DataFrame (DatetimeIndex, UTC). If None, loads from
        data/macro_cache.parquet. If the cache doesn't exist or the fetch fails,
        the snapshot is written with partial=True and null macro values.

    Returns
    -------
    int
        Number of new rows written to the store (0 if all dates already present).
    """
    import json

    from ml.feature_store import _MACRO_SERIES, SCHEMA_VERSION, append_snapshot, load_snapshots
    from ml.feature_store import STORE_PATH as _DEFAULT_STORE_PATH

    _store_path: Path = store_path or _DEFAULT_STORE_PATH
    _ibja_path: Path = ibja_path or (Path(__file__).parent.parent / "data" / "ibja_rates.parquet")
    _duty_path: Path = duty_events_path or (
        Path(__file__).parent.parent / "data" / "duty_events.json"
    )

    # ------------------------------------------------------------------
    # 1. Load existing store; extract set of dates already present
    # ------------------------------------------------------------------
    existing_store = load_snapshots(_store_path)
    existing_dates: set[str] = set()
    existing_live_pit_dates: set[str] = set()

    if not existing_store.empty and "as_of_date" in existing_store.columns:
        existing_dates = set(existing_store["as_of_date"].tolist())
        if "source" in existing_store.columns:
            live_pit_mask = existing_store["source"] == "live_pit"
            existing_live_pit_dates = set(existing_store.loc[live_pit_mask, "as_of_date"].tolist())

    # ------------------------------------------------------------------
    # 2. Load IBJA data and filter
    # ------------------------------------------------------------------
    if not _ibja_path.exists():
        logger.warning("run_backfill: IBJA parquet not found at %s", _ibja_path)
        print("Backfill complete: 0 new rows written (0 skipped)")
        return 0

    ibja_df = pd.read_parquet(_ibja_path)
    if ibja_df.empty or "date" not in ibja_df.columns or "pm_916" not in ibja_df.columns:
        logger.warning("run_backfill: IBJA parquet is empty or missing required columns")
        print("Backfill complete: 0 new rows written (0 skipped)")
        return 0

    # Filter: date >= start_date, pm_916 non-null, sort ascending
    ibja_filtered = (
        ibja_df[(ibja_df["date"] >= start_date) & (ibja_df["pm_916"].notna())]
        .sort_values("date")
        .reset_index(drop=True)
    )

    # ------------------------------------------------------------------
    # 3. Load macro if not provided
    # ------------------------------------------------------------------
    _macro_available = macro_df is not None
    if not _macro_available:
        try:
            from ml.macro import load_macro_features

            loaded = load_macro_features()
            if loaded is not None:
                macro_df = loaded
                _macro_available = True
            else:
                logger.warning("run_backfill: macro cache unavailable — partial mode")
        except Exception as exc:
            logger.warning("run_backfill: macro load failed (%s) — partial mode", exc)

    # ------------------------------------------------------------------
    # 4. Load duty events
    # ------------------------------------------------------------------
    duty_events: list[dict] = []
    try:
        if _duty_path.exists():
            with _duty_path.open("r", encoding="utf-8") as fh:
                duty_events = json.load(fh)
        else:
            logger.warning("run_backfill: duty_events.json not found at %s", _duty_path)
    except Exception as exc:
        logger.warning("run_backfill: duty_events.json load failed — %s", exc)

    # ------------------------------------------------------------------
    # 5. Backfill loop
    # ------------------------------------------------------------------
    from ml.calendar_events import get_festival_info

    now_utc = datetime.now(UTC)
    capture_utc: str = now_utc.isoformat(timespec="seconds").replace("+00:00", "Z")

    n_written = 0
    n_skipped = 0

    for _, ibja_row in ibja_filtered.iterrows():
        d: str = ibja_row["date"]

        # 5a. Skip if live_pit row exists
        if d in existing_live_pit_dates:
            logger.debug("Skip %s: live_pit row exists", d)
            n_skipped += 1
            continue

        # 5b. Skip if any row exists (idempotency)
        if d in existing_dates:
            logger.debug("Skip %s: already in store", d)
            n_skipped += 1
            continue

        # 5c. Build snapshot
        d_obj = _date.fromisoformat(d)

        # --- Macro values ---
        partial: bool = not _macro_available
        macro_values: dict[str, object] = {}
        macro_asof: dict[str, object] = {}

        if macro_df is not None:
            target_ts = pd.Timestamp(d, tz="UTC")
            try:
                available = macro_df.loc[:target_ts]
            except Exception:
                available = pd.DataFrame()

            if available.empty:
                partial = True
                for series in _MACRO_SERIES:
                    macro_values[series] = None
                    macro_asof[f"{series}_asof_date"] = None
            else:
                macro_row = available.iloc[-1]
                macro_row_date = available.index[-1]
                for series in _MACRO_SERIES:
                    if series in macro_df.columns and not pd.isna(macro_row.get(series)):
                        macro_values[series] = float(macro_row[series])
                        if hasattr(macro_row_date, "date"):
                            macro_asof[f"{series}_asof_date"] = macro_row_date.date().isoformat()
                        else:
                            macro_asof[f"{series}_asof_date"] = str(macro_row_date)
                    else:
                        macro_values[series] = None
                        macro_asof[f"{series}_asof_date"] = None
        else:
            partial = True
            for series in _MACRO_SERIES:
                macro_values[series] = None
                macro_asof[f"{series}_asof_date"] = None

        # --- IBJA values ---
        ibja_pm_916: float | None = float(ibja_row["pm_916"])
        ibja_am_916: float | None = None
        if "am_916" in ibja_df.columns and not pd.isna(ibja_row.get("am_916")):
            ibja_am_916 = float(ibja_row["am_916"])

        # --- Duty events ---
        duty_change_active: bool = False
        days_since_last_duty_change: int = 9999
        try:
            past_events = [e for e in duty_events if e.get("date", "") <= d]
            if past_events:
                latest_event = max(past_events, key=lambda e: e["date"])
                event_date_obj = _date.fromisoformat(latest_event["date"])
                days_delta = (d_obj - event_date_obj).days
                days_since_last_duty_change = days_delta
                duty_change_active = days_delta <= 30
        except Exception as exc:
            logger.warning("run_backfill: duty compute failed for %s — %s", d, exc)

        # --- Festival / calendar info ---
        festival_info = get_festival_info(d_obj)

        # --- Assemble snapshot ---
        snapshot: dict[str, object] = {
            "capture_utc": capture_utc,
            "as_of_date": d,
            "schema_version": SCHEMA_VERSION,
            "source": "backfill_yfinance",
            "partial": partial,
            **macro_values,
            **macro_asof,
            "ibja_pm_916": ibja_pm_916,
            "ibja_am_916": ibja_am_916,
            "tanishq_22k": None,
            "dow": d_obj.weekday(),
            "dom": d_obj.day,
            "month": d_obj.month,
            "is_festival_window": festival_info["is_festival_window"],
            "festival_name": festival_info["festival_name"],
            "days_to_next_festival": festival_info["days_to_next_festival"],
            "duty_change_active": duty_change_active,
            "days_since_last_duty_change": days_since_last_duty_change,
        }

        append_snapshot(snapshot, _store_path)
        # Track the new date so subsequent iterations in the same run detect it
        existing_dates.add(d)
        n_written += 1

    # ------------------------------------------------------------------
    # 6. Summary
    # ------------------------------------------------------------------
    print(f"Backfill complete: {n_written} new rows written ({n_skipped} skipped)")
    return n_written


if __name__ == "__main__":
    n = run_backfill()
    print(f"Done: {n} rows written")
