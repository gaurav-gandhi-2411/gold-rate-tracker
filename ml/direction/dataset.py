"""ml.direction.dataset — build a leak-free directional forecast dataset.

Features come from snapshots.parquet (captured at day t).
Labels come from the NEXT available IBJA pm_916 entry (day t+1 or later).
This guarantees no look-ahead leakage: the model only sees information
that was known at capture time.
"""

from __future__ import annotations

import bisect
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT: Path = Path(__file__).resolve().parent.parent.parent
DATA_DIR: Path = ROOT / "data"
SNAPSHOTS_PARQUET: Path = DATA_DIR / "feature_store" / "snapshots.parquet"
IBJA_PARQUET: Path = DATA_DIR / "ibja_rates.parquet"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEAD_BAND_PER_10G: float = 500.0
DEAD_BAND_PER_GRAM: float = 50.0
MAX_N_MACRO_NULL: int = 3

FEATURE_COLS: list[str] = [
    "gold_usd",
    "usd_inr",
    "us_10y_yield",
    "dxy",
    "sensex",
    "vix",
    "crude_wti",
    "tips",
    "ibja_pm_916",
    "ibja_am_916",
    "tanishq_22k",
    "dow",
    "dom",
    "month",
    "is_festival_window",
    "days_to_next_festival",
    "duty_change_active",
    "days_since_last_duty_change",
]


# ---------------------------------------------------------------------------
# Label builder
# ---------------------------------------------------------------------------


def make_label(
    current_pm916_per_10g: float,
    next_pm916_per_10g: float,
    dead_band_per_gram: float = DEAD_BAND_PER_GRAM,
) -> tuple[str, int]:
    """Compute ternary and binary directional labels.

    Args:
        current_pm916_per_10g: Current IBJA pm 916 price (per 10g).
        next_pm916_per_10g: Next available IBJA pm 916 price (per 10g).
        dead_band_per_gram: Minimum move per gram to count as directional.

    Returns:
        Tuple of (label_ternary, label_binary) where:
            label_ternary: "up", "down", or "flat"
            label_binary:  1 if next > current, else 0  (matches always-up baseline)
    """
    delta_per_gram: float = (next_pm916_per_10g - current_pm916_per_10g) / 10.0
    if delta_per_gram > dead_band_per_gram:
        label_ternary = "up"
    elif delta_per_gram < -dead_band_per_gram:
        label_ternary = "down"
    else:
        label_ternary = "flat"

    label_binary: int = int(next_pm916_per_10g > current_pm916_per_10g)
    return label_ternary, label_binary


# ---------------------------------------------------------------------------
# Dataset builder
# ---------------------------------------------------------------------------


def build_dataset(
    snapshots_path: Path = SNAPSHOTS_PARQUET,
    ibja_path: Path = IBJA_PARQUET,
    dead_band_per_gram: float = DEAD_BAND_PER_GRAM,
    max_n_macro_null: int = MAX_N_MACRO_NULL,
    verbose: bool = False,
    snapshots_df: pd.DataFrame | None = None,
    ibja_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Build a leak-free directional dataset for walk-forward evaluation.

    Features are taken from the snapshot at day t.  Labels are derived from
    the NEXT available IBJA pm_916 entry strictly after the snapshot's
    as_of_date — guaranteed no look-ahead.

    Exclusion rules (applied in order):
        (a) Stale IBJA: ibja_pm_916_asof_date < as_of_date (snapshot reused
            an older IBJA price; current price is unknown at capture time).
        (b) Too many macro nulls: n_macro_null > max_n_macro_null.
        (c) No next-day IBJA label available (last snapshot in the series).

    Args:
        snapshots_path: Path to snapshots.parquet.
        ibja_path: Path to ibja_rates.parquet.
        dead_band_per_gram: Dead band in ₹/gram for ternary labelling.
        max_n_macro_null: Maximum tolerated macro-null count.
        verbose: If True, print a build summary to stdout.
        snapshots_df: Inject a DataFrame in place of reading snapshots_path
            (for unit tests — parquet read is skipped when provided).
        ibja_df: Inject a DataFrame in place of reading ibja_path.

    Returns:
        DataFrame with columns: as_of_date, <FEATURE_COLS>, current_pm916,
        next_pm916, delta_per_gram, label_ternary, label_binary, label_date,
        ibja_pm_916_asof_date, n_macro_null.  One row per kept snapshot,
        sorted by as_of_date.
    """
    # --- Load data -----------------------------------------------------------
    if snapshots_df is None:
        snaps = pd.read_parquet(snapshots_path)
    else:
        snaps = snapshots_df.copy()

    if ibja_df is None:
        ibja = pd.read_parquet(ibja_path)
    else:
        ibja = ibja_df.copy()

    # Normalise types
    snaps["as_of_date"] = snaps["as_of_date"].astype(str)
    snaps["ibja_pm_916_asof_date"] = snaps["ibja_pm_916_asof_date"].astype(str)
    ibja["date"] = ibja["date"].astype(str)

    # Sort snapshots; deduplicate on as_of_date (keep last capture per day)
    snaps = snaps.sort_values("as_of_date").drop_duplicates("as_of_date", keep="last")
    snaps = snaps.reset_index(drop=True)

    # Build a sorted list of IBJA dates for bisect lookups
    ibja_sorted = ibja.sort_values("date").reset_index(drop=True)
    ibja_dates: list[str] = ibja_sorted["date"].tolist()
    ibja_pm916: list[float] = ibja_sorted["pm_916"].tolist()

    n_input = len(snaps)
    n_stale = 0
    n_macro = 0
    n_no_label = 0

    rows: list[dict] = []

    for _, row in snaps.iterrows():
        as_of = str(row["as_of_date"])
        ibja_asof = str(row["ibja_pm_916_asof_date"])

        # (a) Stale IBJA check
        if ibja_asof < as_of:
            n_stale += 1
            continue

        # (b) Macro null check
        n_macro_null_val = int(row["n_macro_null"]) if pd.notna(row["n_macro_null"]) else 0
        if n_macro_null_val > max_n_macro_null:
            n_macro += 1
            continue

        current_pm916 = float(row["ibja_pm_916"])

        # Find next IBJA pm_916 strictly after as_of_date
        idx = bisect.bisect_right(ibja_dates, as_of)
        if idx >= len(ibja_dates):
            n_no_label += 1
            continue

        next_pm916 = ibja_pm916[idx]
        label_date = ibja_dates[idx]

        if pd.isna(next_pm916):
            n_no_label += 1
            continue

        delta_per_gram = (next_pm916 - current_pm916) / 10.0
        label_ternary, label_binary = make_label(current_pm916, next_pm916, dead_band_per_gram)

        feature_vals = {col: row[col] for col in FEATURE_COLS}

        rows.append(
            {
                "as_of_date": as_of,
                **feature_vals,
                "current_pm916": current_pm916,
                "next_pm916": next_pm916,
                "delta_per_gram": delta_per_gram,
                "label_ternary": label_ternary,
                "label_binary": label_binary,
                "label_date": label_date,
                "ibja_pm_916_asof_date": ibja_asof,
                "n_macro_null": n_macro_null_val,
            }
        )

    dataset = pd.DataFrame(rows)
    if not dataset.empty:
        dataset = dataset.sort_values("as_of_date").reset_index(drop=True)

    if verbose:
        print("=== Phi23 Dataset Build ===")
        print(f"  Input snapshots   : {n_input}")
        print(f"  Excluded (stale)  : {n_stale}")
        print(f"  Excluded (macro)  : {n_macro}")
        print(f"  Excluded (no label): {n_no_label}")
        print(f"  Kept rows         : {len(dataset)}")
        if not dataset.empty:
            lv = dataset["label_binary"].value_counts().to_dict()
            print(f"  Label distribution: {lv}")
            print(
                f"  Date range        : {dataset['as_of_date'].min()} "
                f"to {dataset['as_of_date'].max()}"
            )

    return dataset
