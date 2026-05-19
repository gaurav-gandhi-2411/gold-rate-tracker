"""One-shot Wayback Machine backfill of IBJA daily gold rates.

Queries the Internet Archive CDX API for all HTTP-200 captures of ibjarates.com
from 2022-01-01 onward, then extracts IBJA-916-PM (and all purities) via two modes:

  Mode A: parse table#TodayRatesTableDataYes from the archived HTML  → 1 row per capture
  Mode B: extract the embedded 30-day PDF URL from the HTML, fetch the archived PDF
          via Wayback proxy, parse with pdfplumber                   → up to 30 rows per PDF

Rows are deduplicated by date and merged into data/ibja_rates.parquet.

Usage:
    python scripts/wayback_ibja_backfill.py              # Mode A+B (default)
    python scripts/wayback_ibja_backfill.py --mode a     # HTML only (faster)
    python scripts/wayback_ibja_backfill.py --dry-run    # CDX query only, no fetches
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "data"
IBJA_PARQUET = DATA_DIR / "ibja_rates.parquet"

_WAYBACK_CDX = "https://web.archive.org/cdx/search/cdx"
_IBJA_URL = "https://ibjarates.com/"
_UA = "gold-rate-tracker/wayback-backfill (research)"
_DELAY = 5.0
_TIMEOUT = 60
_MAX_RETRIES = 3

logger = logging.getLogger(__name__)

_PURITY_MAP = {
    "Gold 999": "999",
    "Gold 995": "995",
    "Gold 916": "916",
    "Gold 750": "750",
    "Gold 585": "585",
}

_SCHEMA_COLS = [
    "date",
    "fetched_at",
    "am_999",
    "pm_999",
    "am_995",
    "pm_995",
    "am_916",
    "pm_916",
    "am_750",
    "pm_750",
    "am_585",
    "pm_585",
]


# ---------------------------------------------------------------------------
# CDX query
# ---------------------------------------------------------------------------


def get_cdx_captures(from_date: str = "20220101") -> list[dict]:
    """Return one CDX record per calendar day for ibjarates.com root (HTTP 200)."""
    to_date = datetime.now(UTC).strftime("%Y%m%d")
    params = {
        "url": "ibjarates.com/",
        "matchType": "exact",
        "output": "json",
        "from": from_date,
        "to": to_date,
        "filter": "statuscode:200",
        "fl": "timestamp,original",
        "collapse": "timestamp:8",
        "limit": "2000",
    }
    logger.info("CDX: querying ibjarates.com captures %s to %s", from_date, to_date)
    resp = requests.get(_WAYBACK_CDX, params=params, headers={"User-Agent": _UA}, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if not data or len(data) < 2:
        logger.warning("CDX: no captures found")
        return []
    header = data[0]
    captures = [dict(zip(header, row, strict=True)) for row in data[1:]]
    logger.info("CDX: %d unique-day captures found", len(captures))
    return captures


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _wayback_get(ts: str, target_url: str) -> bytes | None:
    """Fetch an archived resource from Wayback Machine (raw mode, no toolbar rewrite).

    Returns raw bytes or None on permanent failure (404) or exhausted retries.
    """
    wb_url = f"https://web.archive.org/web/{ts}id_/{target_url}"
    headers = {"User-Agent": _UA}
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.get(wb_url, headers=headers, timeout=_TIMEOUT, allow_redirects=True)
            if resp.status_code == 429:
                wait = _DELAY * (2**attempt)
                logger.warning("Wayback 429 — sleeping %.0fs", wait)
                time.sleep(wait)
                continue
            if resp.status_code == 404:
                return None  # not archived
            if resp.status_code >= 500:
                wait = _DELAY * (attempt + 1)
                logger.warning("Wayback HTTP %d — retrying in %.0fs", resp.status_code, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.content
        except requests.RequestException as exc:
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_DELAY * (attempt + 1))
            else:
                logger.warning("Wayback fetch failed after %d tries: %s", _MAX_RETRIES, exc)
    return None


# ---------------------------------------------------------------------------
# HTML parsing (Mode A)
# ---------------------------------------------------------------------------

# Purity map for the current site format: "Gold 999" → "999"
_PURITY_MAP_COMBINED = {
    "Gold 999": "999",
    "Gold 995": "995",
    "Gold 916": "916",
    "Gold 750": "750",
    "Gold 585": "585",
}
# Purity map for the old site format: "999" → "999" (purity in separate column)
_PURITY_MAP_SPLIT = {"999": "999", "995": "995", "916": "916", "750": "750", "585": "585"}


def _parse_rates_from_html(html: str, date_iso: str, ts: str) -> dict | None:
    """Extract IBJA rate row from archived ibjarates.com HTML.

    Handles two historical formats:
    - Current (2024+): table#TodayRatesTableDataYes with "Gold 999 | AM | PM" (3 cols)
    - Old (2022-2023): two .table-striped tables; "today's" table identified by
      <span id="clock"> or by position; columns are "empty | Purity | AM | PM" (4 cols)
      where Purity values are bare numbers like "999", "916" etc.

    Returns a row dict with pm_916 (and other purities) filled, or None on failure.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    fetched_at = datetime.strptime(ts, "%Y%m%d%H%M%S").strftime("%Y-%m-%dT%H:%M:%S+00:00")

    # --- Strategy 1: current format with named table ID ---
    current_table = soup.find("table", {"id": "TodayRatesTableDataYes"})
    if current_table:
        result = _try_parse_3col(str(current_table), date_iso, fetched_at)
        if result:
            return result

    # --- Strategy 2: old format — find the "today's rates" table via clock span ---
    all_tables = soup.find_all("table")
    clock_table = None
    for t in all_tables:
        if t.find("span", {"id": "clock"}):
            clock_table = t
            break

    if clock_table:
        result = _try_parse_4col(str(clock_table), date_iso, fetched_at)
        if result:
            return result

    # --- Strategy 3: try every table with >= 3 cols, both 3-col and 4-col parsers ---
    for t in all_tables:
        try:
            dfs = pd.read_html(StringIO(str(t)))
        except Exception:
            continue
        for df in dfs:
            if df.shape[1] == 3:
                result = _try_parse_df_3col(df, date_iso, fetched_at)
                if result:
                    return result
            elif df.shape[1] >= 4:
                result = _try_parse_df_4col(df, date_iso, fetched_at)
                if result:
                    return result

    return None


def _try_parse_3col(table_html: str, date_iso: str, fetched_at: str) -> dict | None:
    try:
        df = pd.read_html(StringIO(table_html))[0]
    except Exception:
        return None
    return _try_parse_df_3col(df, date_iso, fetched_at)


def _try_parse_df_3col(df: pd.DataFrame, date_iso: str, fetched_at: str) -> dict | None:
    """Parse 3-column format: Gold 999 | AM | PM."""
    if df.shape[1] < 3:
        return None
    df.columns = [str(c).strip() for c in df.columns]
    purity_col, am_col, pm_col = df.columns[0], df.columns[1], df.columns[2]
    row_out: dict[str, object] = {"date": date_iso, "fetched_at": fetched_at}
    found = 0
    for _, row in df.iterrows():
        key = str(row[purity_col]).strip()
        if key not in _PURITY_MAP_COMBINED:
            continue
        suffix = _PURITY_MAP_COMBINED[key]
        try:
            row_out[f"am_{suffix}"] = float(str(row[am_col]).replace(",", "").strip())
            row_out[f"pm_{suffix}"] = float(str(row[pm_col]).replace(",", "").strip())
            found += 1
        except (ValueError, TypeError):
            pass
    return row_out if found >= 1 else None


def _try_parse_4col(table_html: str, date_iso: str, fetched_at: str) -> dict | None:
    try:
        df = pd.read_html(StringIO(table_html))[0]
    except Exception:
        return None
    return _try_parse_df_4col(df, date_iso, fetched_at)


def _try_parse_df_4col(df: pd.DataFrame, date_iso: str, fetched_at: str) -> dict | None:
    """Parse 4-column format: Metal | Purity | AM | PM.

    The table has rows for Gold and Silver; only Gold rows are extracted.
    Purity column contains bare numbers like 999, 916 — not "Gold 999".
    """
    if df.shape[1] < 4:
        return None
    df.columns = [str(c).strip() for c in df.columns]
    metal_col = df.columns[0]  # "Gold" or "Silver"
    purity_col = df.columns[1]  # 999, 995, 916, 750, 585
    am_col = df.columns[2]
    pm_col = df.columns[3]
    row_out: dict[str, object] = {"date": date_iso, "fetched_at": fetched_at}
    found = 0
    for _, row in df.iterrows():
        if str(row[metal_col]).strip() != "Gold":
            continue  # skip Silver and header artefacts
        key = str(row[purity_col]).strip()
        if key not in _PURITY_MAP_SPLIT:
            continue
        suffix = _PURITY_MAP_SPLIT[key]
        try:
            row_out[f"am_{suffix}"] = float(str(row[am_col]).replace(",", "").strip())
            row_out[f"pm_{suffix}"] = float(str(row[pm_col]).replace(",", "").strip())
            found += 1
        except (ValueError, TypeError):
            pass
    return row_out if found >= 1 else None


# ---------------------------------------------------------------------------
# PDF extraction (Mode B)
# ---------------------------------------------------------------------------


def _extract_pdf_url(html: str) -> str | None:
    """Extract the absolute 30-day PDF URL from ibjarates.com HTML."""
    match = re.search(r'href=["\']([^"\']*30DaysPdf[^"\']*\.pdf)["\']', html, re.IGNORECASE)
    if not match:
        return None
    return urljoin(_IBJA_URL, match.group(1))


def _parse_pdf_rows(pdf_bytes: bytes) -> list[dict]:
    """Parse ibjarates.com 30-day PDF. Reuses ml.ibja._parse_ibja_pdf."""
    try:
        from ml.ibja import _parse_ibja_pdf

        df = _parse_ibja_pdf(pdf_bytes)
        if df.empty:
            return []
        return df.to_dict(orient="records")
    except Exception as exc:
        logger.warning("PDF parse failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Per-capture processing
# ---------------------------------------------------------------------------


def process_capture(
    ts: str,
    mode: str,
    failures: list[tuple],
) -> list[dict]:
    """Fetch one Wayback capture and extract rows.

    Returns a list of row dicts (may be empty on failure).
    """
    date_iso = datetime.strptime(ts[:8], "%Y%m%d").strftime("%Y-%m-%d")
    logger.info("[%s] ts=%s", date_iso, ts)

    html_bytes = _wayback_get(ts, _IBJA_URL)
    if html_bytes is None:
        failures.append((ts, date_iso, "HTML fetch returned None (404/timeout)"))
        return []

    html = html_bytes.decode("utf-8", errors="replace")
    rows: list[dict] = []

    # Mode A — parse the HTML snapshot
    row_a = _parse_rates_from_html(html, date_iso, ts)
    if row_a and "pm_916" in row_a:
        rows.append(row_a)
        logger.debug("  Mode A: pm_916=%.1f", row_a["pm_916"])
    else:
        failures.append((ts, date_iso, "HTML table parse failed or pm_916 missing"))

    # Mode B — extract PDF, fetch archived version, parse
    if "b" in mode:
        pdf_url = _extract_pdf_url(html)
        if not pdf_url:
            failures.append((ts, date_iso, "PDF URL not found in HTML"))
        else:
            time.sleep(_DELAY)
            pdf_bytes = _wayback_get(ts, pdf_url)
            if pdf_bytes is None:
                failures.append((ts, date_iso, f"PDF not archived: {pdf_url}"))
            else:
                pdf_rows = _parse_pdf_rows(pdf_bytes)
                if pdf_rows:
                    rows.extend(pdf_rows)
                    logger.info("  Mode B: %d rows from PDF", len(pdf_rows))
                else:
                    failures.append((ts, date_iso, "PDF parse returned 0 rows"))

    return rows


# ---------------------------------------------------------------------------
# Main backfill routine
# ---------------------------------------------------------------------------


def run_backfill(mode: str = "ab", dry_run: bool = False) -> dict:
    """Query CDX, fetch captures, parse rates, merge into ibja_rates.parquet.

    Returns a summary dict with counts and failure list.
    """
    captures = get_cdx_captures()
    if not captures:
        logger.error("No CDX captures found — aborting.")
        return {"status": "no_captures"}

    # Load existing parquet
    if IBJA_PARQUET.exists():
        existing = pd.read_parquet(IBJA_PARQUET)
    else:
        existing = pd.DataFrame(columns=_SCHEMA_COLS)
    rows_before = len(existing)
    existing_dates: set[str] = set(existing["date"].values) if not existing.empty else set()
    logger.info("Existing parquet: %d rows (%d unique dates)", rows_before, len(existing_dates))

    if dry_run:
        logger.info("DRY RUN: would process %d captures (mode=%s)", len(captures), mode)
        return {"status": "dry_run", "n_captures": len(captures)}

    all_new_rows: list[dict] = []
    failures: list[tuple] = []
    n_processed = 0

    for i, cap in enumerate(captures):
        ts = cap["timestamp"]
        date_iso = datetime.strptime(ts[:8], "%Y%m%d").strftime("%Y-%m-%d")

        # Mode A: skip if date already in parquet — saves one HTML fetch
        # Mode AB: still do Mode B even if HTML date is known (PDF covers prior 30 days)
        if mode == "a" and date_iso in existing_dates:
            logger.debug("Skip %s (Mode A, already in parquet)", date_iso)
            continue

        rows = process_capture(ts, mode, failures)
        all_new_rows.extend(rows)
        n_processed += 1

        if (i + 1) % 10 == 0:
            logger.info(
                "Progress: %d/%d captures, %d rows collected so far",
                i + 1,
                len(captures),
                len(all_new_rows),
            )

        time.sleep(_DELAY)

    logger.info(
        "Collection complete: %d rows from %d captures (%d failures)",
        len(all_new_rows),
        n_processed,
        len(failures),
    )

    if not all_new_rows:
        logger.warning("No new rows collected.")
        return {
            "status": "no_new_rows",
            "rows_before": rows_before,
            "rows_added": 0,
            "rows_after": rows_before,
            "n_captures_processed": n_processed,
            "n_failures": len(failures),
            "failures": failures,
        }

    # Build DataFrame, keep rows with pm_916
    df_new = pd.DataFrame(all_new_rows)
    df_new = df_new[df_new["date"].notna()].copy()
    if "pm_916" in df_new.columns:
        df_new = df_new[df_new["pm_916"].notna()].copy()

    # Deduplicate by date — last row wins (latest Wayback source / Mode B > Mode A)
    df_new = df_new.drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)

    # Remove dates already in parquet
    df_add = df_new[~df_new["date"].isin(existing_dates)].copy()

    if df_add.empty:
        logger.info("All collected dates already in parquet — nothing new to add.")
        return {
            "status": "already_current",
            "rows_before": rows_before,
            "rows_added": 0,
            "rows_after": rows_before,
            "n_captures_processed": n_processed,
            "n_failures": len(failures),
            "failures": failures,
        }

    # Ensure all schema columns present
    for col in _SCHEMA_COLS:
        if col not in df_add.columns:
            df_add[col] = float("nan")
    df_add = df_add[_SCHEMA_COLS].copy()

    # Merge, sort, write
    combined = pd.concat([existing[_SCHEMA_COLS], df_add], ignore_index=True)
    combined = combined.sort_values("date").reset_index(drop=True)
    combined.to_parquet(IBJA_PARQUET, index=False)

    rows_added = len(df_add)
    rows_after = len(combined)
    logger.info("Parquet updated: %d new rows added (%d total)", rows_added, rows_after)

    return {
        "status": "success",
        "rows_before": rows_before,
        "rows_added": rows_added,
        "rows_after": rows_after,
        "n_captures_processed": n_processed,
        "n_failures": len(failures),
        "failures": failures,
    }


# ---------------------------------------------------------------------------
# Post-run reporting
# ---------------------------------------------------------------------------


def print_summary(result: dict) -> None:
    """Print rows/coverage/sample summary after backfill."""
    print()
    print("=== Wayback IBJA Backfill Summary ===")
    print(f"Status:              {result.get('status')}")
    print(f"Rows before:         {result.get('rows_before', '?')}")
    print(f"Rows added:          {result.get('rows_added', '?')}")
    print(f"Rows after:          {result.get('rows_after', '?')}")
    print(
        f"Captures processed:  {result.get('n_captures_processed', '?')} "
        f"({result.get('n_failures', '?')} failures)"
    )

    if not IBJA_PARQUET.exists():
        return

    df = pd.read_parquet(IBJA_PARQUET)
    if df.empty:
        return

    df["year_month"] = df["date"].str[:7]
    counts = df.groupby("year_month").size()
    print("\nDate coverage histogram (rows per month):")
    for ym, n in counts.items():
        bar = "#" * min(n, 35)
        print(f"  {ym}  {bar} ({n})")

    print("\nSample 5 random rows:")
    sample = df[["date", "pm_916"]].dropna().sample(min(5, len(df)), random_state=42)
    for _, row in sample.sort_values("date").iterrows():
        per_gram = row["pm_916"] / 10.0
        print(f"  {row['date']}  pm_916={row['pm_916']:.0f} INR/10g  ({per_gram:.1f} INR/g)")

    failures = result.get("failures", [])
    if failures:
        print(f"\nParse failures ({len(failures)} total — first 15):")
        for ts, date_iso, reason in failures[:15]:
            print(f"  {date_iso} [{ts[:8]}]: {reason}")
        if len(failures) > 15:
            print(f"  ... and {len(failures) - 15} more")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(
        description="Wayback Machine backfill of IBJA daily rates into data/ibja_rates.parquet"
    )
    parser.add_argument(
        "--mode",
        choices=["a", "ab"],
        default="ab",
        help="a = HTML only (faster, ~1 row/capture); ab = HTML + PDF (~30 rows/capture)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Query CDX only; print capture count and exit without fetching",
    )
    args = parser.parse_args()

    result = run_backfill(mode=args.mode, dry_run=args.dry_run)
    print_summary(result)
    raise SystemExit(0 if result["status"] in ("success", "already_current", "dry_run") else 1)


if __name__ == "__main__":
    main()
