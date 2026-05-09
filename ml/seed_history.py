"""
seed_history.py — Bootstrap ~2 years of daily Indian 22K gold rate data.

Data sources tried in order:

1. goodreturns.in  — https://www.goodreturns.in/gold-rates/
   HTML table scrape for historical 22K rates per gram (India).

2. goldpriceindia.in — https://goldpriceindia.in/gold-rate-history.html
   HTML table scrape.

3. Yahoo Finance ESTIMATED (primary working source) —
   Downloads GC=F (Gold Futures, USD/troy oz) and INR=X (USD/INR spot) via
   Yahoo Finance chart API, then computes:

     price_22k_inr_per_gram = GC_close * USDINR_close / 31.1035 * (22/24) * 1.15

   The factor 1.15 approximates India's ~15% retail premium over international
   spot price (10% import duty + 3% GST + ~2% dealer margin). THIS IS AN
   ESTIMATE — see README "Data sources" for disclosure. The estimated prices
   are realistic for bootstrapping the ML model but should NOT be used as
   ground-truth retail rates.

   Yahoo Finance API endpoint (public, no auth):
     https://query1.finance.yahoo.com/v8/finance/chart/<SYMBOL>?interval=1d&range=2y

Output: data/history_seed.json — same schema as data/prices.json:
  [{"timestamp": "ISO-8601Z", "22k": int, "24k": int, "18k": int,
    "source": "url"}, ...]

Run once:
    python ml/seed_history.py

Requires: requests, pandas, lxml (all in ml/requirements.txt)
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

DATA_DIR = Path(__file__).parent.parent / "data"
OUT_PATH = DATA_DIR / "history_seed.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
}

INDIA_RETAIL_PREMIUM = 1.15   # ~10% import duty + 3% GST + ~2% margin
TROY_OZ_TO_GRAM = 31.1035


def compute_rates(gold_usd_per_oz: float, usd_inr: float) -> dict:
    """
    Compute estimated Indian retail gold prices from international spot + FX.

    Derives base 24K price first, then scales to 22K and 18K so rounding is
    applied once from the common base rather than cascading.

    Returns dict with keys '22k', '24k', '18k' (INR per gram, int).
    """
    base_24k = gold_usd_per_oz * usd_inr / TROY_OZ_TO_GRAM * INDIA_RETAIL_PREMIUM
    return {
        "24k": int(round(base_24k)),
        "22k": int(round(base_24k * 22 / 24)),
        "18k": int(round(base_24k * 18 / 24)),
    }


# ---------------------------------------------------------------------------
# Source 1: goodreturns.in
# ---------------------------------------------------------------------------

def _try_goodreturns() -> list[dict] | None:
    url = "https://www.goodreturns.in/gold-rates/"
    print(f"Trying {url} ...")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        tables = pd.read_html(StringIO(resp.text))
    except Exception as e:
        print(f"  goodreturns.in failed: {e}")
        return None

    for tbl in tables:
        tbl.columns = [str(c).lower().strip() for c in tbl.columns]
        date_cols = [c for c in tbl.columns if "date" in c]
        price_cols = [c for c in tbl.columns if "22" in c]
        if not date_cols or not price_cols:
            continue
        try:
            tbl = tbl.copy()
            tbl["_date"] = pd.to_datetime(tbl[date_cols[0]], dayfirst=True, errors="coerce")
            tbl = tbl.dropna(subset=["_date"])
            price_raw = (
                tbl[price_cols[0]]
                .astype(str)
                .str.replace(",", "")
                .str.extract(r"(\d{4,6})", expand=False)
                .astype(float)
            )
            tbl = tbl.copy()
            tbl["_price22k"] = price_raw
            tbl = tbl.dropna(subset=["_price22k"])
            tbl = tbl[(tbl["_price22k"] >= 3000) & (tbl["_price22k"] <= 15000)]
            if len(tbl) < 50:
                continue
            entries = _rows_to_entries(tbl["_date"], tbl["_price22k"], url)
            print(f"  goodreturns.in: {len(entries)} entries")
            return entries
        except Exception as e:
            print(f"  goodreturns.in parse error: {e}")
            continue
    print("  goodreturns.in: no usable table found")
    return None


# ---------------------------------------------------------------------------
# Source 2: goldpriceindia.in
# ---------------------------------------------------------------------------

def _try_goldpriceindia() -> list[dict] | None:
    url = "https://goldpriceindia.in/gold-rate-history.html"
    print(f"Trying {url} ...")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        tables = pd.read_html(StringIO(resp.text))
    except Exception as e:
        print(f"  goldpriceindia.in failed: {e}")
        return None

    for tbl in tables:
        tbl.columns = [str(c).lower().strip() for c in tbl.columns]
        date_cols = [c for c in tbl.columns if "date" in c]
        price_cols = [c for c in tbl.columns if "22" in c or "price" in c]
        if not date_cols or not price_cols:
            continue
        try:
            tbl = tbl.copy()
            tbl["_date"] = pd.to_datetime(tbl[date_cols[0]], dayfirst=True, errors="coerce")
            tbl = tbl.dropna(subset=["_date"])
            price_raw = (
                tbl[price_cols[0]]
                .astype(str)
                .str.replace(",", "")
                .str.extract(r"(\d{4,6})", expand=False)
                .astype(float)
            )
            tbl = tbl.copy()
            tbl["_price22k"] = price_raw
            tbl = tbl.dropna(subset=["_price22k"])
            tbl = tbl[(tbl["_price22k"] >= 3000) & (tbl["_price22k"] <= 15000)]
            if len(tbl) < 50:
                continue
            entries = _rows_to_entries(tbl["_date"], tbl["_price22k"], url)
            print(f"  goldpriceindia.in: {len(entries)} entries")
            return entries
        except Exception as e:
            print(f"  goldpriceindia.in parse error: {e}")
            continue
    print("  goldpriceindia.in: no usable table found")
    return None


# ---------------------------------------------------------------------------
# Source 3: Yahoo Finance (ESTIMATED)
# ---------------------------------------------------------------------------

def _yf_daily(symbol: str, period: str = "2y") -> pd.DataFrame | None:
    """Download daily OHLCV from Yahoo Finance chart API."""
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?interval=1d&range={period}"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
        df = pd.DataFrame({"timestamp": timestamps, "close": closes})
        df["date"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.date
        df = df.dropna(subset=["close"])
        return df[["date", "close"]].copy()
    except Exception as e:
        print(f"  Yahoo Finance {symbol}: {e}")
        return None


def _try_yahoo_finance() -> list[dict] | None:
    """
    FALLBACK: Estimate Indian 22K retail price from international spot.

    GC=F  = Gold Futures (USD/troy oz, continuous front-month contract)
    INR=X = USD/INR spot rate

    Formula:
      price_22k = GC_close * USDINR_close / 31.1035 * (22/24) * 1.15

    The 1.15 premium approximates India's import duty (~10%) + GST (3%) +
    typical dealer margin (~2%). This is an ESTIMATE — real retail prices
    differ by city, jeweller, and making-charge structure.
    See README 'Data sources' for disclosure.
    """
    print("Trying Yahoo Finance fallback (GC=F + INR=X — ESTIMATED) ...")
    gc = _yf_daily("GC=F")
    inr = _yf_daily("INR=X")
    if gc is None or inr is None:
        return None

    merged = pd.merge(gc, inr, on="date", suffixes=("_gc", "_inr"))
    if len(merged) < 100:
        print(f"  Yahoo Finance: only {len(merged)} overlapping rows")
        return None

    source = "yahoo-finance-estimated-GC=F*INR=X/31.1035*1.15"
    entries = []
    for _, row in merged.iterrows():
        rates = compute_rates(float(row["close_gc"]), float(row["close_inr"]))
        if not (3000 <= rates["22k"] <= 20000):
            continue
        ts = pd.Timestamp(row["date"], tz="UTC")
        entries.append(
            {
                "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "22k": rates["22k"],
                "24k": rates["24k"],
                "18k": rates["18k"],
                "source": source,
            }
        )
    entries.sort(key=lambda x: x["timestamp"])
    # Dedup (shouldn't happen with daily data, but be safe)
    seen: set[str] = set()
    entries = [e for e in entries if not (e["timestamp"] in seen or seen.add(e["timestamp"]))]
    print(f"  Yahoo Finance: {len(entries)} entries (ESTIMATED — see README)")
    return entries


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

def _rows_to_entries(dates: pd.Series, prices22k: pd.Series, source: str) -> list[dict]:
    """Convert aligned date/price series to JSON-schema entries."""
    entries = []
    for d, p22 in zip(dates, prices22k):
        if pd.isna(d) or pd.isna(p22):
            continue
        p22 = int(round(float(p22)))
        p24 = int(round(p22 * 24 / 22))
        p18 = int(round(p22 * 18 / 22))
        ts = pd.Timestamp(d)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        entries.append(
            {
                "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "22k": p22,
                "24k": p24,
                "18k": p18,
                "source": source,
            }
        )
    entries.sort(key=lambda x: x["timestamp"])
    seen: set[str] = set()
    out = []
    for e in entries:
        if e["timestamp"] not in seen:
            seen.add(e["timestamp"])
            out.append(e)
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    DATA_DIR.mkdir(exist_ok=True)

    entries = None
    for fn in [_try_goodreturns, _try_goldpriceindia, _try_yahoo_finance]:
        entries = fn()
        if entries and len(entries) >= 100:
            break
        time.sleep(1)

    if not entries or len(entries) < 100:
        print(
            "\nERROR: Could not obtain >= 100 entries from any source.\n"
            "Please check the scripts above and data source availability,\n"
            "then re-run or manually supply data/history_seed.json.\n"
            "Do NOT fabricate data."
        )
        sys.exit(1)

    # Span check
    dates = [e["timestamp"] for e in entries]
    span_days = (
        datetime.fromisoformat(dates[-1].rstrip("Z"))
        - datetime.fromisoformat(dates[0].rstrip("Z"))
    ).days

    print(f"\nTotal entries : {len(entries)}")
    print(f"Date range    : {dates[0][:10]} to {dates[-1][:10]} ({span_days} days)")

    if len(entries) < 300 or span_days < 270:
        print(
            f"\nWARNING: Only {len(entries)} entries spanning {span_days} days.\n"
            "Target is >= 300 entries over >= 9 months.\n"
            "Writing what we have — consider supplementing manually."
        )

    max_22k = max(e["22k"] for e in entries)
    assert max_22k < 25000, f"Sanity check failed: max 22K = Rs.{max_22k} >= 25000"

    OUT_PATH.write_text(json.dumps(entries, indent=2) + "\n")
    print(f"Written to {OUT_PATH}")
    print(f"22K range: Rs.{min(e['22k'] for e in entries)} to Rs.{max_22k}")


if __name__ == "__main__":
    main()
