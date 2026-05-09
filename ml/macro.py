"""
macro.py — Download, cache, and serve macro-economic features.

Tickers fetched daily from Yahoo Finance:
  INR=X    — USD/INR spot rate
  GC=F     — COMEX gold futures (USD/troy-oz)
  ^TNX     — US 10-year Treasury yield (%)
  DX-Y.NYB — US Dollar Index (DXY)
  ^BSESN   — BSE Sensex
  ^VIX     — CBOE Volatility Index

Usage (from repo root):
    python ml/macro.py          # incremental update — appends last 14 days to cache
    python ml/macro.py --full   # force full 2-year re-fetch

Reads:  data/macro_cache.parquet (if present)
Writes: data/macro_cache.parquet (incremental merge, new data wins on overlap)

The cache is NOT committed to the repo — it is regenerated on every CI run by the
"Fetch macro features" step (continue-on-error: true), and read by forecast.py.
"""

from __future__ import annotations

import json
import sys
import time
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except ImportError:
    yf = None  # type: ignore[assignment]

warnings.filterwarnings("ignore", category=FutureWarning)

sys.path.insert(0, str(Path(__file__).parent.parent))

DATA_DIR = Path(__file__).parent.parent / "data"
CACHE_PATH = DATA_DIR / "macro_cache.parquet"

# Map internal column names → Yahoo Finance ticker symbols
TICKER_MAP: dict[str, str] = {
    "usd_inr":     "INR=X",
    "gold_usd":    "GC=F",
    "us_10y_yield": "^TNX",
    "dxy":         "DX-Y.NYB",
    "sensex":      "^BSESN",
    "vix":         "^VIX",
}

# Calendar days of history to fetch on first run (cold start)
_DEFAULT_LOOKBACK_DAYS = 760  # ~2 years plus buffer


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _download_with_retry(
    tickers: list[str],
    start: str,
    end: str,
    max_retries: int = 3,
    backoff: float = 2.0,
) -> pd.DataFrame:
    """Call yfinance.download with exponential-backoff on transient errors."""
    if yf is None:
        raise ImportError("yfinance is required. Install it with: pip install yfinance")

    last_exc: Exception = RuntimeError("No download attempts were made")
    for attempt in range(max_retries):
        try:
            raw = yf.download(
                tickers=tickers,
                start=start,
                end=end,
                auto_adjust=True,
                progress=False,
                threads=False,
            )
            return raw
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                wait = backoff ** attempt
                print(
                    f"yfinance attempt {attempt + 1}/{max_retries} failed ({exc}); "
                    f"retrying in {wait:.0f}s"
                )
                time.sleep(wait)

    raise last_exc


def _extract_close(raw: pd.DataFrame, ticker_map: dict[str, str]) -> pd.DataFrame:
    """
    Pull the Close price for each ticker out of a yfinance download result.

    yfinance >= 0.2 returns a MultiIndex columns DataFrame with shape
    (PriceType, Ticker) or (Ticker, PriceType) depending on version.
    Both orderings are handled.
    """
    out = pd.DataFrame(index=raw.index)

    for col_name, ticker in ticker_map.items():
        found = False

        if isinstance(raw.columns, pd.MultiIndex):
            # Try the two known orderings for MultiIndex columns
            for price_col in ("Close", "Adj Close", "Price"):
                # Ordering 1: (price_type, ticker)
                if (price_col, ticker) in raw.columns:
                    out[col_name] = raw[(price_col, ticker)].values
                    found = True
                    break
                # Ordering 2: (ticker, price_type)
                if (ticker, price_col) in raw.columns:
                    out[col_name] = raw[(ticker, price_col)].values
                    found = True
                    break
        else:
            # Flat columns — single-ticker download (shouldn't happen here, but handle it)
            for price_col in ("Close", "Adj Close"):
                if price_col in raw.columns:
                    out[col_name] = raw[price_col].values
                    found = True
                    break

        if not found:
            print(f"  Warning: Close price not found for {ticker} ({col_name}) — filling NaN")
            out[col_name] = np.nan

    return out


def _derive_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Forward-fill core series across weekends/holidays, then compute derived features.

    Input columns expected: usd_inr, gold_usd, us_10y_yield, dxy, sensex, vix.
    """
    df = df.copy()
    core = list(TICKER_MAP.keys())

    # Forward-fill so weekends and holidays inherit the last known value
    df[core] = df[core].ffill()

    # vix_level is the cleaned VIX series (same values, clearer name for features)
    df["vix_level"] = df["vix"]

    # Daily % changes
    df["usd_inr_change_1d"] = df["usd_inr"].pct_change(1)
    df["gold_usd_change_1d"] = df["gold_usd"].pct_change(1)

    # 5-day rolling volatility of gold log-returns (std of daily log-returns over 5 days)
    gold_log_ret = np.log(df["gold_usd"] / df["gold_usd"].shift(1))
    df["gold_usd_5d_vol"] = gold_log_ret.rolling(5, min_periods=2).std()

    # 5-day total return on Sensex
    df["sensex_5d_return"] = df["sensex"].pct_change(5)

    return df


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_macro_features(
    start_date: str,
    end_date: str,
    cache_path: Path = CACHE_PATH,
) -> pd.DataFrame:
    """
    Download macro data for [start_date, end_date), derive features, and write cache.

    The returned DataFrame has a UTC DatetimeIndex (one row per calendar day).
    New data wins over cached data on overlapping dates.

    Parameters
    ----------
    start_date : str
        ISO date string, e.g. "2024-01-01" (inclusive).
    end_date : str
        ISO date string (exclusive — matches yfinance convention).
    cache_path : Path
        Where to read/write the Parquet cache.
    """
    tickers = list(TICKER_MAP.values())
    print(f"  Downloading {len(tickers)} tickers from {start_date} to {end_date}…")
    raw = _download_with_retry(tickers, start=start_date, end=end_date)

    if raw.empty:
        raise RuntimeError(
            f"yfinance returned an empty DataFrame for tickers {tickers}. "
            "Check network connectivity or ticker symbols."
        )

    # Ensure UTC-aware DatetimeIndex
    raw.index = pd.to_datetime(raw.index, utc=True)

    # Expand to a full daily calendar (fills weekend/holiday gaps for reindex)
    full_idx = pd.date_range(
        start=raw.index.min(), end=raw.index.max(), freq="D", tz="UTC"
    )
    raw = raw.reindex(full_idx)

    # Extract close prices into named columns
    df = _extract_close(raw, TICKER_MAP)

    # Derive volatility / return features (includes ffill of core columns)
    df = _derive_features(df)

    # Merge with existing cache — new data wins on duplicate dates
    if cache_path.exists():
        try:
            cached = pd.read_parquet(cache_path)
            cached.index = pd.to_datetime(cached.index, utc=True)
            df = pd.concat([cached, df])
            df = df[~df.index.duplicated(keep="last")]
            df.sort_index(inplace=True)
        except Exception as exc:
            print(f"  Warning: could not merge existing cache ({exc}) — starting fresh")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_path)
    return df


def update_macro_cache(
    cache_path: Path = CACHE_PATH,
    lookback_days: int = 14,
) -> pd.DataFrame:
    """
    Incremental update: re-fetch the last `lookback_days` calendar days.

    On a cold start (no cache), fetches `_DEFAULT_LOOKBACK_DAYS` (~2 years).
    """
    if not cache_path.exists():
        start = (date.today() - timedelta(days=_DEFAULT_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        print(f"Cold start — fetching full macro history from {start}")
    else:
        start = (date.today() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        print(f"Incremental update from {start}")

    # yfinance end date is exclusive; use tomorrow so today is included
    end = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    return fetch_macro_features(start, end, cache_path=cache_path)


def load_macro_features(cache_path: Path = CACHE_PATH) -> Optional[pd.DataFrame]:
    """
    Load cached macro features from Parquet.

    Returns None if the cache does not exist or cannot be parsed.
    The returned DataFrame has a UTC DatetimeIndex (daily rows).
    """
    if not cache_path.exists():
        return None
    try:
        df = pd.read_parquet(cache_path)
        df.index = pd.to_datetime(df.index, utc=True)
        return df
    except Exception as exc:
        print(f"Warning: could not load macro cache ({exc})")
        return None


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    full = "--full" in sys.argv
    if full:
        cache_path = CACHE_PATH
        start = (date.today() - timedelta(days=_DEFAULT_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        end = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
        print(f"Full re-fetch from {start}")
        df = fetch_macro_features(start, end, cache_path=cache_path)
    else:
        df = update_macro_cache()

    print(
        f"\nCache: {len(df)} rows  |  "
        f"{df.index.min().date()} to {df.index.max().date()}"
    )

    display_cols = [
        "usd_inr", "gold_usd", "us_10y_yield", "dxy",
        "vix_level", "usd_inr_change_1d", "gold_usd_change_1d",
        "gold_usd_5d_vol", "sensex_5d_return",
    ]
    print("\nLast 7 rows:")
    print(df[[c for c in display_cols if c in df.columns]].tail(7).round(4).to_string())


if __name__ == "__main__":
    main()
