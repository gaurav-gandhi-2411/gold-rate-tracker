"""ml.macro_drivers — CFTC positioning + US real-yield loaders for item 8 (ADR 053).

Two new, genuinely new pieces of information for the longer-horizon direction/return
work, both fetched AT RUN TIME (never cached to disk, never committed to the repo --
only derived analysis results are committed, per the legal/data rules in ADR 053):

  * CFTC Commitments of Traders, Disaggregated report, COMEX gold (contract code
    088691): managed-money net position as a share of open interest, its 4-week
    change, and a 3-year rolling z-score. Public domain (17 U.S.C. Sec 105), fetched
    from the Socrata "Public Reporting Environment" API (publicreporting.cftc.gov),
    not scraped HTML.
  * US Treasury Daily Par Real Yield Curve, 10-year point: level and its 4-week
    change. Public domain, fetched from Treasury's own XML feed (never FRED's HTML
    pages -- FRED's own ToS bars scraping; Treasury's feed is the primary source FRED
    itself republishes from and needs no API key).

Both loaders fail LOUDLY (MacroFetchError) on a network error, empty response, or a
response Socrata/Treasury etc. that looks parseable-but-hollow -- never a silent
stale/empty fallback (this is an analysis-only research module; there is no "serve
something" pressure that would justify one).

Release-lag alignment (the reason this module exists, not just "a downloader"): every
feature must be legitimately public as of the DECISION day it is used for. See
docs/adr/053-macro-drivers-longer-horizons.md and the research note this was built
from (scratchpad sources_cot_events.md, 2026-09-24) for the primary-source citations
behind each rule below.

  * COT: a report "as of" Tuesday T is not public until CFTC actually releases it --
    normally Friday T+3, 15:30 ET (CFTC Release Schedule page, VERIFIED), usable for
    a decision made AT/AFTER that Friday's close. `cot_release_available_date`
    computes that Friday, shifts past a US federal holiday landing on it, and
    overrides the two known multi-week shutdown gaps (2013, 2018-19) to their
    documented catch-up dates -- the arithmetic rule alone would silently claim a
    shutdown-delayed report was public weeks before it actually was.
  * Real yields: Treasury posts by ~18:00 ET the same day (Yield Curve Methodology
    page, VERIFIED). A forecast made at COMEX's close is made before 18:00 ET, so the
    conservative, documented rule this pipeline uses throughout: a decision made at
    the close of day d may use day d-1's real yield, never day d's own.
    `real_yield_available_date` implements this as quote_date + 1 calendar day.

`align_feature_to_decision_dates` does the actual as-of merge (backward-looking,
`pandas.merge_asof`) -- by construction it can never pull a value whose available
date is after the decision date, so no-look-ahead is structural, not just documented.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from typing import Any

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Shared fetch plumbing -- timeouts, retries, fail loudly
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT_S = 30.0
_MAX_RETRIES = 3
_BACKOFF_S = 3.0


class MacroFetchError(RuntimeError):
    """A COT or real-yield fetch failed after retries, or returned no usable rows.

    Raised instead of returning stale/empty data -- an analysis run must fail loudly,
    never silently score against a truncated or fabricated series (item 8's legal/data
    rule: "fetch at run time... fail loudly, never silently return stale data")."""


def _get_with_retry(
    url: str,
    params: dict[str, Any] | None = None,
    timeout: float = _DEFAULT_TIMEOUT_S,
    max_retries: int = _MAX_RETRIES,
    backoff: float = _BACKOFF_S,
) -> requests.Response:
    last_exc: Exception = RuntimeError("no attempt was made")
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                time.sleep(backoff**attempt)
    raise MacroFetchError(
        f"GET {url} failed after {max_retries} attempts: {last_exc}"
    ) from last_exc


# ---------------------------------------------------------------------------
# CFTC COT -- Disaggregated report, COMEX gold
# ---------------------------------------------------------------------------

COT_DATASET_ID = "72hh-3qpy"  # Disaggregated, Futures Only (publicreporting.cftc.gov)
COT_BASE_URL = f"https://publicreporting.cftc.gov/resource/{COT_DATASET_ID}.json"
COT_CONTRACT_CODE = "088691"  # COMEX gold
# Empirically confirmed 2026-09-24 (first row returned by the API for this contract
# code, ordered ascending) -- the CFTC's own two "About" vs "Historical Compressed"
# pages disagree by ~3 months on this; this is the ground truth, not either page's text.
COT_DATA_START = date(2006, 6, 13)

# Known multi-week publication gaps: any as-of Tuesday inside one of these windows is
# forced to the documented catch-up date, overriding the Friday+holiday arithmetic
# entirely -- see module docstring and docs/adr/053 for the primary-source citations.
_COT_SHUTDOWN_OVERRIDES: tuple[tuple[date, date, date], ...] = (
    # 2013 federal shutdown (Oct 1-17, 2013). CFTC press release 6745-13: first
    # delayed report published 2013-10-25 (for the report originally due 2013-10-04,
    # as-of 2013-10-01). Every as-of Tuesday in the window is forced to this one
    # conservative date rather than guessing at later catch-up reports' exact dates.
    (date(2013, 10, 1), date(2013, 10, 15), date(2013, 10, 25)),
    # 2018-19 shutdown (Dec 22, 2018 - Jan 25, 2019, 35 days). CFTC: report
    # suspended during the lapse; external reporting (not opened as a primary source
    # this session -- see sources_cot_events.md) puts full catch-up at 2019-03-08,
    # used here as the conservative forced date for the whole gap.
    (date(2018, 12, 18), date(2019, 1, 29), date(2019, 3, 8)),
)


def _shift_past_us_federal_holiday(d: date) -> date:
    """Push `d` forward past a US federal holiday or weekend (CFTC: "federal holidays
    may delay release by one or two days"). Only ever moves later, never earlier."""
    from pandas.tseries.holiday import USFederalHolidayCalendar

    cal = USFederalHolidayCalendar()
    window_start = pd.Timestamp(d) - pd.Timedelta(days=1)
    window_end = pd.Timestamp(d) + pd.Timedelta(days=7)
    holidays = {ts.date() for ts in cal.holidays(start=window_start, end=window_end)}
    while d.weekday() >= 5 or d in holidays:
        d += timedelta(days=1)
    return d


def cot_release_available_date(report_date: date) -> date:
    """The first day a COT report "as of" `report_date` (a Tuesday) is legitimately
    public and usable for a decision -- see module docstring for the citations."""
    for start, end, forced in _COT_SHUTDOWN_OVERRIDES:
        if start <= report_date <= end:
            return forced
    friday = report_date + timedelta(days=3)  # Tuesday (weekday 1) -> Friday (weekday 4)
    return _shift_past_us_federal_holiday(friday)


def _parse_cot_json(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        raise MacroFetchError("CFTC COT API returned zero rows for gold (088691)")
    out = (
        pd.DataFrame(
            {
                "report_date": [pd.Timestamp(r["report_date_as_yyyy_mm_dd"]).date() for r in rows],
                "open_interest_all": [float(r["open_interest_all"]) for r in rows],
                "managed_money_long": [float(r["m_money_positions_long_all"]) for r in rows],
                "managed_money_short": [float(r["m_money_positions_short_all"]) for r in rows],
            }
        )
        .sort_values("report_date")
        .reset_index(drop=True)
    )
    if out["open_interest_all"].le(0).any():
        raise MacroFetchError("CFTC COT response has a non-positive open_interest_all row")
    return out


def fetch_cot_disaggregated(
    start: date = COT_DATA_START, end: date | None = None, limit: int = 10000
) -> pd.DataFrame:
    """Fetches every Disaggregated COT report for COMEX gold in [start, end] and
    returns one row per report with `report_date` (as-of Tuesday) and
    `available_date` (the release-lag-adjusted day it becomes usable). Raises
    MacroFetchError on any network failure or an empty/malformed response."""
    end = end or date.today()
    where = (
        f"cftc_contract_market_code='{COT_CONTRACT_CODE}' AND "
        f"report_date_as_yyyy_mm_dd between '{start.isoformat()}T00:00:00' "
        f"and '{end.isoformat()}T00:00:00'"
    )
    resp = _get_with_retry(
        COT_BASE_URL,
        params={"$where": where, "$order": "report_date_as_yyyy_mm_dd", "$limit": limit},
    )
    try:
        rows = resp.json()
    except ValueError as exc:
        raise MacroFetchError(f"CFTC COT response was not valid JSON: {exc}") from exc
    df = _parse_cot_json(rows)
    df["available_date"] = df["report_date"].map(cot_release_available_date)
    return df


def add_cot_features(
    cot: pd.DataFrame, z_window_weeks: int = 156, change_weeks: int = 4
) -> pd.DataFrame:
    """Adds managed-money-net-as-%-of-OI, its `change_weeks`-report change, and a
    `z_window_weeks`-report (trailing, full-window-only) z-score -- all computed
    strictly on the report timeline (no future report ever informs a past one)."""
    out = cot.copy()
    net = out["managed_money_long"] - out["managed_money_short"]
    out["cot_net_pct_oi"] = 100.0 * net / out["open_interest_all"]
    out["cot_net_pct_oi_chg_4w"] = out["cot_net_pct_oi"].diff(change_weeks)
    roll = out["cot_net_pct_oi"].rolling(z_window_weeks, min_periods=z_window_weeks)
    out["cot_net_pct_oi_z"] = (out["cot_net_pct_oi"] - roll.mean()) / roll.std(ddof=1)
    return out


# ---------------------------------------------------------------------------
# US Treasury Daily Par Real Yield Curve -- 10-year point
# ---------------------------------------------------------------------------

TREASURY_REAL_YIELD_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xmlview"
)
REAL_YIELD_DATA_START = date(2003, 1, 2)  # Treasury's own history floor (VERIFIED)


def real_yield_available_date(quote_date: date) -> date:
    """Treasury posts by ~18:00 ET the SAME day. This pipeline's conservative rule
    (documented in ADR 053, applied uniformly): a decision made at COMEX's close on
    day d may use day d-1's real yield, not day d's own -- so `quote_date`'s value
    becomes usable starting the next calendar day."""
    return quote_date + timedelta(days=1)


def _parse_real_yield_xml(xml_text: str) -> pd.DataFrame:
    root = ET.fromstring(xml_text)
    rows: list[dict[str, Any]] = []
    for props in root.iter():
        if not props.tag.endswith("}properties") and props.tag != "properties":
            continue
        rec: dict[str, str] = {}
        for child in props:
            local = child.tag.rsplit("}", 1)[-1]
            rec[local] = child.text or ""
        if rec.get("NEW_DATE") and rec.get("TC_10YEAR"):
            rows.append(rec)
    if not rows:
        raise MacroFetchError("Treasury real-yield XML feed returned zero usable rows")
    return pd.DataFrame(
        {
            "quote_date": [pd.Timestamp(r["NEW_DATE"]).date() for r in rows],
            "real_yield_10y": [float(r["TC_10YEAR"]) for r in rows],
        }
    )


def fetch_real_yield(start: date = REAL_YIELD_DATA_START, end: date | None = None) -> pd.DataFrame:
    """Fetches the Treasury 10-year real (TIPS) par yield for every calendar year in
    [start.year, end.year], one XML request per year (the feed's own granularity),
    and returns one row per trading day with `quote_date` and `available_date`.
    Raises MacroFetchError on any network failure or an empty/malformed response."""
    end = end or date.today()
    frames = []
    for year in range(start.year, end.year + 1):
        resp = _get_with_retry(
            TREASURY_REAL_YIELD_URL,
            params={"data": "daily_treasury_real_yield_curve", "field_tdr_date_value": year},
            timeout=90.0,  # this feed serves a whole year of XML in one response; slow
        )
        frames.append(_parse_real_yield_xml(resp.text))
    df = pd.concat(frames, ignore_index=True).sort_values("quote_date").reset_index(drop=True)
    df = df[(df["quote_date"] >= start) & (df["quote_date"] <= end)].reset_index(drop=True)
    if df.empty:
        raise MacroFetchError(f"Treasury real-yield series is empty for [{start}, {end}]")
    df["available_date"] = df["quote_date"].map(real_yield_available_date)
    return df


def add_real_yield_features(real_yield: pd.DataFrame, change_days: int = 20) -> pd.DataFrame:
    """Adds the `change_days`-trading-day change (~4 weeks) to the real-yield level."""
    out = real_yield.copy()
    out["real_yield_10y_chg_4w"] = out["real_yield_10y"].diff(change_days)
    return out


# ---------------------------------------------------------------------------
# No-look-ahead alignment: source (report/quote timeline) -> decision dates
# ---------------------------------------------------------------------------


def align_feature_to_decision_dates(
    source: pd.DataFrame,
    value_cols: list[str],
    decision_dates: pd.Series,
    available_date_col: str = "available_date",
) -> pd.DataFrame:
    """For every decision date, attaches the most recent `source` row whose
    `available_date_col` is <= that decision date (a backward `merge_asof`) -- a
    decision date earlier than every available source row gets NaN, never a
    look-ahead value. `source` need not be pre-sorted; this sorts a copy.

    `merge_asof` requires both join keys to share the exact same datetime64 unit, and
    `pd.to_datetime` can infer DIFFERENT units for the two sides (a coarser `[s]` for a
    column of Python `date` objects vs `[us]` for a column of ISO date strings) -- both
    sides are forced to `datetime64[ns]` explicitly below so that mismatch can never
    raise instead of merging (hit for real on the first CI run of this script,
    2026-09-24: `source["available_date"]` is built from `date` objects,
    `decision_dates` from `as_of_date` strings)."""
    src = source.sort_values(available_date_col)[[available_date_col, *value_cols]]
    dd = pd.DataFrame({"decision_date": pd.to_datetime(decision_dates).astype("datetime64[ns]")})
    dd["_order"] = range(len(dd))
    dd = dd.sort_values("decision_date")
    merged = pd.merge_asof(
        dd,
        src.assign(
            **{available_date_col: pd.to_datetime(src[available_date_col]).astype("datetime64[ns]")}
        ),
        left_on="decision_date",
        right_on=available_date_col,
        direction="backward",
    )
    merged = merged.sort_values("_order").drop(columns=["_order", available_date_col])
    return merged.reset_index(drop=True)
