from __future__ import annotations

from pathlib import Path

import pandas as pd

SCHEMA_VERSION: int = 1

STORE_PATH: Path = Path(__file__).parent.parent / "data" / "feature_store" / "snapshots.parquet"

_ALL_COLUMNS: list[str] = [
    "capture_utc",
    "as_of_date",
    "schema_version",
    "source",
    "partial",
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

    Idempotent: a second call with the same as_of_date is a no-op and the
    existing row is never modified.
    """
    existing = load_snapshots(store_path)

    if len(existing) > 0 and snapshot["as_of_date"] in existing["as_of_date"].values:
        return

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


if __name__ == "__main__":
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not STORE_PATH.exists():
        pd.DataFrame(columns=_ALL_COLUMNS).to_parquet(STORE_PATH, index=False)
        print(f"Initialized empty store: {STORE_PATH}")
    else:
        df = load_snapshots()
        print(f"Store has {len(df)} rows")
