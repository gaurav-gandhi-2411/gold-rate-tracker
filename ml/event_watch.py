"""ml.event_watch — F4 "Event watch": does price MOVE SIZE spike around known
calendar events (ADR 050)?

"Budget day is next week — on similar days in the past, prices moved about
Rs.X/g." Measures |log return| (move SIZE, never direction) on event days
vs. a normal-day baseline, per event type, with a block-bootstrap
significance test and Bonferroni correction across types. Every constant
below is frozen by ADR 050 — changing one makes a result exploratory, not a
confirmation of the pre-registration.

Four event types (festivals excluded — see ADR 050 "why festivals are
excluded"):
  fomc_decision    -- COMEX GC=F (roll-adjusted), 2000-08-30..present
  us_cpi           -- COMEX GC=F (roll-adjusted), 2000-08-30..present
  us_jobs_report   -- COMEX GC=F (roll-adjusted), 2000-08-30..present
  india_budget     -- INR proxy (data/history_seed_inr22k_label.parquet),
                       2013-01-01..present

Never emits a directional (up/down) signal -- this module is orthogonal to
ml.direction and is not wired into ml.direction.gate or any promotion path.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from ml.inr_proxy import _detect_and_adjust_rolls
from ml.macro import _download_with_retry

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
EVENTS_CALENDAR_PATH = DATA_DIR / "events_calendar.json"
INR_PROXY_LABEL_PATH = DATA_DIR / "history_seed_inr22k_label.parquet"
IBJA_RATES_PATH = DATA_DIR / "ibja_rates.parquet"

COMEX_START = "2000-08-01"  # a little before GC=F's actual 2000-08-30 first row
_FETCH_BUFFER_DAYS = 10

# ADR 050 "Event types considered" -- which price series backs each type.
EVENT_TYPES: dict[str, str] = {
    "fomc_decision": "comex",
    "us_cpi": "comex",
    "us_jobs_report": "comex",
    "india_budget": "inr_proxy",
}

BONFERRONI_M = 4
ALPHA = 0.05
BONFERRONI_THRESHOLD = ALPHA / BONFERRONI_M  # 0.0125

SUCCESS_RATIO_MEAN_THRESHOLD = 1.2  # ADR 050 "Success per type"
NORMAL_DAY_EXCLUSION_WINDOW_DAYS = 1  # ±1 calendar day around ANY event, any type
NORMAL_BLOCK_LENGTH = 5  # trading days -- ADR 050 "Method — block bootstrap"
N_BOOT = 2000
BOOT_SEED = 42

HALF_SPLIT_DATE = "2013-01-01"  # ADR 050 "Consistency check"


# ---------------------------------------------------------------------------
# 1. Calendar
# ---------------------------------------------------------------------------


def load_events_calendar(path: Path = EVENTS_CALENDAR_PATH) -> list[dict]:
    """Only verified:true rows are usable (ADR 050: 'only verified rows are
    used')."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [r for r in rows if r.get("verified") is True]


def events_of_type(rows: list[dict], event_type: str) -> list[str]:
    """Sorted list of ISO date strings for one event type."""
    return sorted(r["date"] for r in rows if r["type"] == event_type)


def exclusion_window_dates(
    rows: list[dict], window_days: int = NORMAL_DAY_EXCLUSION_WINDOW_DAYS
) -> set[str]:
    """Every calendar date within ±window_days of ANY event of ANY type --
    ADR 050 'Normal-day set': one shared exclusion set built from the FULL
    calendar, reused for every type's normal-day pool."""
    excluded: set[str] = set()
    for r in rows:
        center = date.fromisoformat(r["date"])
        for delta in range(-window_days, window_days + 1):
            excluded.add((center + timedelta(days=delta)).isoformat())
    return excluded


# ---------------------------------------------------------------------------
# 2. Price series
# ---------------------------------------------------------------------------


def load_comex_series(end: str | None = None) -> tuple[pd.Series, pd.Series]:
    """Roll-adjusted GC=F daily close (USD/troy-oz), 2000-08-30..end, plus a
    boolean genuine-trading-day mask (same construction as
    ml/direction/comex_daily.py's is_gc_trading_day: a real, non-forward-
    filled Yahoo Finance close, not a weekend/holiday carry-forward)."""
    if end is None:
        end = (date.today() + timedelta(days=1)).isoformat()
    fetch_start = (date.fromisoformat(COMEX_START) - timedelta(days=_FETCH_BUFFER_DAYS)).isoformat()

    raw = _download_with_retry(["GC=F", "GLD"], start=fetch_start, end=end)
    if raw.empty:
        raise RuntimeError("yfinance returned no data for GC=F/GLD")

    raw.index = pd.to_datetime(raw.index, utc=True)
    full_idx = pd.date_range(start=raw.index.min(), end=raw.index.max(), freq="D", tz="UTC")
    is_genuine = raw[("Close", "GC=F")].reindex(full_idx).notna()
    raw = raw.reindex(full_idx)

    gold = raw[("Close", "GC=F")].ffill()
    gld = raw[("Close", "GLD")].ffill()
    adjusted, _flags = _detect_and_adjust_rolls(gold, gld)
    adjusted.index = pd.DatetimeIndex(adjusted.index).tz_convert(None).normalize()
    is_genuine.index = pd.DatetimeIndex(is_genuine.index).tz_convert(None).normalize()
    return adjusted, is_genuine


def load_inr_proxy_series() -> tuple[pd.Series, pd.Series]:
    """label_22k_per_10g from the existing INR-proxy parquet, plus a
    genuine-update-day mask inferred as 'value differs from the immediately
    preceding calendar day's value' (the series has no separately published
    raw-vs-ffilled flag -- see ADR 050 'Event-day definition')."""
    df = pd.read_parquet(INR_PROXY_LABEL_PATH)
    df.index = pd.to_datetime(df.index).tz_convert(None).normalize()
    series = df["label_22k_per_10g"].sort_index()
    is_genuine = series.ne(series.shift(1))
    if len(is_genuine):
        is_genuine.iloc[0] = True  # first observation has no predecessor to compare
    return series, is_genuine


def latest_ibja_price_per_gram(path: Path = IBJA_RATES_PATH) -> float:
    """Today's real, live 22K Rs/gram anchor for the card figure (ADR 050
    'Card figure') -- latest pm_916, falling back to am_916, divided by 10
    (the parquet is Rs per 10 g)."""
    df = pd.read_parquet(path).sort_values("date")
    last = df.iloc[-1]
    per_10g = last["pm_916"] if pd.notna(last["pm_916"]) else last["am_916"]
    return float(per_10g) / 10.0


# ---------------------------------------------------------------------------
# 3. Event-day / normal-day move extraction
# ---------------------------------------------------------------------------


def _roll_forward_to_genuine(
    d: pd.Timestamp, index: pd.DatetimeIndex, is_genuine: pd.Series
) -> pd.Timestamp | None:
    """The 'first trading-day close that could reflect the release' -- if d
    itself is not a genuine trading/update day, advance to the next one that
    is. Returns None if no such day exists within the series."""
    candidates = index[index >= d]
    genuine_candidates = candidates[is_genuine.reindex(candidates).fillna(False).to_numpy()]
    if len(genuine_candidates) == 0:
        return None
    return genuine_candidates[0]


def _prior_genuine(
    d: pd.Timestamp, index: pd.DatetimeIndex, is_genuine: pd.Series
) -> pd.Timestamp | None:
    """The last genuine day strictly before d."""
    candidates = index[index < d]
    genuine_candidates = candidates[is_genuine.reindex(candidates).fillna(False).to_numpy()]
    if len(genuine_candidates) == 0:
        return None
    return genuine_candidates[-1]


def event_day_log_return(
    event_date: str, series: pd.Series, is_genuine: pd.Series
) -> tuple[float, str, str] | None:
    """|ln(event_close / prior_close)| for one event occurrence, plus the two
    dates actually used (post roll-forward). None if either endpoint is
    outside the series' coverage."""
    index = series.index
    d = pd.Timestamp(event_date)
    event_day = _roll_forward_to_genuine(d, index, is_genuine)
    if event_day is None:
        return None
    prior_day = _prior_genuine(event_day, index, is_genuine)
    if prior_day is None:
        return None
    event_val = series.loc[event_day]
    prior_val = series.loc[prior_day]
    if pd.isna(event_val) or pd.isna(prior_val) or prior_val == 0:
        return None
    log_ret = float(np.log(event_val / prior_val))
    return abs(log_ret), event_day.date().isoformat(), prior_day.date().isoformat()


def normal_day_log_returns(
    series: pd.Series, is_genuine: pd.Series, excluded_dates: set[str]
) -> pd.Series:
    """|log return| for every genuine day NOT within ±1 day of any event
    (any type), in chronological order (block bootstrap needs the order
    preserved)."""
    genuine_days = series.index[is_genuine.reindex(series.index).fillna(False).to_numpy()]
    genuine_days = genuine_days[
        genuine_days.map(lambda d: d.date().isoformat() not in excluded_dates)
    ]
    vals = series.loc[genuine_days]
    log_ret = np.log(vals / vals.shift(1)).abs()
    return log_ret.dropna()


# ---------------------------------------------------------------------------
# 4. Block bootstrap test
# ---------------------------------------------------------------------------


def _moving_block_bootstrap_means(
    values: np.ndarray, block_len: int, n_boot: int, rng: np.random.Generator
) -> np.ndarray:
    """Circular moving block bootstrap of the SAMPLE MEAN. block_len=1 is
    equivalent to ordinary i.i.d. resampling (used for event-day series)."""
    n = len(values)
    if n == 0:
        return np.full(n_boot, np.nan)
    block_len = max(1, min(block_len, n))
    n_blocks_needed = int(np.ceil(n / block_len))
    means = np.empty(n_boot)
    for b in range(n_boot):
        starts = rng.integers(0, n, size=n_blocks_needed)
        sample = np.concatenate(
            [np.take(values, np.arange(s, s + block_len), mode="wrap") for s in starts]
        )[:n]
        means[b] = sample.mean()
    return means


def block_bootstrap_diff_test(
    event_moves: np.ndarray,
    normal_moves: np.ndarray,
    normal_block_len: int = NORMAL_BLOCK_LENGTH,
    n_boot: int = N_BOOT,
    seed: int = BOOT_SEED,
) -> dict:
    """One-sided (event > normal) block-bootstrap test of difference of
    means -- ADR 050 'Method — block bootstrap'. Event days: i.i.d. (block
    length 1). Normal days: moving block bootstrap, block length 5 trading
    days."""
    if len(event_moves) == 0 or len(normal_moves) == 0:
        return {
            "n_event": len(event_moves),
            "n_normal": len(normal_moves),
            "observed_diff": None,
            "p_one_sided": None,
            "ci_95": None,
            "mean_event": None,
            "mean_normal": None,
            "median_event": None,
            "median_normal": None,
            "ratio_of_means": None,
            "ratio_of_medians": None,
        }
    rng = np.random.default_rng(seed)
    event_boot = _moving_block_bootstrap_means(np.asarray(event_moves, dtype=float), 1, n_boot, rng)
    normal_boot = _moving_block_bootstrap_means(
        np.asarray(normal_moves, dtype=float), normal_block_len, n_boot, rng
    )
    diffs = event_boot - normal_boot
    mean_event = float(np.mean(event_moves))
    mean_normal = float(np.mean(normal_moves))
    median_event = float(np.median(event_moves))
    median_normal = float(np.median(normal_moves))
    observed_diff = mean_event - mean_normal
    p_one_sided = float(np.mean(diffs <= 0))
    return {
        "n_event": len(event_moves),
        "n_normal": len(normal_moves),
        "observed_diff": observed_diff,
        "p_one_sided": p_one_sided,
        "ci_95": [float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))],
        "mean_event": mean_event,
        "mean_normal": mean_normal,
        "median_event": median_event,
        "median_normal": median_normal,
        "ratio_of_means": (mean_event / mean_normal) if mean_normal > 0 else None,
        "ratio_of_medians": (median_event / median_normal) if median_normal > 0 else None,
    }


def bonferroni_significant(
    p_one_sided: float | None, m: int = BONFERRONI_M, alpha: float = ALPHA
) -> bool:
    if p_one_sided is None:
        return False
    return p_one_sided <= (alpha / m)


def passes_success_gate(test_result: dict) -> bool:
    """ADR 050 'Success per type': Bonferroni-significant AND ratio of MEAN
    |move| >= 1.2."""
    if test_result["p_one_sided"] is None or test_result["ratio_of_means"] is None:
        return False
    return bonferroni_significant(test_result["p_one_sided"]) and (
        test_result["ratio_of_means"] >= SUCCESS_RATIO_MEAN_THRESHOLD
    )


def bootstrap_median_ci(
    values: np.ndarray, n_boot: int = N_BOOT, seed: int = BOOT_SEED
) -> tuple[float, float, float]:
    """(median, ci_low, ci_high) via i.i.d. bootstrap of the median -- used
    for the card's interval (ADR 050 'Card figure')."""
    values = np.asarray(values, dtype=float)
    if len(values) == 0:
        return (float("nan"), float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    n = len(values)
    boot_medians = np.empty(n_boot)
    for b in range(n_boot):
        sample = rng.choice(values, size=n, replace=True)
        boot_medians[b] = np.median(sample)
    return (
        float(np.median(values)),
        float(np.percentile(boot_medians, 2.5)),
        float(np.percentile(boot_medians, 97.5)),
    )


# ---------------------------------------------------------------------------
# 5. Sentence builder
# ---------------------------------------------------------------------------

EVENT_TYPE_LABELS: dict[str, str] = {
    "fomc_decision": "a US Fed rate decision",
    "us_cpi": "a US inflation (CPI) report",
    "us_jobs_report": "a US jobs report",
    "india_budget": "Budget day",
}


def build_sentence(event_type: str, event_date: str, move_rs_per_g: float) -> str:
    """Plain-language sentence built only from computed values (ADR 050
    'Card figure') -- never a hand-typed figure."""
    label = EVENT_TYPE_LABELS.get(event_type, event_type)
    d = datetime.fromisoformat(event_date)
    when = f"{d.day} {d.strftime('%B')}"  # platform-portable day-of-month (no %-d/%#d)
    return (
        f"{label} is coming up on {when} — on similar days in the past, "
        f"prices moved about Rs.{move_rs_per_g:.0f}/g."
    )
