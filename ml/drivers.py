"""
drivers.py — Log-decomposition attribution of recent Indian gold moves.

IBJA ≈ gold_usd × usdinr × premium is MULTIPLICATIVE. Uses the log decomposition:

    Δln(IBJA_916) = Δln(gold_usd) + Δln(usd_inr) + Δln(premium)

which is genuinely additive — terms sum exactly, no cross-term artefact.

HONESTY HARD LINE (ADR 005 + Φ14 spec):
  - DESCRIPTIVE only — attributes an ALREADY-OBSERVED move, NOT a forecast.
  - The premium residual is a sanity check; it is NEVER shown as a "driver".
  - If |premium share| > PREMIUM_THRESHOLD_PCT → attribution_valid = False;
    display degrades to driver-state-only or suppresses (norm #8, no silent fallback).
  - If macro is stale → attribution_valid = False for all windows.

TIMESTAMP CONVENTION (ADR 058, timing audit A16; fixed 2026-09-25):
  Every IBJA row is a FIX at a known instant: PM ~17:00 IST (11:30 UTC) on its date, or AM
  ~12:00 IST (06:30 UTC) when that day has no PM value (repo convention, ml/sources/ibja.py and
  ml/markup.py; IBJA's real publish times are not recorded). A fix-to-fix IBJA move is split only
  against the global move BETWEEN THE TWO FIX INSTANTS:
    attribution windows  gold_usd / usd_inr = Yahoo 1-hour GC=F / INR=X bars (written by
              ml/macro.py to macro_intraday.parquet, bars labelled by their START in UTC), read
              as the Close of the last bar that ENDED at or before the fix. A fix with no bar
              ending within MAX_INTRADAY_GAP_HOURS before it is dropped. If intraday bars are
              missing or do not cover the latest fix, every window degrades to
              attribution_valid=False (the Rs split and the premium residual are not shown).
    driver_state (30d % change, "now" levels) uses the same intraday-at-fix frame when it is
              available; otherwise the latest DAILY bars public at each fix: GC=F daily Close =
              COMEX settle 13:30 America/New_York (VERIFIED in ADR 058), so IBJA date D gets the
              previous NY day's settle; INR=X daily is a snapshot with no pinned clock, taken as
              known at 23:59 UTC of its date (conservative), so D gets the previous day's bar.
  Why not the lagged daily bar for the split as well: it is leak-free but sits 17-33 h before
  the fix, which misses more of the move than the old same-date join did. Measured on
  2024-10-08..2026-09-24 (n=207 fix pairs, scripts/analysis_drivers_timing.py): SD of the
  fix-to-fix premium residual is 1.35% same-date (old), 1.55% lagged daily, 0.63% intraday at the
  fix; direction agreement 68% / 67% / 90%. The old same-date join paired each fix with a COMEX
  settle taken ~6.5 h AFTER it, so gold moves after the fix were counted against a fix that could
  not contain them, and the mismatch surfaced as "premium / local factors".

Units (verified from ml/calibration.py and ml/ibja.py):
  ibja pm_916 : INR per 10g (raw integer; ibja_per_g = pm_916 / 10)
  macro gold_usd : USD per troy oz  (GC=F)
  macro usd_inr  : INR per USD      (INR=X)
  tanishq 22k    : INR per gram     (prices.json)
"""

from __future__ import annotations

import json
import logging
import math
import time
from datetime import UTC, datetime
from datetime import time as dtime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Unit constants
# ---------------------------------------------------------------------------
_TROY_G_PER_OZ: float = 31.1035  # grams per troy ounce
_PURITY_916: float = 0.916  # 916‰ purity (22K)
# Pure-gold troy-oz equivalent in 10g of 916-purity gold
_CONV_10G_916: float = (10.0 / _TROY_G_PER_OZ) * _PURITY_916  # ≈ 0.2945

# ---------------------------------------------------------------------------
# Tuning knobs (all in one place, auditable)
# ---------------------------------------------------------------------------
PREMIUM_THRESHOLD_PCT: float = 15.0  # |premium share| above this → attribution invalid
MACRO_STALE_THRESHOLD_DAYS: float = 14.0  # matches macro.py hard-fail threshold
WINDOWS_DAYS: list[int] = [7, 30]  # attribution windows for 7d headline + 30d context
MAX_BAR_AGE_DAYS: float = 5.0  # a long weekend + one holiday; older = data gap, row dropped
# Staleness cap for the last 1-hour bar before a fix. A US holiday Monday has no GC=F bars
# after Friday 21:00 UTC (62.5 h before Monday's PM fix), and Yahoo's INR=X hourly feed has
# multi-hour holes; in both cases the last traded price IS the price at the fix. Beyond 3 days
# the feed is broken, not quiet.
MAX_INTRADAY_GAP_HOURS: float = 72.0

# ---------------------------------------------------------------------------
# Publication clocks (ADR 058): wall-clock time in a zone on the value's label date
# ---------------------------------------------------------------------------
_IBJA_AM_CLOCK: tuple[str, dtime] = ("Asia/Kolkata", dtime(12, 0))  # repo convention
_IBJA_PM_CLOCK: tuple[str, dtime] = ("Asia/Kolkata", dtime(17, 0))  # repo convention
_COMEX_SETTLE_CLOCK: tuple[str, dtime] = ("America/New_York", dtime(13, 30))  # VERIFIED
_USDINR_CONSERVATIVE_CLOCK: tuple[str, dtime] = ("UTC", dtime(23, 59))  # no pinned clock
_MACRO_CLOCKS: dict[str, tuple[str, dtime]] = {
    "gold_usd": _COMEX_SETTLE_CLOCK,
    "usd_inr": _USDINR_CONSERVATIVE_CLOCK,
}
TIMING_CONVENTION: str = (
    "IBJA fix (PM 17:00 IST, else AM 12:00 IST) split against GC=F x INR=X 1-hour bars "
    "that closed by that fix (ADR 058)"
)


# ---------------------------------------------------------------------------
# Private loaders
# ---------------------------------------------------------------------------


def _load_ibja(data_dir: Path) -> pd.DataFrame:
    """Load ibja_rates.parquet with parsed date index and ibja_10g column (INR/10g)."""
    path = data_dir / "ibja_rates.parquet"
    if not path.exists():
        return pd.DataFrame()
    ibja = pd.read_parquet(path)
    ibja["date_parsed"] = pd.to_datetime(ibja["date"])
    ibja = ibja.set_index("date_parsed").sort_index()
    # Prefer PM rate (more representative closing fix); fall back to AM. Record which fix the
    # value is, because the two are published ~5 h apart and the macro pairing depends on it.
    ibja["ibja_10g"] = ibja["pm_916"].fillna(ibja["am_916"])
    ibja["fix"] = np.where(ibja["pm_916"].notna(), "pm", "am")
    return ibja[["ibja_10g", "fix"]].dropna(subset=["ibja_10g"])


def _known_at_utc(dates: pd.Index, clock: tuple[str, dtime]) -> pd.DatetimeIndex:
    """UTC instants at which values labelled with calendar `dates` became public."""
    tz, at = clock
    local = pd.DatetimeIndex(dates).normalize() + pd.Timedelta(hours=at.hour, minutes=at.minute)
    return local.tz_localize(tz).tz_convert("UTC")


def _align_macro_to_fixes(ibja: pd.DataFrame, macro: pd.DataFrame) -> pd.DataFrame:
    """Pair each IBJA fix with the latest gold_usd / usd_inr DAILY bar public at that fix.

    Used for driver_state only, when intraday bars are unavailable (module docstring). Returns a frame indexed by IBJA date with
    ibja_10g, gold_usd, usd_inr (plus the *_bar_date actually used, for audit). Rows with no
    known bar within MAX_BAR_AGE_DAYS of the fix are dropped.
    """
    out = _fix_instants(ibja).sort_values("fix_at")

    max_age = pd.Timedelta(days=MAX_BAR_AGE_DAYS)
    for col, clock in _MACRO_CLOCKS.items():
        bars = macro[[col]].dropna().sort_index()
        bars = bars.assign(
            known_at=_known_at_utc(bars.index, clock), **{f"{col}_bar_date": bars.index}
        )
        out = pd.merge_asof(
            out,
            bars.sort_values("known_at"),
            left_on="fix_at",
            right_on="known_at",
            direction="backward",  # latest bar known at or before the fix
        )
        too_old = (out["fix_at"] - out["known_at"]) > max_age
        out.loc[too_old, col] = np.nan
        out = out.drop(columns=["known_at"])

    out = out.set_index("ibja_date").sort_index()
    out.index.name = None
    return out.dropna(subset=["ibja_10g", "gold_usd", "usd_inr"])


def _load_macro(data_dir: Path) -> pd.DataFrame:
    """Load macro_cache.parquet with tz-naive DatetimeIndex."""
    path = data_dir / "macro_cache.parquet"
    if not path.exists():
        return pd.DataFrame()
    macro = pd.read_parquet(path)
    macro.index = pd.to_datetime(macro.index, utc=True)
    macro.index = macro.index.tz_localize(None)
    return macro[["gold_usd", "usd_inr"]].dropna(subset=["gold_usd", "usd_inr"])


def _load_intraday(data_dir: Path) -> pd.DataFrame:
    """Load macro_intraday.parquet: 1-hour gold_usd / usd_inr closes, UTC index = bar START."""
    path = data_dir / "macro_intraday.parquet"
    if not path.exists():
        return pd.DataFrame()
    try:
        bars = pd.read_parquet(path)
    except Exception as exc:
        logger.warning("drivers: could not read %s: %s", path.name, exc)
        return pd.DataFrame()
    idx = pd.DatetimeIndex(pd.to_datetime(bars.index))
    bars.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    return bars[["gold_usd", "usd_inr"]].sort_index()


def _fix_instants(ibja: pd.DataFrame) -> pd.DataFrame:
    """ibja_10g, fix, fix_at (UTC instant of the fix) and ibja_date, one row per IBJA date."""
    fixes = ibja[["ibja_10g", "fix"]].copy()
    am_at = _known_at_utc(fixes.index, _IBJA_AM_CLOCK)
    pm_at = _known_at_utc(fixes.index, _IBJA_PM_CLOCK)
    fixes["fix_at"] = pd.to_datetime(
        np.where(fixes["fix"].to_numpy() == "pm", pm_at, am_at), utc=True
    )
    fixes["ibja_date"] = fixes.index
    return fixes


def _align_intraday_to_fixes(ibja: pd.DataFrame, intraday: pd.DataFrame) -> pd.DataFrame:
    """Pair each IBJA fix with the Close of the last 1-hour bar that ENDED by the fix instant.

    Each series is read on its own; a fix with no bar ending within MAX_INTRADAY_GAP_HOURS
    before it gets NaN for that series and is dropped.
    """
    out = _fix_instants(ibja).sort_values("fix_at")
    max_gap = pd.Timedelta(hours=MAX_INTRADAY_GAP_HOURS)
    for col in ("gold_usd", "usd_inr"):
        bars = intraday[[col]].dropna()
        bars = bars.assign(bar_end=bars.index + pd.Timedelta(hours=1)).sort_values("bar_end")
        out = pd.merge_asof(out, bars, left_on="fix_at", right_on="bar_end", direction="backward")
        out.loc[(out["fix_at"] - out["bar_end"]) > max_gap, col] = np.nan
        out = out.drop(columns=["bar_end"])
    out = out.set_index("ibja_date").sort_index()
    out.index.name = None
    return out.dropna(subset=["ibja_10g", "gold_usd", "usd_inr"])


def _resolve_macro_staleness(data_dir: Path) -> float | None:
    """Return macro cache age in calendar days; None if unknown.

    Reads macro_status.json (written by macro.py; gitignored) first.
    Falls back to macro_cache.parquet file mtime if status file absent.
    """
    status_path = data_dir / "macro_status.json"
    if status_path.exists():
        try:
            status = json.loads(status_path.read_text())
            return status.get("cache_age_days")
        except Exception:
            pass
    parquet_path = data_dir / "macro_cache.parquet"
    if parquet_path.exists():
        return (time.time() - parquet_path.stat().st_mtime) / 86400
    return None


def _null_window(reason: str) -> dict:
    return {
        "n_obs": 0,
        "t0_date": None,
        "t1_date": None,
        "delta_pct_ibja": None,
        "delta_pct_gold_usd": None,
        "delta_pct_usdinr": None,
        "delta_pct_premium": None,
        "premium_share_pct": None,
        "attribution_valid": False,
        "attribution_valid_reason": reason,
        "total_move_rs_per_g": None,
        "gold_usd_contrib_rs_per_g": None,
        "usdinr_contrib_rs_per_g": None,
        "premium_contrib_rs_per_g": None,
    }


# ---------------------------------------------------------------------------
# Core: single-window log decomposition
# ---------------------------------------------------------------------------


def _decompose_window(
    merged: pd.DataFrame,
    window_days: int,
    tanishq_df: pd.DataFrame | None,
) -> dict:
    """Log decomposition for one [t0, t1] window.

    Parameters
    ----------
    merged : DataFrame with columns ibja_10g, gold_usd, usd_inr,
             ln_ibja, ln_gold_usd, ln_usdinr, ln_premium (pre-computed).
    window_days : calendar days to look back from the most recent row.
    tanishq_df : optional; prices.json as DataFrame with columns ts (UTC) and 22k (INR/g).
                 Used only for display-ready Rs contributions; decomposition is IBJA-based.
    """
    now = merged.index.max()
    w = merged[merged.index >= now - pd.Timedelta(days=window_days)]

    if len(w) < 2:
        return _null_window(f"fewer than 2 IBJA/macro rows in past {window_days}d")

    t0, t1 = w.iloc[0], w.iloc[-1]

    dln_ibja = float(t1["ln_ibja"] - t0["ln_ibja"])
    dln_g = float(t1["ln_gold_usd"] - t0["ln_gold_usd"])
    dln_r = float(t1["ln_usdinr"] - t0["ln_usdinr"])
    dln_p = float(t1["ln_premium"] - t0["ln_premium"])

    result: dict = {
        "n_obs": len(w),
        "t0_date": w.index[0].strftime("%Y-%m-%d"),
        "t1_date": w.index[-1].strftime("%Y-%m-%d"),
        "delta_pct_ibja": round(dln_ibja * 100, 3),
        "delta_pct_gold_usd": round(dln_g * 100, 3),
        "delta_pct_usdinr": round(dln_r * 100, 3),
        "delta_pct_premium": round(dln_p * 100, 3),
        "total_move_rs_per_g": None,
        "gold_usd_contrib_rs_per_g": None,
        "usdinr_contrib_rs_per_g": None,
        "premium_contrib_rs_per_g": None,
    }

    if abs(dln_ibja) < 1e-8:
        result["premium_share_pct"] = 0.0
        result["attribution_valid"] = False
        result["attribution_valid_reason"] = "IBJA unchanged over window — no move to attribute"
        return result

    premium_share = dln_p / dln_ibja
    prem_abs_pct = abs(premium_share) * 100.0

    result["premium_share_pct"] = round(prem_abs_pct, 1)

    if prem_abs_pct > PREMIUM_THRESHOLD_PCT:
        result["attribution_valid"] = False
        result["attribution_valid_reason"] = (
            f"premium share {prem_abs_pct:.1f}% exceeds {PREMIUM_THRESHOLD_PCT:.0f}% threshold"
        )
    else:
        result["attribution_valid"] = True
        result["attribution_valid_reason"] = "clean — premium share within threshold"

    # Tanishq total move in Rs/g for display
    total_move: float | None = None
    if tanishq_df is not None and len(tanishq_df) > 1:
        p_now = float(tanishq_df["22k"].iloc[-1])
        cutoff = tanishq_df["ts"].iloc[-1] - pd.Timedelta(days=window_days)
        period = tanishq_df[tanishq_df["ts"] >= cutoff]
        if len(period) > 1:
            total_move = round(p_now - float(period["22k"].iloc[0]), 1)

    result["total_move_rs_per_g"] = total_move

    # A board move that rounds to Rs 0 has nothing to split; leaving the parts None keeps the
    # page from printing "up about Rs 0 this week" (app.js needs all three as numbers).
    if total_move is not None and abs(total_move) >= 0.5:
        sg = dln_g / dln_ibja
        sr = dln_r / dln_ibja
        sp = dln_p / dln_ibja
        result["gold_usd_contrib_rs_per_g"] = round(sg * total_move, 1)
        result["usdinr_contrib_rs_per_g"] = round(sr * total_move, 1)
        result["premium_contrib_rs_per_g"] = round(sp * total_move, 1)

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_driver_attribution(
    data_dir: Path = DATA_DIR,
    macro_staleness_days: float | None = None,
) -> dict:
    """Compute log-decomposition driver attribution for configured windows.

    Returns a dict for inclusion in forecast.json as ``driver_context``.
    Never raises — all failures produce attribution_valid=False with a reason string
    (norm #8: no silent fallback).

    Parameters
    ----------
    data_dir : path to the data directory (override for testing).
    macro_staleness_days : optional explicit age override (skips file read).
    """
    if macro_staleness_days is None:
        macro_staleness_days = _resolve_macro_staleness(data_dir)

    macro_fresh = (
        macro_staleness_days is not None and macro_staleness_days <= MACRO_STALE_THRESHOLD_DAYS
    )

    ctx: dict = {
        "computed_at": datetime.now(UTC).isoformat(),
        "macro_staleness_days": (
            round(macro_staleness_days, 2) if macro_staleness_days is not None else None
        ),
        "macro_fresh": macro_fresh,
        "premium_threshold_pct": PREMIUM_THRESHOLD_PCT,
        "timing_convention": TIMING_CONVENTION,
        "windows": {},
        "driver_state": None,
    }

    if not macro_fresh:
        stale_msg = (
            f"macro stale ({macro_staleness_days:.1f}d > {MACRO_STALE_THRESHOLD_DAYS:.0f}d)"
            if macro_staleness_days is not None
            else "macro cache missing"
        )
        logger.warning("drivers: %s — attribution_valid=False for all windows", stale_msg)
        for wd in WINDOWS_DAYS:
            ctx["windows"][f"{wd}d"] = _null_window(stale_msg)
        return ctx

    ibja = _load_ibja(data_dir)
    macro = _load_macro(data_dir)

    if ibja.empty or macro.empty:
        reason = "IBJA or macro data unavailable"
        logger.warning("drivers: %s", reason)
        for wd in WINDOWS_DAYS:
            ctx["windows"][f"{wd}d"] = _null_window(reason)
        return ctx

    # Load Tanishq prices for display-ready Rs contributions (INR/g)
    tanishq_df: pd.DataFrame | None = None
    prices_path = data_dir / "prices.json"
    if prices_path.exists():
        try:
            raw = json.loads(prices_path.read_text())
            tanishq_df = pd.DataFrame(raw)
            tanishq_df["ts"] = pd.to_datetime(tanishq_df["timestamp"])
            tanishq_df = tanishq_df.sort_values("ts")
        except Exception as exc:
            logger.warning("drivers: could not load prices.json: %s", exc)

    # Pair each IBJA fix with global prices AT the fix instant (ADR 058 A16). Never a same-date
    # join: the COMEX settle labelled D is published ~6.5 h after IBJA's PM fix of D.
    intraday = _load_intraday(data_dir)
    merged = _align_intraday_to_fixes(ibja, intraday) if not intraday.empty else pd.DataFrame()
    covers_latest = not merged.empty and merged.index.max() == ibja.index.max()
    ctx["alignment"] = "intraday_at_fix" if covers_latest else "daily_lagged_state_only"
    no_split_reason = (
        "intraday gold/USD-INR prices unavailable at the latest IBJA fix -- "
        "the move cannot be split at fix times"
    )
    if not covers_latest:
        logger.warning("drivers: %s -- driver_state from lagged daily bars only", no_split_reason)
        merged = _align_macro_to_fixes(ibja, macro)

    if len(merged) < 2:
        reason = "insufficient merged rows after IBJA/macro join"
        for wd in WINDOWS_DAYS:
            ctx["windows"][f"{wd}d"] = _null_window(reason)
        return ctx

    # Pre-compute log series (done once; shared across all window calls)
    ln_conv = math.log(_CONV_10G_916)
    merged = merged.copy()
    merged["ln_ibja"] = np.log(merged["ibja_10g"])
    merged["ln_gold_usd"] = np.log(merged["gold_usd"])
    merged["ln_usdinr"] = np.log(merged["usd_inr"])
    # ln_premium = ln(ibja) − ln(gold_usd) − ln(usd_inr) − ln(conv)
    merged["ln_premium"] = merged["ln_ibja"] - merged["ln_gold_usd"] - merged["ln_usdinr"] - ln_conv

    # Driver state: 30d raw % changes for the supporting display copy. "now" = the value in
    # force at the latest IBJA fix (see TIMESTAMP CONVENTION), not the latest quote.
    now = merged.index.max()
    w30 = merged[merged.index >= now - pd.Timedelta(days=30)]
    if len(w30) >= 2:
        r0, r1 = w30.iloc[0], w30.iloc[-1]
        ctx["driver_state"] = {
            "usd_inr_now": round(float(r1["usd_inr"]), 3),
            "gold_usd_now": round(float(r1["gold_usd"]), 1),
            "usd_inr_30d_pct_change": round(
                (float(r1["usd_inr"]) - float(r0["usd_inr"])) / float(r0["usd_inr"]) * 100, 2
            ),
            "gold_usd_30d_pct_change": round(
                (float(r1["gold_usd"]) - float(r0["gold_usd"])) / float(r0["gold_usd"]) * 100, 2
            ),
        }

    if not covers_latest:
        for wd in WINDOWS_DAYS:
            ctx["windows"][f"{wd}d"] = _null_window(no_split_reason)
        return ctx

    for wd in WINDOWS_DAYS:
        ctx["windows"][f"{wd}d"] = _decompose_window(merged, wd, tanishq_df)

    return ctx
