# Roadmap

Primary metric going forward: **decision accuracy** — when the model says "wait, price will drop," does the next-5-day minimum drop ≥ ₹100 below today's price? MAE and directional accuracy kept for academic transparency only.

---

## ✓ Phase 0 — Operational cleanup (2026-05-14)

- PR #2 (checkout v4→v6): merged, runner 2.334.0 ✓, validated by scheduled bot push.
- PR #3 (setup-node v4→v6): merged, all steps green, validated by full workflow_dispatch.
- Roadmap #4 (rebase guard): `git pull --rebase origin master` added between `git commit` and `git push` in check-price.yml. Commit `4fef06e`. Validated live.
- **weekly-backtest.yml:** same push pattern without rebase guard — not yet fixed. Apply same one-liner if weekly run starts failing on push.

---

## ✓ Phase 1A — Tanishq history investigation (2026-05-14)

**Findings:**

- **Location:** Same URL (`tanishq.co.in/gold-rate.html`), same page. No separate endpoint needed.
- **Date range:** 31 daily entries visible (14-Apr-2026 → 14-May-2026). UI has 7/14/30-day period tabs — 30 days is the maximum; all data is pre-loaded in DOM, no lazy API calls for more history.
- **Granularity:** One reading per calendar day. Weekends carry forward Friday's rate (e.g., 09-05 Sat = same as 10-05 Fri). No intraday data.
- **Purity tiers:** All three (22K, 24K, 18K) present in `data-goldrate22kt/24kt/18kt` attributes on each `span.goldpurity-rate` row — identical mechanism to live scraper.
- **Format match:** History 22K for 14-05-2026 = ₹14,845. Live scrape = ₹14,845. ✓ Same retail-inclusive price.
- **Parseability:** Trivial. Same `querySelectorAll("span.goldpurity-rate[data-goldrate22kt]")` query, collect ALL results instead of just `[0]`. No API, no auth, no pagination.
- **No backend API found:** Data fully embedded in page HTML. No deeper history accessible beyond 31 days.
- **Bot detection:** None observed.

**Corpus impact:**
- prices.json currently: 31 readings, 6 unique dates (2026-05-09 → 2026-05-14).
- Backfill would add 25 net new daily dates → 31 total unique daily dates.
- Net effect: 5× expansion of real-reading corpus.

**Verdict: Middle case. Recommend Phase 1C immediately after Session B.**
The backfill is trivial (30 min) and multiplies our real-data corpus 5×. Not a substitute for IBJA reseed (2 years of data, Phase 2), but provides 31 real Tanishq retail reference points from our actual scrape source before Phase 2 lands.

---

## ✓ Phase 1B — Session B: Model simplification (2026-05-14)

1. **Roadmap #5 (B1): Drop TFT + N-BEATS from prod inference (ml/inference.py)**
   - `MIN_REAL_READINGS_FOR_NBEATS = 1000`, `MIN_REAL_READINGS_FOR_TFT = 2000`, `MIN_REAL_READINGS_FOR_WARMUP_CLEAR = 30` added as module-level constants
   - TFT and N-BEATS removed from hot path; both print gate message and skip
   - LightGBM-only CI from p10/p90 quantile models
   - `training_rows` fixed: was hardcoded 0, now read from `lgbm-meta.json` (`n_train=361`)
   - `warmup` threshold fixed: was hardcoded `< 56`, now `real_readings_count < 30`
   - New forecast.json schema: `model_version=lgbm-only`, `nbeats_available=false`, `ensemble.method=lgbm_only`, `excluded_reason=data_gate`, thresholds self-documented in JSON
   - Commit: `306d5ad`

2. **Roadmap #10 (B2): Warmup banner in UI**
   - `#warmup-banner` added inside `.freshness-pill` (flex-wrap: wrap → full-width row above updated span)
   - Banner: "⚠ Model in warmup — predictions unreliable until N+ real readings collected. Current: X."
   - Threshold and current count read from `fc.ensemble.min_readings_for_warmup_clear` and `fc.real_readings_count`; fallback to 30
   - Existing `forecast-warmup` card element also updated to use dynamic threshold
   - Commit: `4ee4af0`

**Validation:** CI run `253af6e` produced correct lgbm-only forecast.json: `training_rows=361`, `warmup=false` (58 real readings ≥ 30), all new ensemble fields present.

---

## ✓ Phase 1C — Tanishq history backfill (2026-05-14)

- Script: `scraper/backfill-history.js` (one-shot, not wired into CI)
- Merged 25 net new daily readings (2026-04-14 → 2026-05-08) into prices.json
- prices.json: 31 → 56 rows; 6 → 31 unique dates (5× corpus expansion)
- Backfilled entries carry `source: "...?lang=en_IN (history backfill)"` for auditability
- Idempotent — second run inserts 0 entries ✓
- Weekend carry-forward observed: Tanishq holds Friday rate through weekends. Real behaviour, not a bug. Relevant for metrics phase — don't penalize direction errors on weekends.
- Commit: `da001d9`

---

## ✓ Session B.5 — Smart daily notification (2026-05-14)

- Script: `ml/daily_summary.py` — 5 triggers (T1 ≥2% daily, T2/T3 ±₹50 from 30d low/high, T4 ≥3% 5-day, T5 scrape gap); Groq commentary with template fallback; idempotency via `data/last_summary.json`
- Workflow: `.github/workflows/daily-summary.yml` — cron `30 10 * * *` (10:30 UTC = 4pm IST); `workflow_dispatch` for manual trigger; rebase guard before push; failure alert step
- IST date handling: `ZoneInfo("Asia/Kolkata")` throughout — not manual `timedelta(hours=5, minutes=30)`
- ASCII-only ntfy headers: `Rs.XX,XXX` format (`_fmt_inr_ascii`) — ₹ symbol (U+20B9) is non-ASCII and raises TypeError in HTTP headers
- 39 tests in `tests/test_daily_summary.py`, all passing
- Smoke test confirmed end-to-end: T3 triggered (BAND_30D=100 temporarily), Groq commentary delivered, ntfy push received on phone
- BAND_30D reverted to 50 after smoke test
- Commits: `a27eace` (implementation), `ead1a5e` (zoneinfo fix), `6b6a308` (smoke test BAND_30D), this commit (revert)
- Threshold tuning note: revisit ~2026-05-28; goal ~3–4 notifications/week. If T3 fires every other day, tighten to ±30. See `docs/DAILY_SUMMARY_DESIGN.md` for calibration rationale.

---

## ✓ Phase 2 — Session C: IBJA seed replacement (2026-05-14, partially complete)

**Original goal:** Replace Yahoo-derived seed with 2 years of real IBJA daily rates.

**Phase 0 investigation result:** Not feasible as specified. IBJA exposes only 4 dates on the main page and a 30-day PDF (binary FlateDecode format, not readily parseable). No JSON/REST API. Historical access beyond 30 days requires a paid subscription. goodreturns.in and goldpriceindia.in are both blocked (403 / ECONNREFUSED). The premise that IBJA had free 2-year historical access was incorrect.

**What was applied (Option A — partial fix):**
- `IMPORT_DUTY_BREAK_DATE = date(2024, 7, 23)` added to `ml/seed_history.py`
- `_retail_premium_for_date(d)` replaces the fixed `INDIA_RETAIL_PREMIUM = 1.15`
  - Pre-break: 1.15 (10% duty + 3% GST + 2% margin)
  - Post-break: 1.11 (6% duty + 3% GST + 2% margin)
- Validated: 1.11 gives ₹14,725 vs IBJA actual ₹14,762 on 2026-05-13 (0.25% gap)
- `data/history_seed.json` regenerated: pre-break unchanged, post-break ~3.48% lower
- Old seed archived as `data/history_seed_v1_uniform_premium.json`
- Calibration scale_factor: 0.9727 (was ~0.943) — less distortion on 2024 pre-break data
- Forecast unchanged (14,965): calibration was already handling absolute level; structural improvement is in training data shape
- 6 new tests added for time-varying premium boundary conditions
- Commit: `d6ed924`

**Remaining gap:** The 0.25% residual is handled by `_calibrate_seed`. The seed is still synthetic (Yahoo Finance estimated), not real IBJA data. The data quality improvement is real but narrow.

---

## Session C.2 — IBJA PDF accumulation (long-term, monthly cadence, ~2h setup + 5 min/month)

Build a `pdfplumber`-based parser for IBJA's 30-day PDF. Run once per month to accumulate
real IBJA reference rates. After 24 months the seed can be fully replaced.

- New script: `ml/ibja_pdf_ingest.py` — fetch the current 30-day PDF, extract rate table, merge into a running `data/ibja_historical.json` (separate from history_seed.json)
- PDF URL pattern: `https://ibjarates.com/UploadedFiles/30DaysPdf/Pdf_5587_YYYYMMDD...` — filename encodes a generation timestamp, not predictable. Workflow must fetch the live page first, extract the current PDF href, then download.
- Fields to capture: date, 916 PM (22K), 999 PM (24K), 750 PM (18K)
- Not urgent — Option B (passive Tanishq readings accumulation) handles short-term data quality on its own timeline. Prioritise only if retrain is imminent and IBJA corpus would materially help.

---

## ✓ Phase 3 — Metrics infrastructure (2026-05-14)

Design (Phase 3A) and implementation (Phase 3B) complete. Commits: `b9fd135` (design doc), `0ac925e` (implementation).

**What was built:**
- `ml/metrics.py`: `compute_decision` (Rule A delta≤-100), `resolve_outcome` (5-trading-day window, carry-forward exclusion), `aggregate_metrics` (decision accuracy, MAE, directional), `record_prediction` (idempotent daily write), `resolve_pending` (weekly resolution)
- `data/metrics_history.json`: accumulates pending entries daily; resolved weekly
- `check-price.yml`: `python -m ml.metrics --record` after commentary step
- `weekly-backtest.yml`: `python -m ml.metrics --resolve` before commit
- UI accuracy card: client-side aggregation from metrics_history.json; "collecting" state until first resolved entry
- `tests/test_metrics.py`: 17 tests, all passing; 257 total

**Key decisions (see docs/METRICS_DESIGN.md for full rationale):**
- Rule A: `delta ≤ -100` → "wait". Threshold revisit: ₹20,000+ or 5 years from 2026-05-14.
- No bootstrap: LightGBM retrains from scratch on every inference — retrospective eval measures memorization.
- No pre-aggregated summary file: UI computes client-side from metrics_history.json directly.
- actual_next_22k stored in each entry on resolution so UI needs only metrics_history.json.

---

## ✓ Session C.3 — Model audit & UI honesty (2026-05-15)

**UI honesty (Part 1):**
- `val_mae`, `naive_mae`, `model_status`, `min_readings_for_model_improvement` added to `forecast.json`
- `#model-status-banner` added to sticky header in `index.html`; shows when model is not clearly beating naive
- `model_status` states: `beating_naive` (ratio < 0.99), `matching_naive` (≤ 1.01), `trailing_naive` (> 1.01)
- Current status: `matching_naive` (val_mae=184.4 vs naive_mae=181.1, ratio=1.018 — within statistical noise at n=64)

**LightGBM hyperparameter audit (Part 2):**
- Identified 4 over-fit-risk parameters: num_leaves=31→16, lr=0.05→0.02, feature_fraction=0.9→0.6, bagging_fraction=0.8→0.7
- Added `min_data_in_leaf=40` and `lambda_l2=1.0` (both were unset; code wasn't reading them from config — fixed `_make_lgbm()`)
- Retrain with lambda_l2=2.0 caused feature collapse (2 features used); pulled back to 1.0
- Final config: `configs/model/lightgbm.yaml` — committed as `tune(lgbm): regularize for small-data regime (367 rows × 44 features)`
- New production meta: `best_epoch=1, val_mae=184.4, naive_mae=181.1, n_train=367, n_val=64`
- Note: best_epoch=1 is data-bound (367 rows, noisy delta target), not a tuning failure

**Feature inventory (Part 3):**
- `docs/FEATURE_INVENTORY.md` generated: all 44 features categorized by source and usage
- ACTIVE: 40 features (split ≥ 1 in any model)
- dead_weight (never split in any model): `hour`, `akshaya_tritiya`, `dhanteras`, `regime` (4 features)
- `docs/FEATURE_IMPORTANCE_2026-05-14.md` also added (gain-based cross-model report from earlier in session)
- LightGBM API crash (STATUS_STACK_BUFFER_OVERRUN) on p10/p90 model load; worked around by parsing model text files directly

---

## Phase 4 — Tier 2 features (PAUSED until ~200 real readings)

**Gate:** `real_readings_count ≥ 200` (estimated ~2026-07-15 at 4 readings/day).
**Re-audit trigger:** re-run feature inventory at that point; only then consider adding/dropping features.

When gate is cleared, items in priority order:
- Drop `hour`, `akshaya_tritiya`, `dhanteras` from `FEATURE_COLS` (confirmed dead weight)
- Investigate why `regime` is never split despite macro data being present
- Add Diwali and Gudi Padwa festival flags (akshaya_tritiya and dhanteras stay as placeholders until drops confirmed)
- 14-day realised volatility (roll_14d_std)
- Tanishq premium lag (Tanishq retail vs IBJA spot spread) — blocked by IBJA data access

Already confirmed as implemented (no action needed):
- dow, dom, month: ACTIVE in quantile models
- gold_usd_5d_vol: ACTIVE (top macro feature)

---

## ✓ Phase 5 — Tier 3 macro audit + adds (2026-05-17)

**Macro dead-weight audit:** All 24 MACRO_FEATURE_COLS are ACTIVE (split ≥ 1 in at
least one model). Nothing to drop from ml/macro.py. The 4 dead-weight features
(hour, akshaya_tritiya, dhanteras, regime) are in FEATURE_COLS — gated until
real_readings_count ≥ 200 (~2026-07-15).

**India VIX (`^INDIAVIX`):** ✓ Accessible via yfinance. Added as `india_vix_level`
to TICKER_MAP, MACRO_FEATURE_COLS (25 features, was 24), and MINIMAL_FEATURE_COLS
(9 features, was 8). Commit: `04183fa`.

**India 10Y government bond yield:** ✗ Not accessible via yfinance. Exhaustively
tested: ^INTEN, IN10Y=RR, 0IN10YT=RR, IN10YT=RR, GIND10YR.NS, IN10Y.NS,
GSEC10.NS, IN10YG=XX, ^INBMK10Y, GSBW10Y=RR, INR10Y=RRPS, GSEC10YR.BO — all
return 404 or no data. No free API path identified; requires paid data vendor.

**DGFT monthly gold import volume:** ✗ Not accessible via free API. Data published
as PDF/Excel on dgft.gov.in only; no JSON/REST endpoint.

---

## Phase 6 — Deep model re-introduction (eventually)

Re-introduce N-BEATS/TFT via champion/challenger when:
- N-BEATS: real_readings_count ≥ 1,000
- TFT: real_readings_count ≥ 2,000

---

## Remaining roadmap items (not yet scheduled)

- #7: Add fallback scrape source (goodreturns.in or IBJA) as secondary in scraper/scrape.js
- #8: Pydantic schema validation at prices.json write boundary
- #9: Interval coverage metric (% of actuals inside p10–p90)
- #14: GROQ key rotation reminder (quarterly)

---

## Held Dependabot PRs

- **PR #15:** pyarrow >=13.0→>=24.0.0. Spans 11 major releases, no CVE forcing urgency, parquet round-trip untested. Currently installed at 19.0.1. Revisit when: (a) security advisory lands on pyarrow <24, or (b) time to test parquet read/write on real data files. Last checked: 2026-05-14 — no CVE.

---

## Deferred ML migrations

- **MLflow 3.x migration:** rewrite promotion.py and test_promotion.py to use registered-model aliases instead of stages. Removes deprecated API (`transition_model_version_stage`, `get_latest_versions(stages=...)`). Estimated effort: 1 focused session.

---

## Behavior notes for CC in future sessions

- When a git command fails (rebase, push, etc.): surface the failure and propose alternative. Do NOT silently switch strategies (e.g. rebase → merge fallback).
- Diagnostic ranking: pull actual logs before ranking hypotheses. The May 2026 stale-scraper diagnostic put "workflow disabled" and "IP block" above the actual cause (₹ symbol in HTTP header at update-and-notify.js:47), which was visible in the Actions log all along.
- Do NOT use `cast()` for mypy fixes — use TypedDict or `assert isinstance` instead. cast() bypasses mypy silently; if the dict shape changes, the cast lies and produces a runtime crash instead of a mypy error.
