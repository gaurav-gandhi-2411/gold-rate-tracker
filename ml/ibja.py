"""IBJA daily gold rate fetcher.

Data source: ibjarates.com (static HTML, no JavaScript required).

Page structure (verified 2026-05-18):
    Table selector: ``table tbody tr``
    Columns: Purity | AM | PM
    Values are in Rs per 10g (e.g. 157821 → Rs 15,782.10 / 10g).

    Note: ibja.co (official IBJA site) was inspected but serves only a simple
    <li>-format list without an AM/PM breakdown. ibjarates.com exposes the
    structured AM/PM table and is therefore used as the primary data source.

robots.txt findings (verified 2026-05-18):
    ibja.co:       User-agent: * / Disallow: /cgi-bin/ — scraping allowed
    ibjarates.com: HTTP 404 (no robots.txt) — no restrictions

Usage:
    python -m ml.ibja          # append today's rates and exit
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
IBJA_PARQUET = DATA_DIR / "ibja_rates.parquet"

_IBJA_URL = "https://ibjarates.com/"
_USER_AGENT = "gold-rate-tracker/1.0 (portfolio project; gaurav.gandhi2411@gmail.com)"
_TIMEOUT = 30
_POLITE_DELAY = 1.0  # seconds; ibjarates.com has no Crawl-delay; 1s is conservative

logger = logging.getLogger(__name__)

_PURITY_MAP: dict[str, str] = {
    "Gold 999": "999",
    "Gold 995": "995",
    "Gold 916": "916",
    "Gold 750": "750",
    "Gold 585": "585",
}


def fetch_ibja_daily() -> dict[str, float]:
    """Fetch today's IBJA AM/PM gold rates from ibjarates.com.

    Returns a dict with keys ``am_<purity>`` and ``pm_<purity>`` for each
    recognised purity row. Values are in Rs per 10g.

    Returns an empty dict (and logs WARNING) on any network or parse failure.
    Single retry on HTTP 5xx before giving up.
    """
    time.sleep(_POLITE_DELAY)
    headers = {"User-Agent": _USER_AGENT}

    html = _get_with_retry(headers)
    if html is None:
        return {}

    try:
        tables = pd.read_html(StringIO(html))
    except Exception as exc:
        logger.warning("ibja: could not parse HTML tables: %s", exc)
        return {}

    if not tables:
        logger.warning("ibja: no tables found on page")
        return {}

    df = tables[0]
    if df.shape[1] < 3:
        logger.warning("ibja: expected >=3 columns, got %d", df.shape[1])
        return {}

    df.columns = [str(c).strip() for c in df.columns]
    purity_col, am_col, pm_col = df.columns[0], df.columns[1], df.columns[2]

    result: dict[str, float] = {}
    for _, row in df.iterrows():
        key = str(row[purity_col]).strip()
        if key not in _PURITY_MAP:
            continue
        suffix = _PURITY_MAP[key]
        try:
            result[f"am_{suffix}"] = float(str(row[am_col]).replace(",", ""))
            result[f"pm_{suffix}"] = float(str(row[pm_col]).replace(",", ""))
        except (ValueError, TypeError) as exc:
            logger.warning("ibja: could not parse %s row: %s", key, exc)

    if not result:
        logger.warning("ibja: no recognised purity rows parsed from table")
    return result


def _get_with_retry(headers: dict[str, str]) -> str | None:
    """GET _IBJA_URL with a single retry on 5xx. Returns HTML str or None."""
    for attempt in range(2):
        try:
            resp = requests.get(_IBJA_URL, headers=headers, timeout=_TIMEOUT)
            if resp.status_code < 500:
                resp.raise_for_status()
                return resp.text
            logger.warning("ibja: HTTP %d on attempt %d", resp.status_code, attempt + 1)
            if attempt == 0:
                time.sleep(_POLITE_DELAY)
        except requests.RequestException as exc:
            logger.warning("ibja: request failed (attempt %d): %s", attempt + 1, exc)
            if attempt == 0:
                time.sleep(_POLITE_DELAY)
    return None


def load_ibja_parquet(path: Path | None = None) -> pd.DataFrame:
    """Load ibja_rates.parquet. Returns empty DataFrame if file does not exist."""
    p = path or IBJA_PARQUET
    if not p.exists():
        return pd.DataFrame()
    return pd.read_parquet(p)


def append_ibja_today(path: Path | None = None) -> bool:
    """Fetch today's IBJA rates and append to the parquet store.

    Returns True if a new row was appended, False on failure or duplicate.
    """
    rates = fetch_ibja_daily()
    if not rates:
        logger.warning("ibja: no rates fetched — parquet not updated")
        return False

    today = date.today().isoformat()
    existing = load_ibja_parquet(path)

    if not existing.empty and "date" in existing.columns and today in existing["date"].values:
        logger.info("ibja: %s already in parquet — skipping", today)
        return False

    row = {"date": today, "fetched_at": datetime.now(UTC).isoformat(), **rates}
    combined = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)

    p = path or IBJA_PARQUET
    p.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(p, index=False)
    logger.info("ibja: appended %s to parquet (%d rows total)", today, len(combined))
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    raise SystemExit(0 if append_ibja_today() else 1)
