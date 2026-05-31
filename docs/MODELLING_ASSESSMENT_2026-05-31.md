# Modelling Assessment — 2026-05-31

**Status:** Read-only diagnosis. No code or data changes.  
**Purpose:** Assess where model/data stand after the Ψ3C UI sprint.  
**Awaiting:** Consultant + GG direction before any implementation.

---

## 1. Data Inventory

### 1.1 Tanishq prices.json

| Item | Value |
|---|---|
| Total readings | **125** (up from 71 at Phase 3 close; 54 new real readings) |
| Date range | 2026-04-14 to 2026-05-31 |
| Expected readings (4×/day, 47 days) | ~188 |
| Gaps >9h | 34 of 124 transitions = **27.4%** |
| Gaps >12h | 31 |
| Consecutive flat holds (22K identical across successive readings) | 0 (every stored reading has a distinct timestamp; flat holds are visual-only and handled in `app.js:dedupReadings`) |

**Note on gap rate:** The 27.4% figure is the **pre-ADR-016 baseline**. ADR 016 (scraper hardening) merged on 2026-05-31 — the same day as this assessment. No post-hardening production data yet. ADR 016 estimates 27% → ~10–15% for transient CF failures; IP-level blocks remain a gap source. Verdict: baseline confirmed, impact TBD in ~4 weeks.

### 1.2 IBJA ibja_rates.parquet

| Item | Value |
|---|---|
| Total rows | **185** (Wayback ceiling: 177 + 8 live appends since 2026-05-19) |
| Date range | 2022-01-19 to 2026-05-29 |
| Live appends added | 2026-05-19, 05-20, 05-21, 05-22, 05-25, 05-26, 05-27, 05-29 (8 trading days) |
| Missing trading day | 2026-05-28 (Thursday) — unexplained gap; ibja-append either failed or ibjarates.com didn't publish that day |
| Accumulation rate | ~20–22 trading days/month at current cadence |

**⚠ Probe staleness:** `data/chronos_probe.json` (committed 2026-05-31T00:03Z) shows `ibja_context_days: 177` and `ibja_last_date: 2026-05-18` — i.e., it reflects the Wayback-ceiling snapshot, NOT the 185-row current parquet. The 8 new rows (2026-05-19 to 2026-05-29) are in the parquet but not in the probe the live forecast currently uses. Most likely cause: the probe CI step committed on 2026-05-31T00:03Z with a run in which ibja-append had failed; subsequent ibja-append successes updated the parquet (committed at the end-of-run step) but the intermediate probe commit step either failed or its changes were overwritten. The live directional signal is therefore based on IBJA context that is 13 calendar days stale. This is a CI reliability issue, not a data correctness issue.

### 1.3 Tanishq/IBJA Overlap Pairs (Calibration Gate)

| Item | Value |
|---|---|
| Overlap pairs (Tanishq UTC date ∩ IBJA date) | **29** |
| Pairs at last calibration fit (2026-05-19) | 21 |
| New pairs since last fit | 8 |
| Pairs needed to flip calibration valid | 30 (1 more) |
| Overlap date range | 2026-04-17 to 2026-05-29 |

---

## 2. Calibration Status

### 2.1 Current State

`data/calibration.json`: `valid: false`, `n_observations: 21`, `fit_date: 2026-05-19`. **This file is frozen at its initial Phase 3 stub — it has not been updated since then despite 8 new pairs accumulating.**

| Field | Value |
|---|---|
| valid | **false** |
| n_observations in file | 21 (stale) |
| Actual overlap pairs on disk | **29** |
| Pairs needed to flip | **1 more** |
| Applied to live forecast | **No** (`calibration_applied: false` in forecast.json) |
| calibration_just_unlocked | false |
| T6 (calibration-unlock notification) | Never fired |

### 2.2 Root Cause: Calibration Wiring is Missing

**`fit_calibration()` and `should_refit()` are defined in `ml/calibration.py` and tested in `tests/test_calibration.py`, but are never called from any production CI step.** They are dead-to-production code.

`check-price.yml` has no step that calls `python -m ml.calibration` or invokes `fit_calibration()`. The IBJA-append step only updates the parquet; inference.py only reads calibration.json without refitting it.

**Consequence:** The "self-flip at 30 pairs" described in `CURRENT_STATE.md`, `ADR 014`, and the ADR 016 H5 deferral rationale will NOT happen automatically. calibration.json will remain `valid: false` indefinitely regardless of how many pairs accumulate, until a calibration refit step is explicitly added to the CI pipeline.

**This contradicts the documented system behavior.** This is a gap introduced silently during the Phase 3 implementation — the calibration machinery was built and tested, but the CI wiring step was never added.

### 2.3 Unlock ETA (if bug fixed)

With calibration wiring added to CI:
- 1 more overlap pair needed (next IBJA trading day = 2026-06-01 Monday)
- `should_refit()` requires 10 new pairs since last fit: currently 8/10 → would fire at 2 more pairs (2026-06-02/03)
- Calibration would flip `valid: true` on the refit run, and T6 would fire once

---

## 3. Model Performance — Current Numbers

### 3.1 Walk-Forward Backtest (Last Run: 2026-05-24)

Backtest used the **177-row parquet snapshot** (Wayback ceiling). 8 new IBJA rows (to 2026-05-29) are not reflected. The parquet now has 185 rows; a fresh backtest would yield ~180 folds (~15 more than current 165).

| Metric | Value | vs ADR 012 |
|---|---|---|
| Folds | 165 | Unchanged |
| Naive MAE (overall, h=1..5 avg) | **Rs.249.53** | Same |
| Chronos MAE (overall, h=1..5 avg) | **Rs.275.5** | Same |
| Chronos gap vs naive | **+10.4% worse** (p=0.0089) | Same |
| Direction acc (overall) | **55.76%** | +0% (55.8% in ADR 012) |
| Direction acc (last 30 folds) | **63.3%** (19/30) | Same as ADR 012 |
| PI 80% coverage (avg) | 87.0% | Same |
| Decision acc (Rs.100 drop prediction) | Precision 32.4%, Recall 23.4% | New figure |

**Last-30-fold breakdown** (folds 135–164, context dates 2025-11-28 to 2026-05-11 — the strong uptrend era):

| Metric | Value |
|---|---|
| Naive avg MAE (h=1..5) | Rs.358.2 |
| Chronos avg MAE (h=1..5) | Rs.413.6 |
| Naive h=5 MAE (used for conformal PI) | Rs.566.9 |
| Chronos h=5 MAE (last 30 folds) | Rs.585.7 |
| Chronos h=5 gap vs naive (last 30) | **+3.3%** (vs +10.4% overall) |

The h=5 MAE gap has **narrowed** from 10.4% overall to 3.3% in the last 30 folds (the post-2025 uptrend regime). This is directionally encouraging but still Chronos-trailing.

### 3.2 Conformal PI (Current Live)

From forecast.json (computed from last 30 folds' h=5 naive errors):

| Field | Value |
|---|---|
| naive_mae_recent_30 | Rs.566.9 |
| conformal_pi_half (80th pct) | Rs.935.7 |
| Live PI band at Rs.14,440 | [13,504, 15,376] = ±6.5% |

The bands are wide because the 2025-2026 uptrend made 5-day naive errors large (price moved Rs.200–2000 in 5 days during bull runs).

### 3.3 Chronos Probe (Current — but 13 days stale)

From chronos_probe.json (based on 177-row IBJA context, last date 2026-05-18):

| Field | Value |
|---|---|
| Status | success |
| num_samples | 5 |
| sample_directions | ["up","up","up","up","up"] |
| majority_direction | up |
| direction_consensus | 1.0 (5/5 agree) |
| lean_strength_pct | 2.645% |
| ibja_last_date | 2026-05-18 (stale by 13 days) |

The probe shows a 5/5 unanimous "up" signal with 2.6% lean. This would satisfy T1/T2 gates (consensus ≥ 0.6, strength ≥ 0.5%, dir_acc_30f = 0.633 ≥ 0.55). However, the probe is using 13-day-old IBJA context — it doesn't reflect the 2026-05-19 to 2026-05-29 price action. A fresh probe run would be needed to trust the current signal.

### 3.4 Drift Monitor

`data/drift_metrics.json` contains entries from 2026-05-11 to 2026-05-12 with `model_version: "ensemble-inv-mae"` — **legacy LightGBM era, not the current naive headline.** The drift monitor is running (`continue-on-error: true` in CI) but its output is stale/legacy. It is not providing meaningful signal about the current naive headline path.

---

## 4. Does ADR 012 Still Hold?

**Yes. ADR 012 still holds.**

The backtest from 2026-05-24 is numerically identical to the ADR 012 backtest (same 165-fold dataset, same methodology). No new backtest has run with the 8 additional IBJA rows.

**With the additional evidence from the last-30-fold breakdown:** the h=5 gap has narrowed from 10.4% to 3.3% in the uptrend regime. This is potentially meaningful but does not reverse the finding — Chronos is still trailing naive. More importantly, the narrowing is likely driven by the strong uptrend making flat-hold less competitive (naive systematically undershoots in a rising market, and Chronos may benefit), not by Chronos becoming fundamentally better.

**Trigger for re-evaluating ADR 012:** a fresh backtest with ≥250 IBJA rows (ADR 012 promotion criterion). Current: 185 rows, ~65 more needed, ETA August–September 2026.

**Flag — nothing contradicts ADR 012 with statistical significance.** The 3.3% h=5 gap in last 30 folds is in the right direction to watch, but it's not a fresh Wilcoxon result. It would need to survive a full 250-row backtest before any narrative update.

---

## 5. Honest Options for Improvement

### Option 1: Wire calibration refit into CI [HIGH VALUE, ACTIONABLE]

**What:** Add a `python -m ml.calibration` (or equivalent) CI step after `Append IBJA rates` that calls `fit_calibration()` when `should_refit()` returns True, then `save_calibration()`. Wire the result into the main data commit.

**Expected payoff:**
- Calibration flips `valid: true` within 1–2 trading days (1 more pair needed)
- Chronos companion horizon arrays start being calibrated to Tanishq retail prices (the `calibration_applied: true` path in `forecast.json.chronos_companion`)
- T6 (calibration-unlock notification) fires once
- Unblocks H5 (IBJA-fallback reading for scraper failures, ADR 016) when the time comes

**Data supports it:** 29 pairs on disk. The calibration function is tested and correct. The only missing piece is the CI wiring.

**Complexity:** Low — a new CI step (or addition to an existing step), plus verifying the calibration flip in a test run. No model changes.

**Honest caveat:** The first refit will use 29–31 overlap pairs over ~6 weeks (2026-04-17 to ~2026-06-01). HuberRegressor at this scale will produce slope/intercept that may still have non-trivial residual_std. Monitor `residual_std` after the first refit.

---

### Option 2: Fix probe staleness [MEDIUM VALUE, LOW EFFORT]

**What:** Debug why `data/chronos_probe.json` is not being recommitted in CI runs after 2026-05-31T00:03Z. Likely cause: intermediate probe commit step failing after git-rebase conflict (the ibja-parquet commit and probe commit both push independently, creating race conditions). Fix: consolidate into the single end-of-run commit.

**Expected payoff:** Live forecast uses current IBJA context (185 rows vs 177). Directional signal quality reflects May 19–29 data. Minor improvement.

**Data supports it:** Immediate value, zero risk.

---

### Option 3: Fresh backtest with 185-row parquet [MEDIUM VALUE, MEDIUM EFFORT]

**What:** Manually trigger `weekly-backtest.yml` (or let it run on its schedule). This would produce ~180 folds covering through 2026-05-24 context (the 8 new rows add folds 165-179).

**Expected payoff:**
- Updated `naive_mae_recent_30` and conformal PI (currently based on folds through 2026-05-11)
- Updated `dir_acc_30f` — the direction accuracy that gates T1/T2
- The last 15 new folds cover April-May 2026 action (IBJA ~13,500–14,449/g range). Given the 3.3% h=5 gap trend, these folds will likely tighten the gap slightly further, but are unlikely to reverse the finding.

**Data supports it:** 185 rows available. Takes ~20 minutes on CI (Chronos walk-forward).

---

### Option 4: Nothing until 250 rows (ADR 012 promotion criterion) [HONEST BASELINE]

**What:** Keep accumulating. No model changes. Fix calibration wiring (Option 1) and probe staleness (Option 2) as engineering hygiene, but do no model work.

**Rationale:**
- Chronos trails naive at all measured timeframes. The 3.3% narrowing in last-30-folds is directionally interesting but not statistically actionable.
- The promotion criterion (≥250 rows, Chronos beats naive, Wilcoxon p<0.05) is the right bar. Current: 185 rows, ~65 more needed.
- At ~20–22 trading days/month: ETA for 250 rows is **August–September 2026**.
- The naive flat-hold headline continues to be honest and correct.
- The directional signal (63.3% last-30-fold accuracy) continues to provide value for T1/T2 and T8 digests.

**Expected payoff:** Nothing changes on the modelling side. Engineering hygiene (Options 1–3) keeps the system correct.

---

### What Is NOT Worth Doing Now

- **Switching to Chronos-2 or Moirai-MoE:** Both require the promotion criterion to be met first (ADR 012). Starting the switch before verifying naive-vs-Chronos with 250 rows would violate norm #5 (walk-forward evidence as precondition for model claims).
- **LightGBM residual corrector (Phase 4 stretch):** Requires Chronos to beat naive first (the residual head only makes sense when Chronos already has positive signal). Not applicable yet.
- **Adding macro covariates to Chronos:** Chronos-Bolt-Tiny is strictly univariate. Deferred to Phase 4 multivariate upgrade (Chronos-2/Moirai), gated by promotion criterion.
- **Manual calibration flip:** Explicitly prohibited in CURRENT_STATE.md. Correct fix is to wire the function, not bypass the gate.

---

## 6. Recommendation

In priority order:

**Immediate (before next modelling work):**
1. **Fix calibration wiring (Option 1)** — this is a latent implementation bug, not a model decision. The system claims calibration will self-update; it will not. One CI step change closes this gap and unlocks calibration within 1–2 trading days.

**Housekeeping (low effort, restore data fidelity):**
2. **Fix probe staleness (Option 2)** — the live directional signal should use current IBJA context, not 13-day-old data.
3. **Trigger fresh backtest (Option 3)** — update dir_acc_30f and naive_mae_recent_30 to cover the April-May 2026 uptrend period.

**Model posture:**
4. **No model changes (Option 4 remains the base case)** — ADR 012 holds. Keep accumulating toward 250 rows. Revisit Chronos promotion in August–September 2026.

---

MODELLING ASSESSMENT COMPLETE — AWAITING CONSULTANT REVIEW.
