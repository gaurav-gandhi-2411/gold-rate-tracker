"""ml.direction.dataset — build a leak-free directional forecast dataset.

Features come from snapshots.parquet (captured at day t).
Labels come from IBJA pm_916 entries STRICTLY AFTER the capture date:
  - h=1 (next trading day)      → label_binary_h1 / label_ternary_h1
  - h=2 (the day after that)    → label_binary_h2 / label_ternary_h2  (None if absent)
The unsuffixed columns (label_binary, next_pm916, ...) are retained as h=1
aliases for backward-compat. Because every label day is strictly after the
feature-capture date, the dataset is leak-free for either horizon.
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

        # Find the next IBJA pm_916 strictly after as_of_date. idx0 is the h=1
        # label day (next trading day), idx0+1 is the h=2 label day. Both are
        # strictly after the feature-capture date → leak-free for either horizon.
        idx0 = bisect.bisect_right(ibja_dates, as_of)
        if idx0 >= len(ibja_dates) or pd.isna(ibja_pm916[idx0]):
            # No h=1 label at all → row is unusable for any horizon.
            n_no_label += 1
            continue

        # h=1 (next trading day)
        next_pm916_h1 = ibja_pm916[idx0]
        label_date_h1 = ibja_dates[idx0]
        delta_h1 = (next_pm916_h1 - current_pm916) / 10.0
        ternary_h1, binary_h1 = make_label(current_pm916, next_pm916_h1, dead_band_per_gram)

        # h=2 (the trading day after that). Optional — absent for the last usable
        # row(s); stored as NaN/None so the h=2 eval can drop them without
        # affecting the h=1 dataset.
        next_pm916_h2: float | None = None
        label_date_h2: str | None = None
        delta_h2: float | None = None
        binary_h2: float | None = None
        ternary_h2_val: str | None = None
        if idx0 + 1 < len(ibja_dates) and not pd.isna(ibja_pm916[idx0 + 1]):
            n2 = float(ibja_pm916[idx0 + 1])  # known-float inside this branch
            next_pm916_h2 = n2
            label_date_h2 = ibja_dates[idx0 + 1]
            delta_h2 = (n2 - current_pm916) / 10.0
            ternary_h2_val, binary_h2_int = make_label(current_pm916, n2, dead_band_per_gram)
            binary_h2 = float(binary_h2_int)

        feature_vals = {col: row[col] for col in FEATURE_COLS}

        rows.append(
            {
                "as_of_date": as_of,
                **feature_vals,
                "current_pm916": current_pm916,
                # h=1 (unsuffixed columns retained as h1 for backward-compat)
                "next_pm916": next_pm916_h1,
                "delta_per_gram": delta_h1,
                "label_ternary": ternary_h1,
                "label_binary": binary_h1,
                "label_date": label_date_h1,
                "next_pm916_h1": next_pm916_h1,
                "delta_per_gram_h1": delta_h1,
                "label_ternary_h1": ternary_h1,
                "label_binary_h1": binary_h1,
                "label_date_h1": label_date_h1,
                # h=2 (None when unavailable)
                "next_pm916_h2": next_pm916_h2,
                "delta_per_gram_h2": delta_h2,
                "label_ternary_h2": ternary_h2_val,
                "label_binary_h2": binary_h2,
                "label_date_h2": label_date_h2,
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
            lv = dataset["label_binary_h1"].value_counts().to_dict()
            n_h2 = int(dataset["label_binary_h2"].notna().sum())
            print(f"  Label distribution (h1): {lv}")
            print(f"  Rows with h2 label: {n_h2}")
            print(
                f"  Date range        : {dataset['as_of_date'].min()} "
                f"to {dataset['as_of_date'].max()}"
            )

    return dataset
