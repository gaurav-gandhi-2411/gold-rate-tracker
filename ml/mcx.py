"""MCX gold futures data via yfinance (GC=F ticker).

MCX India's Bhavcopy CSV requires Selenium browser automation — there is no
direct URL template for programmatic downloads. yfinance ``GC=F`` (COMEX gold
front-month) is used instead: it provides daily settlement prices that serve
the same purpose for basis computation and backfill depth.

B1 — ``backfill_mcx_bhavcopy()``:
    CLI-only. Pulls historical GC=F closes from yfinance and writes
    ``data/mcx_gold.parquet``. Run once to seed historical depth.

B2 — ``append_mcx_today_yfinance()``:
    CI path. Appends today's GC=F close to ``data/mcx_gold.parquet``.

Prices are in USD/troy oz (COMEX). Callers are responsible for any
USD→INR or oz→10g conversion when computing basis against IBJA prices.

Usage:
    python -m ml.mcx backfill --start 2024-01-01
    python -m ml.mcx append
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MCX_PARQUET = DATA_DIR / "mcx_gold.parquet"

logger = logging.getLogger(__name__)


def backfill_mcx_bhavcopy(
    start: str,
    end: str | None = None,
    out: Path | None = None,
) -> pd.DataFrame:
    """Pull historical GC=F (COMEX gold front-month) closes via yfinance.

    Args:
        start: ISO date string, e.g. ``"2024-01-01"``.
        end:   ISO date string (exclusive). Defaults to today.
        out:   Output parquet path. Defaults to ``data/mcx_gold.parquet``.

    Returns:
        DataFrame with columns: date (str), close_usd (float), ticker (str).
        Empty DataFrame on failure (does not write file).
    """
    import yfinance as yf

    end_date = end or date.today().isoformat()
    logger.info("Backfilling GC=F from %s to %s", start, end_date)

    raw = yf.download("GC=F", start=start, end=end_date, auto_adjust=True, progress=False)
    if raw.empty:
        logger.warning("mcx: yfinance returned empty DataFrame for GC=F")
        return pd.DataFrame()

    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [col[0].lower() for col in raw.columns]
    else:
        raw.columns = [str(c).lower() for c in raw.columns]

    df = pd.DataFrame(
        {
            "date": raw.index.strftime("%Y-%m-%d"),
            "close_usd": raw["close"].round(2),
            "ticker": "GC=F",
        }
    ).reset_index(drop=True)
    df = df.dropna(subset=["close_usd"])

    p = out or MCX_PARQUET
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, index=False)
    logger.info("mcx: saved %d rows to %s", len(df), p)
    return df


def append_mcx_today_yfinance(path: Path | None = None) -> bool:
    """Fetch today's GC=F close from yfinance and append to the parquet store.

    Returns True if a new row was appended, False on failure or duplicate.
    """
    import yfinance as yf

    today = date.today().isoformat()
    existing = _load_mcx_parquet(path)

    if not existing.empty and "date" in existing.columns and today in existing["date"].values:
        logger.info("mcx: %s already in parquet — skipping", today)
        return False

    try:
        raw = yf.download("GC=F", period="2d", auto_adjust=True, progress=False)
    except Exception as exc:
        logger.warning("mcx: yfinance download failed: %s", exc)
        return False

    if raw.empty:
        logger.warning("mcx: yfinance returned empty response")
        return False

    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [col[0].lower() for col in raw.columns]
    else:
        raw.columns = [str(c).lower() for c in raw.columns]

    latest = raw.iloc[-1]
    try:
        close_usd = float(latest["close"])
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("mcx: could not extract close price: %s", exc)
        return False

    row = pd.DataFrame([{"date": today, "close_usd": round(close_usd, 2), "ticker": "GC=F"}])
    combined = pd.concat([existing, row], ignore_index=True)

    p = path or MCX_PARQUET
    p.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(p, index=False)
    logger.info("mcx: appended %s close_usd=%.2f (%d rows total)", today, close_usd, len(combined))
    return True


def _load_mcx_parquet(path: Path | None = None) -> pd.DataFrame:
    p = path or MCX_PARQUET
    if not p.exists():
        return pd.DataFrame()
    return pd.read_parquet(p)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="MCX gold data via yfinance (GC=F)")
    sub = parser.add_subparsers(dest="cmd")

    bp = sub.add_parser("backfill", help="Pull historical GC=F closes")
    bp.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    bp.add_argument("--end", help="End date YYYY-MM-DD (default: today)")
    bp.add_argument("--out", help="Output parquet path")

    sub.add_parser("append", help="Append today's GC=F close")

    args = parser.parse_args()
    if args.cmd == "backfill":
        df = backfill_mcx_bhavcopy(args.start, args.end, Path(args.out) if args.out else None)
        sys.exit(0 if not df.empty else 1)
    elif args.cmd == "append":
        sys.exit(0 if append_mcx_today_yfinance() else 1)
    else:
        parser.print_help()
        sys.exit(1)
