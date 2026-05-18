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

### Phase 3 — Implementation Plan  🟡 IN REVIEW — 2026-05-18

#### 3.1 Data Layer Rebuild

##### 3.1.1 New scrapers / data sources

**Source A — IBJA daily rates (primary target series)**

| Field | Value |
|-------|-------|
| URL | `https://ibja.co/` (official IBJA site — primary; HTML table of AM/PM daily rates) |
| Fallback URL | `https://ibjarates.com/` (same data, different layout — fallback only) |
| Fields extracted | Date, 916-PM (22K closing), 916-AM (22K opening), 999-PM (24K), 750-PM (18K) |
| Frequency | Once daily (published by ~09:30 IST after AM fix; PM rate by ~17:00 IST) |
| robots.txt | ⚠️ **UNVERIFIED — CC must check `https://ibja.co/robots.txt` manually and document findings in the PR C description before PR C is opened.** If blocked on primary, check fallback `https://ibjarates.com/robots.txt`. If both block scrapers, fall back to Source C (Metals.Dev free tier, 100 requests/month — insufficient for daily; would require rate-limited weekly fetch). |
| Auth | None observed; no login required for daily rate table |
| New file | `ml/ibja.py` |

> **IBJA backfill plan:** ibja.co (official) publishes current rates. Historical depth via ibja.co is unverified; third-party aggregators have accumulated multi-year IBJA history. **Before PR C:** manually verify whether ibja.co exposes HTML history beyond 30 days. If not, Source B (MCX) provides sufficient price-level continuity for Chronos context.

**Source B — MCX Gold near-month daily settlement (two complementary roles)**

B1 and B2 serve different roles and are both used — they are not alternatives to each other:

| | **B1: MCX Bhavcopy** | **B2: yfinance `GOLD.MCX`** |
|---|---|---|
| URL | `https://www.mcxindia.com/market-data/bhavcopy` | `yfinance.download("GOLD.MCX", ...)` |
| Format | Per-day CSV download (one file per date) | DataFrame, same API already wired |
| History available | Multi-year (MCX launched 2003; daily files assumed available from ~2010) | ~3 years via yfinance |
| INR denominated? | Yes (INR/10g settlement price) | Yes |
| Auth | None — public portal download | None (unofficial API) |
| Python integration | Custom downloader loop by date range | Already in `ml/macro.py` framework |
| Known limitations | No bulk download API; must loop dates or scrape filenames; URL pattern may require inspection | Unofficial; schema instability precedent (same issue as GC=F in macro.py) |
| robots.txt | UNVERIFIED | N/A (API, not scrape) |
| **Role** | **One-time historical backfill** — run once to build the initial 730-day corpus; result written to `data/mcx_gold.parquet` | **Ongoing daily updates** — incremental daily fetch appended to `data/mcx_gold.parquet` each CI run |

**Usage split:**
- **B1 (Bhavcopy):** executed once (manually or in a setup script) to backfill 730 days of MCX settlement history. Not re-run in routine CI.
- **B2 (yfinance):** called in `check-price.yml` on every 6h run to append the latest day's MCX close. Continues even if B1 is unavailable in future — it's the live feed.

This split avoids hammering the Bhavcopy portal daily while keeping the price series current via the already-trusted yfinance code path.

**Source C — yfinance USD/INR + Gold-USD spot (macro features, keep)**

Already wired in `ml/macro.py`. No change. Used as covariates in the LightGBM residual head (Phase 4 stretch); not needed for Chronos.

**Source D — Tanishq live scrape (keep as-is)**

`scraper/scrape.js` — Playwright scraping Tanishq. No change. This remains the ground-truth retail series; IBJA is the modeled series.

##### 3.1.2 Storage schema

Two new Parquet tables (gitignored, regenerated in CI):

**`data/ibja_rates.parquet`**

| Column | Type | Description |
|--------|------|-------------|
| `date` | `date` (index) | UTC calendar date |
| `purity_916_am` | `float32` | IBJA 22K AM fix (INR/g) |
| `purity_916_pm` | `float32` | IBJA 22K PM fix (INR/g) — **primary modeled series** |
| `purity_999_pm` | `float32` | 24K PM fix |
| `purity_750_pm` | `float32` | 18K PM fix |
| `source` | `str` | `"ibjarates.com"` or `"mcx-backfill"` |

**`data/mcx_gold.parquet`**

| Column | Type | Description |
|--------|------|-------------|
| `date` | `date` (index) | Settlement date |
| `mcx_gold_settle` | `float32` | Near-month MCX Gold settlement (INR/10g) |
| `mcx_gold_22k_equiv` | `float32` | Derived: `settle × (22/24) / 10` = INR/g 22K equiv |

**Update `.gitignore`** to add:
```
data/ibja_rates.parquet
data/mcx_gold.parquet
data/notification_state.json
```

##### 3.1.3 Calibration layer

Tanishq retail price = IBJA-916-PM × `premium_factor` + `fixed_markup`

With 71 overlap readings (2026-04-14 to 2026-05-17), fit a robust regression: `tanishq_22k ~ ibja_916_pm`. Expected premium_factor ≈ 1.04–1.08 (GST 3% + making charges ≈ 1–5%). Simple ratio model is sufficient; R² should be ≥ 0.98.

> **⚠️ Pre-Phase-3 verification task (before PR D):** Print one current Tanishq scrape value next to the same-day IBJA-916-PM rate. Confirm whether Tanishq displays the price **pre-GST or post-GST**. If post-GST (3% already included), the premium_factor will be ~1.01–1.05 (making charges only). If pre-GST, the premium_factor will be ~1.04–1.08. Document the finding in the PR D description and hardcode the correct interpretation as a comment in `ml/calibration.py`.

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
| `ml/ibja.py` | **ADD** | Scraper + Parquet cache manager for IBJA rates |
| `ml/mcx.py` | **ADD** | MCX Bhavcopy one-time backfill downloader + Parquet cache; yfinance daily append logic |
| `ml/calibration.py` | **ADD** | HuberRegressor calibration layer (IBJA→Tanishq) |
| `ml/macro.py` | **EDIT L44–54** | Add `mcx_gold_settle` to ticker-map or load from `mcx_gold.parquet`; no macro changes needed for Chronos path |
| `data/history_seed.json` | **MOVE** | → `archive/history_seed_synthetic.json` |
| `data/history_seed_v1_uniform_premium.json` | **MOVE** | → `archive/history_seed_v1_uniform_premium.json` |
| `ml/seed_history.py` | **DELETE** | No longer needed; move to `archive/scripts/seed_history.py` |
| `.gitignore` | **EDIT** | Add `data/ibja_rates.parquet`, `data/mcx_gold.parquet`, `data/notification_state.json` |

##### 3.1.5 Migration: keeping live CI intact

A `FORECAST_ENGINE` GitHub Actions variable (not secret) gates the inference path:

- **`FORECAST_ENGINE=legacy`** (default during PRs A–G): current LightGBM path runs unchanged. IBJA data fetch is an additive CI step (`continue-on-error: true`).
- **`FORECAST_ENGINE=chronos`** (set in PR H): new Chronos path becomes active.

The flag is read inside `ml/inference.py` via `os.environ.get("FORECAST_ENGINE", "legacy")`. Live CI never breaks for more than one cycle: if the new path raises, it logs and falls back to the legacy path within the same run.

**T5 fallback behaviour:** When the Chronos path raises and the legacy LightGBM path runs instead, two things happen atomically before `inference.py` exits:
1. `forecast.json` is written with `"model_fallback": true` (field added to schema in PR E).
2. The next `check-price.yml` step that calls `ml/notifications.py` reads `model_fallback=true` and fires **T5** (see §3.4.2). This ensures silent fallbacks are surfaced as a low-priority alert within the same CI cycle.

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
  "model_fallback": false
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
| **T5** Model degraded | `model_fallback=true` in `forecast.json` (Chronos path failed; legacy LightGBM ran instead) | low (2) | No fixed cooldown — max **once per calendar day** (fire on first occurrence per UTC date only) | N/A | `warning,rotating_light` | No |

> **T1/T2 gating rationale:** The tighter gate (`≤ naive_mae_5d` vs the previous `< 1.5 × naive_mae_5d`) ensures T1/T2 only fire when the model is actually beating naive — not merely within 50% above it. The `backtest_fold_count ≥ 60` requirement ensures gating is based on a statistically meaningful backtest window (≥60 fold-days), not an early-run estimate with high variance.

> **T5 gating rationale:** T5 has no fixed cooldown because the failure condition (`model_fallback=true`) is already transient — it only appears in `forecast.json` for one CI cycle. The calendar-day dedup prevents T5 from firing on every 6h run during a prolonged outage. T5 does not count toward the T1–T3 combined cap of 3 per 24h.

**Anti-spam:** T1 + T2 + T3 combined max = **3 per rolling 24h window**. T4 is exempt and does not count toward this cap.

**Global quiet hours:** 22:00–07:00 IST. T1, T2, T3 alerts triggered during quiet hours are **queued** in `NotificationState.queued`; the first `check_triggers()` call after 07:00 IST delivers them (if still ≤12h old; discard otherwise). T4 always delivers at 18:00 IST.

##### 3.4.3 State persistence

**File:** `data/notification_state.json` — **gitignored** (per-machine state, not part of repo).

**Rationale for gitignored:** The state file tracks cooldown timestamps. If committed, bot commits would clutter git history for every alert state change.

**GitHub Actions cache (primary persistence mechanism):**

The state file is persisted across CI runs via `actions/cache`. Without this, cooldowns would reset on every fresh checkout and T1/T2/T3 could fire multiple times per day.

```yaml
# In check-price.yml — restore before notifications step
- name: Restore notification state
  uses: actions/cache@v4
  with:
    path: data/notification_state.json
    key: notification-state-${{ github.repository }}-${{ github.ref_name }}

# ... notifications step runs here, writes data/notification_state.json ...

# In check-price.yml — save after notifications step
- name: Save notification state
  uses: actions/cache@v4
  with:
    path: data/notification_state.json
    key: notification-state-${{ github.repository }}-${{ github.ref_name }}
```

> **Note:** `actions/cache` does not support updating an existing key within the same workflow run. The recommended pattern is: restore with the same key (which will hit on subsequent runs) and save unconditionally — the cache API accepts overwrites for the same key on re-save.

**Save trigger:** After every `check_triggers()` + `send_pending()` call (i.e., at the end of every CI notifications step). Always save, even if no alerts fired — the `last_sent` timestamps and quiet-hours queue must persist.

**Cache miss behaviour (cold runner or cache eviction):** Treat as fresh state — no cooldowns enforced. Worst case: at most one duplicate alert per trigger per cache eviction event. This is acceptable; cache eviction on GitHub Actions is infrequent (7-day LRU by default). Log `"notification_state: cache miss, starting fresh"` to stdout on first run.

**Schema (`data/notification_state.json`):**
```json
{
  "last_sent": {"T1": "2026-05-18T14:00:00+05:30", "T4": "2026-05-17T18:00:00+05:30"},
  "queued": [],
  "sent_today_triggers": ["T3"],
  "t5_last_fired_date": "2026-05-18",
  "schema_version": 1
}
```

> `t5_last_fired_date` (UTC calendar date string) is the dedup key for T5's once-per-day constraint. If `t5_last_fired_date == today_utc`, T5 is suppressed regardless of `model_fallback` state.

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
| **PR C** | `feat(data): IBJA + MCX data layer` | Add `ml/ibja.py`, `ml/mcx.py`; update `.gitignore`; add IBJA fetch step in `check-price.yml` (`continue-on-error: true`); add `tests/test_ibja.py`, `tests/test_mcx.py` | IBJA fetch step added to CI; no inference change | `FORECAST_ENGINE=legacy` (default) |
| **PR D** | `feat(ml): Tanishq-vs-IBJA calibration layer` | Add `ml/calibration.py`; `data/calibration.json` bootstrapped from 71 overlap readings; add `tests/test_calibration.py` | No inference change | `FORECAST_ENGINE=legacy` |
| **PR E** | `feat(ml): Chronos-Bolt-Tiny inference path (flag off)` | Add `ml/chronos_forecast.py`; update `ml/inference.py` with `FORECAST_ENGINE=chronos` branch; update `forecast.json` schema (backward-compatible aliases); add HF model cache step in CI; update `tests/test_inference_main.py` for both paths; draft ADR 009 | Chronos installed in CI but **not called** (`FORECAST_ENGINE=legacy`); CI timing probe logged | `FORECAST_ENGINE=legacy` |
| **PR F** | `feat(ml): walk-forward backtest at h=5` | Rewrite `ml/backtest.py` for h=5 Chronos protocol; update `ml/metrics.py` for 5d decision rule; run new backtest, commit `data/backtest.json`; update `weekly-backtest.yml` | Backtest results updated in CI | N/A |
| **PR G** | `feat: notification system` | Add `ml/notifications.py`, `tests/test_notifications.py`; wire into `check-price.yml`; disable `daily_summary.yml` + mark `daily_summary.py` deprecated; draft ADR 011 | New ntfy alerts begin firing | `FORECAST_ENGINE=legacy` |
| **PR H** | `feat(ml): flip to Chronos + final cleanup` | Set `FORECAST_ENGINE=chronos` in `check-price.yml`; delete `ml/regime.py`, `ml/forecast.py`, `ml/compare_feature_sets.py`, `ml/seed_history.py`, `ml/daily_summary.py`, `ml/tuning/study.py`, `tests/test_regime.py`, `tests/test_daily_summary.py`; delete TFT/N-BEATS configs; update README architecture section | **Chronos becomes live production path** | Flag becomes permanent; variable removed |

**Ordering constraints:**
- PR B must land before PR C (archive seed before IBJA goes live to avoid dual-source confusion).
- PR D must land before PR E (calibration must exist before Chronos path can produce Tanishq-level output).
- **PR F depends on PR E** (the h=5 walk-forward backtest calls the Chronos inference path directly; the `ml/chronos_forecast.py` module added in PR E must exist before PR F's backtest rewrite can be tested end-to-end).
- PR E must land before PR H (Chronos path must be in the codebase, timing-validated, and CI entry point updated before the flag is flipped).

---

#### 3.7 Risks & Open Questions

**New risks introduced by the pivot:**

| Risk | Severity | Mitigation |
|------|----------|-----------|
| IBJA robots.txt may block scrapers (unverified) | Major | **CC verifies `ibja.co/robots.txt` (primary) and `ibjarates.com/robots.txt` (fallback) before PR C is opened.** Document findings in PR C description. If both block: fall back to MCX Bhavcopy for primary IBJA-substitute series; calibration layer unchanged. |
| Chronos-Bolt CPU inference latency on Actions runner is unverified (estimated 2–5s; could be 30s+) | Major | **PR E must include a wall-clock timing probe.** If > 15s, switch to Chronos-Bolt-Tiny with `num_samples=1` (deterministic mode) for P50 only; PI falls back to conformal. Flag PR E timing results before PR H. |
| PyTorch wheel download (280 MB CPU-only) inflates CI cold-start by ~3 min | Minor | `actions/cache` on `ml/requirements-inference.lock` hash; verify cache hit rate in PR E. |
| HuggingFace Hub unavailable during CI run (transient) | Minor | `continue-on-error: true` on model download step; fall back to legacy LightGBM path for that cycle (feature flag); T5 fires to surface the fallback. |
| ibja.co or ibjarates.com HTML structure changes break IBJA scraper | Major | Selector-based scraper + `prices_rejected.json` fallback pattern (existing precedent from `scraper/`); ntfy alert on scraper failure (existing pattern from `check-price.yml`). |
| MCX Bhavcopy URL pattern changes or requires login in future | Minor | yfinance `GOLD.MCX` as documented fallback (Option B2); degrade gracefully. |
| Calibration model has only 71 observations at launch | Minor | 71 points is sufficient for a 1-parameter ratio; R² expected ≥ 0.98. HuberRegressor chosen for robustness to outliers. Refit threshold (10 new readings) will improve it quickly. Log calibration params and residual_std to `forecast.json` for auditing. |
| LightGBM residual head (Phase 4) may not clear the 2% promotion gate | Low | By design — it's a stretch goal. Chronos standalone is the primary; LightGBM is optional. |

**Open questions — all answered:**

1. ✅ **IBJA robots.txt:** **CC checks `ibja.co/robots.txt` manually** (primary) and `ibjarates.com/robots.txt` (fallback). Document findings in the PR C description. No legal review required before PR C — this is a pre-scrape due-diligence check, not a formal TOS review.

2. ✅ **MCX Bhavcopy backfill depth:** **730 days (2 years)** — run `--lookback-days 730` for the B1 one-time backfill. This is well within Chronos-Bolt's 2,048-observation maximum and gives the model two full annual demand cycles.

3. ✅ **Notification T3 vs existing drop alert:** **T1 replaces the existing `drop_threshold=100` alert in `check-price.yml`** entirely. The old `drop_threshold=100` config variable in `check-price.yml` is **retired in PR G**. T3 (₹150 actual move) is a new trigger — it is not a duplicate of T1 because T3 fires on *observed* moves (model-agnostic), while T1 fires on *predicted* 5d moves. No parallel run; no duplicates.

4. ✅ **PWA 5-day chart UI:** **Deferred to Phase 4.** During Phase 3 transition, the PWA continues to show the day-1 point estimate (computed from `forecast.days[0].p50`). The `forecast.json` backward-compatible aliases (`predicted_22k`, `lower`, `upper`) preserve existing PWA behaviour without any `app.js` changes in Phase 3.

5. ✅ **LightGBM residual head gate (Phase 4):** **Gate confirmed: `0.80 ≤ mae_chronos / naive_mae_5d ≤ 1.00`.** Attempt the residual head only when Chronos is beating naive (ratio ≤ 1.00) but within 20% of naive (ratio ≥ 0.80) — i.e., beating naive but with headroom to improve. If Chronos achieves ratio < 0.80 (>20% better than naive), residual head is unnecessary. If ratio > 1.00 (Chronos worse than naive), the residual head is not the right fix — revisit context window or model choice first. This gate is reflected in §3.2.4.

---

### Phase 4 — Build  ⏸️ NOT STARTED
*Previously Phase 3. Will begin after Phase 3 plan is approved by consultant and GG.*

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
| 2026-05-18 | IBJA primary URL = ibja.co (official) | Consultant | ibjarates.com is a third-party aggregator; ibja.co is the authoritative source |
| 2026-05-18 | Calibration model = HuberRegressor (not OLS) | Consultant | OLS is sensitive to Tanishq promotional outliers; HuberRegressor is robust to occasional spread deviations |
| 2026-05-18 | Chronos context = 365d baseline / 730d upgrade | Consultant | 60d was insufficient for seasonal signal; 365d captures full annual demand cycle |
| 2026-05-18 | MCX backfill = 730 days (B1 one-time) + yfinance daily (B2 ongoing) | Consultant (Q2) | Clear role split: B1 for depth, B2 for currency; avoids hammering Bhavcopy portal daily |
| 2026-05-18 | T1 replaces drop_threshold=100 alert; T3 is new (observed moves) | Consultant (Q3) | No parallel alerts; retire drop_threshold=100 config variable in PR G |
| 2026-05-18 | PWA 5d chart UI deferred to Phase 4 | Consultant (Q4) | day-1 alias in forecast.json preserves existing PWA; chart work not in Phase 3 scope |
| 2026-05-18 | Residual head gate = 0.80 ≤ mae_chronos/naive ≤ 1.00 | Consultant (Q5) | Residual head only when beating naive but with headroom; if ratio < 0.80, standalone Chronos is sufficient |
| 2026-05-18 | Add T5 (model degraded) trigger | Consultant (critical fix) | Silent Chronos failures must surface as low-priority ntfy; model_fallback=true in forecast.json is the signal |
| 2026-05-18 | T1/T2 gate = ≤ naive_mae AND fold_count ≥ 60 | Consultant (medium fix) | Previous 1.5× threshold was too loose; new gate requires model to actually beat naive |

---

## Risks Register

| Risk | Severity | Owner | Mitigation status |
|------|----------|-------|-------------------|
| Model 34.6% worse than naive on backtest (MAE ₹225.33 vs ₹167.36, 69 folds) | Blocker | Consultant | Chronos-Bolt pivot approved; new h=5 backtest will establish new baseline |
| 86% synthetic training data | Major | GG (resolved) | Resolved: synthetic seed dropped. Real-only corpus from PR B onward |
| IBJA robots.txt unverified — may block scraper | Major | CC | Must verify `ibja.co/robots.txt` (primary) and `ibjarates.com/robots.txt` (fallback) before PR C; document findings in PR C description; fallback plan is MCX series |
| Chronos-Bolt CPU latency unverified on Actions runner | Major | CC | Timing probe in PR E; fallback is `num_samples=1` deterministic mode |
| PyTorch cold-start CI overhead (~3 min) | Minor | CC | `actions/cache` in PR E; confirmed as acceptable |
| Unpinned deps (yfinance schema risk) | Major | CC | Lockfile added in PR A |
| `inference.py` has no dedicated test | Major | CC | Smoke test added in PR A |
| WANDB env vars stale in `.env` | Minor | CC | Remove in PR A (local .env change only; gitignored) |
| Regime feature dead-weight | Minor | CC | Resolved: `ml/regime.py` deleted in PR H |
| Sentry DSN placeholder not activated | Minor | GG | Unchanged; Sentry not required for Phase 3 |
| ADR 006 numbering gap | Minor | GG | Pending; ADRs 009–011 drafted in Phase 3 PRs |

---

## Glossary / Pointers
- **Naive baseline (updated):** `naive_5d` — hold current price flat for 5 days. Replaces `naive_1step`.
- **Walk-forward backtest:** `ml/backtest.py`, 180-day window (was 90), h=5 (was h=1), Chronos zero-shot.
- **Honest-baseline ADR:** `docs/adr/005-honest-baseline-reporting.md`.
- **Warmup flag:** `forecast.json:warmup = true` while `real_readings_count < 100`; also gates T1/T2 notifications.
- **Production model artifacts (post-pivot):** `models/production/lgbm.txt` (legacy, kept until PR H); ONNX artifacts deleted in PR B.
- **Calibration layer:** `ml/calibration.py` + `data/calibration.json` — HuberRegressor(ibja_916_pm, tanishq_22k) from 71 overlap readings. Premium factor ≈ 1.04–1.08 (post-GST) or 1.01–1.05 (pre-GST — verify before PR D).
- **FORECAST_ENGINE flag:** env var in `check-price.yml`; `legacy` = LightGBM (safe default); `chronos` = Chronos-Bolt (active from PR H).
- **Chronos-Bolt-Tiny:** `amazon/chronos-bolt-tiny` on HuggingFace; 9M params; 8.65 MB weights; installed via `pip install chronos-forecasting` (requires torch CPU-only wheel first).
- **IBJA:** India Bullion and Jewellers Association; 916-PM rate = daily closing 22K benchmark in INR/g. Primary modeled series.
- **MCX Bhavcopy:** MCX India official daily settlement file; free; used for multi-year IBJA-proxy backfill depth.
- **notification_state.json:** Gitignored per-machine state file tracking cooldowns and quiet-hours queue. Persisted across CI runs via GitHub Actions cache (`notification-state-{repo}-{branch}`). Cache miss = fresh state = at most one duplicate alert per trigger.
- **T5 (model degraded):** New trigger; fires when Chronos path fails and LightGBM legacy path runs. Signal is `model_fallback=true` in `forecast.json`. Max once per calendar day (UTC). Low priority.
- **IBJA primary URL:** `ibja.co` (official IBJA site). `ibjarates.com` is fallback only.
- **MCX backfill roles:** B1 = MCX Bhavcopy (one-time 730-day historical backfill); B2 = yfinance GOLD.MCX (ongoing daily append in CI).
