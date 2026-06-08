# Feature Store — Reference Guide

> **WARNING: DO NOT TRAIN until accumulation is sufficient.**
> This is a data-capture phase. The feature store starts accumulating 2026-06-07.
> Minimum useful training window: ~12 months of `live_pit` rows (target: mid-2027).
> Backfill rows (`source='backfill_yfinance'`) use revised closes — they introduce
> look-ahead bias if used as the primary training set. Use them for warm-start only.

---

## 1. Overview

The feature store (`data/feature_store/snapshots.parquet`) is an **append-only, point-in-time record** of gold price drivers captured on every pipeline run. One row per IST trading day. No row is ever modified or deleted after it is written.

Purpose: accumulate a clean, look-ahead-bias-free dataset for a future directional model. The model is not built in this phase. Training starts once ~12 months of `live_pit` rows exist (target: mid-2027).

---

## 2. Schema Reference

The following columns are defined in `_ALL_COLUMNS` in `ml/feature_store.py` (`SCHEMA_VERSION = 3`).

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `capture_utc` | str | No | ISO-8601 UTC timestamp of when this snapshot was written (e.g. `2026-06-07T10:30:00Z`). |
| `as_of_date` | str | No | IST calendar date for this snapshot (YYYY-MM-DD). Primary key — must be unique across all rows. |
| `schema_version` | int | No | Integer schema version. Currently `1`. Increment when columns are added or semantics change. |
| `source` | str | No | Provenance tag. Either `live_pit` (written by live CI pipeline) or `backfill_yfinance` (reconstructed from historical data). |
| `partial` | bool | Yes | `True` if the macro cache was unavailable at capture time; macro columns will be null. Uses pandas nullable boolean (`pd.BooleanDtype`). |
| `n_macro_null` | int | No | Count of null values across the **canonical 8 macro series** (denominator = 8). `n_macro_null == 0` means all 8 series are present. `partial=True` implies `n_macro_null == 8`. Per-series presence is recoverable without a separate column: `df['tips'].notna()` tells you whether TIPS is present for each row. The canonical 8 series (fixed; documented below) are the denominator for all time. |
| `gold_usd` | float | Yes | Gold spot price in USD/oz (ticker `GC=F` via yfinance). Null when `partial=True`. |
| `usd_inr` | float | Yes | USD/INR exchange rate (ticker `INR=X` via yfinance). Null when `partial=True`. |
| `us_10y_yield` | float | Yes | US 10-year Treasury yield in % (ticker `^TNX` via yfinance). Null when `partial=True`. |
| `dxy` | float | Yes | US Dollar Index (ticker `DX-Y.NYB` via yfinance). Null when `partial=True`. |
| `sensex` | float | Yes | BSE Sensex index level (ticker `^BSESN` via yfinance). Null when `partial=True`. |
| `vix` | float | Yes | CBOE Volatility Index (ticker `^VIX` via yfinance). Null when `partial=True`. |
| `crude_wti` | float | Yes | WTI crude oil futures price in USD/barrel (ticker `CL=F` via yfinance). Null when `partial=True`. |
| `tips` | float | Yes | iShares TIPS Bond ETF price, USD (ticker `TIP` via yfinance; proxy for real rate expectations). Null when `partial=True`. |
| `gold_usd_asof_date` | str | Yes | ISO date of the last non-null `gold_usd` observation in the macro cache (may lag `as_of_date` on weekends/holidays). |
| `usd_inr_asof_date` | str | Yes | ISO date of the last non-null `usd_inr` observation. |
| `us_10y_yield_asof_date` | str | Yes | ISO date of the last non-null `us_10y_yield` observation. |
| `dxy_asof_date` | str | Yes | ISO date of the last non-null `dxy` observation. |
| `sensex_asof_date` | str | Yes | ISO date of the last non-null `sensex` observation. |
| `vix_asof_date` | str | Yes | ISO date of the last non-null `vix` observation. |
| `crude_wti_asof_date` | str | Yes | ISO date of the last non-null `crude_wti` observation. |
| `tips_asof_date` | str | Yes | ISO date of the last non-null `tips` observation. |
| `ibja_pm_916` | float | Yes | IBJA PM fix for 916 hallmark gold in INR/g (22K daily closing benchmark). Null only if IBJA parquet unavailable. |
| `ibja_am_916` | float | Yes | IBJA AM fix for 916 hallmark gold in INR/g. Null if AM fix not available or IBJA parquet unavailable. |
| `tanishq_22k` | float | Yes | Tanishq 22K retail price in INR/g scraped from tanishq.com. `None` for all backfill rows (historical scrapes not available). **Φ22 H5 (IBJA-calibrated display estimate) is display-only and is NEVER written here — only genuinely scraped retail prices are stored (ADR 021 §5).** |
| `ibja_pm_916_asof_date` | str | Yes | ISO date of the IBJA row that `ibja_pm_916` came from (the row's own `date` field, not the capture date). Null when `ibja_pm_916` is null. |
| `ibja_am_916_asof_date` | str | Yes | ISO date of the IBJA row that `ibja_am_916` came from. Null when `ibja_am_916` is null. |
| `tanishq_22k_asof_date` | str | Yes | IST calendar date of the prices.json entry that `tanishq_22k` came from. Derived by converting the entry's UTC timestamp to IST (+05:30) and taking the calendar date — not by string-slicing the UTC date. Null when `tanishq_22k` is null. |
| `dow` | int | No | Day of week (0 = Monday … 6 = Sunday), derived from `as_of_date`. |
| `dom` | int | No | Day of month (1–31), derived from `as_of_date`. |
| `month` | int | No | Month (1–12), derived from `as_of_date`. |
| `is_festival_window` | bool | No | `True` if `as_of_date` falls within the window of a tracked Indian gold-buying festival. |
| `festival_name` | str | Yes | Name of the active festival (e.g. `"Akshaya Tritiya"`). `None` if `is_festival_window=False`. |
| `days_to_next_festival` | int | No | Calendar days from `as_of_date` to the nearest upcoming festival anchor date. `0` when currently inside a festival window. |
| `duty_change_active` | bool | No | `True` when `as_of_date` falls within 30 calendar days of a duty change event in `data/duty_events.json`. |
| `days_since_last_duty_change` | int | No | Calendar days between the most recent past duty event and `as_of_date`. `9999` if no event is on record before `as_of_date`. |

### Canonical macro series (fixed denominator for `n_macro_null`)

The 8 series below are the permanent denominator. `n_macro_null` is always counted against this exact list. The list is stable — adding a new macro series requires a SCHEMA_VERSION bump and a new `n_macro_null` recompute.

| Column | Ticker | Description |
|--------|--------|-------------|
| `gold_usd` | `GC=F` | Gold spot price USD/oz |
| `usd_inr` | `INR=X` | USD/INR exchange rate |
| `us_10y_yield` | `^TNX` | US 10-year Treasury yield (%) |
| `dxy` | `DX-Y.NYB` | US Dollar Index |
| `sensex` | `^BSESN` | BSE Sensex index level |
| `vix` | `^VIX` | CBOE Volatility Index |
| `crude_wti` | `CL=F` | WTI crude oil futures USD/barrel |
| `tips` | `TIP` | iShares TIPS Bond ETF (real rate proxy) |

**Per-series presence query pattern:**
```python
# Check if a series is present for each row (no separate boolean column needed):
df['crude_wti'].notna()   # True where crude_wti was available on that date
df['tips'].notna()        # True where TIPS was available
# Filter to rows where all 8 series are present:
df[df['n_macro_null'] == 0]
```

---

## 3. Immutability and Append-Only Contract

These properties are enforced in code and tested in `tests/test_feature_store.py`.

**Once a row is written for an `as_of_date`, it is NEVER modified or deleted.**

- `append_snapshot()` in `ml/feature_store.py` is the **only write path**. There is no update path, no delete path, and no bulk-replace path.
- Before writing, `append_snapshot()` checks whether `as_of_date` already exists in the store. If it does, the function returns immediately — the existing row is left unchanged.
- **Idempotency guarantee:** calling `append_snapshot()` twice for the same `as_of_date` is a no-op. The first write wins.
- The parquet file grows by exactly one row per new IST trading day.
- Tests in `tests/test_feature_store.py` (classes `TestIdempotency`, `TestImmutability`) prove these properties explicitly: they write a row, attempt a second write with different values, then assert the stored row is unchanged.

### Carry-forward detection

IBJA fixes and Tanishq scrapes are not available every day (weekends, market holidays, scrape failures). When the latest available observation predates `as_of_date`, the last-known value is recorded unchanged and the observation-date stamp reflects the older date:

```
ibja_pm_916_asof_date < as_of_date   ⟺   ibja_pm_916 is a carried value
tanishq_22k_asof_date < as_of_date   ⟺   tanishq_22k is a carried value
```

When `asof_date == as_of_date`, the value was observed on that day. **No separate `is_carried` boolean is needed** — derive it at training time from the asof column. Training should filter or downweight carried-value rows for the label series.

### Schema version history

| Version | Change |
|---------|--------|
| 1 | Initial schema (2026-06-07). |
| 2 | Added `ibja_pm_916_asof_date`, `ibja_am_916_asof_date`, `tanishq_22k_asof_date` to complete the observation-date stamp pattern already present on macro fields. Existing rows (2026-06-07, 2026-06-08) were migrated once with their verified true observation dates. This was the only permitted exception to the immutability contract: the migration added correct provenance that was always factually true; no recorded observation value was altered. |
| 3 | Added `n_macro_null` (integer count of null values across the canonical 8 macro series). Patched 109 `backfill_yfinance` rows that had null `crude_wti`/`tips` because the macro cache only held ~5 days of history for those series at backfill time — true historical closes fetched from yfinance (CL=F, TIP) and written for all dates where data existed; genuinely missing dates left null (no imputation). `live_pit` rows were not touched. All 116 rows had `n_macro_null` computed and `schema_version` bumped to 3. |

---

## 4. Provenance Semantics

Each row carries a `source` value that records how it was produced.

### `source='live_pit'`

- Written by `capture_daily_snapshot()` in `ml/feature_store.py`.
- Called during the live CI pipeline (`check-price.yml`), after the macro fetch step, once per IST calendar day.
- **True point-in-time:** captures what was known at the moment of capture — macro closes as of the UTC timestamp of the run, the IBJA rate from the most recently completed fix, and the Tanishq price from the most recent successful scrape.
- Each price field carries an `_asof_date` stamp recording the actual observation date. When `ibja_pm_916_asof_date < as_of_date`, the IBJA value is the last-known fix (weekend/holiday carry); when equal, it was observed on that day. Same convention for Tanishq.
- These rows are the gold standard for eventual model training. Do not overwrite.

### `source='backfill_yfinance'`

- Written by `run_backfill()` in `ml/feature_store_backfill.py`.
- Reconstructed from yfinance historical adjusted closes. These are **NOT true PIT**: yfinance may return revised close prices that were not available on the historical date (corporate actions, data corrections). The `ibja_pm_916` column is accurate (sourced from the committed `data/ibja_rates.parquet`), but all macro columns reflect yfinance's current adjusted history at the time the backfill was run.
- Safe to use for warm-start experiments or exploratory analysis.
- **Do NOT use as the primary training set for a live-deployment model.** Revised closes can produce spurious accuracy that does not generalise to the live pipeline.
- `run_backfill()` will never overwrite a `live_pit` row. It also skips any `as_of_date` already present in the store, regardless of source.
- `tanishq_22k` is always `None` for backfill rows (historical scrapes were not retained).

---

## 5. How Labels Are Derived at Training Time

**Labels are NOT stored in the feature store.** This is intentional.

Storing `next_day_pm_916` at capture time would embed a future value in the row, creating look-ahead bias: at capture time on day D, the next day's PM fix is not yet known. By keeping labels out of the store and joining from `data/ibja_rates.parquet` at training time, every feature row is a genuine "what did we know on day D" record.

`data/ibja_rates.parquet` continues to accumulate daily via the live CI pipeline and is the authoritative source for label construction.

**Schema of `data/ibja_rates.parquet`** (190 rows as of 2026-06-07, shape `(190, 12)`):

| Column | dtype |
|--------|-------|
| `date` | object (ISO date string) |
| `fetched_at` | object |
| `am_999` | float64 |
| `pm_999` | float64 |
| `am_995` | float64 |
| `pm_995` | float64 |
| `am_916` | float64 |
| `pm_916` | float64 |
| `am_750` | float64 |
| `pm_750` | float64 |
| `am_585` | float64 |
| `pm_585` | float64 |

**Label construction at training time:**

```python
import pandas as pd
from math import log

ibja = pd.read_parquet("data/ibja_rates.parquet")
ibja = ibja.set_index("date").sort_index()

for row in feature_store_rows:
    D = row["as_of_date"]           # current date (IST), already in snapshot
    D_plus_1 = next_trading_date(D) # skip weekends and holidays

    current_pm = ibja.loc[D, "pm_916"]          # today's close (mirrors ibja_pm_916 in snapshot)
    next_pm    = ibja.loc[D_plus_1, "pm_916"]   # label: next trading day's PM fix

    direction  = 1 if next_pm > current_pm else 0   # binary: up (1) or down/flat (0)
    log_return = log(next_pm / current_pm)           # continuous label
```

**Why labels are not stored:**
- At the moment of capture on day D, the next day's PM fix does not yet exist. Storing it would require a deferred write — a second write pass that would violate the immutability contract, or a future overwrite that would corrupt provenance.
- The PIT join pattern is standard practice in production feature stores. It keeps the feature record self-contained as a "what was known" snapshot, and treats label construction as a separate supervised-learning concern.

---

## 6. Duty Events (`data/duty_events.json`)

`data/duty_events.json` is an **append-only** list of India gold import duty changes. Each entry has the following fields:

| Field | Type | Notes |
|-------|------|-------|
| `date` | str | ISO date of the policy change. |
| `event_type` | str | Always `"duty_change"` in current entries. |
| `direction` | str | `"cut"` or `"increase"`. |
| `magnitude_pct` | float \| null | Percentage-point change in duty rate. `null` if the exact split between customs duty and cess components is unverified. |
| `note` | str | Free-text context (budget announcement, observable price impact, caveats). |
| `source` | str | Canonical source for the event (e.g. `"Union Budget 2024-25 public announcement"`). |

**How it feeds the feature store:**

- At snapshot time, the most recent event with `date <= as_of_date` is found.
- `duty_change_active = True` for any snapshot within 30 calendar days of that event.
- `days_since_last_duty_change` is the exact calendar-day distance from that event to `as_of_date`.
- If no past event exists, `days_since_last_duty_change = 9999` and `duty_change_active = False`.

**Current entries:** one event — 2024-07-23, Union Budget 2024-25 duty cut. `magnitude_pct` is `null` because the exact split between basic customs duty and Agriculture Infrastructure Development Cess (AIDC) components was not independently verified; direction (cut) and significant local price impact are both verifiable from public record.

**To add a new event:** append a new JSON object to the array. **Never edit existing entries.**

---

## 7. Festival Calendar (`ml/calendar_events.py`)

Four Indian gold-buying festivals are tracked, with multi-year anchor dates covering 2022–2027.

| Festival | Window type | Window logic |
|----------|-------------|-------------|
| Akshaya Tritiya | ±3 days | `[anchor − 3, anchor + 3]` (7-day window) |
| Dhanteras | ±3 days | `[anchor − 3, anchor + 3]` (7-day window) |
| Diwali | ±3 days | `[anchor − 3, anchor + 3]` (7-day window) |
| Navratri | 9-night span | `[anchor, anchor + 9]` (anchor = first night; 10-day window) |

**API:** `get_festival_info(query_date: date) -> dict`

Returns a dict with three keys:

| Key | Type | Value |
|-----|------|-------|
| `is_festival_window` | bool | `True` if `query_date` falls within any tracked festival window. |
| `festival_name` | str \| None | Name of the matching festival. `None` if not in any window. |
| `days_to_next_festival` | int | `0` if currently inside a window; otherwise calendar days to the nearest upcoming anchor date on or after `query_date`. `9999` if all anchors are in the past. |

If a date falls within windows of multiple festivals simultaneously, the first match in source order (Akshaya Tritiya → Dhanteras → Diwali → Navratri) is returned. Currently, Dhanteras and Diwali anchor dates are 2 days apart and their ±3-day windows overlap; Dhanteras takes precedence.

---

## 8. Running the Backfill

The backfill reconstructs snapshots for 2025+ IBJA dates where no live-capture row is present. It is idempotent: re-running it on a store that already contains all dates is a no-op (prints `0 new rows written`).

```bash
# One-shot: reconstruct 2025+ snapshots from yfinance historical + committed ibja_rates
python -m ml.feature_store_backfill

# With a custom start date:
python -c "from ml.feature_store_backfill import run_backfill; run_backfill(start_date='2025-04-01')"
```

**Prerequisites:**

- `data/ibja_rates.parquet` — committed to the repo; already present.
- `data/macro_cache.parquet` — generated by running `python ml/macro.py` (fetches live yfinance data). If absent, the backfill runs in partial mode: macro columns are null, `partial=True`.

**Output:** prints `Backfill complete: N new rows written (M skipped)` on completion. Returns the count of new rows written.

**Safety guarantee:** `run_backfill()` will never write over a `live_pit` row. Any `as_of_date` already present in the store (regardless of source) is skipped unconditionally.
