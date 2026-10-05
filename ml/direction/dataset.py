"""ml.direction.dataset — build a leak-free directional forecast dataset.

Features come from snapshots.parquet (captured at day t).
Labels come from IBJA pm_916 entries STRICTLY AFTER the capture date:
  - h=1 (next trading day)      → label_binary_h1 / label_ternary_h1
  - h=2 (the day after that)    → label_binary_h2 / label_ternary_h2  (None if absent)
The unsuffixed columns (label_binary, next_pm916, ...) are retained as h=1
aliases for backward-compat. Because every label day is strictly after the
feature-capture date, the dataset is leak-free for either horizon.

Consecutive publication days only (GG decision G2, ADR 042). data/ibja_rates.parquet is
not daily before 2025-Q2 and has multi-week holes after it (14-101 days), so "the next
IBJA row" was sometimes months away: 13 of 182 "2-day" labels spanned 7-101 days. A label
is now built only when every step from the capture day to the label day is between
consecutive IBJA publication days -- at most one weekday without a publication in
between (a single IBJA holiday; every such step in the dense data falls on one, e.g.
Good Friday 2025-04-18, Maharashtra Day 2025/2026-05-01, Ganesh Chaturthi 2026-09-14). A row whose h=1 step is
not consecutive is dropped; an h=2/h=N label crossing a hole is None.
"""

from __future__ import annotations

import bisect
from pathlib import Path

import numpy as np
import pandas as pd

from ml.direction.price_units import INR_PER_10G, declare_units

PROVENANCE_COLS: tuple[str, ...] = ("source", "capture_utc")

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
DEAD_BAND_PER_GRAM: float = 50.0
MAX_N_MACRO_NULL: int = 3
# np.busday_count(prev, next) counts weekdays in [prev, next): 1 = next weekday (incl.
# Fri -> Mon), 2 = one weekday skipped (a single holiday). 3+ = a hole in the record.
MAX_WEEKDAYS_PER_STEP: int = 2

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
    extra_horizons: tuple[int, ...] = (),
    require_consecutive: bool = True,
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
        (d) require_consecutive: the h=1 label day is not the next IBJA
            publication day (a hole in the record, see the module docstring).

    Args:
        snapshots_path: Path to snapshots.parquet.
        ibja_path: Path to ibja_rates.parquet.
        dead_band_per_gram: Dead band in ₹/gram for ternary labelling.
        max_n_macro_null: Maximum tolerated macro-null count.
        verbose: If True, print a build summary to stdout.
        snapshots_df: Inject a DataFrame in place of reading snapshots_path
            (for unit tests — parquet read is skipped when provided).
        ibja_df: Inject a DataFrame in place of reading ibja_path.
        extra_horizons: Additional trading-day horizons (e.g. (5, 10)) beyond
            the always-present h=1/h=2, for M2's longer-horizon reframed
            targets. For each N here, adds next_pm916_hN, delta_per_gram_hN,
            label_ternary_hN, label_binary_hN, label_date_hN (None/NaN when
            fewer than N future IBJA days exist yet), and window_min_pm916_hN
            (the minimum IBJA pm_916 across days [t+1 .. t+N] — the "did it
            dip along the way" path information a single endpoint delta
            can't answer, needed for the buyer's-decision target).
        require_consecutive: build labels only across consecutive IBJA
            publication days (default). False reproduces the pre-G2 labels,
            which bridge holes of up to 101 days -- kept ONLY so the
            superseded ADR 038 (v1) reference figures stay reproducible.

    Returns:
        DataFrame with columns: as_of_date, <FEATURE_COLS>, current_pm916,
        next_pm916, delta_per_gram, label_ternary, label_binary, label_date,
        ibja_pm_916_asof_date, n_macro_null, plus h1/h2 and any
        extra_horizons columns described above.  One row per kept snapshot,
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
    ibja_day64 = np.array(ibja_dates, dtype="datetime64[D]")

    def _consecutive(start: str, end_idx: int) -> bool:
        """True when every IBJA step from `start` (an IBJA date) to ibja_dates[end_idx]
        is between consecutive publication days."""
        if not require_consecutive:
            return True
        prev = np.datetime64(start, "D")
        for k in range(bisect.bisect_right(ibja_dates, start), end_idx + 1):
            if int(np.busday_count(prev, ibja_day64[k])) > MAX_WEEKDAYS_PER_STEP:
                return False
            prev = ibja_day64[k]
        return True

    n_input = len(snaps)
    n_stale = 0
    n_macro = 0
    n_no_label = 0
    n_gap_h1 = 0

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
        # (d) The h=1 label must be the next publication day after the capture day.
        if not _consecutive(as_of, idx0):
            n_gap_h1 += 1
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
        if (
            idx0 + 1 < len(ibja_dates)
            and not pd.isna(ibja_pm916[idx0 + 1])
            and _consecutive(as_of, idx0 + 1)
        ):
            n2 = float(ibja_pm916[idx0 + 1])  # known-float inside this branch
            next_pm916_h2 = n2
            label_date_h2 = ibja_dates[idx0 + 1]
            delta_h2 = (n2 - current_pm916) / 10.0
            ternary_h2_val, binary_h2_int = make_label(current_pm916, n2, dead_band_per_gram)
            binary_h2 = float(binary_h2_int)

        feature_vals = {col: row[col] for col in FEATURE_COLS}
        # Provenance for ml.direction.leak_checks (ADR 061): when each feature became known.
        # Not model inputs -- every harness selects feature columns explicitly.
        provenance = {col: row.get(col) for col in PROVENANCE_COLS if col in row.index} | {
            f"{col}_asof_date": row.get(f"{col}_asof_date")
            for col in FEATURE_COLS
            if f"{col}_asof_date" in row.index and col != "ibja_pm_916"
        }

        row_out: dict = {
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
            **provenance,
        }

        # Extra horizons (M2: 5/10-day reframed targets). idx0 + (N-1) is the
        # h=N label day (idx0 is h=1, idx0+1 is h=2, by the same convention
        # above) — strictly after as_of_date for any N >= 1, so still
        # leak-free. window_min_pm916_hN is the minimum pm_916 across days
        # [idx0 .. idx0+N-1] inclusive (every day strictly between t and the
        # h=N label day, plus the label day itself) — the buyer's-decision
        # target needs "did it dip along the way," not just the endpoint.
        for horizon_n in extra_horizons:
            end_idx = idx0 + (horizon_n - 1)
            key = f"h{horizon_n}"
            if (
                end_idx < len(ibja_dates)
                and not pd.isna(ibja_pm916[end_idx])
                and _consecutive(as_of, end_idx)
            ):
                window = ibja_pm916[idx0 : end_idx + 1]
                window_valid = [v for v in window if not pd.isna(v)]
                end_val = float(ibja_pm916[end_idx])
                ternary_n, binary_n_int = make_label(current_pm916, end_val, dead_band_per_gram)
                row_out[f"next_pm916_{key}"] = end_val
                row_out[f"delta_per_gram_{key}"] = (end_val - current_pm916) / 10.0
                row_out[f"label_ternary_{key}"] = ternary_n
                row_out[f"label_binary_{key}"] = float(binary_n_int)
                row_out[f"label_date_{key}"] = ibja_dates[end_idx]
                row_out[f"window_min_pm916_{key}"] = min(window_valid) if window_valid else None
            else:
                row_out[f"next_pm916_{key}"] = None
                row_out[f"delta_per_gram_{key}"] = None
                row_out[f"label_ternary_{key}"] = None
                row_out[f"label_binary_{key}"] = None
                row_out[f"label_date_{key}"] = None
                row_out[f"window_min_pm916_{key}"] = None

        rows.append(row_out)

    dataset = pd.DataFrame(rows)
    if not dataset.empty:
        dataset = dataset.sort_values("as_of_date").reset_index(drop=True)

    if verbose:
        print("=== Phi23 Dataset Build ===")
        print(f"  Input snapshots   : {n_input}")
        print(f"  Excluded (stale)  : {n_stale}")
        print(f"  Excluded (macro)  : {n_macro}")
        print(f"  Excluded (no label): {n_no_label}")
        print(f"  Excluded (h1 crosses a hole in the IBJA record): {n_gap_h1}")
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

    return declare_units(dataset, INR_PER_10G)
