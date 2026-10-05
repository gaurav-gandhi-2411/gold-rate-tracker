"""inr_proxy.py — Reconstruct a long-history INR 22K gold-price proxy (M1).

Purpose: the direction model (M2) and Chronos companion (M4) both need many
more years of daily 22K INR/10g history than real IBJA data provides
(IBJA overlap only starts 2022-01-19 — data/ibja_rates.parquet). This module
builds a proxy series back to 2013-01-01 (13+ years) from free daily data,
so those models can pretrain on the proxy's *shape* and be evaluated only on
real forward folds. It is a PRETRAINING INPUT, never a production price
source — ml.sources.ibja / ml.calibration remain the only source used for
live pricing.

Construction, in order:
  1. COMEX gold futures (GC=F, USD/troy-oz) x USD/INR spot (INR=X)
     / 31.1034768 (troy oz -> gram) x (22/24) (24K -> 22K purity) x 10
     (per-gram -> per-10g, matching IBJA's quoting convention) = raw
     pre-duty 22K INR/10g.
  2. Multiplied by (1 + effective import duty/cess rate in force on that
     date), reconstructed from data/duty_cbic.json (verified rows 2019-07-06+,
     plus its unverified_pre_2019 legacy segment for 2013-2019 coverage — see
     ml.duty_schedule and ADR references in the D2 migration PR).
  3. A residual premium (scale + offset) fit walk-forward against real IBJA
     rates over the 2022-01-19+ overlap, to absorb everything the formula
     above can't capture: local demand premium, GST timing, dealer spreads,
     exchange-rate quoting conventions.

Leakage controls (the three traps GG's spec calls out explicitly):
  1. TIME ALIGNMENT — every day's proxy uses ONLY the PRIOR calendar day's
     (T-1) COMEX/FX close. COMEX/Globex trades almost continuously through
     the IST evening — well past IBJA's ~17:00 IST PM fix (see
     ml.sources.ibja._IBJA_PUBLISH_UTC) — so a same-day close would leak
     information the real fix could not have seen at publish time. T-1's
     close is the most recent value that was fully final and public before
     either of a day's two IST fixes.
  2. FUTURES ROLLS — GC=F is a front-month continuous series; each monthly
     contract roll can produce a price discontinuity unrelated to the spot
     gold price. Detected via divergence from GLD (SPDR Gold Shares, tracks
     physical spot gold 1:1 minus expense ratio, and does NOT roll — see
     _detect_and_adjust_rolls) and ratio-back-adjusted so the reconstructed
     series has no artificial jumps, preserving genuine co-moves.
  3. PREMIUM CALIBRATION — fit ONLY on an expanding window of PAST overlap
     pairs (never the full-period fit). Reuses ml.calibration's
     _fit_robust/_recency_weights primitives (same HuberRegressor +
     half-life recency weighting already validated for the Tanishq fit)
     rather than inventing a second fitting scheme. Dates before the first
     walk-forward fold (i.e. before 30 overlap pairs exist) use that first
     fold's frozen parameters — a fixed, non-adaptive transform, so no
     information from later dates leaks backward into earlier proxy values.

Usage (from repo root):
    python -m ml.inr_proxy --build     # writes data/history_seed_inr22k_proxy.parquet
    python -m ml.inr_proxy --validate  # prints MAE/correlation/direction-agreement vs real IBJA
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from ml.calibration import _DEFAULT_HALF_LIFE, _DEFAULT_HUBER_EPSILON, _fit_robust, _recency_weights
from ml.duty_schedule import DUTY_TABLE_PATH, load_all_rows_including_unverified
from ml.macro import _download_with_retry

DATA_DIR = Path(__file__).parent.parent / "data"
IBJA_PARQUET_PATH = DATA_DIR / "ibja_rates.parquet"
PROXY_OUTPUT_PATH = DATA_DIR / "history_seed_inr22k_proxy.parquet"

TROY_OZ_TO_GRAM = 31.1034768
PURITY_22K_OF_24K = 22.0 / 24.0
GRAMS_PER_QUOTE_UNIT = 10.0  # IBJA quotes per 10g

PROXY_START_DATE = "2013-01-01"  # matches duty_cbic.json's unverified_pre_2019 earliest row
_FETCH_BUFFER_DAYS = 30  # extra lookback so shift(1)/rolling windows have no NaN at the start

# Basic customs duty in force immediately before the first recorded event
# (2013-01-01: "raised from 4% to 6%"). WGC/RBI historical references confirm
# 4% was the prevailing ad-valorem rate for several years before 2013 — used
# only as the pre-2013-01-01 base; the proxy series itself starts 2013-01-01.
_BASE_DUTY_PCT = 4.0

_ROLL_DIVERGENCE_SIGMA = 6.0  # see _detect_and_adjust_rolls
_ROLL_ROLLING_WINDOW = 60

MIN_OVERLAP_FOR_WALK_FORWARD = 30  # matches ml.calibration._MIN_FIT_OBSERVATIONS


# ---------------------------------------------------------------------------
# 1. Raw driver data (COMEX, FX, roll-reference)
# ---------------------------------------------------------------------------


def _fetch_raw_drivers(start: str, end: str) -> pd.DataFrame:
    """Download GC=F, INR=X, GLD daily closes, forward-filled onto a full
    calendar (weekends/holidays inherit the last known value, same
    convention ml.macro uses)."""
    tickers = ["GC=F", "INR=X", "GLD"]
    raw = _download_with_retry(tickers, start=start, end=end)
    if raw.empty:
        raise RuntimeError(f"yfinance returned no data for {tickers} in [{start}, {end})")

    raw.index = pd.to_datetime(raw.index, utc=True)
    full_idx = pd.date_range(start=raw.index.min(), end=raw.index.max(), freq="D", tz="UTC")
    raw = raw.reindex(full_idx)

    out = pd.DataFrame(index=raw.index)
    for col, ticker in [("gold_usd", "GC=F"), ("usd_inr", "INR=X"), ("gld", "GLD")]:
        if (("Close", ticker)) in raw.columns:
            out[col] = raw[("Close", ticker)].values
        else:
            raise RuntimeError(f"Close/{ticker} column missing from yfinance download")

    out = out.ffill()
    return out


def _detect_and_adjust_rolls(
    gc: pd.Series,
    gld: pd.Series,
    threshold_sigma: float = _ROLL_DIVERGENCE_SIGMA,
    rolling_window: int = _ROLL_ROLLING_WINDOW,
) -> tuple[pd.Series, pd.Series]:
    """Ratio-back-adjust GC=F for front-month roll discontinuities.

    GLD tracks physical spot gold and never rolls, so on a genuine gold-price
    move both series' daily log-returns move together; a day where GC=F's
    log-return diverges sharply from GLD's is much more likely to be a roll
    artifact. Each flagged day is corrected by multiplying that day and every
    subsequent day by the ratio needed to remove exactly the excess
    divergence, so genuine cumulative moves before/after are preserved and
    only the artificial jump is spliced out.

    Returns (adjusted_gc, flagged_mask).
    """
    log_ret_gc = pd.Series(np.log(gc / gc.shift(1)), index=gc.index)
    log_ret_gld = pd.Series(np.log(gld / gld.shift(1)), index=gld.index)
    divergence = log_ret_gc - log_ret_gld

    rolling_median = divergence.rolling(rolling_window, min_periods=20).median()
    rolling_std = divergence.rolling(rolling_window, min_periods=20).std()
    excess = divergence - rolling_median

    flagged = (
        (excess.abs() > threshold_sigma * rolling_std) & rolling_std.notna() & (rolling_std > 0)
    )
    flagged = flagged.fillna(False)

    adjusted = gc.copy()
    for dt in gc.index[flagged]:
        ratio = np.exp(-excess.loc[dt])
        adjusted.loc[dt:] = adjusted.loc[dt:] * ratio

    return adjusted, flagged


# ---------------------------------------------------------------------------
# 2. Duty/cess schedule
# ---------------------------------------------------------------------------


def load_duty_schedule(
    path: Path = DUTY_TABLE_PATH, index: pd.DatetimeIndex | None = None
) -> pd.Series:
    """Return the total ad-valorem duty+cess rate (%) in force on each date,
    as a daily step series derived from data/duty_cbic.json.

    Unlike the retired data/duty_events.json (cumulative per-event deltas of
    inconsistent basis — some BCD-only, some total-tax-incl-GST), each
    duty_cbic.json row's total_duty_pct is an ABSOLUTE level (BCD+AIDC+SWS,
    ex-GST) taken as-is for that date onward — no summing. Rows come from
    ml.duty_schedule.load_all_rows_including_unverified, i.e. the verified
    2019-07-06+ rows plus the unverified pre-2019 legacy segment this proxy
    needs for its 2013+ coverage (see that module's docstring). Dates before
    the earliest row (2013-01-01) use _BASE_DUTY_PCT (4.0, the assumed
    pre-2013 rate — not itself a row, see _BASE_DUTY_PCT's own comment).
    """
    rows = load_all_rows_including_unverified(path)

    step_dates: list[pd.Timestamp] = []
    step_rates: list[float] = []
    for row in rows:
        step_dates.append(pd.Timestamp(row["effective_date"], tz="UTC"))
        step_rates.append(float(row["total_duty_pct"]))

    steps = pd.Series(step_rates, index=pd.DatetimeIndex(step_dates))

    if index is None:
        return steps

    daily = steps.reindex(index.union(pd.DatetimeIndex(steps.index))).ffill()
    daily = daily.reindex(index)
    daily = daily.fillna(_BASE_DUTY_PCT)  # dates before the first row
    return daily


# ---------------------------------------------------------------------------
# 3. Walk-forward premium calibration against real IBJA
# ---------------------------------------------------------------------------


def _load_ibja_per_g(path: Path = IBJA_PARQUET_PATH) -> pd.DataFrame:
    """Return a DataFrame indexed by UTC date with a single `ibja_22k_per_10g`
    column (= pm_916, already per-10g at 91.6% / 22K purity), PM-fix rows
    only, non-null, sorted ascending."""
    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df["date"], utc=True)
    df = df[df["pm_916"].notna()].sort_values("date")
    out = df[["date", "pm_916"]].rename(columns={"pm_916": "ibja_22k_per_10g"}).set_index("date")
    return out


def _walk_forward_premium(
    raw_with_duty: pd.Series,
    ibja: pd.Series,
    huber_epsilon: float = _DEFAULT_HUBER_EPSILON,
    half_life: float = _DEFAULT_HALF_LIFE,
    min_train: int = MIN_OVERLAP_FOR_WALK_FORWARD,
) -> pd.DataFrame:
    """Expanding-window walk-forward fit of raw_with_duty -> ibja_22k_per_10g.

    Returns a DataFrame indexed like the overlap, with columns
    slope/intercept/predicted/is_oos (is_oos=False for the first min_train
    rows, which have no walk-forward fold yet and are excluded from
    validation metrics — they still get a fit, but it's the frozen first
    fold's params applied to their own (already-seen) inputs, same posture
    as ml.calibration.fit_calibration's in-sample-until-unlocked behavior).
    """
    merged = pd.DataFrame({"x": raw_with_duty, "y": ibja}).dropna()
    merged = merged.sort_index()

    if len(merged) < min_train + 1:
        raise ValueError(
            f"_walk_forward_premium requires >= {min_train + 1} overlap days; got {len(merged)}"
        )

    X = merged["x"].to_numpy().reshape(-1, 1)
    y = merged["y"].to_numpy()

    slopes = np.full(len(merged), np.nan)
    intercepts = np.full(len(merged), np.nan)
    predicted = np.full(len(merged), np.nan)
    is_oos = np.zeros(len(merged), dtype=bool)

    first_slope, first_intercept = _fit_robust(
        X[:min_train],
        y[:min_train],
        huber_epsilon=huber_epsilon,
        weights=_recency_weights(min_train, half_life),
    )
    slopes[:min_train] = first_slope
    intercepts[:min_train] = first_intercept
    predicted[:min_train] = first_slope * X[:min_train, 0] + first_intercept

    for i in range(min_train, len(merged)):
        weights = _recency_weights(i, half_life)
        slope, intercept = _fit_robust(X[:i], y[:i], huber_epsilon=huber_epsilon, weights=weights)
        slopes[i] = slope
        intercepts[i] = intercept
        predicted[i] = slope * X[i, 0] + intercept
        is_oos[i] = True

    return pd.DataFrame(
        {"slope": slopes, "intercept": intercepts, "predicted": predicted, "is_oos": is_oos},
        index=merged.index,
    )


# ---------------------------------------------------------------------------
# 4. Public pipeline
# ---------------------------------------------------------------------------


def build_proxy_history(
    start: str = PROXY_START_DATE,
    end: str | None = None,
    duty_events_path: Path = DUTY_TABLE_PATH,
    ibja_path: Path = IBJA_PARQUET_PATH,
) -> pd.DataFrame:
    """Build the full proxy history DataFrame.

    ``duty_events_path`` points at data/duty_cbic.json (kept as the historical
    parameter name; the file it points to changed under D2 — see
    load_duty_schedule).

    Returns a DataFrame indexed by UTC date with columns:
      raw_pre_duty        — COMEX x FX x purity, no duty, no calibration
      duty_pct            — total ad-valorem duty+cess rate in force
      raw_with_duty       — raw_pre_duty x (1 + duty_pct/100)
      proxy_22k_per_10g   — final calibrated proxy (the column to use)
      is_walk_forward_oos — True where proxy_22k_per_10g came from a genuine
                             walk-forward fold (no future information used);
                             False where it uses the frozen first fold
                             (pre-2022-01-19-plus-30-days, or missing IBJA)
      roll_adjusted       — True on days GC=F was ratio-adjusted for a
                             detected futures-roll discontinuity
    """
    if end is None:
        end = (date.today() + timedelta(days=1)).isoformat()

    fetch_start = (
        datetime.fromisoformat(start).date() - timedelta(days=_FETCH_BUFFER_DAYS)
    ).isoformat()
    drivers = _fetch_raw_drivers(fetch_start, end)

    gc_adjusted, roll_flags = _detect_and_adjust_rolls(drivers["gold_usd"], drivers["gld"])

    # Leakage control 1: T-1 lag -- shift(1) means day T's proxy uses only
    # what was known and final as of the close BEFORE day T's IST fixes.
    gc_lag = gc_adjusted.shift(1)
    usd_inr_lag = drivers["usd_inr"].shift(1)
    roll_flags_lag = roll_flags.shift(1).astype("boolean").fillna(False).astype(bool)

    raw_pre_duty = gc_lag / TROY_OZ_TO_GRAM * usd_inr_lag * PURITY_22K_OF_24K * GRAMS_PER_QUOTE_UNIT

    full_index = pd.DatetimeIndex(raw_pre_duty.index)
    duty_pct = load_duty_schedule(path=duty_events_path, index=full_index)
    raw_with_duty = raw_pre_duty * (1.0 + duty_pct / 100.0)

    ibja = _load_ibja_per_g(ibja_path)["ibja_22k_per_10g"]
    ibja = ibja.reindex(full_index)  # NaN outside the real IBJA overlap

    try:
        premium = _walk_forward_premium(raw_with_duty, ibja)
    except ValueError:
        # Not enough overlap yet anywhere in range -- return an uncalibrated
        # proxy rather than fail the whole build (honest, not fabricated).
        result = pd.DataFrame(
            {
                "raw_pre_duty": raw_pre_duty,
                "duty_pct": duty_pct,
                "raw_with_duty": raw_with_duty,
                "proxy_22k_per_10g": raw_with_duty,
                "is_walk_forward_oos": False,
                "roll_adjusted": roll_flags_lag,
            }
        )
        return result.loc[start:end]  # type: ignore[misc]  # str dates valid at runtime on a DatetimeIndex

    first_slope = premium["slope"].iloc[0]
    first_intercept = premium["intercept"].iloc[0]
    proxy = first_slope * raw_with_duty + first_intercept
    proxy.loc[premium.index] = premium["predicted"]

    is_oos = pd.Series(False, index=full_index)
    is_oos.loc[premium.index] = premium["is_oos"]

    result = pd.DataFrame(
        {
            "raw_pre_duty": raw_pre_duty,
            "duty_pct": duty_pct,
            "raw_with_duty": raw_with_duty,
            "proxy_22k_per_10g": proxy,
            "is_walk_forward_oos": is_oos,
            "roll_adjusted": roll_flags_lag,
        }
    )
    return result.loc[start:end]  # type: ignore[misc]  # str dates valid at runtime on a DatetimeIndex


def save_proxy_history(df: pd.DataFrame, path: Path = PROXY_OUTPUT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)


# ---------------------------------------------------------------------------
# 5. Validation against real IBJA
# ---------------------------------------------------------------------------


def _wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = successes / n
    denom = 1 + z**2 / n
    centre = p + z**2 / (2 * n)
    margin = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))
    return ((centre - margin) / denom, (centre + margin) / denom)


def validate_against_ibja(proxy_df: pd.DataFrame, ibja_path: Path = IBJA_PARQUET_PATH) -> dict:
    """Validate proxy_22k_per_10g against real IBJA on the OOS-only overlap.

    Reports MAE (Rs/g and %), Pearson correlation of levels, and — the
    metric that matters most per GG's spec — DIRECTION AGREEMENT between
    day-over-day proxy changes and day-over-day real IBJA changes, with a
    Wilson 95% CI and an always-up baseline for comparison. Only genuine
    new-value IBJA days are used (duplicate/carried-forward PM values are
    dropped before computing changes, so a flat weekend carry-forward isn't
    counted as a real "no change" the proxy has to match).
    """
    ibja = _load_ibja_per_g(ibja_path)["ibja_22k_per_10g"]
    merged = pd.DataFrame({"proxy": proxy_df["proxy_22k_per_10g"], "ibja": ibja}).dropna()
    merged = merged[proxy_df.loc[merged.index, "is_walk_forward_oos"]]

    if merged.empty:
        return {"error": "no walk-forward OOS overlap with real IBJA data"}

    residual = merged["proxy"] - merged["ibja"]
    mae_rs = float(residual.abs().mean())
    mae_pct = float((residual.abs() / merged["ibja"]).mean() * 100)
    corr_level = float(merged["proxy"].corr(merged["ibja"]))

    # Drop consecutive-duplicate IBJA values (carried-forward, not a new fix)
    # before computing day-over-day changes.
    is_new_value = merged["ibja"].diff().fillna(1.0) != 0
    changes = merged[is_new_value].copy()
    d_proxy = changes["proxy"].diff()
    d_ibja = changes["ibja"].diff()
    valid = d_proxy.notna() & d_ibja.notna() & (d_ibja != 0)

    n_dir = int(valid.sum())
    if n_dir == 0:
        direction_agreement = float("nan")
        ci = (float("nan"), float("nan"))
        baseline_always_up = float("nan")
    else:
        agree = int((np.sign(d_proxy[valid]) == np.sign(d_ibja[valid])).sum())
        direction_agreement = agree / n_dir
        ci = _wilson_ci(agree, n_dir)
        baseline_always_up = float((d_ibja[valid] > 0).mean())

    return {
        "n_level_overlap_oos": len(merged),
        "mae_rs_per_10g": mae_rs,
        "mae_pct": mae_pct,
        "correlation_level": corr_level,
        "n_direction_days": n_dir,
        "direction_agreement": direction_agreement,
        "direction_agreement_wilson_ci_95": list(ci),
        "baseline_always_up_rate": baseline_always_up,
        "method": "walk_forward_oos_only_new_value_days",
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()

    if args.build or not args.validate:
        print(f"Building proxy history from {PROXY_START_DATE}...")
        df = build_proxy_history()
        save_proxy_history(df)
        print(
            f"Wrote {len(df)} rows to {PROXY_OUTPUT_PATH} "
            f"({df.index.min().date()} to {df.index.max().date()})"
        )
    else:
        df = pd.read_parquet(PROXY_OUTPUT_PATH)

    if args.validate:
        result = validate_against_ibja(df)
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
