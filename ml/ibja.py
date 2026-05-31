"""IBJA daily gold rate fetcher.

Data source: ibjarates.com (static HTML, no JavaScript required).

Page structure (verified 2026-05-18):
    Table selector: ``table#TodayRatesTableDataYes``
    Columns: Purity | AM | PM
    Values are in Rs per 10g (e.g. 157821 → Rs 15,782.10 / 10g).

    ibja.co (official IBJA site) was inspected but serves only a single
    AM or PM rate at a time (no dual-column layout). ibjarates.com is the
    sole source that provides both AM and PM in one request.

robots.txt findings (verified 2026-05-18):
    ibja.co:       User-agent: * / Disallow: /cgi-bin/ — scraping allowed
    ibjarates.com: HTTP 404 (no robots.txt) — no restrictions

Usage:
    python -m ml.ibja           # append today's rates and exit
    python -m ml.ibja backfill  # one-time 30-day PDF backfill and exit
"""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, date, datetime
from io import StringIO
from pathlib import Path
from urllib.parse import urljoin

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

    df.columns = pd.Index([str(c).strip() for c in df.columns])
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


def append_ibja_today(path: Path | None = None) -> bool | None:
    """Fetch today's IBJA rates and append (or update) the parquet store.

    The PM fix publishes at ~17:00 IST; the AM fix at ~09:30 IST.  Early CI
    runs (pre-AM) may write today's row with null rates.  When a fresh fetch
    has a valid pm_916 and the stored row has a null pm_916, the existing row
    is updated in-place so that post-PM-fix runs fill in the complete data.
    Without this upsert, the write-once skip would leave pm_916 null forever,
    blocking calibration from accumulating valid overlap pairs.

    Returns:
        True   — new row appended or existing row updated with PM rate
        False  — already in parquet and up to date (no-op, not an error)
        None   — fetch failed; parquet not updated
    """
    rates = fetch_ibja_daily()
    if not rates:
        logger.warning("ibja: no rates fetched — parquet not updated")
        return None

    today = date.today().isoformat()
    existing = load_ibja_parquet(path)

    if not existing.empty and "date" in existing.columns and today in existing["date"].values:
        # Row exists — check if pm_916 is null and the new fetch has a valid value.
        # The pm_916 column may not exist at all if a pre-AM-fix run only wrote am_ data.
        new_pm = rates.get("pm_916")
        if "pm_916" in existing.columns:
            existing_pm_series = existing.loc[existing["date"] == today, "pm_916"]
            existing_pm_null = existing_pm_series.empty or pd.isna(existing_pm_series.iloc[0])
        else:
            existing_pm_null = True  # column absent → treat as null

        if new_pm is not None and not pd.isna(new_pm) and existing_pm_null:
            # Upsert: write all rates from the fresh fetch into the existing row,
            # adding new columns (e.g. pm_916) if they weren't present before.
            for col, val in rates.items():
                existing.loc[existing["date"] == today, col] = val
            existing.loc[existing["date"] == today, "fetched_at"] = datetime.now(UTC).isoformat()
            p = path or IBJA_PARQUET
            p.parent.mkdir(parents=True, exist_ok=True)
            existing.to_parquet(p, index=False)
            logger.info("ibja: updated pm_916 for %s (was null, now %.1f)", today, new_pm)
            return True
        logger.info("ibja: %s already in parquet — skipping", today)
        return False

    row = {"date": today, "fetched_at": datetime.now(UTC).isoformat(), **rates}
    combined = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)

    p = path or IBJA_PARQUET
    p.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(p, index=False)
    logger.info("ibja: appended %s to parquet (%d rows total)", today, len(combined))
    return True


def _extract_pdf_url(html: str) -> str | None:
    """Extract the absolute 30-day PDF URL from ibjarates.com HTML.

    The link has href like ``../UploadedFiles/30DaysPdf/Pdf_XXXX_timestamp_....pdf``.
    urljoin resolves it correctly against the base URL.
    """
    match = re.search(r'href="([^"]*30DaysPdf[^"]*\.pdf)"', html, re.IGNORECASE)
    if not match:
        return None
    return urljoin(_IBJA_URL, match.group(1))


def _download_pdf_bytes(url: str) -> bytes | None:
    """Download a PDF and return raw bytes, or None on error."""
    headers = {"User-Agent": _USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.content
    except requests.RequestException as exc:
        logger.warning("ibja: PDF download failed (%s): %s", url, exc)
        return None


def _parse_ibja_pdf(pdf_bytes: bytes) -> pd.DataFrame:
    """Parse ibjarates.com 30-day PDF bytes into ibja_rates schema rows.

    Column layout (positional, verified 2026-05-18):
        0=Date, 1=G999 AM, 2=G999 PM, 3=G995 AM, 4=G995 PM,
        5=G916 AM, 6=G916 PM, 7=G750 AM, 8=G750 PM, 9=G585 AM, 10=G585 PM,
        11=S999 AM (silver, ignored), 12=S999 PM (silver, ignored)
    Rows 0 and 1 are header/units; data rows start at index 2.
    Weekend rows have 'SAT' or 'SUN' in col[1]; holiday rows have 'Holiday'.
    """
    try:
        import pdfplumber
    except ImportError:
        logger.warning("ibja: pdfplumber not installed; PDF backfill unavailable")
        return pd.DataFrame()

    import io

    rows: list[dict] = []
    now_utc = datetime.now(UTC).isoformat()
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                if not tables:
                    continue
                for row in tables[0][2:]:  # skip header + units rows
                    if not row[0]:
                        continue
                    am_val = str(row[1]).strip() if row[1] else ""
                    if am_val in ("SUN", "SAT") or "Holiday" in am_val:
                        continue
                    try:
                        date_iso = pd.to_datetime(row[0].strip(), format="%d-%b-%y").strftime(
                            "%Y-%m-%d"
                        )
                    except (ValueError, TypeError):
                        logger.warning("ibja: PDF: could not parse date '%s'", row[0])
                        continue
                    try:
                        rows.append(
                            {
                                "date": date_iso,
                                "fetched_at": now_utc,
                                "am_999": float(am_val.replace(",", "")),
                                "pm_999": float(str(row[2]).replace(",", "")),
                                "am_995": float(str(row[3]).replace(",", "")),
                                "pm_995": float(str(row[4]).replace(",", "")),
                                "am_916": float(str(row[5]).replace(",", "")),
                                "pm_916": float(str(row[6]).replace(",", "")),
                                "am_750": float(str(row[7]).replace(",", "")),
                                "pm_750": float(str(row[8]).replace(",", "")),
                                "am_585": float(str(row[9]).replace(",", "")),
                                "pm_585": float(str(row[10]).replace(",", "")),
                            }
                        )
                    except (ValueError, TypeError, IndexError) as exc:
                        logger.warning("ibja: PDF: could not parse row for %s: %s", row[0], exc)
    except Exception as exc:
        logger.warning("ibja: pdfplumber failed: %s", exc)
        return pd.DataFrame()

    return pd.DataFrame(rows)


def backfill_ibja_from_pdf(path: Path | None = None) -> int:
    """Fetch ibjarates.com, extract the 30-day PDF URL, download, parse, append.

    Idempotent: rows whose date already exists in the parquet are skipped.
    Returns the count of new rows appended (0 on failure or if already current).
    """
    time.sleep(_POLITE_DELAY)
    html = _get_with_retry({"User-Agent": _USER_AGENT})
    if html is None:
        logger.warning("ibja: backfill_ibja_from_pdf: HTML fetch failed")
        return 0

    pdf_url = _extract_pdf_url(html)
    if pdf_url is None:
        logger.warning("ibja: backfill_ibja_from_pdf: no PDF URL found in HTML")
        return 0
    logger.info("ibja: backfill PDF URL: %s", pdf_url)

    pdf_bytes = _download_pdf_bytes(pdf_url)
    if pdf_bytes is None:
        return 0

    df_new = _parse_ibja_pdf(pdf_bytes)
    if df_new.empty:
        logger.warning("ibja: backfill_ibja_from_pdf: PDF parse returned empty DataFrame")
        return 0

    existing = load_ibja_parquet(path)
    if not existing.empty and "date" in existing.columns:
        known_dates = set(existing["date"].values)
        df_new = df_new[~df_new["date"].isin(known_dates)].reset_index(drop=True)

    if df_new.empty:
        logger.info("ibja: backfill_ibja_from_pdf: no new rows (all dates already present)")
        return 0

    p = path or IBJA_PARQUET
    p.parent.mkdir(parents=True, exist_ok=True)
    combined = pd.concat([existing, df_new], ignore_index=True)
    combined.to_parquet(p, index=False)
    logger.info(
        "ibja: backfill_ibja_from_pdf: appended %d rows (%d total)", len(df_new), len(combined)
    )
    return len(df_new)


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="IBJA gold rate data")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("append", help="Append today's rates (default)")
    sub.add_parser("backfill", help="One-time 30-day PDF backfill")
    args = parser.parse_args()
    if args.cmd == "backfill":
        n = backfill_ibja_from_pdf()
        raise SystemExit(0 if n >= 0 else 1)
    else:
        # True (appended) and False (already present) are both success — exit 0.
        # None means fetch failed — exit 1.
        raise SystemExit(0 if append_ibja_today() is not None else 1)
