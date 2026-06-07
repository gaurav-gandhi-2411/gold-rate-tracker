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
        n_macro_null: int = sum(1 for s in _MACRO_SERIES if macro_values.get(s) is None)

        snapshot: dict[str, object] = {
            "capture_utc": capture_utc,
            "as_of_date": d,
            "schema_version": SCHEMA_VERSION,
            "source": "backfill_yfinance",
            "partial": partial,
            "n_macro_null": n_macro_null,
            **macro_values,
            **macro_asof,
            "ibja_pm_916": ibja_pm_916,
            "ibja_am_916": ibja_am_916,
            "tanishq_22k": None,
            # For backfill rows the IBJA row IS for date d, so asof == d.
            # tanishq_22k is always None for backfill (historical scrapes not retained).
            "ibja_pm_916_asof_date": d if ibja_pm_916 is not None else None,
            "ibja_am_916_asof_date": d if ibja_am_916 is not None else None,
            "tanishq_22k_asof_date": None,
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


def _fetch_yf_close(ticker: str, start: str) -> pd.DataFrame:
    """Download historical close prices for a single yfinance ticker.

    Returns a DataFrame with a UTC DatetimeIndex and a "close" column.
    Returns an empty DataFrame (columns=["close"]) on any failure.
    """
    try:
        import yfinance as yf

        raw = yf.download(ticker, start=start, auto_adjust=True, progress=False, threads=False)
        if raw.empty:
            return pd.DataFrame(columns=["close"])

        # yfinance >= 0.2 returns MultiIndex columns; handle both orderings.
        if isinstance(raw.columns, pd.MultiIndex):
            close: pd.Series | None = None
            for price_type in ("Close", "Adj Close"):
                if (price_type, ticker) in raw.columns:
                    close = raw[(price_type, ticker)]
                    break
                if (ticker, price_type) in raw.columns:
                    close = raw[(ticker, price_type)]
                    break
            if close is None:
                logger.warning("_fetch_yf_close: Close not found for %s in MultiIndex", ticker)
                return pd.DataFrame(columns=["close"])
        else:
            flat_close: pd.Series | None = None
            for col_name in ("Close", "Adj Close"):
                if col_name in raw.columns:
                    flat_close = raw[col_name]
                    break
            if flat_close is None:
                logger.warning("_fetch_yf_close: Close column not found for %s", ticker)
                return pd.DataFrame(columns=["close"])
            close = flat_close

        result = close.rename("close").to_frame()
        dt_index = pd.DatetimeIndex(result.index)
        result.index = (
            dt_index.tz_localize("UTC") if dt_index.tz is None else dt_index.tz_convert("UTC")
        )
        return result.dropna()

    except Exception as exc:
        logger.warning("_fetch_yf_close: download failed for %s — %s", ticker, exc)
        return pd.DataFrame(columns=["close"])


def patch_missing_macro_series(
    store_path: Path | None = None,
    crude_df: pd.DataFrame | None = None,
    tips_df: pd.DataFrame | None = None,
) -> dict[str, int]:
    """Patch null crude_wti/tips on backfill_yfinance rows; recompute n_macro_null for all rows.

    Only ``source='backfill_yfinance'`` rows with null values are modified.
    ``source='live_pit'`` rows are never touched.

    After patching, ``n_macro_null`` is recomputed for ALL rows against the canonical
    8-series list and ``schema_version`` is bumped to SCHEMA_VERSION (3) for all rows.

    Parameters
    ----------
    store_path : Path | None
        Path to feature store parquet. Defaults to STORE_PATH.
    crude_df : pd.DataFrame | None
        Pre-loaded price DataFrame for CL=F. Must have a UTC DatetimeIndex and a
        "close" column with float values. If None, fetches from yfinance (live network).
    tips_df : pd.DataFrame | None
        Pre-loaded price DataFrame for TIP. Same format as crude_df. If None,
        fetches from yfinance (live network).

    Returns
    -------
    dict[str, int]
        Keys: ``crude_patched``, ``tips_patched``, ``n_macro_null_recomputed``.
    """
    from ml.feature_store import _MACRO_SERIES, SCHEMA_VERSION, load_snapshots
    from ml.feature_store import STORE_PATH as _DEFAULT_STORE_PATH

    _store_path: Path = store_path or _DEFAULT_STORE_PATH

    df = load_snapshots(_store_path)
    if df.empty:
        return {"crude_patched": 0, "tips_patched": 0, "n_macro_null_recomputed": 0}

    # Add n_macro_null column when migrating from schema_version < 3.
    if "n_macro_null" not in df.columns:
        df["n_macro_null"] = 0

    # Fetch price history from yfinance when not injected (requires live network).
    if crude_df is None:
        crude_df = _fetch_yf_close("CL=F", start="2024-01-01")
    if tips_df is None:
        tips_df = _fetch_yf_close("TIP", start="2024-01-01")

    crude_patched = 0
    tips_patched = 0

    for i, row in df.iterrows():
        if row.get("source") != "backfill_yfinance":
            continue

        d: str = row["as_of_date"]
        target_ts = pd.Timestamp(d, tz="UTC")

        if pd.isna(row.get("crude_wti")) and crude_df is not None and not crude_df.empty:
            available = crude_df.loc[:target_ts]
            if not available.empty:
                df.at[i, "crude_wti"] = float(available["close"].iloc[-1])
                df.at[i, "crude_wti_asof_date"] = available.index[-1].date().isoformat()
                crude_patched += 1

        if pd.isna(row.get("tips")) and tips_df is not None and not tips_df.empty:
            available = tips_df.loc[:target_ts]
            if not available.empty:
                df.at[i, "tips"] = float(available["close"].iloc[-1])
                df.at[i, "tips_asof_date"] = available.index[-1].date().isoformat()
                tips_patched += 1

    # Recompute n_macro_null for ALL rows + bump schema_version.
    for i in df.index:
        n_null = sum(1 for s in _MACRO_SERIES if pd.isna(df.at[i, s]))
        df.at[i, "n_macro_null"] = n_null
        df.at[i, "schema_version"] = SCHEMA_VERSION

    _store_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(_store_path, index=False)

    return {
        "crude_patched": crude_patched,
        "tips_patched": tips_patched,
        "n_macro_null_recomputed": len(df),
    }


if __name__ == "__main__":
    n = run_backfill()
    print(f"Done: {n} rows written")
