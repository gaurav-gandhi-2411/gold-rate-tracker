# Gold Rate Tracker — Engagement Progress

> Living document. Updated by CC at end of every phase. External consultant reviews; GG approves direction.

## Engagement Goals
- Production-ready gold price predictor on free tier (GitHub Actions + Pages + Groq + ntfy).
- Beat naive baseline on walk-forward backtest with statistical significance.
- Use 2024-2026 SOTA where it serves the use case — no outdated methods.
- Maintain honest-baseline reporting and ADR discipline already established.

## Current State Snapshot (as of 2026-05-18)
- Branch audited: `fix/lint-code-review-errors`
- Live forecast: predicted_22k = ₹14,410, model_status = "matching_naive", warmup = true
- Backtest: model MAE ₹225.33 vs baseline MAE ₹167.36 (34.6% gap, model **worse**)
- Real readings: 71 | Synthetic seed rows: 444 | Combined training rows: 454
- Production stack: GitHub Actions cron (6h) → Playwright scrapes Tanishq → LightGBM retrains from scratch → Groq LLM commentary → git push → GitHub Pages PWA reads JSON files directly

---

## Phase Log

### Phase 1 — Pre-engagement Audit  ✅ COMPLETE — 2026-05-18

**Goal:** Discovery only. Map the project, identify strengths and gaps.

**Deliverable:** Full audit report delivered in-session (see PR #9 description for full text).

**Top 5 strengths:**
- Honest baseline reporting enforced end-to-end: `val_mae` + `naive_mae` written to `forecast.json` every run, surfaced in PWA header, codified in ADR 005, stated plainly in README
- Full walk-forward backtest (69 folds, expanding window, paired fold statistics) running automatically weekly via `weekly-backtest.yml`
- Feature engineering is leakage-free and unit-tested: time-based lags use `searchsorted` with strict backward lookup, rolling windows are right-aligned, target is a pure forward shift
- Zero-infrastructure ₹0/month stack: GitHub Actions + Pages + yfinance (unofficial, free) + Groq free tier + ntfy.sh — live and functional
- Multi-layer production monitoring: rolling 7-day drift check, forecast staleness (18h), data staleness (8h), macro cache age (7d warn / 14d hard fail), UptimeRobot HTTP+keyword, Sentry wiring in PWA (DSN placeholder — not yet active)

**Top 5 gaps:**
- [BLOCKER] Model 34.6% worse than naive on walk-forward backtest: MAE ₹225.33 vs ₹167.36 over 69 folds; blend weight averages 42% LGBM / 58% naive; live forecast is effectively "predict no change"
- [MAJOR] 86% of combined training corpus is synthetic: 444 of ~515 combined daily rows are estimated from Yahoo Finance GC=F × INR=X × time-varying premium — not real Tanishq retail data; distribution may not match
- [MAJOR] No pinned dependency versions in CI: `ml/requirements.txt` uses `>=` specifiers throughout; yfinance has prior column-schema instability that already required a multi-path workaround in `macro.py:L108–138`
- [MAJOR] `inference.py` (live CI hot path, runs every 6h) has no dedicated unit or integration test; `continue-on-error: true` in workflow means a regression produces a silent bad forecast
- [MINOR] Regime feature (2-state Gaussian HMM on gold_usd log-returns) has zero splits in all three LightGBM models; root cause unresolved; the HMM fits and runs but the `regime` column contributes nothing

**Verdict:** High confidence on topology, data layer, CI serving path, and monitoring — all findings are grounded in direct file reads and committed data files. Confidence on modeling performance is moderate: the backtest numbers come from `data/backtest.json` (last run 2026-05-17T05:42:19 UTC), not freshly computed; the 69-fold result is consistent with `forecast.json` blend weights and is treated as authoritative. The synthetic-data quality risk is real but its magnitude is unquantifiable without a pure real-data holdout — that test becomes possible around 200 real readings (~2026-07-15). The dominant uncertainty is whether the model improves naturally as real readings accumulate (71 now, warmup clears at 100, meaningful signal expected at 200+) or whether the feature set and architecture need to change to establish signal at all.

---

### Phase 2 — Strategic Direction  ✅ COMPLETE — 2026-05-18

**GG's answers (received):**
- Q1 Target horizon: **5 days ahead.** Scraping cadence unchanged (6h: 10:00, 16:00, 22:00, 04:00 IST).
- Q2 Use case: **Dashboard + ntfy alerts** (see notification spec in Phase 3 §3.4).
- Q3 Synthetic seed: **Drop entirely.** Real data only.

**Consultant's direction (confirmed, not negotiable):**
- Primary forecaster: **Chronos-Bolt** (Amazon, zero-shot, HuggingFace, CPU-runnable).
- Target series: Tanishq 22K retail INR/g, 5-day forward at every 6h reading.
- Data stack: IBJA daily AM/PM 916-purity rates (real history) + MCX Gold near-month (backfill depth) + yfinance USD/INR + Tanishq live scrape. Forecast IBJA with Chronos; calibrate to Tanishq from 71 overlap readings.
- Drop synthetic seed (`history_seed.json`) entirely. Archive only.
- Retire TFT and N-BEATS. Remove ONNX artifacts in same PR as drop-synthetic.
- Keep all engineering discipline: honest baseline (`naive_5d`), walk-forward backtest at h=5, ADRs, drift monitoring, conformal PIs.

---

### Phase 3 — Implementation Plan  ✅ COMPLETE — 2026-05-19

#### 3.1 Data Layer Rebuild

##### 3.1.1 New scrapers / data sources

**Source A — IBJA daily rates (primary target series)**

| Field | Value |
|-------|-------|
| URL | `https://ibjarates.com/` — sole source providing both AM and PM in one request |
| Table selector | `table#TodayRatesTableDataYes` (server-side rendered, no JS required) |
| Fields extracted | Date, purity_916_am, purity_916_pm, purity_999_am, purity_999_pm, purity_995_am/pm, purity_750_am/pm, purity_585_am/pm |
| Frequency | Live scrape: once per 6h CI run. PDF backfill: 1st of each month (rolling 30 days). |
| robots.txt | ibja.co: `User-agent: * / Disallow: /cgi-bin/` — scraping allowed. ibjarates.com: HTTP 404 — no robots.txt, no restrictions. Both verified 2026-05-18. |
| Auth | None |
| New file | `ml/ibja.py` |

> **Why ibjarates.com (not ibja.co):** ibja.co was spec'd as primary but investigation (2026-05-18) confirmed it shows only the current session (AM **or** PM at a time, never both). The `id="lblHeaderTextForTimeUnit"` span explicitly states which session is active. ibja.co cannot yield dual AM/PM in a single request under any scraping approach. ibjarates.com is the sole source that provides both columns. This is a spec correction, not a substitution.

**Tier 2 — ibjarates.com 30-day PDF backfill**

Each CI run of the monthly backfill workflow:
1. Fetches the live ibjarates.com HTML
2. Extracts the dynamic PDF URL via regex: `href="([^"]*30DaysPdf[^"]*\.pdf)"`
3. Downloads and parses with `pdfplumber` (positional columns, verified 2026-05-18)
4. Appends non-duplicate rows to `data/ibja_rates.parquet`

PDF structure (verified with live download 2026-05-18):
- 1 page, 34 rows (2 header + 32 calendar days)
- 13 columns: Date + 5 gold purities × AM/PM + Silver 999 × AM/PM
- Weekend rows: `r[1]` in {'SAT','SUN'}; holiday rows: 'Holiday' in `r[1]`
- ~21 trading days per PDF; date format `DD-Mon-YY` (e.g. `18-May-26`)
- Values in Rs per 10g for gold, Rs per kg for silver

**Tier 3 — Deep historical backfill (deferred, see §3.7)**

**Source B — yfinance USD/INR + Gold-USD spot (macro features, keep)**

Already wired in `ml/macro.py`. No change. Used as covariates in the LightGBM residual head (Phase 4 stretch); not needed for Chronos.

**Source C — Tanishq live scrape (keep as-is)**

`scraper/scrape.js` — Playwright scraping Tanishq. No change. This remains the ground-truth retail series; IBJA is the modeled series.

##### 3.1.2 Storage schema

One Parquet table (committed, reference data, ~10–20 KB; updated each CI cycle):

**`data/ibja_rates.parquet`**

| Column | Type | Description |
|--------|------|-------------|
| `date` | `str` | ISO date `YYYY-MM-DD` |
| `fetched_at` | `str` | UTC ISO-8601 timestamp of fetch |
| `am_999` | `float64` | IBJA 999-purity AM rate (INR/10g) |
| `pm_999` | `float64` | IBJA 999-purity PM rate (INR/10g) |
| `am_995` | `float64` | IBJA 995-purity AM rate |
| `pm_995` | `float64` | IBJA 995-purity PM rate |
| `am_916` | `float64` | IBJA 22K AM fix (INR/10g) |
| `pm_916` | `float64` | IBJA 22K PM fix (INR/10g) — **primary modeled series** |
| `am_750` | `float64` | IBJA 750-purity AM rate |
| `pm_750` | `float64` | IBJA 750-purity PM rate |
| `am_585` | `float64` | IBJA 585-purity AM rate |
| `pm_585` | `float64` | IBJA 585-purity PM rate |

`data/mcx_gold.parquet` — **REMOVED** (MCX strategy dropped; see incident log).

> **Storage decision (PR E, post-hoc):** `data/ibja_rates.parquet` was initially gitignored (§3.1.4 plan). Un-gitignored in PR E after CI had no historical IBJA context (only 1 row — the live append). Historical context cannot be regenerated from live scrape alone; a 21-row seed committed to the repo gives Chronos meaningful context immediately. Same architectural pattern as the prior MCX-parquet decision (PR C). See Decision Log 2026-05-19.

##### 3.1.3 Calibration layer

Tanishq retail price = IBJA-916-PM × `premium_factor` + `fixed_markup`

With 71 overlap readings (2026-04-14 to 2026-05-17), fit a robust regression: `tanishq_22k ~ ibja_916_pm`. Expected premium_factor ≈ 1.04–1.08 (GST 3% + making charges ≈ 1–5%). Simple ratio model is sufficient; R² should be ≥ 0.98.

> **Note:** No MCX-to-IBJA basis adjustment is needed. The calibration layer is purely IBJA-916-PM → Tanishq-22K. See §3.1.6 for the removed MCX basis section.

> **GST verification (completed PR D, 2026-05-19):** Tanishq displays **PRE-GST** price.
> Empirical sample over 21 aligned trading days (2026-04-17 to 2026-05-18):
> - Median ratio `tanishq_22k / ibja_916_pm_per_gram` = **1.017** (std = 0.015).
> - Spot check 2026-05-18: tanishq_22k = ₹14,345/g, ibja_916_pm = ₹14,448.9/g, ratio = 0.993
>   (depressed by a 2% IBJA drop that Tanishq had not yet propagated; median is representative).
> - Low-ratio outliers (2026-05-13: 0.960, 2026-05-18: 0.993) occur when IBJA moves sharply
>   intraday and Tanishq's published rate lags by one session. HuberRegressor handles these.
> - **Conclusion:** The calibration captures **markup only** (~1.7% over IBJA-916-PM). No GST component.
>   Expected `premium_factor` ≈ 1.01–1.03 once fit on 30+ pairs (not 1.04–1.08 as initially estimated).

> **Time-of-day alignment note (follow-up candidate, post-PR E):** Time-of-day alignment between Tanishq scrapes (10/16/22/04 IST) and IBJA fixes (AM ~09:30 IST, PM ~17:00 IST) is unaddressed in the initial calibration. Current implementation pairs by UTC date. Refinement candidate for a hardening PR after PR E: pair each Tanishq snapshot with the most-recent-available IBJA fix at that IST timestamp.

- **Fit:** `sklearn.linear_model.HuberRegressor` on (ibja_916_pm, tanishq_22k) pairs aligned by UTC date. HuberRegressor is robust to the occasional outlier caused by Tanishq promotional pricing or GST recalculation events — these would inflate OLS slope estimates.
- **Residual monitoring:** After every refit, compute `residual_std = std(tanishq_22k - predicted)`. Log this value to `data/calibration.json`. If `residual_std` at next refit is > 2× the baseline value from the initial fit, log a WARNING (printed to stdout; visible in CI logs). This signals calibration drift worth investigating.
- **Refresh cadence:** Refit when 10+ new overlap readings have accumulated since last fit. Fit cached in `data/calibration.json` (committed; small).
- **Fallback:** If IBJA unavailable, use last known `premium_factor` from `data/calibration.json`. Log warning.
- **New file:** `ml/calibration.py`

```python
# calibration.py — public API
def fit_calibration(ibja_df: pd.DataFrame, tanishq_df: pd.DataFrame) -> CalibrationParams
def apply_calibration(ibja_forecast: np.ndarray, params: CalibrationParams) -> np.ndarray
def load_calibration(path: Path) -> CalibrationParams
def save_calibration(params: CalibrationParams, path: Path) -> None
```

##### 3.1.4 File-level changes

| File | Action | Detail |
|------|--------|--------|
| `ml/ibja.py` | **ADD** | Live scraper + PDF backfill + Parquet cache manager for IBJA rates |
| `ml/calibration.py` | **ADD** (PR D) | HuberRegressor calibration layer (IBJA→Tanishq) |
| `data/history_seed.json` | **MOVE** | → `archive/history_seed_synthetic.json` |
| `data/history_seed_v1_uniform_premium.json` | **MOVE** | → `archive/history_seed_v1_uniform_premium.json` |
| `ml/seed_history.py` | **DELETE** | No longer needed; move to `archive/scripts/seed_history.py` |
| `.gitignore` | **EDIT** | Add `data/notification_state.json`; `data/ibja_rates.parquet` removed from .gitignore in PR E (reference data, committed) |
| `ml/mcx.py` | **NOT ADDED** | Dropped — no automated INR MCX source available without Selenium |
| `ml/basis.py` | **NOT ADDED** | Dropped — single-series IBJA strategy eliminates basis adjustment requirement |

##### 3.1.5 Migration: keeping live CI intact

A `FORECAST_ENGINE` GitHub Actions variable (not secret) gates the inference path:

- **`FORECAST_ENGINE=legacy`** (default during PRs A–G): current LightGBM path runs unchanged. IBJA data fetch is an additive CI step (`continue-on-error: true`).
- **`FORECAST_ENGINE=chronos`** (set in PR H): new Chronos path becomes active.

The flag is read inside `ml/inference.py` via `os.environ.get("FORECAST_ENGINE", "legacy")`. Live CI never breaks for more than one cycle: if the new path raises, it logs and falls back to the legacy path within the same run.

**T5 fallback behaviour:** When the Chronos path raises and the legacy LightGBM path runs instead, two things happen atomically before `inference.py` exits:
1. `forecast.json` is written with `"model_fallback": true` (field added to schema in PR E).
2. The next `check-price.yml` step that calls `ml/notifications.py` reads `model_fallback=true` and fires **T5** (see §3.4.2). This ensures silent fallbacks are surfaced as a low-priority alert within the same CI cycle.

##### 3.1.6 MCX-to-IBJA basis adjustment

**Section removed 2026-05-19 — single-series IBJA strategy eliminates basis adjustment requirement. See incident log.**

The original §3.1.6 described a rolling median ratio between MCX-derived prices and live IBJA-916-PM rates. This section is no longer applicable because `ml/mcx.py` and `ml/basis.py` were not added. The IBJA parquet contains only real IBJA rates with no stitched proxy segment, so no basis correction is needed.

##### 3.1.7 Wayback Machine deep backfill (PR F.5) ✅ COMPLETE — 2026-05-19

**Goal:** Extend `data/ibja_rates.parquet` from 21 rows to 200+ via archived ibjarates.com captures, to produce a statistically meaningful h=5 backtest.

**Script:** `scripts/wayback_ibja_backfill.py` (one-shot; idempotent; branch `feat/pr-f5-wayback-ibja-backfill`)

**Two extraction modes:**

| Mode | Method | Yield | Notes |
|------|--------|-------|-------|
| A | Parse archived HTML snapshot (one row per capture) | ~73 new rows from 2022-2024 CDX captures | Required multi-strategy parser: 3-col (`table#TodayRatesTableDataYes`, 2024+) and 4-col (Metal\|Purity\|AM\|PM with Gold-only filter, 2022-2023). 10 failures (2 WinError 10061, 8 HTML parse). |
| B | Extract embedded 30-day PDF URL → fetch archived PDF → pdfplumber | ~83 rows from 2025-2026 PDFs | Only post-2025 PDFs were archived in Wayback. Used in Run 1 (prior context). |

**Run 1 (Mode AB, prior context):** Rows 21 → 104 (+83 via Mode B PDFs, 2025-2026). Mode A returned 0 rows — old parser couldn't handle 4-col format.

**Run 2 (Mode A only, 2026-05-19):** Rows 104 → 177 (+73 via fixed Mode A HTML parser, 2022-2024 + early-2025 + 2026-Q1).

**Final parquet state (177 rows):**

```
Date range:  2022-01-19 to 2026-05-18
Dense runs:  2025-04-15–2025-06-03 (34 rows), 2025-07-07–2025-08-08 (25 rows),
             2025-11-17–2025-12-16 (22 rows), 2026-04-17–2026-05-18 (21 rows)
Sparse:      2022-2024 (1-8 rows/month); max calendar gap 123 days
```

**Backtest re-run on 177 rows (165 folds):**

| Metric | Value |
|--------|-------|
| MAE Chronos | Rs.275.5 |
| MAE Naive | Rs.249.5 |
| Chronos vs Naive | **10.4% worse** |
| Wilcoxon p | **0.0089** (statistically significant) |
| Direction acc (h=5) | 55.8% (naive: 50%) |
| PI 80 coverage | 87.0% |
| Sub-30-context folds (22) | Chronos Rs.72 vs Naive Rs.76 (**Chronos slightly better**) |
| ≥30-context folds (143) | Chronos Rs.307 vs Naive Rs.276 (**Chronos 11.1% worse**) |

**Verdict:** Chronos-Bolt-Tiny does not outperform naive hold on IBJA-916-PM at any context size tested. The strong 2025-2026 uptrend (~Rs.85,000 → Rs.145,000) makes flat-hold very competitive. Directional accuracy at 55.8% shows weak but non-zero signal.

**Known limitation:** 2022-2024 rows are sparse (1-8/month); folds spanning that data have "h=5 actuals" separated by weeks, not days — the nominal horizon assumption is violated. Both Chronos and naive face identical conditions, so the relative comparison remains valid; absolute MAE values are not interpretable as 5-calendar-day errors for those folds.

**Ceiling reached:** Wayback CDX has 103 unique-day captures for `ibjarates.com/` (2022-2026). All have been processed. 2022-2024 PDFs were not archived. 177 rows is effectively the Wayback ceiling for this source.

---

#### 3.2 Model Pivot — Chronos-Bolt Primary

##### 3.2.1 Model choice: Chronos-Bolt-Tiny

**Variant selection:**

| Variant | Params | Safetensors size | Estimated CPU inference (single series, 5-step)† |
|---------|--------|-----------------|--------------------------------------------------|
| Tiny | 9M | 8.65 MB | ~2–5s |
| Mini | 21M | ~40 MB | ~5–10s |
| Small | 48M | 47.7 MB | ~10–20s |
| Base | 205M | 821 MB | ~30–60s |

†UNVERIFIED on GitHub Actions 2-vCPU / 7 GB runner. GPU benchmarks show 250× advantage over original Chronos; CPU estimates are 10–30× GPU times scaled from the A10G baseline. **PR 5 must include a CI timing probe (print wall-clock) before the flag is flipped.**

**Choice: Chronos-Bolt-Tiny** (`amazon/chronos-bolt-tiny`, HuggingFace).

Rationale:
1. 8.65 MB weights — downloads in < 1s in CI even on a cold runner; negligible cache footprint vs 821 MB for Base.
2. Zero-shot accuracy difference between Tiny and Base is marginal when context is 71–200 real readings — the model's uncertainty at this data volume exceeds the cross-variant gap.
3. Estimated 2–5s inference on CPU keeps the 6h CI run well within the 20-minute timeout even accounting for PyTorch install caching.
4. If Tiny underperforms, upgrade path to Mini/Small is a 1-line model ID change + re-run.

**Phase 4 upgrade path (not in Phase 3 scope):** If the Phase 3 walk-forward backtest shows `mae_5d_avg` plateauing above the 0.80 × `naive_5d_mae` target despite full context window, two multivariate successors are candidates: **Chronos-2** (Amazon, supports covariates; planned 2025–2026 HF release — verify availability at Phase 4 start) and **Moirai-MoE-Small** (Salesforce, probabilistic, multivariate, CPU-runnable at ~50 MB). Either would allow wiring in USD/INR and Gold-USD as covariates — the same series already fetched by `ml/macro.py`. Adoption decision: Phase 4, after Phase 3 backtest numbers are known.

> **⚠️ FLAG — PyTorch install overhead in CI.** `pip install chronos-forecasting` transitively installs PyTorch 2.x, Transformers ≥4.49, Accelerate ≥0.34, and einops. The default `torch` wheel includes CUDA (~2 GB). **Must install CPU-only torch first:**
> ```
> pip install torch --index-url https://download.pytorch.org/whl/cpu
> pip install chronos-forecasting
> ```
> CPU-only torch is ~280 MB. With GitHub Actions `pip` cache keyed on `ml/requirements.txt`, subsequent runs skip the download. Cold start adds ~3 min once; cached runs add ~30s. This is acceptable for a 6h cadence but must be confirmed in PR 5 timing probe.

**HuggingFace model pin:** pin to a specific commit SHA in `ml/requirements-inference.txt`:

```
# Chronos-Bolt-Tiny — pin to HF commit SHA for reproducibility
# Update deliberately; verify with: huggingface-cli download amazon/chronos-bolt-tiny --revision <sha>
CHRONOS_BOLT_TINY_REVISION = "a1b2c3..."  # placeholder; fill at PR 5 time
```

The revision is read at inference time: `ChronosBoltPipeline.from_pretrained("amazon/chronos-bolt-tiny", revision=CHRONOS_BOLT_TINY_REVISION)`.

##### 3.2.2 Input series and forecast horizon

**Chronos-Bolt is strictly univariate** — it takes a single target series and predicts future values of that same series. No covariate support in Chronos-Bolt (Chronos-2/Moirai are the multivariate successors; excluded here due to complexity and lack of established free-tier CPU benchmarks at this data volume).

**Input series fed to Chronos:** IBJA-916-PM daily rate series, forward-filled on weekends/holidays, UTC date index. Context window: last **365 calendar days** of IBJA history (≈ 260 trading days). Longer context allows Chronos to capture annual seasonality (Akshaya Tritiya, Dhanteras demand cycles) and multi-month trend patterns that are invisible in a 60-day window. Maximum 2,048 observations supported by Chronos-Bolt; we use 365 as the baseline. **Upgrade path:** extend to 730 days (≈ 2 years) if memory permits on the Actions runner — verify in the PR E timing probe. The value of the second year is in capturing year-over-year comparisons; the first year is the minimum for seasonal signal.

**Forecast output:** 5-day trajectory at quantiles q10/q50/q90 (Chronos returns full predictive distribution). Raw output is IBJA-916 levels. Apply `ml/calibration.py` → Tanishq-22K levels.

**New `forecast.json` schema (5d trajectory):**
```json
{
  "predicted_at": "2026-05-18T10:21:27Z",
  "current_22k": 14410,
  "horizon_days": 5,
  "forecast": [
    {"day": 1, "date": "2026-05-19", "p50": 14390, "p10": 14200, "p90": 14580},
    {"day": 2, "date": "2026-05-20", "p50": 14360, "p10": 14100, "p90": 14620},
    {"day": 3, "date": "2026-05-21", "p50": 14340, "p10": 14050, "p90": 14630},
    {"day": 4, "date": "2026-05-22", "p50": 14320, "p10": 14000, "p90": 14640},
    {"day": 5, "date": "2026-05-23", "p50": 14310, "p10": 13980, "p90": 14640}
  ],
  "model_version": "chronos-bolt-tiny",
  "model_status": "warmup|beating_naive|matching_naive|trailing_naive",
  "warmup": true,
  "real_readings_count": 71,
  "calibration_scale": 1.042,
  "val_mae_5d": null,
  "naive_mae_5d": null,
  "ibja_context_days": 365,
  "model_fallback": false,
  "basis_adjustment_applied": false
}
```

Legacy single-point fields (`predicted_22k`, `lower`, `upper`) kept as aliases computed from `forecast[0]` to avoid PWA breakage during migration.

##### 3.2.3 Inference pipeline

**New file: `ml/chronos_forecast.py`**

```python
# Public API
def load_chronos_pipeline(
    model_id: str = "amazon/chronos-bolt-tiny",
    revision: str | None = None,
) -> ChronosBoltPipeline

def forecast_ibja(
    pipeline: ChronosBoltPipeline,
    ibja_series: pd.Series,        # DatetimeIndex, daily, INR/g
    horizon: int = 5,
    num_samples: int = 20,         # samples for quantile estimation
    quantile_levels: list[float] = [0.1, 0.5, 0.9],
) -> pd.DataFrame                  # columns: date, p10, p50, p90 (IBJA levels)

def chronos_to_tanishq(
    ibja_forecast: pd.DataFrame,
    calib: CalibrationParams,
) -> pd.DataFrame                  # same schema, Tanishq 22K levels
```

**Location in `ml/inference.py`:** When `FORECAST_ENGINE=chronos`, `main()` calls:
1. `load_macro_features()` → IBJA series via `ml/ibja.py:load_ibja_series()`
2. `load_chronos_pipeline()` — model cached in `~/.cache/huggingface/hub/`; HF cache persists across CI runs via `actions/cache`
3. `forecast_ibja(pipeline, ibja_series, horizon=5)` → raw IBJA forecast
4. `load_calibration()` → `chronos_to_tanishq()` → Tanishq 5d curve
5. Write new `forecast.json` schema
6. Existing `ml/drift.py` and `ml/commentary.py` need minor update for 5d schema (day-1 value is the new single-point proxy)

**HuggingFace model cache in CI (`check-price.yml`):**
```yaml
- name: Cache Chronos model weights
  uses: actions/cache@v4
  with:
    path: ~/.cache/huggingface/hub
    key: chronos-bolt-tiny-${{ env.CHRONOS_REVISION }}
```

Cold start (first run after PR 5): ~35s download (8.65 MB) + ~3 min torch install. Subsequent runs: <5s cache restore.

> **Context handling adapted (PR E, 2026-05-19):** IBJA history at PR E merge is ~25–30 daily readings (21 from PDF backfill + days since PR C). The original plan assumed 365d context; that target is reached when daily IBJA accumulation catches up (~10–11 months). Chronos-Bolt is robust to short context; no truncation or padding applied. Minimum context enforced at 8 observations (`ValueError` below). Full 365d context per original plan reached gradually as the parquet accumulates.
>
> **Pinned revision (PR E):** `a0e552de83495b5c28c14c71c374f3e33280b340` (HuggingFace, last modified 2025-11-21). Update SHA in a dedicated chore commit when backtest confirms improvement.
>
> **Probe-only mode (PR E → PR H):** `run_probe()` writes `data/chronos_probe.json` each CI cycle. `forecast.json` unchanged. `FORECAST_ENGINE=legacy` until PR H. See ADR 009.

##### 3.2.4 Residual head (Phase 4 stretch — NOT in Phase 3 scope)

If Chronos-alone backtest shows `val_mae_5d > 1.5 × naive_5d_mae`, add a LightGBM residual corrector trained on `(chronos_p50_h5, usd_inr_change_1d, gold_usd_change_1d, dow) → actual_5d_delta - chronos_p50_h5`. Only promotes if 2% gate passes (ADR 003). Deferred to Phase 4.

##### 3.2.5 Retirement plan

**Files to DELETE in PR 2:**

| File | Reason |
|------|--------|
| `models/production/tft.onnx` (824 KB) | TFT retired |
| `models/production/nbeats.onnx` (1.36 MB) | N-BEATS retired |
| `models/production/tft-meta.json` | TFT meta |
| `models/production/nbeats-meta.json` | N-BEATS meta |
| `models/production/normalizer.json` | Used only by ONNX runners |
| `ml/nbeats.py` | N-BEATS pure Python module |
| `ml/models/nbeats.py` | N-BEATS forecaster class |
| `ml/models/tft.py` | TFT forecaster class |
| `ml/training/train_nbeats.py` | N-BEATS training script |
| `ml/training/train_tft.py` | TFT training script |
| `tests/test_nbeats.py` | N-BEATS unit tests |
| `tests/test_nbeats_forecaster.py` | N-BEATS forecaster tests |
| `tests/test_tft.py` | TFT tests |

**Files to EDIT in PR 2:**

| File | Lines affected | Change |
|------|---------------|--------|
| `ml/inference.py` | L29–31 (`MIN_REAL_READINGS_*`) | Remove; replace gating constants |
| `ml/inference.py` | L34–47 (`_PAST_COV_COLS`, `_FUTURE_COV_COLS`, `_ICL`) | Delete (TFT-only) |
| `ml/inference.py` | L55–71 (`_load_normalizer`) | Delete |
| `ml/inference.py` | L110–198 (TFT/N-BEATS input builders + ONNX runners) | Delete |
| `ml/inference.py` | L225–240 (`_load_model_maes`) | Remove TFT/N-BEATS entries |
| `ml/inference.py` | L304–308 (TFT/N-BEATS gate print) | Delete |

**Files to DELETE in PR H (after Chronos fully live):**

| File | Reason |
|------|--------|
| `ml/regime.py` | Dead-weight feature; LightGBM leaving hot path |
| `tests/test_regime.py` | Regime tests |
| `ml/forecast.py` | Superseded by `ml/chronos_forecast.py` + new inference path |
| `ml/compare_feature_sets.py` | LightGBM feature comparison script; no longer needed |
| `ml/tuning/study.py` | Optuna LightGBM tuning; no longer primary model |
| `ml/training/train_lgbm.py` | LightGBM training script (keep if residual head in Phase 4) |
| `configs/model/tft.yaml` | TFT config |
| `configs/model/nbeats.yaml` | N-BEATS config |
| `configs/model/ensemble.yaml` | Multi-model ensemble config |

> **CI entry point continuity (critical):** `check-price.yml` currently calls `python ml/forecast.py` (or equivalent) as the inference entry point. **PR E must update the CI step to call `python -m ml.inference`** (the unified entry point that branches on `FORECAST_ENGINE`). This ensures that when PR H deletes `ml/forecast.py`, the CI step does not break — it was already calling `ml.inference`, not `forecast.py`. Confirm the CI step change is included in PR E before PR H is opened.

---

#### 3.3 Target Redefinition

##### 3.3.1 Old vs new target

| | Old | New |
|---|---|---|
| Target | Next-reading delta (~6h or ~1d after resample) | 5-day forward price trajectory (h=1..5) |
| Series modeled | Tanishq 22K (direct, LightGBM) | IBJA 916 daily (Chronos) → calibrate to Tanishq |
| Unit | INR/g delta | INR/g levels |
| Output | Single point estimate + 80% PI | 5-day curve, q10/q50/q90 per day |

##### 3.3.2 New naive baseline: `naive_5d`

For each step h ∈ {1, 2, 3, 4, 5}: predict `price(t+h) = price(t)` (flat hold). Naive MAE@5d = mean(|actual(t+h) - price(t)|) averaged over h=1..5. This replaces the current `naive_1step` everywhere in `backtest.py`, `inference.py`, and `drift.py`.

##### 3.3.3 New metrics

| Metric | Description | Where computed |
|--------|-------------|----------------|
| `mae_h1..h5` | MAE per step | `ml/backtest.py` |
| `mae_5d_avg` | Mean MAE over h=1..5 | Primary summary metric |
| `mae_5d_naive` | Naive hold-flat MAE over h=1..5 | Always reported alongside |
| `dir_acc_5d` | Sign accuracy for h=5 vs current | `ml/backtest.py` |
| `pi_coverage_80_5d` | Fraction of actuals inside [p10, p90] at each h | `ml/backtest.py` |
| `decision_acc` | When model predicts ≥₹100 drop in 5 days, does min(actual h=1..5) deliver? | `ml/metrics.py` (existing, extend for 5d) |
| `peak_timing_err` | Median absolute error in days between the predicted peak (or trough) day and the actual peak (or trough) day within the 5-day window. Computed as: `median(|argmin(predicted_p50_h1..5) - argmin(actual_h1..5)|)` across folds. Captures whether the model is right about *when* the extreme will occur, not just *whether* it will. | `ml/backtest.py` (new) |

`mae_5d_avg` replaces `mae` as the primary headline metric in `forecast.json`, `backtest.json`, and the PWA display.

##### 3.3.4 Walk-forward backtest changes

| | Current (`ml/backtest.py`) | New |
|---|---|---|
| Forecast horizon | h=1 (next step) | h=5 (5-day trajectory) |
| Model | LightGBM (retrain each fold) | Chronos-Bolt (zero-shot; no retrain per fold) |
| Step size | 1 reading (~6h or 1d) | 1 calendar day |
| Train series | Combined Tanishq+seed | IBJA daily (calibrated to Tanishq for comparison) |
| Window | Last 90 calendar days | Last 180 calendar days (more history → more folds) |
| Min train size | 10 rows | 30 IBJA daily rows (Chronos needs context; calibration needs 10+ overlap readings) |
| Expected folds | ~69 | ~120 |
| Output | `data/backtest.json` | `data/backtest.json` (new schema with per-step MAE array) |

**Walk-forward protocol for zero-shot Chronos:**
1. For each fold date `t` in the test window:
   - `context = ibja_series.loc[:t]` (all history up to and including `t`)
   - `forecast = chronos.predict(context, horizon=5)` → IBJA p10/p50/p90 × 5 days
   - `tanishq_forecast = calibrate(forecast, calib_params)` → Tanishq p50 × 5 days
   - `actuals = tanishq_series.loc[t+1 : t+5]` → compare
2. No retraining at each fold — Chronos is zero-shot.
3. Calibration params fixed at fit time (using all overlap readings up to start of test window).

---

#### 3.4 Notification System

##### 3.4.1 New file: `ml/notifications.py`

Replaces the trigger logic in `ml/daily_summary.py`. Commentary generation stays in `ml/commentary.py`.

**Module public API:**

```python
# State management
def load_state(path: Path) -> NotificationState       # reads data/notification_state.json
def save_state(state: NotificationState, path: Path)  # writes data/notification_state.json

# Trigger evaluation
def check_triggers(
    forecast: ForecastPayload,       # loaded from data/forecast.json
    prices: list[PriceReading],      # loaded from data/prices.json
    backtest: BacktestResult,        # loaded from data/backtest.json
    state: NotificationState,
    now_ist: datetime,
) -> list[PendingAlert]

# Delivery
def send_pending(
    alerts: list[PendingAlert],
    state: NotificationState,
    now_ist: datetime,
) -> list[SentAlert]

def queue_for_quiet_hours(
    alerts: list[PendingAlert],
    state: NotificationState,
) -> NotificationState
```

**Data classes:**

```python
@dataclass
class PendingAlert:
    trigger_id: str           # "T1"|"T2"|"T3"|"T4"|"T5"
    title: str
    body: str
    priority: int             # ntfy priority 1–5
    tags: list[str]
    click_url: str
    queued_at: datetime       # IST
    bypass_quiet: bool        # True only for T4

@dataclass
class NotificationState:
    last_sent: dict[str, str]   # trigger_id → ISO timestamp of last successful send
    queued: list[PendingAlert]  # alerts held during quiet hours
    sent_today: list[str]       # trigger_ids sent in rolling 24h window
```

##### 3.4.2 Trigger specification and cooldown table

> **Canonical reference table — reproduce verbatim in `ml/notifications.py` as a module-level constant `TRIGGER_CONFIG`.**

| Trigger | Condition | ntfy Priority | Min Cooldown | Max per 24h (excl T4/T5) | ntfy Tags | Bypass quiet? |
|---------|-----------|---------------|-------------|----------------------|----------|---------------|
| **T1** Predicted 5d drop | `min(forecast p50 h1..5) ≤ current_22k − 100` AND `min(forecast p90 h1..5) < current_22k` AND `warmup=false` AND **`backtest_mae_5d_avg ≤ naive_mae_5d`** AND **`backtest_fold_count ≥ 60`** | high (4) | 24h | 1 | `decline,chart_with_downwards_trend` | No |
| **T2** Predicted 5d rise | `max(forecast p50 h1..5) ≥ current_22k + 100` AND `max(forecast p10 h1..5) > current_22k` AND `warmup=false` AND **`backtest_mae_5d_avg ≤ naive_mae_5d`** AND **`backtest_fold_count ≥ 60`** | default (3) | 24h | 1 | `rise,chart_with_upwards_trend` | No |
| **T3** Actual large move | `abs(current_22k − prev_22k) ≥ 150` (any price reading; model-agnostic) | urgent (5) if `abs Δ ≥ 300` else high (4) | 4h | 2 | `warning,chart_with_upwards_trend` | No |
| **T4** Weekly digest | Sunday 18:00 IST ± 30 min window | low (2) | 168h | 1 (unlimited vs T1–T3) | `newspaper,white_flower` | Yes — send at 18:00 IST regardless of quiet hours |
| **T5** Model degraded | `model_fallback=true` in `forecast.json` (Chronos path failed; legacy LightGBM ran instead) | low (2) | No fixed cooldown — max **once per IST calendar day** (fire on first occurrence per IST date only) | N/A | `warning,rotating_light` | No |

> **T1/T2 gating rationale:** The tighter gate (`≤ naive_mae_5d` vs the previous `< 1.5 × naive_mae_5d`) ensures T1/T2 only fire when the model is actually beating naive — not merely within 50% above it. The `backtest_fold_count ≥ 60` requirement ensures gating is based on a statistically meaningful backtest window (≥60 fold-days), not an early-run estimate with high variance.

> **T5 gating rationale:** T5 has no fixed cooldown because the failure condition (`model_fallback=true`) is already transient — it only appears in `forecast.json` for one CI cycle. The IST calendar-day dedup prevents T5 from firing on every 6h run during a prolonged outage (consistent with IST timezone used by T1–T4). T5 does not count toward the T1–T3 combined cap of 3 per 24h.

**Anti-spam:** T1 + T2 + T3 combined max = **3 per rolling 24h window**. T4 is exempt and does not count toward this cap.

**Global quiet hours:** 22:00–07:00 IST. T1, T2, T3 alerts triggered during quiet hours are **queued** in `NotificationState.queued`; the first `check_triggers()` call after 07:00 IST delivers them (if still ≤12h old; discard otherwise). T4 always delivers at 18:00 IST.

##### 3.4.3 State persistence

**File:** `data/notification_state.json` — **gitignored** (per-machine state, not part of repo).

**Rationale for gitignored:** The state file tracks cooldown timestamps. If committed, bot commits would clutter git history for every alert state change.

**GitHub Actions cache (primary persistence mechanism):**

The state file is persisted across CI runs via separate `actions/cache/restore` and `actions/cache/save` steps. The composite `actions/cache@v4` action cannot overwrite an existing key — using the same key for both restore and save would freeze state at the first run. The correct pattern keys each save on `run_id` and uses a prefix match to restore the most recent entry:

```yaml
# In check-price.yml — restore before notifications step
- name: Restore notification state
  uses: actions/cache/restore@v4
  with:
    path: data/notification_state.json
    key: notification-state-${{ github.run_id }}
    restore-keys: |
      notification-state-

# ... notifications step runs here, modifies data/notification_state.json ...

# In check-price.yml — save after notifications step
- name: Save notification state
  uses: actions/cache/save@v4
  if: always() && github.ref_name == 'master'
  with:
    path: data/notification_state.json
    key: notification-state-${{ github.run_id }}
```

**How it works:**
- Each run writes a new cache entry keyed on `run_id` (unique per run). On the next run, `restore-keys: notification-state-` prefix-matches and restores the most recently saved entry.
- Save is gated on `github.ref_name == 'master'`. PR runs and feature branches treat state as a cache miss and start fresh — no cross-PR state poisoning (e.g., a PR test run cannot corrupt the production cooldown timestamps).
- `if: always()` ensures state is saved even if the notifications step itself exits non-zero (partial send, network error).

**Notification state cache is master-branch only.** PR runs treat state as cache-miss and start fresh. This is intentional: PR CI runs should not inherit or mutate production alert state.

**Save trigger:** Always save after the notifications step completes, even if no alerts fired — the `last_sent` timestamps and quiet-hours queue must persist for cooldowns to function.

**Cache miss behaviour (cold runner, cache eviction after 7 days of inactivity, or non-master branch):** Treat as fresh state — no cooldowns enforced. Worst case: at most one duplicate alert per trigger per eviction event. Log `"notification_state: cache miss, starting fresh"` to stdout on first run.

**Schema (`data/notification_state.json`):**
```json
{
  "last_sent": {"T1": "2026-05-18T14:00:00+05:30", "T4": "2026-05-17T18:00:00+05:30"},
  "queued": [],
  "sent_today_triggers": ["T3"],
  "t5_last_fired_date_ist": "2026-05-18",
  "schema_version": 1
}
```

> `t5_last_fired_date_ist` (IST calendar date string `YYYY-MM-DD`) is the dedup key for T5's once-per-IST-day constraint. If `t5_last_fired_date_ist == today_ist`, T5 is suppressed regardless of `model_fallback` state.

##### 3.4.4 ntfy payload format

```python
{
    "url": f"https://ntfy.sh/{NTFY_TOPIC}",
    "method": "POST",
    "headers": {
        "Title": title,                    # ASCII-only; no ₹ symbol (KI-001 precedent)
        "Priority": str(priority),         # "4" not "high"
        "Tags": ",".join(tags),
        "Click": "https://gaurav-gandhi-2411.github.io/gold-rate-tracker/",
        "Content-Type": "text/plain",
    },
    "body": body.encode("utf-8"),
}
```

**Title templates (ASCII-safe — no ₹ symbol in headers):**

| Trigger | Title template |
|---------|---------------|
| T1 | `"Gold: Buy window? Drop of Rs.{drop} predicted in 5d"` |
| T2 | `"Gold: Price rising Rs.{rise} predicted in 5d"` |
| T3 | `"Gold: Rs.{abs_delta} {direction} detected ({pct}%)"` |
| T4 | `"Gold Weekly: {verdict} (22K: Rs.{price})"` |
| T5 | `"Gold forecast: Chronos failed, LightGBM fallback active"` |

**Body** (UTF-8 payload, ₹ symbol allowed here): 2–3 sentence summary including current price, prediction, and model status. Weekly digest body generated by `ml/commentary.py` (Groq).

##### 3.4.5 `daily_summary.py` overlap and deprecation

`ml/daily_summary.py` triggers T1–T4 using different thresholds and business logic. (T5 is new and has no precedent in `daily_summary.py`.) After PR 7 ships `ml/notifications.py`:
- `daily_summary.py` is marked deprecated (docstring + `# DEPRECATED: use ml/notifications.py`) in PR 7.
- Removed from `check-price.yml` in PR 7 (disabled, not deleted yet).
- Deleted from repo in PR 8 with confirmation that all trigger coverage is replicated.
- `tests/test_daily_summary.py` (39 tests) is removed in PR 8 alongside the source.

---

#### 3.5 Engineering Hygiene

| Item | Action | PR |
|------|--------|----|
| `tests/test_inference_main.py` smoke test | **ADD** — call `main()` with synthetic fixture (no live JSON), assert `forecast.json` keys present, PI positive and symmetric | PR 1 |
| Dependency lockfile | **ADD** `ml/requirements-inference.lock` via `uv pip compile ml/requirements.txt --output-file ml/requirements-inference.lock`. Update `check-price.yml` to use `pip install --no-deps -r ml/requirements-inference.lock` | PR 1 |
| `gitleaks` pre-commit hook | **ADD** to `.pre-commit-config.yaml` — `repo: https://github.com/gitleaks/gitleaks`, `rev: v8.x` (latest stable). Scans for secrets on commit | PR 1 |
| Dead `regime` feature | **DROP** — `ml/regime.py` deleted in PR 8 (LightGBM leaving hot path; regime was dead weight in all models per Feature Inventory). `tests/test_regime.py` deleted with it | PR 8 |
| WANDB stale env vars | **REMOVE** from `.env` (local change only; `.env` is gitignored). Add note to `docs/RUNBOOK.md` that WANDB is not wired | PR 1 |
| ADR 009 — Chronos pivot | **DRAFT** at `docs/adr/009-chronos-bolt-primary.md` — Status: "Draft pending Phase 3 build" | PR 5 |
| ADR 010 — Drop synthetic seed | **DRAFT** at `docs/adr/010-drop-synthetic-seed.md` — Status: "Draft pending Phase 3 build" | PR 2 |
| ADR 011 — Notification design | **DRAFT** at `docs/adr/011-notification-design.md` — Status: "Draft pending Phase 3 build" | PR 7 |

---

#### 3.6 Sequencing & PR Plan

Each PR is independently mergeable. CI remains green after every merge. `FORECAST_ENGINE` feature flag keeps the legacy LightGBM path live until PR 8.

| PR | Title | Contents | CI impact | Feature flag |
|----|-------|----------|-----------|-------------|
| **PR A** | `fix: engineering hygiene baseline` | Add `tests/test_inference_main.py`; add `ml/requirements-inference.lock`; add gitleaks to pre-commit; remove WANDB from `.env` note | None — additive only | None |
| **PR B** | `chore: retire TFT/N-BEATS and archive synthetic seed` | Delete ONNX artifacts + TFT/N-BEATS source + tests; simplify `ml/inference.py` (remove TFT/N-BEATS dead paths L34–240); archive `data/history_seed.json`; draft ADR 010 | `inference.py` code shrinks; CI unaffected (TFT/N-BEATS were already gated) | None needed — already gated |
| **PR C** | `feat(data): IBJA data layer (single-series)` | Add `ml/ibja.py` (live scrape + PDF backfill); add `monthly-ibja-backfill.yml`; update `.gitignore`; add IBJA fetch step in `check-price.yml`; add `tests/test_ibja.py` (14 existing + 15 new backfill tests). `ml/mcx.py` and `ml/basis.py` NOT added — see incident log. | IBJA fetch step added to CI; monthly PDF backfill workflow added; no inference change | `FORECAST_ENGINE=legacy` (default) |
| **PR D** | `feat(ml): Tanishq-vs-IBJA calibration layer` | Add `ml/calibration.py` (HuberRegressor, fit/apply/save/load/should_refit); `data/calibration.json` stub (`valid: false` — 21/30 pairs at merge); add `tests/test_calibration.py` (21 tests); GST verified: PRE-GST, median ratio 1.017 | No inference change | `FORECAST_ENGINE=legacy` |
| **PR E** | `feat(ml): Chronos-Bolt-Tiny inference path (probe-on, legacy-active)` | Add `ml/chronos_forecast.py` (load/forecast_ibja/chronos_to_tanishq/run_probe); add HF model cache + probe step in `check-price.yml`; add `tests/test_chronos_forecast.py` (17 mocked tests + 1 integration); add ADR 009; update `ml/requirements.txt` + lockfile (torch CPU-only); `data/chronos_probe.json` written each CI cycle | `ml/inference.py` untouched; `forecast.json` untouched; probe writes only `chronos_probe.json` | `FORECAST_ENGINE=legacy` |
| **PR F** | `feat(ml): walk-forward backtest at h=5` ✅ | Rewrite `ml/backtest.py` for h=5 Chronos protocol; update `ml/metrics.py` for 5d decision rule; run new backtest (9 folds, 21 rows), commit `data/backtest.json`; update `weekly-backtest.yml`. Results: Chronos MAE 5d avg Rs.319 vs Naive Rs.305 (4.6% worse). `insufficient_evidence=false`, `wilcoxon_p=0.1641`. See PR F verdict. | Backtest results updated in CI | N/A |
| **PR F.5** | `feat(data): Wayback Machine IBJA backfill` ✅ | Add `scripts/wayback_ibja_backfill.py` (Mode A HTML + Mode B PDF); extend `data/ibja_rates.parquet` 21→177 rows. Re-run backtest: 165 folds, Chronos MAE Rs.275 vs Naive Rs.249 (10.4% worse), Wilcoxon p=0.0089. Wayback ceiling reached (103 CDX captures fully processed). See §3.1.7. | `data/ibja_rates.parquet` extended to 177 rows | N/A |
| **PR G** | `feat: notification system` ✅ | Add `ml/notifications.py`, `tests/test_notifications.py`, `docs/adr/011-notification-design.md`, `docs/adr/012-naive-headline-chronos-companion.md`; wire into `check-price.yml` (restore/run/save notification state); disable `daily_summary.yml` (if: false) + mark `daily_summary.py` deprecated. T1/T2 use Chronos directional signal (dir_acc gate ≥0.55, lean ≥0.5%, momentum agreement). Dir acc last-30f: 0.633. | New ntfy alerts begin firing; daily_summary.yml disabled | `FORECAST_ENGINE=legacy` |
| **PR H** | `feat(ml): flip to Chronos + final cleanup` | Set `FORECAST_ENGINE=chronos` in `check-price.yml`; delete `ml/regime.py`, `ml/forecast.py`, `ml/compare_feature_sets.py`, `ml/seed_history.py`, `ml/daily_summary.py`, `ml/tuning/study.py`, `tests/test_regime.py`, `tests/test_daily_summary.py`; delete TFT/N-BEATS configs; update README architecture section | **Chronos becomes live production path** | Flag becomes permanent; variable removed |

**Ordering constraints:**
- PR B must land before PR C (archive seed before IBJA goes live to avoid dual-source confusion).
- PR D must land before PR E (calibration must exist before Chronos path can produce Tanishq-level output).
- **PR F depends on PR E** (the h=5 walk-forward backtest calls the Chronos inference path directly; the `ml/chronos_forecast.py` module added in PR E must exist before PR F's backtest rewrite can be tested end-to-end).
- PR E must land before PR H (Chronos path must be in the codebase, timing-validated, and CI entry point updated before the flag is flipped).

---

#### 3.8 Strategic Re-scope: Naive as Headline, Chronos as Directional Companion

**Trigger:** PR F.5 backtest results (165 folds, Wilcoxon p=0.0089).

**Evidence table:**

| Metric | Value |
|--------|-------|
| MAE Chronos (165 folds) | Rs.275.5 |
| MAE Naive (165 folds) | Rs.249.5 |
| Gap | Chronos 10.4% worse |
| Statistical significance | p = 0.0089 (significant) |
| Direction accuracy h=5 | 55.8% (last 30 folds: 63.3%) |
| Wayback ceiling | 177 rows (103 CDX captures fully processed) |

**Decision (ADR 012, 2026-05-19):** Naive flat-hold becomes the production headline forecast. Chronos is retained as a directional companion only — `chronos_probe.json` continues to be written each CI cycle as input for T1/T2 notification triggers.

**Revised PR G:** T1/T2 triggers use Chronos directional signal (`chronos_lean` from `chronos_probe.json`) gated on rolling 30-fold direction accuracy ≥ 0.55, not Chronos level forecast. Title language is explicit: "Model and momentum both lean [DOWN/UP] over next 5d."

**Revised PR H:** Naive path write to `predicted_22k` becomes explicit. Legacy LightGBM, `ml/daily_summary.py`, and dead-weight files deleted. Chronos probe continues running as directional companion.

**Phase 4 upgrade path (Chronos to headline):** Promotion criterion: ≥250-row backtest, `mae_5d_avg_chronos < mae_5d_avg_naive`, Wilcoxon p < 0.05. Expected data availability: 2026-09 to 2026-10.

---

#### 3.9 Appendix — Prompt Caching Audit (Post-PR G)  ✅ COMPLETE — 2026-05-19

**Scope:** Evaluate all LLM call sites for Anthropic prompt caching or Groq KV-cache eligibility.
**Branch:** `feat/prompt-caching-batch-paths` | **ADR:** `docs/adr/013-prompt-caching-scope.md`

**Verdict: Do not apply caching to the live path.** Three independent failure criteria, all failing:

| Criterion | `ml/commentary.py:call_groq()` | `ml/daily_summary.py:call_groq_summary()` |
|-----------|-------------------------------|------------------------------------------|
| Provider | Groq (not Anthropic) | Groq — DEPRECATED |
| Prompt tokens | ~280 (min 1024) | ~150 (min 1024) |
| Cadence vs TTL | 6h cadence vs 1h Groq TTL | Daily vs 1h Groq TTL |

**Deliverables:**
- `ml/llm_cache_helpers.py` — three helpers: `build_cached_system_prompt`, `estimate_groq_cache_eligibility`, `should_use_cache_for_batch` (three failure-reason branches)
- `tests/test_llm_cache_helpers.py` — 16 tests including five boundary cases for `should_use_cache_for_batch`
- `docs/adr/013-prompt-caching-scope.md` — NO decision with three failure criteria and three re-evaluation triggers
- Docstrings on both Groq call sites referencing ADR 013 by file path

**Re-evaluation triggers:** (1) Claude migration, (2) batch backfill path in Phase 4, (3) system prompt growth to ≥1024 tokens.

---

#### 3.10 Phase 3 Retrospective  ✅ COMPLETE — 2026-05-19

Phase 3 began as "find the best magnitude predictor" — a Chronos-Bolt-Tiny model that would outperform the naive flat-hold baseline on IBJA-916-PM 5-day forecasting. The 165-fold walk-forward backtest (p=0.0089) delivered an unambiguous verdict: Chronos is 10.4% worse than naive. Rather than deploying a worse model, Phase 3 pivoted to "find the best honest forecaster" — naming the naive flat-hold explicitly as the production headline, retaining Chronos only for its verified directional signal (55.8% direction accuracy, 63.3% on the last 30 folds). The production stack that emerged from this evidence-driven pivot is simpler, faster, and honest by construction: nine PRs merged, six ADRs enacted (009–014), all legacy LightGBM infrastructure deleted, and a live notification system with verified state persistence across CI cycles. The promotion criterion for Chronos to graduate to headline forecaster is concrete and testable at ≥250 IBJA rows (~2026-09 to 2026-10).

---

#### 3.7 Risks & Open Questions

**New risks introduced by the pivot:**

| Risk | Severity | Mitigation |
|------|----------|-----------|
| IBJA robots.txt may block scrapers (unverified) | Major | **CC verifies `ibja.co/robots.txt` (primary) and `ibjarates.com/robots.txt` (fallback) before PR C is opened.** Document findings in PR C description. If both block: fall back to MCX Bhavcopy for primary IBJA-substitute series; calibration layer unchanged. |
| Chronos-Bolt CPU inference latency on Actions runner is unverified (estimated 2–5s; could be 30s+) | Major | **PR E must include a wall-clock timing probe.** If > 15s, switch to Chronos-Bolt-Tiny with `num_samples=1` (deterministic mode) for P50 only; PI falls back to conformal. Flag PR E timing results before PR H. |
| PyTorch wheel download (280 MB CPU-only) inflates CI cold-start by ~3 min | Minor | `actions/cache` on `ml/requirements-inference.lock` hash; verify cache hit rate in PR E. |
| HuggingFace Hub unavailable during CI run (transient) | Minor | `continue-on-error: true` on model download step; fall back to legacy LightGBM path for that cycle (feature flag); T5 fires to surface the fallback. |
| ibjarates.com HTML structure changes break IBJA scraper | Major | Selector-based scraper (`table#TodayRatesTableDataYes`) + ntfy alert on scraper failure (existing pattern from `check-price.yml`). |
| ibjarates.com PDF filename pattern changes break backfill | Minor | Regex targets `30DaysPdf` directory + `.pdf` extension, not the timestamp portion. Only breaks if the directory structure changes. Monthly cadence limits exposure. |
| Tier 3 deep historical backfill deferred — Chronos zero-shot may need additional context if PR E shows poor performance on thin history | Minor | Decision deferred to post-PR E. Options: (a) Wayback Machine PDF extraction — 103 confirmed captures 2022–2026, each with a 30-day PDF link; note: archive.org may be unreliable from some hosts but is consistently accessible from GitHub Actions runners. (b) Paid IBJA API via indiagoldratesapi.com. Decision criteria: if Chronos PR E backtest shows `mae_5d_avg > 1.5 × naive_5d_mae`, revisit Tier 3. |
| Calibration model has only 71 observations at launch | Minor | 71 points is sufficient for a 1-parameter ratio; R² expected ≥ 0.98. HuberRegressor chosen for robustness to outliers. Refit threshold (10 new readings) will improve it quickly. Log calibration params and residual_std to `forecast.json` for auditing. |
| LightGBM residual head (Phase 4) may not clear the 2% promotion gate | Low | By design — it's a stretch goal. Chronos standalone is the primary; LightGBM is optional. |

**Open questions — all answered:**

1. ✅ **IBJA robots.txt:** **CC checks `ibja.co/robots.txt` manually** (primary) and `ibjarates.com/robots.txt` (fallback). Document findings in the PR C description. No legal review required before PR C — this is a pre-scrape due-diligence check, not a formal TOS review.

2. ✅ **MCX Bhavcopy backfill depth:** **730 days (2 years)** — run `--lookback-days 730` for the B1 one-time backfill. This is well within Chronos-Bolt's 2,048-observation maximum and gives the model two full annual demand cycles.

3. ✅ **Notification T3 vs existing drop alert:** **T1 replaces the existing `drop_threshold=100` alert in `check-price.yml`** entirely. The old `drop_threshold=100` config variable in `check-price.yml` is **retired in PR G**. T3 (₹150 actual move) is a new trigger — it is not a duplicate of T1 because T3 fires on *observed* moves (model-agnostic), while T1 fires on *predicted* 5d moves. No parallel run; no duplicates.

4. ✅ **PWA 5-day chart UI:** **Deferred to Phase 4.** During Phase 3 transition, the PWA continues to show the day-1 point estimate (computed from `forecast.days[0].p50`). The `forecast.json` backward-compatible aliases (`predicted_22k`, `lower`, `upper`) preserve existing PWA behaviour without any `app.js` changes in Phase 3.

5. ✅ **LightGBM residual head gate (Phase 4):** **Gate confirmed: `0.80 ≤ mae_chronos / naive_mae_5d ≤ 1.00`.** Attempt the residual head only when Chronos is beating naive (ratio ≤ 1.00) but within 20% of naive (ratio ≥ 0.80) — i.e., beating naive but with headroom to improve. If Chronos achieves ratio < 0.80 (>20% better than naive), residual head is unnecessary. If ratio > 1.00 (Chronos worse than naive), the residual head is not the right fix — revisit context window or model choice first. This gate is reflected in §3.2.4.

---

### Phase 4 — Build  🟡 SPRINT 2 IN PROGRESS — 2026-05-28
*Sprint 1 (PWA schema alignment, calibration gate prep, multi-sample Chronos probe + consensus gating) complete — see §4.1, §4.2, §4.3 below. Sprint 2 (notification cadence fixes, T7 floor) started — see §4.4. Main build (Chronos as headline) still awaits IBJA parquet ≥250 rows (~2026-09) and Chronos beats naive on re-run backtest. Entry criterion: promotion criterion in ADR 012 met.*

#### 4.1 PR Φ2 — PWA schema alignment (2026-05-22)

`app.js` migrated to the post-PR-H production schema: reads `forecast.headline.{predicted_22k, lower, upper, conformal_pi_half, naive_mae_recent_30}` with defensive fallbacks to back-compat top-level aliases, surfaces `forecast.chronos_companion` as a new methodology section (lean_direction, lean_strength_pct, direction_acc_30f, calibration_applied), migrates backtest reads from the phantom `bt.model`/`bt.baseline` structure to actual aggregate fields (`mae_5d_avg_chronos`/`naive`, `dir_acc_5d_chronos`/`naive`, `n_folds`, `wilcoxon_signed_rank_p`), drops the dead `warmup` and "trailing naive baseline" banners, replaces "Next-day forecast (LightGBM)" with "5-day forecast (naive flat-hold)", and adds a PI-band-width explanation per ADR 014. Verified end-to-end via headless-Playwright smoke test against the rendered DOM: zero JS page errors from app.js, all migrated fields populated (no `—` fallbacks), no "LightGBM" string in DOM. Pre-existing finding surfaced and deferred: Sentry CDN script at `index.html:200` returns HTTP 404 from jsdelivr — affects live site identically pre/post Φ2; guarded by `if (typeof Sentry !== "undefined")` so the page still works. **Hygiene fold-in (user-approved):** discovered during Φ2 CI that the probe writer in `ml/chronos_forecast.py` omitted a trailing newline, leaving `data/chronos_probe.json` failing pre-commit's end-of-file-fixer. Post-Φ1 master Lint was green; a subsequent `[skip ci]` cron run had rewritten the file in the bad shape and master silently drifted to a Lint-failing state. Fixed the writer (1-line `f.write("\n")`) and regenerated the data file through the fixed code path. Merge commit: `6fad6ae`.

#### 4.2 PR Φ3 — Calibration gate prep (2026-05-22)

Prepares for the IBJA↔Tanishq calibration unlock (~2026-06, when overlap pairs reach 30, currently at 21). Added `NotificationState.last_t6_fired_date_ist` field for once-ever T6 dedup (idempotent daily IST dedup pattern, mirrors T5; no separate prior-state cache needed). New `_check_t6(calibration, state, now_ist)` trigger fires once when `calibration.json.valid` first becomes True; `send_pending` stamps `last_t6_fired_date_ist` on T6 success. `check_triggers()` accepts a new `calibration: dict | None = None` kwarg positioned LAST in the signature — additive, backward-compatible, proved by a regression test that calls `check_triggers` without the kwarg and asserts T1–T5 behave identically. `_build_chronos_companion` in `ml/inference.py` accepts optional `notification_state` and emits `calibration_just_unlocked` in the chronos_companion block — True iff calibration is valid AND T6 has never fired. Process-isolation analysis (recorded in the PR body): inference and notification-evaluate are separate Python processes in `check-price.yml`; inference reads state at the start, the companion flag is True for exactly one cycle (the flip cycle), then `notification-save` persists the new state and subsequent cycles show False. Spec-vs-reality drift surfaced and disclosed: `weekly-backtest.yml` already had `workflow_dispatch:` (line 6, bare trigger, no required inputs); spec step (d) was pre-satisfied, file not touched. Merge commit: `402f69d`.

#### 4.3 PR Φ4 — Multi-sample Chronos probe + T1/T2 consensus gate (2026-05-28)

Closes the single-sample direction-noise vulnerability documented in PR E (DOWN 2.29% → UP 3.73% in 24h on unchanged context, CURRENT_STATE.md "Known issues"). The probe (`run_probe`) now calls `forecast_ibja` `DEFAULT_NUM_SAMPLES=5` times per cycle and aggregates: `majority_direction` = most-frequent label across the 5 samples; `direction_consensus` = count(majority)/5. T1/T2 in `ml/notifications.py` gate on `majority_direction in {up, down}` AND `direction_consensus >= 0.6` (3-of-5 minimum), with the existing magnitude gate (`strength >= 0.5%`) preserved as an independent threshold. v1-schema probes (no `majority_direction` field) cause T1/T2 to skip silently — intended degraded behavior during the one-cycle transition window after merge, covered by tests. Schema bumped v1→v2 with `num_samples`, `sample_directions`, `majority_direction`, `direction_consensus` (failure paths default these fields to 0/[]/"neutral"/0.0 so consumers can rely on field presence). ADR 015 documents rationale, the 3-of-5 threshold choice, alternatives considered, and an honest wall-clock measurement range. **Wall-clock reality check (recorded in ADR 015):** spec.md's "<2s budget" was a baseline misjudgment about model_load cost. Actual probe wall-clock is ~7.7–16s local Windows / ~10s on Ubuntu CI; dominant cost is `pipeline_load` (~10s = model deserialization), pre-existing and unaffected by Φ4. Φ4 scales forecast cost 5x linearly: ~75ms CI-extrapolated from 15ms baseline; ~100–330ms observed on local Windows depending on host load. HuggingFace weights cache key (`CHRONOS_BOLT_TINY_REVISION=a0e552de83495b5c28c14c71c374f3e33280b340`) unchanged by Φ4 — no cache invalidation, no cold-start per cycle. 6-hour cron cadence makes a ~10s probe operationally fine. Merge commit: `420f750`.

#### 4.4 PR Ψ1 — T4 cron-miss fix + T7 system-alive floor (2026-05-28)

Addresses two notification gaps discovered post-Sprint 1: T4 (weekly Sunday digest) had never fired in production, and no "heartbeat" trigger existed to confirm the pipeline was alive between significant market events.

**T4 root cause — GH Actions cron drift.** The old implementation fired T4 inside a ±30 min window centred on 18:00 IST (12:00 UTC). GH Actions free-tier scheduling consistently delays `0 */6 * * *` cron triggers 60–90+ minutes past the nominal time, pushing the 12:00 UTC run to 13:09–13:15 UTC (18:39–18:45 IST). The window end was 18:30 IST, so T4 structurally never fired. The new pattern replaces the time-window entirely with **IST-date-based dedup**: on Sunday ≥ 17:00 IST, fire if `NotificationState.last_t4_fired_ist_date` differs from today's IST date; on Monday, fire a `[Delayed]` recovery if Sunday's IST date was never recorded in state. `bypass_quiet=True` preserved (digest is time-sensitive). This pattern is robust to any cron drift up to ~24 hours.

**T7 — system-alive floor.** New trigger fires on the first CI run where `(today_ist - last_t7_fired_ist_date).days >= 3`. Skips silently when `probe.status != success` (T5 handles degraded-state reporting). Priority 2. Does NOT count toward the T1+T2+T3 combined 3-per-24h cap — it is a background heartbeat, not a market signal. State field `last_t7_fired_ist_date` follows the same IST-date-string dedup pattern introduced for T4.

**Scope:** `ml/notifications.py` and `tests/test_notifications.py` only. Test count: 49 (master baseline) + 17 new (5 T4, 11 T7, 1 state round-trip) − 1 renamed (`test_t4_no_fire_outside_window` → `test_t4_fires_sunday_late_run`, logic flipped to match new behaviour) = **65 total**. Merge commit: `06bfb1a`.

**Branch hygiene incident (PR #36 → #37).** The executor branched `feat/pr-psi1-notification-cadence-fix` from `chore/progress-md-sprint-1-summary` instead of `origin/master`. That chore branch's last master sync predated PR #32 (T6) and PR #33 (Phi4 consensus), so 19 tests from those merged PRs were absent from the Ψ1 branch. Detected during pre-merge review by diffing test names across branches; PR #36 was closed without merging and recreated as #37 from current master HEAD. The two Ψ1 commits were cherry-picked onto master HEAD, conflicts resolved by keeping both T6 (from master) and T4/T7 (from Ψ1) state fields. Rule formalised: **all new work branches must be cut from `origin/master` HEAD directly; verify branch base (`git merge-base HEAD origin/master`) before opening a PR.** This rule is cited explicitly in the Ψ2 prompt. See Decision Log for the formal entry.

#### 4.5 PR Ψ2A — PWA Chronos companion visibility fix (2026-05-28)

Fixes the user-facing bug where the Chronos directional signal (lean direction, direction accuracy, model lean) was invisible on the live PWA despite being correctly written to `data/forecast.json`. Diagnosis confirmed the root cause: `renderMethodology()` injected the entire Chronos companion block into `#methodology-body`, the body of a `<details>` accordion collapsed by default. No standalone visible section existed.

**Backend scope expansion (consultant-approved):** Diagnosis also found that `majority_direction` and `direction_consensus` — added to `chronos_probe.json` by Φ4 — were never propagated from the probe into `forecast.json.chronos_companion` by `ml/inference.py._build_chronos_companion()`. Since the PWA uses `forecast.json` as its sole data source and the Ψ2A Pass 2 spec required a consensus display, `_build_chronos_companion()` was updated to read both fields from the probe and include them in the returned dict (2 lines: `probe.get("majority_direction", "neutral")` and `float(probe.get("direction_consensus") or 0.0)`). Three tests added to `test_calibration_integration.py` cover v2 field propagation and safe defaults for v1 probes.

**Frontend:** New `<section id="model-signal-section">` added to `index.html` in the main visible page flow between the commentary card and price trend chart — never inside a `<details>` element. New `renderModelSignal(fc)` function in `app.js` runs unconditionally in `init()` without user interaction. Renders: lean direction (▲/▼/— with `--up`/`--down` color), strength %, direction accuracy % (integer-rounded), consensus count (from `direction_consensus * 5`, defaulting to `—` until next CI run updates forecast.json schema), calibration tag, and T7 floor note. CSS: single margin rule for `model-signal-section` reuses existing `.meth-stats`/`.meth-stat` grid classes. Methodology accordion Chronos section left intact for the "expanded view" use case.

**Visual verification:** Playwright headless screenshot confirmed `#model-signal-section hidden: false`, direction accuracy visible, "63%" value present, "Model signal" heading visible, lean direction "▲ Up" visible, history table 50 rows unaffected. Console errors: pre-existing Sentry CDN 404 (jsdelivr), identical pre/post Ψ2A, guarded by `if (typeof Sentry !== "undefined")`. Lint CI: pass (2m44s). Test count: 307 passed (290+ baseline + 3 new, 0 regressions). Merge commit: pending (#39).

#### 4.6 PR Ψ2B — UI/UX modernization sprint (2026-05-29)

Mobile-first visual redesign targeting iPhone 14 Plus (428×926). User explicitly disliked the History section and Price Trend chart; both were kept but redesigned. All data wiring, backend schemas, and buyer-perspective color semantics (terracotta=up=bad, sage=down=good) preserved unchanged.

**Three-pass execution:** Pass 1 produced a design direction proposal in session (visual audit of current PWA at 428/768/1280px via Playwright, study of tanishq.co.in/gold-rate.html reference site). Consultant approved with two adjustments: keep desktop history table 5-column, skip Today/Yesterday column (delta already covers it). Pass 2 implemented. Pass 3 verified all 15 criteria via Playwright screenshots at all three viewports.

**Design direction: "Signal over Noise."** Two distinct typographic voices: Fraunces (variable optical size, keeps) for price numbers — editorial warmth. Syne 700 (added via Google Fonts CDN) for section headings — architectural/geometric clarity. DM Sans continues for body text. Three-tier hierarchy eliminates the generic "everything is one font" look.

**Palette:** Dark mode `--gold` brightened from `#D4932A` to `#E09B2E` (crisper amber at 160px display size). `--surface-alt: #1C1710` added for desktop table alternating rows. All existing terracotta/sage semantic tokens unchanged. Sparkline SVG colors corrected to match CSS tokens (`--up`/`--down`) which were previously mismatched (#7BC48A vs #6a9a72 etc).

**History redesign (user's primary complaint):** Mobile: eliminated the `max-height: 420px; overflow: hidden auto` scroll-within-scroll container. New date-grouped timeline groups readings by IST calendar day with Syne uppercase dividers ("THU, 29 MAY · 4 READINGS"). Shows 3 days by default; "Show N more days" re-renders inline. Each card: time | price | delta in horizontal flex row — no 2-column grid. Desktop: kept all 5 columns (When/22K/24K/18K/Δ22K) per consultant direction; added alternating row tinting with `--surface-alt`. Column width tuned (first column 40%→35%). Today/Yesterday column from Tanishq reference study excluded — delta column already covers it, 6th column would crowd the table.

**Chart redesign (user's primary complaint):** Mobile aspect ratio fixed from `4/3` (571px tall) to `3/2` (259px tall) — eliminates the chart dominating >60% of viewport. Full-bleed treatment: removed `background`, `border`, `border-radius` from `.chart-wrap` on all viewports. Pass 3 verdict: "open and intentional" (not "detached and floating") — the gold line's visual weight + section heading + range toggle buttons provide sufficient framing without a card box. Gold line brighter (`#E09B2E`), thicker (2→2.5px), gradient opacity 0.30→0.40.

**Model signal redesign:** Replaced the repurposed `.meth-stats` grid (which made the signal look like methodology accordion overflow) with a dedicated `.signal-card` component. Left-border accent in `--up` (terracotta) or `--down` (sage) per lean direction. Syne 700 bold heading for the direction label (Up/Down), Fraunces for the arrow glyph. Stats row: direction accuracy · consensus count · calibration status in a single `flex-wrap` line. Methodology accordion still contains the full Chronos stats grid (unchanged).

**Stale data indicator:** `:has(.freshness--stale)` on `.utility-row` adds a terracotta background wash when data is >8h old. Previously this was invisible muted-gray micro-text for the most actionable warning on the page.

**Visual verification (Pass 3):** All 15 criteria pass at 428×926 (iPhone 14 Plus), 768×1024 (tablet), 1280×900 (desktop). No regressions on wider viewports. Borderless chart verdict confirmed "open and intentional" at all three widths.

**Files changed:** `index.html` (Syne font import), `style.css` (13 targeted changes — 120 insertions), `app.js` (4 render function rewrites — 112 insertions). Zero backend files touched (`ml/`, `data/*.json`, `.github/workflows/`, `scraper/`). Lint CI: pass (2m41s). Merge commit: pending (#40).

#### 4.7 PR Ψ2C — Design polish (2026-05-29)

Three targeted fixes closing out the Ψ2 series. Files changed: `app.js` (delta rendering, both mobile and desktop paths), `style.css` (chart padding, delta opacity). Zero backend files touched.

**Chart internal padding (Item 1):** `.chart-wrap` padding reduced from `20px` all sides to `12px 20px` desktop / `8px 16px` mobile. Chart canvas now fills ~90% of card height instead of ~60%; breathing room matches the page's existing card rhythm. Verified at iPhone 14 Plus, 768px, 1280px.

**Zero-delta de-emphasis (Item 2):** Zero-delta rows previously rendered `±0` in `--cream-mute` at full opacity — visually identical to "no data available" placeholders, read as broken. Changed to `·` (mid-dot) at `opacity: 0.45`. Applied in both mobile date-grouped history cards and desktop table Δ22K column. De-emphasis is intentional and clearly distinct from real deltas.

**Arrow+sign redundancy (Item 3):** Mobile history cards showed `↑ +₹70` / `↓ −₹70` (arrow and sign both present). Desktop table showed `+₹70` / `−₹70` (sign only, no arrow). Applied Option A (keep arrow, drop sign) consistently across both rendering paths: `↑ ₹70` / `↓ ₹70` everywhere. Arrow is the direction signal; compact ₹ without the redundant sign.

Lint CI: pass (2m46s). Merge commit: pending (#41).

**Ψ2 series COMPLETE.** Ψ2A (Chronos companion visibility), Ψ2B (UI/UX modernization), Ψ2C (polish) — all three closed.

#### 4.8 PR Ψ3A — Production health: SW cache invalidation, freshness strip float, lint CI backstop (2026-05-30)

Three production-health fixes. Files changed: `service-worker.js`, `index.html`, `style.css`, `.github/workflows/lint.yml`. Zero backend files, schemas, or data files touched.

**Item 1 — Service worker cache invalidation (`service-worker.js`, `index.html`).** Users had to manually clear browser cache to see the Ψ2B/Ψ2C redesigns on the live site. Two compounding root causes: (a) `VERSION = "v2"` was a static string — Ψ2B and Ψ2C did not touch `service-worker.js`, so the browser never detected a changed SW file, never ran a new install cycle, and never evicted `gold-shell-v2`; (b) no `registration.update()` call, so the browser only re-checked `service-worker.js` within its 24h internal cache window rather than on every page load. Fix: `VERSION` bumped to `"v3-20260530-a"` (new cache name forces old cache eviction on activate); `registration.update()` added after SW register; `controllerchange` listener added with a `refreshing` guard (one auto-reload per page lifetime when new SW takes over via `skipWaiting()`). A `CACHE INVALIDATION CONTRACT` comment at the top of `service-worker.js` documents all four parts — breaking any one reintroduces stale-cache symptoms.

**Item 2 — Freshness strip floating over content (`style.css`).** The `.utility-row` sticky strip (showing "Updated Nh ago · Bengaluru · Tanishq retail") visually merged with scrolling content beneath it. Root cause: `.utility-row:has(.freshness--stale)` overrode the base background with `rgba(198,106,75,0.18)` — 18% opacity; `.has(.freshness--warn)` was 12% opacity. With `backdrop-filter: blur(8px)`, 82–88% of underlying content showed through, making scrolled text legible behind the strip. Fix: stale/warn states now layer the tint colour over fully opaque `var(--ink)` using CSS multi-layer background (`linear-gradient(tint, tint), var(--ink)`). The `--ink` base is `#1A1612` (fully opaque), eliminating all bleed. Tint colour intent from Ψ2B preserved. `box-shadow: 0 1px 8px rgba(0,0,0,0.28)` added to the base `.utility-row` rule for visual separation at all scroll positions. Verified at iPhone 14 Plus (428×926 @3x): normal, stale ("STUCK — 20H AGO"), and warn ("STALE — 9H AGO") states all show fully opaque strip with no content visible behind it.

**Item 3 — [skip ci] suppressing master Lint runs (`lint.yml`).** Squash-merging PRs that contained `docs(progress)` commits with `[skip ci]` in the commit body propagated the annotation to the squash commit body, causing GitHub Actions to skip ALL push-triggered workflows on master (confirmed on Ψ2B #40 and Ψ2C #41). Investigation confirmed that GitHub Actions does not support push triggers that bypass `[skip ci]` — suppression is platform-wide per-commit for push events, with no per-workflow override. Option D as specified ("explicit Lint trigger on master push regardless of commit message content") is technically infeasible. What was implemented: a daily `schedule: cron '0 6 * * *'` (06:00 UTC, between bot cron windows) and `workflow_dispatch` added to `lint.yml` as defense-in-depth backstops, plus a new discipline norm (#13 in `CURRENT_STATE.md`) prohibiting `[skip ci]` in PR squash commit message bodies. The schedule is defense-in-depth, not the primary mechanism — norm #13 is. Residual gap: up to ~24h after an accidental `[skip ci]` squash merge before the daily backstop fires. This gap is acknowledged in the Decision Log.

#### 4.9 PR Ψ3B — PWA-as-App: iOS standalone update detection + refresh affordance (2026-05-30)

"PWA-as-App" sprint targeting iOS home-screen standalone mode. Files changed: `service-worker.js`, `index.html`, `app.js`, `style.css`, `CURRENT_STATE.md`. Zero backend files, schemas, data files, or CI workflows touched.

**Root cause diagnosis.** Ψ3A's `registration.update()` is called inside `window.addEventListener("load", ...)`. In browser mode every navigation fires a new `load` event — update check runs on every visit. In standalone mode the page stays loaded in WebKit memory when backgrounded; iOS resumes the process on foreground without firing `load` again. `registration.update()` is never called on forground return, so the browser never byte-checks `service-worker.js` after the initial load. A new deploy can happen while the app is backgrounded and the user returns to stale content with no mechanism to detect it.

**D1 — `visibilitychange` listener (root-cause fix).** `document.addEventListener("visibilitychange", ...)` calls `registration.update().catch(() => {})` whenever `document.visibilityState === "visible"`. This fires on every foreground return in standalone mode, giving the suspended SW a chance to byte-check `service-worker.js` and trigger the install→`skipWaiting`→`controllerchange`→reload chain that Ψ3A already wired. The existing `refreshing` guard in the `controllerchange` handler prevents reload loops regardless of whether the reload is triggered by the initial load path or the visibilitychange path.

**D2 — 30-minute periodic check (belt-and-suspenders).** `setInterval(() => registration.update().catch(() => {}), 30 * 60 * 1000)`. **Important limitation (Note 3 from consultant review):** iOS freezes JavaScript timers during process suspension. This interval only fires when the app is *continuously foregrounded*. D1 handles the dominant case (return from background); D2 covers only "app left open and foregrounded for 30+ consecutive minutes" — a rare pattern for a price checker. Kept because it is harmless and cheap; documented here to prevent it being elevated to a co-equal fix. D1 is the actual fix; D2 is belt-and-suspenders.

**D3 — Manual tap-to-refresh affordance (Option a: lightweight data re-fetch).** `↻` icon button added to the right side of the utility row (`.utility-right` cluster). Tap → spinner → re-fetches `prices.json` and `forecast.json` only (network-first via SW; `loadJSON` already appends `?t=Date.now()`); commits new data to `allReadings` only after both fetches resolve (FIX 2: local `fresh` variable prevents partial allReadings mutation on failure); re-runs all seven render functions. Backtest/commentary/drift not re-fetched (update every 6h via CI; stale for seconds on a manual tap). Button always visible in both browser and standalone mode — data refresh is useful in both contexts.

**D4 — Standalone mode detection.** `const IS_STANDALONE = window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true`. The `navigator.standalone` property is iOS WebKit's proprietary flag; `matchMedia` is the W3C standard. OR'ing both covers iOS (proprietary flag reliable) and Android (matchMedia reliable) without false positives on desktop (`navigator.standalone` is `undefined === true` is `false`).

**D5 — In-app iOS limitations panel (standalone-only).** `#pwa-help-panel` div below the utility row, hidden by default. In standalone mode: `?` icon button (`.pwa-help-btn`) is revealed by JS; tap toggles panel. Auto-opens when `IS_STANDALONE && ageH >= 12 && !pwaHelpDismissed` (inside `renderFreshness()`). FIX 1: `pwaHelpDismissed` module-level flag set `true` when user taps `✕`; auto-open never re-fires this session after explicit dismissal — prevents the frustration loop where following the panel's instructions (tap ↻) causes it to re-open if data remains stale because iOS didn't deliver fresh content. Manual `?` toggle does not set the flag. Copy: *"iOS limits how often home-screen apps update in the background. Tap ↻ to fetch the latest readings. If data remains stuck, open the App Switcher (swipe up, hold), swipe this app away, and reopen from Home Screen — that forces a full reload."*

**SW VERSION bump.** `"v3-20260530-a"` → `"v4-20260530-b"` per CACHE INVALIDATION CONTRACT. Ψ3B changes `index.html`, `app.js`, `style.css` — all shell assets. The bump is also the propagation mechanism: existing installed users get the D1 visibilitychange fix on first visit after deploy (VERSION change triggers new install → `controllerchange` → reload → fresh `index.html` with D1 wired).

**Apple-specific meta + CSS cleanup.** `apple-touch-icon` gains `sizes="180x180"` (B1); `body { padding-top: env(safe-area-inset-top) }` gains `, 0px` fallback (C1, consistency with all other safe-area rules); stale Yahoo Finance seed-data sentence removed from footer (B4 — factually wrong since PR B).

**CSS specificity fix (found in verification).** `.pwa-help-btn { display: flex }` in the author stylesheet overrides the HTML `[hidden]` attribute's user-agent `display: none`. Added `.pwa-help-btn[hidden] { display: none !important }` to restore correct hiding behavior. Caught by Playwright `isVisible()` test before PR was opened.

**Honest residual limitation (documented in CURRENT_STATE.md dead-ends).** Even with D1+D2+D3, iOS standalone PWA does not provide a developer-controllable 100%-reliable update mechanism. iOS can suspend the SW before `registration.update()` completes; the `controllerchange` reload may not fire. Platform-level workaround: App Switcher → swipe app away → reopen from Home Screen. Documented in CURRENT_STATE.md and in the in-app panel copy.

**Verification (local, pre-PR).** Playwright headless screenshots at 428×926, 768×1024, 1280×900: `↻` visible at all viewports, `?` hidden at all viewports (browser mode; `IS_STANDALONE=false`), `#pwa-help-panel` correctly hidden, utility row not crowded at 428px. Help panel open screenshot: amber tint, readable 47-word copy, `✕` dismiss button present. Pre-existing console errors (Sentry CDN 404, data/*.json 404 from local server) identical to prior PRs — not regressions. SW VERSION bump (`v4`) requires iPhone delete-and-reinstall test post-deploy to verify full chain.

**Ψ3B-hotfix (PR #44, 2026-05-30).** Production bug: `.pwa-help-panel { display: flex }` in the author stylesheet overrode the `[hidden]` attribute's UA `display: none`, causing the panel to render always-visible and the ✕ close handler to have no visual effect. Ψ3B had applied the same fix to `.pwa-help-btn` but missed `.pwa-help-panel`. Fix: one line added — `.pwa-help-panel[hidden] { display: none !important }`. Verified via `getComputedStyle().display` assertions (four-step cycle: load/open/?/close — all pass). Root cause of the miss: Ψ3B's verification checked `el.hidden` (DOM attribute) rather than computed style — attribute presence and visual visibility are independent when author CSS has a competing display rule. Norm #14 added to CURRENT_STATE.md. **Two latent instances of the same bug class** noted for a future hygiene pass: `.hero-change` (`display: inline-flex`) and `.sparkline-wrap` (`display: flex`) both start `hidden` in HTML with no `[hidden]` override rule; not currently reported broken (page loads fast, skeleton masks the loading state).

#### 4.10 PR Ψ3C.1 — Bottom nav + app header chrome (2026-05-30)

Phase 1 of 3 in the Ψ3C app-feel sprint. Files changed: `index.html`, `style.css`, `app.js`, `service-worker.js`. Zero backend files, schemas, data files, or CI workflows touched.

**Design decision — Option B (scroll-anchors, single page preserved).** Two options were evaluated before this PR: Option A would have split content into separate tab panels (hidden/shown by JS); Option B keeps the page as a single scrolling document and uses the bottom nav as scroll-anchors. Option B was approved and implemented. Rationale for Option B over Option A: the glanceable single-page experience — all sections reachable by scrolling, with the price and verdict always visible above the fold on first load — is the core UX value of this app. Splitting into tab panels would require users to actively navigate to see the sparkline, comparison cards, and verdict in combination; it also breaks browser back/forward navigation and makes the "Trend" section invisible to users who only visit the home tab. Scroll-anchors preserve everything while adding app-style chrome.

**Bottom navigation bar.** `<nav class="bottom-nav">` fixed to viewport bottom. Four items — Home (hero), Trend (chart), History (price history), Info (methodology accordion) — each an anchor that calls `scrollIntoView({behavior:"smooth"})` on click. `scroll-margin-top: calc(52px + env(safe-area-inset-top, 0px) + 8px)` on each section ensures smooth-scroll lands below the sticky header. Active state: gold `#E09B2E` color + 2px top-border indicator; inactive items muted (`--cream-mute`). Scrollspy uses a requestAnimationFrame-throttled scroll handler (not IntersectionObserver) — strategy is "last section whose top edge is ≤ scrollY + HEADER_H + 24px", which reliably handles the fixed header offset and avoids the thin-band IntersectionObserver fragility with sections taller than the viewport. Safe area: `padding-bottom: env(safe-area-inset-bottom, 0px)` on the nav; `main` bottom padding increased to `calc(80px + env(safe-area-inset-bottom, 0px))` so footer is never hidden behind the nav.

**Desktop nav breakpoint.** Initial implementation was full-width fixed bottom bar at all viewports — flagged by consultant as "reads as broken-mobile at 1280px". Decision: hide `display: none` at `≥900px`. Rationale: desktop users have mouse scroll and keyboard; a centered fixed-bottom island at 980px with empty viewport flanks is visually awkward and mobile chrome adds no navigational value on desktop. Tablet (641–899px) retains the nav with slightly larger items. `main` padding-bottom restored to the original `calc(var(--space-7) + env(safe-area-inset-bottom, 0px))` at ≥900px.

**App header chrome.** `.utility-row` (~28px) replaced with `.app-header` (52px `min-height`). Left: "Gold" in Fraunces 20px as the app identity anchor. Right: compact freshness badge (text shortened: "2h ago" vs "Updated 2h ago"; "STALE · 8H AGO" vs "Stale — 8h ago") + ↻ button + ? button (standalone-gated, unchanged from Ψ3B). Stale/warn tinted header backgrounds preserved (`:has(.freshness--stale)` / `:has(.freshness--warn)` selectors updated to target `.app-header`). Opaque layered background fix from Ψ3A carried forward.

**Location label relocation.** "Bengaluru · Tanishq retail" moved from header right-cluster to hero card area as `.hero-location` — small uppercase muted text positioned below the price (`margin: -4px 0 var(--space-3)`). Revealed by `renderHero()` alongside the price and eyebrow. Removes the contextual label from the compact header right-cluster (already tight with freshness + ↻ + ?) and grounds the price number in its source context directly below it.

**Latent CSS bug fixes (norm #14).** Closes two instances of the same bug class documented at Ψ3B-hotfix. Both `.hero-change` (`display: inline-flex`) and `.sparkline-wrap` (`display: flex`) had author-side `display` rules that defeated the UA `[hidden]{display:none}` rule when the `hidden` attribute was set. Added `.hero-change[hidden] { display: none !important }` and `.sparkline-wrap[hidden] { display: none !important }`. Norm #14 verification via `getComputedStyle(el).display === "none"`: two-check protocol — (1) before JS load with `hidden=true` from HTML, (2) after JS load with `hidden` re-set explicitly. Both elements, both checks: **ALL PASS**.

**SW VERSION bump.** `v4-20260530-b` → `v5-20260530-c`. Shell assets `index.html`, `style.css`, `app.js` all changed; per the CACHE INVALIDATION CONTRACT in `service-worker.js`, any change to shell assets requires a VERSION bump to force cache eviction and SW reinstall on existing users' devices.

**Verification.** Playwright headless screenshots at 428×926 (iPhone 14 Plus), 768×1024 (tablet), 1280×900 (desktop). Scrollspy: section-home → section-trend → section-history all update correctly on scroll. Footer clearance: footer.bottom=846, nav.top=865 at bottom of page (footer fully above nav). Desktop: `bottom-nav visible=false`, `main padding-bottom=32px`. Lint CI: pass (2m44s, run 26668222716). pages-build-deployment: success. Merge commit: `4362bd7`.

**Post-ship defect (fixed in §4.11 / Ψ3C.1b).** Restructuring `.utility-row` → `.app-header` moved the element from inside `<main>` to a direct `<body>` child, but three rules were inherited unchanged from the `<main>`-interior assumption: (1) negative margins intended to bleed the header edge-to-edge within `<main>`'s side padding — outside `<main>` they overflowed the viewport by 21px each side and placed "Gold" at x=0 instead of x=21.4; (2) `background: rgba(..., 0.90)` + `backdrop-filter` — the 10% transparency made the price text visible through the header when scrolled; (3) `body { padding-top: env(safe-area-inset-top) }` with `header { top: env(safe-area-inset-top) }` — in standalone mode this produced a two-band gap (body padding strip + header strip) above the content. All three fixed in Ψ3C.1b (commit `996ff85`).

**Ψ3C sprint status.** Phase 1 of 3 complete. Phase 2 (Ψ3C.2): motion/transitions, tap feedback, :active scale, pull-to-refresh. Phase 3 (Ψ3C.3): interactive chart, tap-to-reveal price, chart/history dedup, chart timeframe relabel, extended skeletons.

#### 4.11 PR Ψ3C.1b — App header ambient fix: opaque, aligned, elevation-on-scroll (2026-05-30)

Focused fix for the "floating strip between words" reported after Ψ3C.1. Files changed: `style.css`, `app.js`, `service-worker.js`. Zero backend files, schemas, data files, or CI workflows touched.

**Diagnosis (Pass 1).** Three compounding bugs identified via Playwright computed-style capture + screenshots at iPhone 14 Plus (428×926) dark mode: (1) Semi-transparent bleed-through: `rgba(26,22,18,0.90)` + `backdrop-filter:blur(8px)` — the large amber price text was visibly bleeding through the 10% opacity gap when scrolled behind the header, making the header a floating strip over the price words. (2) Negative-margin overflow: header was a `<body>` child (outside `<main>`) but retained margins from when it was inside `<main>`; result was `header.x = -21.4`, `width = 471px` on a 428px viewport — "Gold" title at screen edge (x=0), all content below indented 21px. (3) Two-band standalone effect: `body { padding-top: env(safe-area-inset-top) }` created a visible blank zone above the header, making it read as a strip at the bottom of a large dark zone rather than integrated chrome.

**Direction C (approved): ambient header — merges with page at rest, elevation on scroll.**

**Safe-area absorbed into header.** Removed `body { padding-top: env(safe-area-inset-top) }`; added `padding-top: env(safe-area-inset-top, 0px)` and `min-height: calc(52px + env(safe-area-inset-top, 0px))` to `.app-header`. The header background now fills from screen top through the nav bar — one continuous dark chrome surface in standalone mode. Content position after the header is unchanged (net y offset is identical).

**Fully opaque background.** `background: var(--ink)` (no alpha); `backdrop-filter` removed. Zero bleed-through regardless of scroll position. Light-mode `@media` override removed — `var(--ink)` already resolves to the light cream value via the `:root` token override in the light-mode block.

**Negative-margin overflow fixed.** Removed `margin-left/right: calc(-1 * clamp(...) - env(...))`. Header is now `x=0, width=428px` (was `x=-21.4, w=471px`). Padding `clamp(20px, 5vw, 56px)` on each side aligns "Gold" title (x=21.4) with all hero content below (x=21.4) — confirmed via computed `getBoundingClientRect`.

**Ambient elevation.** No `border-bottom` / `box-shadow` at rest — header merges with page, only content ("Gold", freshness badge, buttons) is visible. Passive scroll listener (`scrollY > 0`) toggles `.scrolled` class on `#utility-row`; `.scrolled` applies `border-bottom: 1px solid var(--line)` + `box-shadow: 0 1px 8px rgba(0,0,0,0.28)` — elevation appears only when content is actually under the header. Initial state set on mount (handles pages loaded at non-zero scroll).

**Freshness alert border fix.** Stale/warn states changed from `border-bottom-color: ...` to `border-bottom: 1px solid ...` (the base rule no longer declares a border; changing only the color had no effect). Alert states always show their tinted border as a visible indicator regardless of scroll position.

**HEADER_H scrollspy fix.** Changed hardcoded `const HEADER_H = 52` to `document.getElementById("utility-row")?.getBoundingClientRect().height ?? 52` — reads actual computed height so the scrollspy trigger is accurate in standalone mode where the header is taller.

**SW VERSION bump.** `v5-20260530-c` → `v6-20260530-d`. Shell assets `style.css`, `app.js`, `service-worker.js` changed; per cache invalidation contract a VERSION bump is required.

**Verification (norm #14).** All computed values confirmed via Playwright `getComputedStyle()` + `getBoundingClientRect()`. Key measurements after fix: `header.x=0.0`, `header.w=428`, `Gold.x=21.4`, `Eyebrow.x=21.4` (aligned), `backgroundColor=rgb(26,22,18)` (solid), `backdropFilter=none`, `.scrolled=false` at rest / `true` at 90px scroll. Regression at 768px and 1280px: header correct at both breakpoints. Lint CI: pass (2m45s). PR #46.

#### 4.12 PR Ψ3C-fix — Commentary correctness: Chronos directional signal + honest framing (2026-05-30)

Two bundled items: commentary framing bug (Item 1) + app-header copy (Item 3). Merge commit: `7504c09`. PR #47.

**Root cause (diagnosed in session).** `ml/commentary.py:build_user_message()` read `forecast.get("predicted_22k")` — the naive flat-hold prediction, which equals the current price by definition — and labeled it `"Point estimate"` in the Groq prompt. The LLM reproduced "The model's point estimate for the next reading is Rs.14440" — which is today's price. The `chronos_companion` block (lean_direction, direction_acc_30f, majority_direction, direction_consensus) was never extracted or passed to the prompt; the 63.3% direction accuracy and 5/5 consensus were invisible to the LLM. Both bugs trace to `commentary.py` predating the naive-headline pivot (ADR 012, PR H) and never being updated alongside it.

**Fix.** `build_user_message()` now extracts `forecast.chronos_companion` and passes all directional fields as a separate "Directional signal (Chronos)" block with a `directional_signal_available: true/false` gate. `status != "success"` → all fields `"N/A"` — LLM cannot fabricate a lean when the probe failed. Naive baseline relabeled `"= current price; no directional signal from this number"`. SYSTEM_PROMPT rewritten to prohibit "point estimate" language, lead with directional lean when available, frame accuracy honestly ("63% is a lean, not a guarantee"). Tests: +3 new (companion present, probe failed, Point-estimate-not-in-msg regression guard). 309 total, 0 regressions. Lint CI: pass (2m38s).

**Item 3.** `index.html` app-title: "Gold" → "Gold · Bengaluru". SW VERSION `v6-20260530-d` → `v7-20260530-e`.

**Lesson.** Norm #15 added to CURRENT_STATE.md: when an architecture pivot changes what a data field means, audit ALL consumers (PWA, commentary, notifications, drift). The Chronos companion reached the PWA in Ψ2A but not commentary until this PR, 8 PRs later.

**Commentary framing verification — PASSED (2026-05-30T11:31:56Z).** `check-price.yml` manually triggered post-merge (run 26682692035, 1m53s, all steps ✓). Actual generated text from `llama-3.3-70b-versatile`:

> "The Chronos signal leans up with 63% recent accuracy and 5 of 5 samples in agreement, while the current 22K gold price is Rs.14440, at the 59th percentile in the last 90 days. The price has decreased by 1.16% over the past 3 days and 1.26% over the past 7 days."

Framing check: leads with directional signal ✓ · names 63% accuracy ✓ · names 5/5 consensus ✓ · current price labeled as current price (not "point estimate") ✓ · no magnitude prediction ✓ · buyer context (percentile + recent moves) ✓ · no buy/sell advice ✓.

#### 4.13 PR Ψ3C.2 — Motion language, tap feedback, pull-to-refresh gesture (2026-05-30)

Phase 2 of 3 in the Ψ3C app-feel sprint. Files changed: `index.html`, `style.css`, `app.js`, `service-worker.js`. Zero backend files, schemas, data files, or CI workflows touched.

**A. Tap feedback (CSS-only, global).** All `<button>` elements: `button:active { transform: scale(0.95) }` + global `-webkit-tap-highlight-color: transparent`. Comparison cards and karat cards: `:active { transform: scale(0.98) }` with `transition: transform 100ms ease-out` added to their existing transition chains. History cards (mobile ≤640px): `:active { background: var(--surface-2) }` — a subtle background flash confirming the tap without implying navigability. Verified: computed-style `transition` property contains `transform` on all target elements (norm #14).

**Range toggle sliding pill.** The range toggle (7d/30d/All) now uses a CSS `::after` pseudo-element as a sliding gold pill. Implementation: (1) `position: relative` on `.range-toggle`; (2) `flex: 1` on each button for equal width so pill math is exact; (3) `.range-toggle::after` positioned at `left: 4px, top: 4px, bottom: 4px, width: calc((100% - 8px) / 3)` with `transition: transform 200ms ease-out`; (4) `:has(button:nth-child(N).active)::after` selectors move the pill to `translateX(100%)` or `translateX(200%)` for buttons 2 and 3. No JS required for the slide — the existing JS class toggle is the only JS involved. Active button text color: `var(--ink)` (readable on gold pill); inactive: `var(--cream-mute)`. Verified: buttons report `flex: 1 1 0%`, inactive button background is `rgba(0,0,0,0)` (pill not button has background), active class correctly moves to clicked button.

**B. Motion transitions.** Data-update stagger: `refreshData()` calls `staggerEnter([comparison-section, karat-strip, model-signal-section])` after renders complete. Each card gets a `card-enter` CSS class with `animation: card-fade-in 200ms ease-out both` and `animationDelay: i * 30ms` — fan-in over ~90ms total at 30ms stagger. Force-reflow (`void el.offsetWidth`) between class remove/add ensures the animation restarts on every refresh call. Initial page load uses the skeleton UI; stagger is refresh-only. Chart fade: `bindRangeToggle()` now wraps `renderChart()` with `chart-fade-out` (150ms) → render → `chart-fade-in` (200ms); no-op if the already-active button is re-clicked. All new `@keyframes` and `transition` rules inherit the existing `prefers-reduced-motion: reduce` rule that sets duration → 0.01ms !important. Verified: reduced-motion computed duration ≈ 0ms; chart-fade-out class observed on range switch.

**C. Pull-to-refresh gesture.** Touch event handlers on `document`. `touchstart` arms when `scrollY ≤ 2`. `touchmove` (registered `{ passive: false }` to allow `preventDefault`): calls `e.preventDefault()` ONLY when `deltaY > 0 AND scrollY === 0` (per spec — does not prevent normal scroll). Adds `html.ptr-pulling` class (CSS: `overscroll-behavior-y: none`) only during active pull. Translates `<main>` down max 60px proportionally (damped 0.5×) to reveal the pull indicator below the app-header. Indicator opacity tracks progress (0 → 1 at 60px threshold); arrow glyph "↓" changes to "↻" at threshold. `touchend` calls `refreshData()` if threshold crossed; spring-back `transform 300ms ease-out` to `""` in all cases. Verified via Playwright simulated touch events: spinner fires, indicator becomes visible.

**iOS risk (flagged — real-device test required).** `overscroll-behavior-y: none` is Safari 16+ only. On iOS < 16, the browser's native rubber-band overscroll coexists with our gesture — the pull may feel cosmetically janky. The gesture is functionally correct (refreshData fires), but the interaction quality on older iOS is unverified. The ↻ button remains functional on all iOS versions regardless. Decision: ship the gesture; flag for real-device test before declaring pull-to-refresh "working" on iOS. If iOS rubber-band conflict is confirmed jarring in real use, drop the pull gesture and keep ↻-only.

**SW VERSION bump.** `v7-20260530-e` → `v8-20260530-f`. Shell assets `index.html`, `style.css`, `app.js` all changed.

**Verification (Playwright headless, 19/19 checks pass).** iPhone 14 Plus (428×926): tap feedback transitions present on buttons/cards ✓ · range-toggle equal-width buttons + transparent inactive background ✓ · chart-fade-out class fires on range switch ✓ · pull indicator fixed-positioned, opacity:0 at rest, becomes visible on gesture ✓ · refreshData fires (spinner observed) on 80px simulated pull ✓ · prefers-reduced-motion: duration → ~0ms ✓. Desktop 1280px: bottom-nav hidden, no layout break ✓. 768px: tap feedback present ✓.

#### 4.14 PR Ψ3C-copy — Plain-language copy sweep for non-technical users (2026-05-30)

Phase 3 of 3 in the Ψ3C sprint. Files changed: `ml/commentary.py`, `app.js`, `index.html`, `service-worker.js`. Zero backend files, schemas, data files, or CI workflows touched. Merge commit `1b1f979`. PR #49.

**Principle.** The ML sophistication stays in the system; plain words go in the UI; honest framing is preserved. Users are non-technical family members — terms like "Chronos signal", "63% direction accuracy (30f)", "5 of 5 samples", "59th percentile", "naive flat-hold", "conformal PI", and "Walk-forward backtest" belong in engineering docs, not in text a buyer reads.

**SYSTEM_PROMPT rewrite (`ml/commentary.py`).** The previous SYSTEM_PROMPT instructed the LLM to produce ML jargon verbatim ("Chronos signal", "63% on recent data", "5 of 5 samples", "59th percentile"). New SYSTEM_PROMPT bans those terms and instructs plain-language framing: reliability described as "right about 6 times out of 10", direction described as "prices look likely to edge up a little", price level as "around average for the past few months". Honesty preserved: "lean or guide, not a certainty", "never say prices 'will' rise or fall".

**Verified LLM output (real run 2026-05-30T17:17:58Z, `llama-3.3-70b-versatile`):**
> "The current gold price is around average for the past few months. Prices look likely to edge up a little, and this signal tends to be right more often than not, about 6 times out of 10, with a consistent signal from all sources. Gold has been down a little over the past week."

Framing check: No "Chronos" ✓ · No "percentile" ✓ · No "samples" ✓ · Direction as lean ✓ · Reliability in plain fractions ✓ · No "will rise/fall" ✓ · No buy/sell advice ✓.

**Model signal section (`app.js`, `index.html`).** "Model signal" → "5-day outlook". Stats row: "63% direction accuracy (30f)" → "Right about 63% of the time recently"; "5 of 5 samples agree" → "Consistent signal" / "Mixed signal" (≥80% threshold); calibration tag dropped from always-visible card (kept in methodology accordion with plain label). Note: "Directional signal only…" → "A guide for which way prices may move — not a guarantee."

**Methodology accordion (`app.js`).** Summary/entry point: "How this verdict is calculated · model accuracy · backtest" → "How this works · how accurate the signal is · historical checks". Inside: "naive flat-hold" → "Predict no change"; "PI from conformal 80% of last 30 naive errors" → "Range covers 80% of typical 5-day swings"; "Chronos directional signal (companion)" → "Direction signal"; "Direction accuracy (last 30 folds)" → "Recent accuracy", "naive baseline: 50%" → "random guessing: 50%"; "Calibration" → "Adjusted to Tanishq prices"; "Walk-forward backtest (h=5) (N folds)" → "Historical accuracy check — N periods, 5-day horizon"; "MAE" → "Avg. price error"; "+₹X vs naive" → "+₹X vs predict-no-change"; "Wilcoxon p" → "Statistical significance"; backtest note rewritten to plain English with honesty preserved ("performs slightly worse than this simple approach").

**Freshness / stale / iOS.** "Stuck" → "Not updating"; stale banner reworded to "Prices last updated {rel} — data may not reflect the current rate."; iOS help panel "fetch the latest readings" → "get the latest prices".

**Comparison cards.** "vs period low" → "vs 30-day low" (aligns with the sub-text which already said "30d low").

**SW VERSION bump.** `v8-20260530-f` → `v9-20260530-g`. Shell assets `index.html`, `app.js` changed.

**Tests.** 309/309 pass, 0 regressions. Commentary tests: 18/18 pass — no test changes needed (tests assert on user-message structure and Groq API call shape, not SYSTEM_PROMPT prose; the one SYSTEM_PROMPT check `"buy/sell" in content` still passes).

**Lesson.** Copy is a consumer of the model architecture and should be audited alongside it. The Ψ3C-fix PR (§4.12) fixed commentary framing bugs; this PR completes the sweep by updating every surface a user reads. Plain language and honest framing are not in tension: "right about 6 times out of 10" is more concrete than "63% accuracy" and equally honest.

#### 4.15 PR Ψ3C-notify — T8 twice-daily plain-language gold digest (2026-05-30)

New T8_MORNING and T8_EVENING triggers added to `ml/notifications.py`. Backend-only PR. Merge commit `0372d9c`. PR #50.

**Motivation.** The existing T1-T7 triggers are conditional and ML-gated — they don't fire reliably every day. Non-technical family users and the owner (who invests in gold) had no guaranteed daily price update. T8 provides: (1) a plain-language daily price report, (2) a system-health signal (if neither T8 fires, something is wrong), and (3) an optional directional lean for the gold investor.

**Design: three scenarios.** Price move vs prior IST day's last reading, ±Rs.25 flat threshold:
- Rose: `Gold rose today - Rs.14490 (up Rs.100 from yesterday).`
- Dropped: `Gold dropped today - Rs.14340 (down Rs.50 from yesterday).`
- Flat: `Gold held steady today - Rs.14440.`

**Optional directional hint (norm #4 — honest framing).** When `forecast.chronos_companion.status == success`:
- lean_direction=up: `Prices look likely to edge up a little in the next few days.`
- lean_direction=down: `The next few days may ease a little.`
- lean_direction=flat or status != success: hint omitted entirely. No fabrication.

**Cron-drift aware (Ψ1 T4 lesson).** IST-date dedup pattern, not clock-window:
- T8_MORNING: fires on first CI run where `now_ist.hour >= 8`. Catches ~10:00 IST cron.
- T8_EVENING: fires on first CI run where `now_ist.hour >= 18`. Catches ~22:00 IST cron.
- Two new state fields: `last_t8_morning_ist_date`, `last_t8_evening_ist_date`. Old state files load cleanly (default to "").

**bypass_quiet rationale for T8_EVENING.** The only cron at/after 18:00 IST is the ~22:00 IST cron, which falls exactly at the quiet-hours boundary (22:00–07:00 IST). Without bypass, the evening digest queues until morning and arrives concurrently with T8_MORNING — defeating the "twice daily" guarantee. `bypass_quiet=True` mirrors T4's pattern (both are guaranteed periodic sends, not conditional alerts).

**Anti-spam.** T8 does NOT count toward the T1+T2+T3 combined 3-per-24h cap. Priority 2 (informational). Coexists with T1-T7 — all prior triggers unchanged.

**ASCII-safe.** Titles and bodies fully ASCII (Rs. not ₹, plain hyphens not em-dashes). Consistent with existing T1-T7 message style (norm #12).

**Tests.** +28 new (93 notification tests, 337 full suite, 0 regressions). All T8 verification criteria from spec covered: threshold timing, cron-drift fire-on-first-run, dedup, both sessions same IST day, three scenarios, hint on/off, fabrication prevention, ASCII-safe, backward-compat, state round-trip, send_pending stamping, T8 exempt from cap.

**Lint CI.** pass (2m45s). Master Lint + pages-build-deployment both green post-merge.

---

### Phase 5 — Validate  ⏸️ NOT STARTED

### Phase 6 — Promote  ⏸️ NOT STARTED

---

## Decision Log

| Date | Decision | Made by | Rationale |
|------|----------|---------|-----------|
| 2026-05-18 | Phase 1 read-only audit | Consultant | Establish evidence base before any changes |
| 2026-05-18 | Target horizon = 5 days | GG | Aligns with buy-signal use case; 6h reading cadence unchanged |
| 2026-05-18 | Use case = dashboard + ntfy alerts | GG | Dual output; notification spec added to Phase 3 |
| 2026-05-18 | Drop synthetic seed (data quality) | GG | Real-only corpus; 444 synthetic rows archived, not used |
| 2026-05-18 | Primary forecaster = Chronos-Bolt-Tiny | Consultant | Zero-shot, 9M params, 8.65 MB, CPU-runnable; appropriate for 71-reading corpus |
| 2026-05-18 | Retire TFT + N-BEATS | Consultant | Data gates (1,000 / 2,000 real readings) would not open until 2027–2028 |
| 2026-05-18 | Variant = Tiny (not Base) | CC plan | 8.65 MB vs 821 MB; accuracy gap negligible at current data volume; upgrade path is 1-line |
| 2026-05-19 | IBJA primary URL corrected to ibjarates.com | CC (evidence audit) | ibja.co cannot yield both AM and PM in one request; ibjarates.com is the only source with dual AM/PM. See incident log. |
| 2026-05-19 | Initial calibration uses UTC-date pairing; time-of-day refinement deferred | Consultant | Calibration machinery is correct in isolation; pairing precision can be improved after PR E validates the end-to-end forecast → calibration pipeline. |
| 2026-05-19 | MCX + basis.py dropped from plan | Consultant (approval) | No automated INR MCX source without Selenium; single-series IBJA strategy eliminates basis adjustment entirely |
| 2026-05-18 | IBJA primary URL = ibja.co (official) | Consultant | ibjarates.com is a third-party aggregator; ibja.co is the authoritative source |
| 2026-05-19 | Calibration model = HuberRegressor (not OLS), epsilon=1.35 | CC (PR D) | OLS inflated by lag artefacts when IBJA moves sharply (e.g. 2026-05-13: ratio 0.960, 2026-05-18: 0.993); HuberRegressor clips these with default epsilon. Verified empirically in outlier test. |
| 2026-05-18 | Calibration model = HuberRegressor (not OLS) | Consultant | OLS is sensitive to Tanishq promotional outliers; HuberRegressor is robust to occasional spread deviations |
| 2026-05-19 | Chronos-Bolt-Tiny selected as primary forecaster; probe-only in PR E | CC (ADR 009) | Zero-shot; no training data required at current data volume (~25–30 real readings). Probe path validates load→forecast→calibration→JSON before live-forecast flip in PR H. See ADR 009 for full alternatives analysis. |
| 2026-05-19 | data/ibja_rates.parquet un-gitignored, committed as reference data | CC (post-hoc) | CI runs need historical context that can't be regenerated from live append alone; same pattern as the prior MCX-parquet decision. 21-row seed (2026-04-24 to 2026-05-18) committed in PR E. |
| 2026-05-19 | Walk-forward backtest uses expanding window, step=1 day, min_context=8 | CC (PR F) | Expanding window accumulates all available IBJA history per fold — appropriate for zero-shot Chronos which benefits from longer context. Step=1 day produces maximum fold count from thin history (9 folds from 21 rows). |
| 2026-05-19 | Wayback backfill used Mode A (HTML parse) for Run 2, not Mode AB | CC (PR F.5) | Mode B (PDF) was used in Run 1 and yielded 83 rows from 2025-2026 PDFs. Mode A HTML parsing failed for all captures in Run 1 because the old parser only handled 3-col format (`table#TodayRatesTableDataYes`); 2022-2023 archives use a 4-col format (Metal\|Purity\|AM\|PM). Run 2 ran Mode A only after the 4-col parser was added. Mode AB was not re-run because 2022-2024 PDFs were not archived in Wayback, so Mode B would yield nothing new and double the fetch time. |
| 2026-05-18 | Chronos context = 365d baseline / 730d upgrade | Consultant | 60d was insufficient for seasonal signal; 365d captures full annual demand cycle |
| 2026-05-18 | MCX backfill = 730 days (B1 one-time) + yfinance daily (B2 ongoing) | Consultant (Q2) | Clear role split: B1 for depth, B2 for currency; avoids hammering Bhavcopy portal daily |
| 2026-05-18 | T1 replaces drop_threshold=100 alert; T3 is new (observed moves) | Consultant (Q3) | No parallel alerts; retire drop_threshold=100 config variable in PR G |
| 2026-05-18 | PWA 5d chart UI deferred to Phase 4 | Consultant (Q4) | day-1 alias in forecast.json preserves existing PWA; chart work not in Phase 3 scope |
| 2026-05-18 | Residual head gate = 0.80 ≤ mae_chronos/naive ≤ 1.00 | Consultant (Q5) | Residual head only when beating naive but with headroom; if ratio < 0.80, standalone Chronos is sufficient |
| 2026-05-18 | Add T5 (model degraded) trigger | Consultant (critical fix) | Silent Chronos failures must surface as low-priority ntfy; model_fallback=true in forecast.json is the signal |
| 2026-05-18 | T1/T2 gate = ≤ naive_mae AND fold_count ≥ 60 | Consultant (medium fix) | Previous 1.5× threshold was too loose; new gate requires model to actually beat naive |
| 2026-05-19 | Naive flat-hold is the production headline forecast (ADR 012) | CC (evidence) | 165-fold backtest (p=0.0089): Chronos 10.4% worse than naive. Deploying Chronos as headline would violate ADR 005. Naive is declared explicitly. Promotion criterion: ≥250 rows, Chronos beats naive, p<0.05. |
| 2026-05-19 | T1/T2 triggers re-scoped to directional signal + momentum agreement (PR G) | CC (ADR 012 consequence) | T1/T2 no longer gate on Chronos level forecast vs naive MAE. Instead: chronos_lean ≥0.5% + 7d momentum agreement + rolling-30f dir_acc ≥0.55. Title makes clear "directional signal, not price forecast." Dir acc last-30f = 0.633 at PR G merge. |
| 2026-05-19 | daily_summary.py deprecated and disabled in CI (PR G) | CC | Superseded by ml/notifications.py with revised T1–T5 triggers. Deleted in PR H. |
| 2026-05-19 | Notification state persistence verified across master CI cycles | CC | Run 26096369659 (workflow_dispatch): Restore step matched prefix key notification-state-26095145270; T2 in cooldown (state survived round-trip); Save wrote new key notification-state-26096369659. Cache restore-keys prefix match confirmed working. |
| 2026-05-19 | Prompt caching NOT applied to live Groq path (ADR 013) | CC | Three failure criteria: wrong provider (Groq, not Anthropic), below token minimum (~280 vs 1024), cadence outside TTL (6h vs 1h). Forward-looking helpers in ml/llm_cache_helpers.py; re-evaluated when Claude migration, batch backfill, or prompt size growth triggers occur. |
| 2026-05-19 | Notification state persistence verified on scheduled cron run (run 26105096443) | CC | Scheduled run restored state from key notification-state-26096369659 (prefix match from prior workflow_dispatch run); saved new key notification-state-26105096443. Full save→restore cycle confirmed on genuine cron trigger. |
| 2026-05-19 | Phase 3 complete — production = naive headline + Chronos directional companion (ADR 014) | CC | All nine Phase 3 PRs merged. Naive flat-hold is the explicit production forecast; Chronos retained for directional signal only. LightGBM and all associated training infrastructure deleted. Promotion criterion: ≥250 IBJA rows, Chronos beats naive, Wilcoxon p<0.05 (~2026-09). |
| 2026-05-22 | PR Φ2 PWA schema alignment merged | CC | `app.js` aligned with post-PR-H schema (`headline.*` + `chronos_companion.*` blocks); LightGBM-era reads (warmup banner, val_mae/naive_mae trailing banner, phantom `bt.model`/`bt.baseline`) deleted; PI-band explanation added per ADR 014. Defensive fallbacks to top-level back-compat aliases retained until coordinated PWA-migration PR. Headless-Playwright smoke test verified zero JS errors + all new fields populated. Hygiene fold-in: fixed EOF writer bug in `ml/chronos_forecast.py` (1-line trailing-newline fix); discovered because post-Φ1 master Lint had drifted to red via [skip ci] cron commit. |
| 2026-05-22 | PR Φ3 T6 calibration-unlocked trigger merged | CC | New `last_t6_fired_date_ist` state field; `_check_t6` fires once-ever when calibration first becomes valid (idempotent daily IST dedup, mirrors T5); `check_triggers()` accepts new `calibration` kwarg LAST (additive, backward-compat regression test included); `inference.py` emits `calibration_just_unlocked` flag in chronos_companion block. Spec step (d) about `weekly-backtest.yml workflow_dispatch` was a no-op — already present on master. |
| 2026-05-28 | PR Φ4 multi-sample Chronos probe + consensus gating merged | CC (ADR 015) | Probe runs 5 independent samples per cycle; T1/T2 fire only when 3+ of 5 agree on direction (consensus ≥ 0.6). Schema v1→v2 with new fields (num_samples, sample_directions, majority_direction, direction_consensus). Strength magnitude gate (≥0.5%) preserved independently. Spec's <2s wall-clock budget revealed as baseline misjudgment about model_load cost — measured pipeline_load is ~10s CI / ~7.5–16s local Windows; Φ4's marginal forecast cost is 5x linear (~75ms CI extrapolation; ~100–330ms local depending on host load). HF cache key unchanged. Closes PR E direction-flip vulnerability. |
| 2026-05-28 | PR Ψ1 — T4 IST-date dedup + Monday recovery + T7 system-alive floor merged (#37) | CC | T4 time-window replaced with IST-date-based dedup after confirming structural cron-drift failure (GH Actions free-tier ≥60 min delay makes ±30 min window structurally unreachable). T7 added as a 3-day heartbeat floor. See §4.4. |
| 2026-05-28 | Branch hygiene rule formalised: all work branches cut from origin/master HEAD | CC (incident: PR #36 closed) | PR #36 branched from chore/progress-md-sprint-1-summary (stale master sync point), missing 19 tests from PR #32 (T6) and PR #33 (Phi4). Detected pre-merge. PR closed, recreated as #37 from current master HEAD. Rule: verify `git merge-base HEAD origin/master` equals current master HEAD before opening a PR. Cited in Ψ2 prompt as standing procedure. |
| 2026-05-28 | PR Ψ2A — majority_direction/direction_consensus propagated to forecast.json.chronos_companion | CC (diagnosis-driven expansion) | Fields were in chronos_probe.json (Φ4) but not forwarded to forecast.json by inference.py._build_chronos_companion(). PWA uses forecast.json as sole data source; propagation was the clean fix vs loading a second JSON. Approved as in-scope expansion after Pass 1 diagnosis. |
| 2026-05-28 | PR Ψ2A — Chronos companion section moved to always-visible page flow (#39) | CC | Root cause was render target inside collapsed <details> accordion. Fixed by adding #model-signal-section to main page flow between commentary and chart. Methodology accordion details retained. |
| 2026-05-28 | PR E falsifiable bet resolved — Chronos lost | CC (evidence) | Bet placed at PR E merge (2026-05-19): Chronos predicted IBJA-916-PM = 14,118.69 INR/g on 2026-05-23 (−2.29% vs 14,448.9 baseline); naive predicted flat at 14,448.9. Actual outcome: price moved UP through the observation window. Chronos predicted a 2.29% drop; price rose instead. Verdict: Chronos lost cleanly. No impact on production routing — naive is already the declared headline (ADR 012). Consistent with the 165-fold backtest result (Chronos 10.4% worse than naive, p=0.0089). |
| 2026-05-29 | Ψ2B chart border restored — full-bleed treatment detached in context | CC (reversibility criterion) | Pass 3 initial screenshots confirmed "open and intentional" for the chart in isolation. Pre-merge contextual screenshot (consultant requirement: chart with adjacent sections in same frame) showed chart floating between two bordered sections — signal card above, history card below. Without its own container the chart read as a broken component, not an editorial choice. Per pre-approved reversibility criterion ("don't ship a worse aesthetic to chase a more-modern one"), border/background/border-radius restored. Mobile 3/2 aspect ratio kept. |
| 2026-05-29 | PR Ψ2C — chart padding, zero-delta, arrow-sign redundancy merged (#41) | CC | Three targeted polish fixes: (1) chart-wrap padding reduced so canvas fills ~90% of card height; (2) zero-delta rendered as `·` at opacity 0.45 vs `±0` at full opacity (read as broken); (3) delta arrows applied consistently across mobile cards and desktop table with sign dropped (Option A). Lint CI pass 2m46s. Ψ2 series complete. |
| 2026-05-30 | SW cache VERSION bumped "v2" → "v3-20260530-a"; registration.update() + controllerchange/refreshing-guard added (PR Ψ3A) | CC | Two compounding failures caused users to need manual cache clear: static VERSION meant service-worker.js never changed between Ψ2B/Ψ2C deploys (browser never installed new SW); missing registration.update() meant 24h browser SW cache window was not bypassed. All four contract parts now in place. |
| 2026-05-30 | Freshness strip stale/warn backgrounds fixed to layered opaque (var(--ink) + tint) + box-shadow added (PR Ψ3A) | CC | `.has(.freshness--stale)` was 18% opacity; `.has(.freshness--warn)` was 12% opacity — content readable through strip while scrolling. Layered CSS background (`gradient(tint), var(--ink)`) makes strip fully opaque in all states while preserving tint colour intent from Ψ2B. |
| 2026-05-30 | Option D for [skip ci] handling proven technically infeasible; accepted daily-schedule + discipline norm as closest achievable fix (PR Ψ3A) | CC | GitHub Actions [skip ci] is platform-wide per-commit for push events — no per-workflow override exists. Daily schedule (06:00 UTC) + workflow_dispatch added to lint.yml as defense-in-depth backstop. CURRENT_STATE.md norm #13 added: PR squash-merge commit messages must not carry [skip ci] in the body; use `gh pr merge --squash --subject ... --body ...` with body explicitly cleaned. Residual gap: up to ~24h delay if a [skip ci] commit reaches master before the schedule fires. |
| 2026-05-30 | Ψ3C.1 — Option B (scroll-anchors) over Option A (tab panels) for bottom nav | GG (approved pre-PR) | Option A splits content into hidden tab panels; breaks the glanceable single-page experience where price, verdict, and sparkline coexist above the fold. Option B keeps the single scrolling document; bottom nav adds thumb-reachable chrome without restructuring content. Scroll-anchors also preserve browser back/forward and allow sections to be seen in combination. |
| 2026-05-30 | Ψ3C.1 — bottom nav hidden at ≥900px (desktop), not full-width or constrained column | CC (post-PR fix on consultant flag) | Full-width fixed bottom bar at 1280px reads as broken-mobile, not intentional-desktop. Constrained 980px centered island (Option b) is also awkward — visible dead zones on both sides. Desktop users have mouse/keyboard scroll; bottom nav adds no navigational value. Cleanest choice: hide with `display:none` at ≥900px. |
| 2026-05-30 | Ψ3C.1b — Direction C (ambient header) over A (solid+safe-area) or B (frosted glass) | GG (approved post-diagnosis) | Diagnosis screenshots showed price text bleeding through 90%-opacity header at mid-scroll — the "strip between words". Direction C (borderless at rest, elevation on scroll) removes the strip feeling entirely by merging the header with the page at rest; elevation appears only when needed. Direction A would also fix bleed-through but retains a permanent dividing line; Direction B keeps transparency and risks continued bleed-through at lower opacity. Native iOS pattern (Mail/Settings ambient header) is the closest analogue. |

---

## Risks Register

| Risk | Severity | Owner | Mitigation status |
|------|----------|-------|-------------------|
| ~~Model 34.6% worse than naive on backtest (MAE ₹225.33 vs ₹167.36, 69 folds)~~ **RESOLVED PR H (2026-05-19):** Naive flat-hold named explicitly as production headline (ADR 012). LightGBM deleted. | Blocker | CC (resolved) | Resolved: naive IS the production forecast; LightGBM deleted in PR H. |
| ~~86% synthetic training data~~ **RESOLVED PR B:** | Major | GG (resolved) | Resolved: synthetic seed dropped. Real-only corpus from PR B onward |
| IBJA robots.txt | Minor | CC (resolved) | Verified PR C: `ibja.co` allows all crawlers (only /cgi-bin/ disallowed); `ibjarates.com` returns HTTP 404 (no restrictions). Both domains clear to scrape. |
| MCX Bhavcopy has no direct URL | Minor | CC (resolved) | Verified PR C: MCX direct 403 (Akamai WAF); Samco archive empty; yfinance Indian MCX symbols all empty/delisted; investpy 403 from investing.com. No automated INR MCX path. MCX strategy dropped entirely — not a risk, a closed question. |
| Chronos-Bolt CPU latency unverified on Actions runner | Major | CC | Timing probe in PR E; fallback is `num_samples=1` deterministic mode |
| PyTorch cold-start CI overhead (~3 min) | Minor | CC | `actions/cache` in PR E; confirmed as acceptable |
| Unpinned deps (yfinance schema risk) | Major | CC | Lockfile added in PR A |
| ~~`inference.py` has no dedicated test~~ **RESOLVED PR A + PR H:** | Major | CC (resolved) | Smoke tests added in PR A; fully rewritten for naive path in PR H (5 smoke tests). |
| WANDB env vars stale in `.env` | Minor | CC | Remove in PR A (local .env change only; gitignored) |
| ~~Regime feature dead-weight~~ **RESOLVED PR H:** | Minor | CC (resolved) | Resolved: `ml/regime.py` deleted in PR H |
| Sentry DSN placeholder not activated | Minor | GG | Unchanged; Sentry not required for Phase 3 |
| ADR 006 numbering gap | Minor | GG | Pending; ADRs 009–011 drafted in Phase 3 PRs |
| 4 training-deps tests fail on local pytest (test_config TFT/N-BEATS overrides, test_promotion sign convention) | Minor | CC | Pre-existing on master; gated in CI via --ignore; fix when training CI job is added |
| structlog not in ml/requirements.txt (inference lockfile) | Minor | CC | Resolve in PR D or earlier; basicConfig used as fallback in ml/inference.py |
| Tanishq–IBJA markup ratio shows high day-to-day variance (median 1.017, std 0.015; spot 0.993 vs median 1.017 = 2.4pp swing) — calibration noise will widen Tanishq PI bands. Investigate in PR E. | Minor | CC | Monitor residual_std after PR E goes live |
| ~~PR F backtest thin-sample limitation: 9 folds from 21 IBJA rows (all sub-30-context). Chronos MAE Rs.319 vs naive Rs.305 (4.6% worse); wilcoxon p=0.1641 (not significant). Results are directional only until 30+ folds accumulate.~~ **RESOLVED PR F.5 (2026-05-19):** 177 rows, 165 folds (143 with ≥30 context). Wilcoxon p=0.0089. Chronos statistically significantly worse than naive (10.4% gap). Wayback ceiling reached. | Minor | CC (resolved) | Backtest now has statistical power. Verdict: Chronos trails naive; directional accuracy (55.8%) is the only positive signal. |

---

## Incident Log

**2026-05-18 — PR #14 rejected.** Two silent data-source substitutions were made without flag-and-stop:
1. ibja.co (spec'd primary) → ibjarates.com (spec'd fallback) — reversed silently.
2. MCX INR/10g → COMEX GC=F USD/troy oz — substituted silently. This is the synthetic-seed problem structurally repeated: USD-denominated futures data relabeled as INR exchange data.

Rejection rationale: data-source substitutions are architecture decisions, not minor implementation choices. Scope discipline protocol: when a spec item proves hard, STOP AND REPORT — not find an adjacent option and move on. Reset and evidence audit required.

**2026-05-19 — Evidence audit completed. Revised architecture approved.**

Findings:
- ibja.co structurally incapable of providing both AM and PM in one request (`id="lblHeaderTextForTimeUnit"` shows only the active session). The 2026-05-18 spec entry designating ibja.co as primary was incorrect; ibjarates.com is the only viable source. This is a spec correction, not a substitution.
- MCX INR data: all five automated paths blocked (MCX direct 403 Akamai WAF; Samco archive empty; yfinance Indian MCX symbols empty/delisted; nsepython NSE-only; investpy 403 from investing.com). No pip-installable library provides automated INR MCX data without Selenium or a paid API.
- Metals.Dev API: `ibja_gold` field returns USD/troy oz — same denomination problem as GC=F, relabeled. Not usable for INR/10g series. Testing the actual documented field denomination (not the marketing description) caught this before implementation.
- ibjarates.com 30-day PDF: confirmed working (308KB, pdfplumber parses correctly, 21 trading days per file, all 5 purities AM+PM, Rs/10g). Provides live + rolling-30-day backfill without rate limits.
- Wayback Machine: 103 captures of ibjarates.com (2022–2026) confirmed via CDX API. Direct Python requests to archive.org time out from local dev machine; GitHub Actions runners have clean connectivity. Viable for Tier 3 deep backfill if needed.

Architectural pivot: single-series IBJA-916-PM forecasting. MCX dropped entirely. `ml/basis.py` and `ml/mcx.py` removed from plan. PR C scope reduced to IBJA live scrape + 30-day PDF backfill only.

**Lesson:** Evidence audits must include: (1) third-party data providers (Metals.Dev) before designating a "backup" data path; (2) archive sources (Wayback Machine) before declaring historical data unavailable; (3) denomination verification against documented response samples, not marketing labels alone. Default to simpler architectures when they exist. A single-series IBJA direct approach is strictly simpler than a two-source MCX-proxy + basis-adjustment architecture.

---

## Glossary / Pointers
- **Naive baseline (updated):** `naive_5d` — hold current price flat for 5 days. Replaces `naive_1step`.
- **Walk-forward backtest:** `ml/backtest.py`, 180-day window (was 90), h=5 (was h=1), Chronos zero-shot.
- **Honest-baseline ADR:** `docs/adr/005-honest-baseline-reporting.md`.
- **Warmup flag:** `forecast.json:warmup = true` while `real_readings_count < 100`; also gates T1/T2 notifications.
- **Production model artifacts (post-pivot):** `models/production/lgbm.txt` (legacy, kept until PR H); ONNX artifacts deleted in PR B.
- **Calibration layer:** `ml/calibration.py` + `data/calibration.json` — HuberRegressor(ibja_916_pm_per_g → tanishq_22k_per_g). Tanishq displays PRE-GST (verified 2026-05-19); median ratio 1.017 over 21 pairs. `data/calibration.json` is `valid: false` at PR D merge (21/30 pairs); activates when 30 pairs accumulate (~9 more trading days).
- **FORECAST_ENGINE flag:** env var in `check-price.yml`; `legacy` = LightGBM (safe default); `chronos` = Chronos-Bolt (active from PR H).
- **Chronos-Bolt-Tiny:** `amazon/chronos-bolt-tiny` on HuggingFace; 9M params; 8.65 MB weights; installed via `pip install chronos-forecasting` (requires torch CPU-only wheel first).
- **IBJA:** India Bullion and Jewellers Association; 916-PM rate = daily closing 22K benchmark in INR/g. Primary modeled series.
- **MCX Bhavcopy:** MCX India official daily settlement file; free; used for multi-year IBJA-proxy backfill depth.
- **notification_state.json:** Gitignored per-machine state file tracking cooldowns and quiet-hours queue. Persisted across CI runs via GitHub Actions cache (`notification-state-{repo}-{branch}`). Cache miss = fresh state = at most one duplicate alert per trigger.
- **T5 (model degraded):** New trigger; fires when Chronos path fails and LightGBM legacy path runs. Signal is `model_fallback=true` in `forecast.json`. Max once per calendar day (UTC). Low priority.
- **IBJA primary URL:** `ibjarates.com` — sole source with both AM and PM rates. `ibja.co` cannot provide dual AM/PM (spec correction 2026-05-19; see incident log).
- **MCX:** Not used. Dropped from plan 2026-05-19. No automated INR MCX data path without Selenium or paid API. See incident log.
- **Basis adjustment:** Not needed. Single-series IBJA strategy means no MCX proxy to reconcile. Section §3.1.6 tombstoned.
- **Notification state cache:** master-branch only (`actions/cache/save` gated on `github.ref_name == 'master'`). PR runs start fresh. Cache key = `notification-state-{run_id}`; restore uses prefix match `notification-state-`.

---

## Calendar Reminders

| Date | Action |
|------|--------|
| ~~2026-05-25~~ RESOLVED 2026-05-28 | ~~Verify Chronos falsifiable bet from PR E probe~~ Resolved: Chronos predicted −2.29% (14,118.69 INR/g); price moved UP. Chronos lost. See Decision Log entry 2026-05-28. |
