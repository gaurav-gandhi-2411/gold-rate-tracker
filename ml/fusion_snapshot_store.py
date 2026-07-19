"""PIT snapshot store for the fusion system's source readings (ADR 026).

Separate from ``ml.feature_store`` on purpose: that store is a wide,
one-row-per-day ML feature vector; fusion source data is naturally tidy/long
(one row per source x city x fetch cycle) and would badly distort the
existing schema if crammed in. This is the history Option 2's weight-
learning will eventually consume -- it starts accumulating the moment
``ml.shadow_fusion`` first runs.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

SCHEMA_VERSION: int = 1

STORE_PATH: Path = Path(__file__).parent.parent / "data" / "fusion_snapshots.parquet"

_ALL_COLUMNS: list[str] = [
    "capture_utc",
    "as_of_date",
    "schema_version",
    "source",
    "city",  # None for national-level sources
    "rate_22k",
    "observed_at",
    "attribution",
]


def append_snapshot_rows(rows: list[dict], store_path: Path = STORE_PATH) -> int:
    """Append snapshot rows, skipping any exact (source, city, capture_utc) duplicate.

    Returns the number of rows actually appended. Idempotent per exact
    ``capture_utc`` -- re-running the same fetch cycle's driver twice (e.g.
    a retried CI job) does not double-count that cycle's readings.
    """
    if not rows:
        return 0

    existing = load_snapshots(store_path)
    existing_keys: set[tuple] = set()
    if not existing.empty:
        existing_keys = set(
            zip(existing["source"], existing["city"], existing["capture_utc"], strict=False)
        )

    new_rows = [
        {col: row.get(col) for col in _ALL_COLUMNS}
        for row in rows
        if (row.get("source"), row.get("city"), row.get("capture_utc")) not in existing_keys
    ]
    if not new_rows:
        return 0

    new_df = pd.DataFrame(new_rows, columns=_ALL_COLUMNS)
    combined = pd.concat([existing, new_df], ignore_index=True) if not existing.empty else new_df

    store_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(store_path, index=False)
    return len(new_rows)


def load_snapshots(store_path: Path = STORE_PATH) -> pd.DataFrame:
    """Load all stored fusion snapshots. Returns an empty DataFrame if the file does not exist."""
    if not store_path.exists():
        return pd.DataFrame(columns=_ALL_COLUMNS)
    return pd.read_parquet(store_path)
