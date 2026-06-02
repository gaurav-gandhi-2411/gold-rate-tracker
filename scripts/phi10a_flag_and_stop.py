from __future__ import annotations

"""
Φ10A Flag-and-Stop Analysis
============================
Data alignment report for IBJA rates + macro cache overlap.
Runs BEFORE any experiment code is built.
This script is read-only — it does NOT touch production files.
"""

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# 1. Load IBJA rates
# ---------------------------------------------------------------------------
def load_ibja(path: str = "data/ibja_rates.parquet") -> pd.DataFrame:
    """Load and clean IBJA 916 series."""
    df = pd.read_parquet(path)
    df = df.sort_values("date").dropna(subset=["pm_916"]).reset_index(drop=True)
    # date column is string — convert to date
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


# ---------------------------------------------------------------------------
# 2. Load macro cache
# ---------------------------------------------------------------------------
def load_macro(path: str = "data/macro_cache.parquet") -> pd.DataFrame:
    """Load macro cache; index is UTC-aware DatetimeIndex."""
    return pd.read_parquet(path)


# ---------------------------------------------------------------------------
# 3. Overlap: for each IBJA date, look up last macro row <= that date
# ---------------------------------------------------------------------------
def compute_overlap(ibja: pd.DataFrame, macro: pd.DataFrame) -> pd.DataFrame:
    """
    For each IBJA trading date, find the last-available macro row whose
    timestamp is <= that IBJA date (look-back join).

    Returns a DataFrame indexed by IBJA date with columns:
        ibja_date, pm_916, usd_inr, gold_usd
    """
    # Build UTC timestamps for each IBJA date (midnight UTC)
    ibja_dates_utc = pd.to_datetime(ibja["date"]).dt.tz_localize("UTC")

    # macro index is already UTC
    macro_sorted = macro.sort_index()

    usd_inr_vals: list[float | None] = []
    gold_usd_vals: list[float | None] = []

    for ts_utc in ibja_dates_utc:
        # last macro row with index <= this IBJA date
        candidates = macro_sorted.loc[macro_sorted.index <= ts_utc]
        if candidates.empty:
            usd_inr_vals.append(None)
            gold_usd_vals.append(None)
        else:
            last = candidates.iloc[-1]
            usd_inr_vals.append(last["usd_inr"] if pd.notna(last["usd_inr"]) else None)
            gold_usd_vals.append(last["gold_usd"] if pd.notna(last["gold_usd"]) else None)

    result = ibja[["date", "pm_916"]].copy()
    result["usd_inr"] = usd_inr_vals
    result["gold_usd"] = gold_usd_vals
    return result


# ---------------------------------------------------------------------------
# 4. Premium series
# ---------------------------------------------------------------------------
def compute_premium(overlap: pd.DataFrame) -> pd.Series:
    """
    premium_t = ibja_pm916_t / (gold_usd_t × usd_inr_t)

    pm_916 is INR per 10g of 916-purity gold.
    Divide by 10 to get INR/g, then divide by (gold_usd × usd_inr) in INR/g.
    gold_usd is typically in USD/troy-oz; 1 troy-oz = 31.1035 g.
    So gold_usd_inr_per_g = gold_usd * usd_inr / 31.1035

    premium = (pm_916 / 10) / (gold_usd * usd_inr / 31.1035)
    """
    mask = (
        overlap["usd_inr"].notna()
        & overlap["gold_usd"].notna()
        & (overlap["usd_inr"] > 0)
        & (overlap["gold_usd"] > 0)
        & overlap["pm_916"].notna()
        & (overlap["pm_916"] > 0)
    )
    filtered = overlap[mask].copy()

    ibja_inr_per_g = filtered["pm_916"] / 10.0
    gold_inr_per_g = filtered["gold_usd"] * filtered["usd_inr"] / 31.1035
    premium = ibja_inr_per_g / gold_inr_per_g
    premium.index = filtered.index
    return premium


# ---------------------------------------------------------------------------
# 5. Walk-forward fold count
# ---------------------------------------------------------------------------
def count_effective_folds(
    overlap: pd.DataFrame,
    h: int = 5,
    min_context: int = 8,
    min_context_gate: int = 30,
) -> tuple[int, int]:
    """
    Count walk-forward folds where:
      - context_size >= min_context_gate (30)
      - macro coverage (both usd_inr and gold_usd) exists at context_end_date

    Walk-forward scheme: for fold i (0-indexed), context ends at index i + min_context - 1,
    horizon starts at i + min_context, ends at i + min_context + h - 1.
    We only count folds where the last index of the horizon <= len(overlap) - 1.

    Returns (total_possible_folds, effective_folds_meeting_gate).
    """
    n = len(overlap)
    has_macro = overlap["usd_inr"].notna() & overlap["gold_usd"].notna()

    total_possible = 0
    effective = 0

    for i in range(n):
        context_end_idx = i + min_context - 1
        horizon_end_idx = i + min_context + h - 1

        if horizon_end_idx >= n:
            break  # not enough data for this fold

        context_size = min_context  # always min_context (expanding window from fold start i)
        # For expanding window: context_size = context_end_idx - 0 + 1 = context_end_idx + 1
        # But the spec says "context_size >= 30", so use expanding window where
        # context_size = context_end_idx + 1 (all rows from 0 to context_end_idx)
        context_size_expanding = context_end_idx + 1

        total_possible += 1

        if context_size_expanding >= min_context_gate and has_macro.iloc[context_end_idx]:
            effective += 1

    return total_possible, effective


# ---------------------------------------------------------------------------
# Main report
# ---------------------------------------------------------------------------
def main() -> None:
    """Run all analyses and print the flag-and-stop report."""
    print("=" * 70)
    print("Φ10A FLAG-AND-STOP ANALYSIS — Data Alignment Report")
    print("=" * 70)

    # -----------------------------------------------------------------------
    # Section 1: IBJA 916 series
    # -----------------------------------------------------------------------
    ibja = load_ibja()
    print()
    print("── SECTION 1: IBJA 916 Series ─────────────────────────────────────")
    print(f"  Column used          : pm_916")
    print(f"  Series length        : {len(ibja):,} rows")
    print(f"  First date           : {ibja['date'].iloc[0]}")
    print(f"  Last date            : {ibja['date'].iloc[-1]}")
    ibja_years = (
        pd.to_datetime(str(ibja["date"].iloc[-1]))
        - pd.to_datetime(str(ibja["date"].iloc[0]))
    ).days / 365.25
    print(f"  Approx years         : {ibja_years:.2f}")

    # -----------------------------------------------------------------------
    # Section 2: Macro cache
    # -----------------------------------------------------------------------
    macro = load_macro()
    print()
    print("── SECTION 2: Macro Cache ──────────────────────────────────────────")
    macro_first = macro.index.min()
    macro_last = macro.index.max()
    macro_years = (macro_last - macro_first).days / 365.25
    usd_inr_nonnull = macro["usd_inr"].notna().sum()
    gold_usd_nonnull = macro["gold_usd"].notna().sum()
    print(f"  First date (UTC)     : {macro_first}")
    print(f"  Last date (UTC)      : {macro_last}")
    print(f"  Total rows           : {len(macro):,}")
    print(f"  Approx years         : {macro_years:.2f}")
    print(f"  usd_inr  non-null    : {usd_inr_nonnull:,} / {len(macro):,}")
    print(f"  gold_usd non-null    : {gold_usd_nonnull:,} / {len(macro):,}")

    # -----------------------------------------------------------------------
    # Section 3: Overlap window
    # -----------------------------------------------------------------------
    print()
    print("── SECTION 3: Overlap Window ───────────────────────────────────────")
    overlap = compute_overlap(ibja, macro)

    has_both = overlap["usd_inr"].notna() & overlap["gold_usd"].notna()
    covered = overlap[has_both]
    not_covered = overlap[~has_both]

    total_ibja = len(overlap)
    ibja_with_macro = has_both.sum()
    ibja_before_macro = (~has_both).sum()

    # Max forward-fill gap: calendar days between consecutive IBJA dates IN the overlap
    if len(covered) >= 2:
        covered_dates = pd.to_datetime(covered["date"].astype(str))
        gaps = covered_dates.diff().dropna().dt.days
        max_gap = int(gaps.max())
        mean_gap = gaps.mean()
    else:
        max_gap = 0
        mean_gap = 0.0

    overlap_start = covered["date"].iloc[0] if len(covered) > 0 else None
    overlap_end = covered["date"].iloc[-1] if len(covered) > 0 else None

    print(f"  Total IBJA rows                    : {total_ibja:,}")
    print(f"  IBJA rows WITH macro coverage      : {ibja_with_macro:,}")
    print(f"  IBJA rows BEFORE macro starts      : {ibja_before_macro:,}")
    print(f"  Overlap start date                 : {overlap_start}")
    print(f"  Overlap end date                   : {overlap_end}")
    print(f"  Max consecutive-date gap (cal days): {max_gap}")
    print(f"  Mean consecutive-date gap          : {mean_gap:.2f} days")

    # -----------------------------------------------------------------------
    # Section 4: Premium series
    # -----------------------------------------------------------------------
    print()
    print("── SECTION 4: Premium Series ───────────────────────────────────────")
    premium = compute_premium(overlap)
    if len(premium) > 0:
        cv = premium.std() / premium.mean()
        print(f"  premium = (pm_916 / 10) / (gold_usd × usd_inr / 31.1035)")
        print(f"  Values computed    : {len(premium):,}")
        print(f"  Mean premium       : {premium.mean():.6f}")
        print(f"  Std  premium       : {premium.std():.6f}")
        print(f"  Min  premium       : {premium.min():.6f}")
        print(f"  Max  premium       : {premium.max():.6f}")
        print(f"  CV (std/mean)      : {cv:.6f}  (stability; lower = more stable)")
    else:
        print("  ERROR: No premium values computed — check driver columns.")

    # -----------------------------------------------------------------------
    # Section 5: Walk-forward fold count
    # -----------------------------------------------------------------------
    print()
    print("── SECTION 5: Walk-Forward Fold Count (h=5, min_context=8) ─────────")
    total_folds, effective_folds = count_effective_folds(overlap)
    gate_pass = effective_folds >= 30
    print(f"  Total possible folds               : {total_folds:,}")
    print(f"  Folds with context>=30 & macro     : {effective_folds:,}")
    print(f"  Gate threshold                     : >= 30 effective folds")
    print(f"  Gate result                        : {'PASS' if gate_pass else 'FAIL'}")

    # -----------------------------------------------------------------------
    # Summary / Flag-and-Stop verdict
    # -----------------------------------------------------------------------
    print()
    print("=" * 70)
    print("FLAG-AND-STOP VERDICT")
    print("=" * 70)
    issues: list[str] = []
    if ibja_with_macro == 0:
        issues.append("CRITICAL: Zero IBJA rows have macro coverage — no overlap at all.")
    if len(premium) == 0:
        issues.append("CRITICAL: Premium series is empty — cannot compute drivers.")
    if not gate_pass:
        issues.append(
            f"GATE FAIL: Only {effective_folds} effective folds (need >= 30)."
        )
    if max_gap > 7:
        issues.append(
            f"WARNING: Max calendar gap in overlap is {max_gap} days "
            "(exceeds 7-day tolerance; stale macro fill risk)."
        )

    if issues:
        print()
        for iss in issues:
            print(f"  [!] {iss}")
        print()
        print("  STOP — resolve the above before building experiment code.")
    else:
        print()
        print("  All checks passed. Experiment build may proceed.")
        print()
        print("  Key numbers for experiment design:")
        print(f"    Overlap rows   : {ibja_with_macro}")
        print(f"    Premium CV     : {premium.std() / premium.mean():.4f}")
        print(f"    Effective folds: {effective_folds}")
    print("=" * 70)


if __name__ == "__main__":
    main()
