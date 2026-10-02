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

from ml.sources.base import MAX_FUTURE_SKEW, MIN_PLAUSIBLE_YEAR

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
    """Load all stored fusion snapshots, INCLUDING rows with a corrupt ``observed_at``.

    Returns an empty DataFrame if the file does not exist. This is the raw
    accessor used internally for idempotency-key dedup (which only cares
    about exact (source, city, capture_utc) duplicates, never
    ``observed_at``'s validity) and by anything that needs the full,
    unfiltered history for forensics/audit. Any reader that treats
    ``observed_at`` as a trustworthy timestamp (PIT weight-learning, markup
    analysis, ...) should use :func:`load_plausible_snapshots` instead.
    """
    if not store_path.exists():
        return pd.DataFrame(columns=_ALL_COLUMNS)
    return pd.read_parquet(store_path)


def load_plausible_snapshots(store_path: Path = STORE_PATH) -> pd.DataFrame:
    """Load stored fusion snapshots, excluding rows with an implausible ``observed_at``.

    As of this fix, every adapter fails closed on an implausible
    ``observed_at`` at fetch time (see :func:`ml.sources.base.
    validate_observed_at`), so no *new* corrupt row should ever be written.
    441 pre-existing kalyan rows captured 2026-07-22..2026-09-24 (before the
    fix) still carry the corrupt epoch-placeholder value
    ``"1969-12-31T18:30:00+00:00"`` -- left in the parquet on purpose (no
    history rewrite) but filtered out here for any reader that treats
    ``observed_at`` as meaningful.
    """
    df = load_snapshots(store_path)
    if df.empty:
        return df
    parsed = pd.to_datetime(df["observed_at"], errors="coerce", utc=True)
    now_utc = pd.Timestamp.now(tz="UTC")
    plausible = (
        parsed.notna()
        & (parsed.dt.year >= MIN_PLAUSIBLE_YEAR)
        & (parsed <= now_utc + MAX_FUTURE_SKEW)
    )
    return df[plausible].reset_index(drop=True)
