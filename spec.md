# Spec — Batch Φ10A: Driver-Decomposition Forecast Experiment (analysis only)

**Date:** 2026-06-03
**Author:** External consultant (via GG) → Orchestrator (CC)
**Status:** Draft for orchestrator execution
**Type:** ANALYSIS ONLY. No production-path change (no edit to inference.py, forecast.json,
app.js, notifications.py, check-price.yml). Pre-registered gate fixed BEFORE results exist.

---

## Hypothesis & why this is different from Φ7

Indian retail gold ≈ international_gold_USD × USD/INR × (duty/GST factor) × local_premium. A
UNIVARIATE model (Chronos, naive) structurally cannot see USD/INR or international gold moves.
Φ7's premium-carry experiment tested the FLAT-CARRY version (hold all drivers constant) and got
the algebraic null — but we explicitly deferred the DRIVER-FORECAST version. This batch runs that:
forecast the drivers (especially USD/INR, which has structural drift properties gold lacks),
compose into an IBJA forecast, and test whether it beats flat-naive.

**The key reason this could differ from Φ7:** USD/INR and gold-USD have YEARS of yfinance history,
which can lend statistical power even though the IBJA target series is short (~177 rows). This is
the central uncertainty — see the flag-and-stop.

This is the experiment that tests GG's original instinct (a driver-aware model may beat naive where
a univariate one can't). If it fails the gate, that is a clean, valuable negative that closes the
question. If it passes, it becomes a production-model ADR proposal AND honest context for the
"is today a good price?" framing ("the rupee's been weakening, which tends to push gold up").

All norms apply — honest-baseline (#4/ADR 005), statistical relevance (#5), flag-and-stop (#1),
append-only PROGRESS (#10), no production change.

---

## FLAG-AND-STOP gate (resolve BEFORE building the experiment)

**Macro history depth aligned to IBJA (norm #5).** Report:
1. How many years of USD/INR and gold-USD daily history yfinance returns (the drivers).
2. The IBJA-916 series length and date range (the target).
3. The OVERLAP window where all three align cleanly to common trading dates (the backtest can only
   run where target + both drivers exist). Report gaps, weekend/holiday misalignment, and any
   forward-fill needed — do NOT silently forward-fill across large gaps.
4. The premium series: premium_t = IBJA_916_t / (gold_usd_t × usdinr_t) — its length, mean, and
   stability (std) over the overlap. A wildly unstable premium means the decomposition identity is
   noisy and the experiment is weaker.

Report all four BEFORE building. If the clean overlap is too short for ≥30 walk-forward folds at
h=5, STOP and report — the experiment may not have the power to clear the gate, and we decide
whether to proceed as exploratory-only or defer.

---

## Pre-registered promotion gate (fixed before results — same as Φ7 + non-bull requirement)

A driver-decomposition variant is promotable ONLY if ALL hold:
1. `mae_variant < mae_flat_naive` by ≥2%: `(mae_naive - mae_variant)/mae_naive >= 0.02`
2. Wilcoxon signed-rank p < 0.05 on paired per-fold absolute errors
3. Holds on ≥30-context folds only (sub-30-context excluded)
4. ≥30 such folds exist
5. **NEW (the Φ7D/ADR-018 lesson): does NOT invert on the non-bull subset.** Report the variant's
   signed performance on non-up folds (realised h=5 change ≤ 0). A variant that wins overall but
   loses catastrophically out-of-regime is a trend-continuation artifact, held not promoted (ADR
   018 precedent). Promotion requires it not be a pure-regime artifact.

Methodology: expanding-window walk-forward, h=5, same fold boundaries as ml/backtest.py so errors
pair with the existing naive/Chronos results (Wilcoxon needs paired folds). random_state=42.

---

## The experiment

**Variant: `driver_decomp`** — for each fold, at context end t, forecast IBJA_{t+h} as:
```
forecast_IBJA_{t+h} = premium_hat × gold_usd_hat_{t+h} × usdinr_hat_{t+h}
```
where:
- **usdinr_hat_{t+h}**: forecast USD/INR with a DRIFT-AWARE model. Test, in order of simplicity:
  (1) drift / random-walk-with-drift (last value + h × recent mean daily change),
  (2) simple ARIMA or ETS if (1) is insufficient.
  Report which. Start with (1) — cheapest, and it directly tests whether USD/INR drift carries
  signal. Do NOT overfit; ~years of data supports a simple model, not a high-variance one.
- **gold_usd_hat_{t+h}**: international gold is closer to a random walk — forecast it as
  random-walk (carry last value) AND, as a second variant, random-walk-with-drift. Report both.
- **premium_hat**: the local premium. Use the recent (e.g. trailing-30d) median premium — it should
  be relatively stable (confirm in the flag-and-stop). Hold it flat over the horizon (we are not
  forecasting premium; we are composing driver forecasts × stable premium).

**Variants to report (each against flat-naive, each with the full gate verdict):**
- `driver_decomp` with usdinr=drift, gold_usd=random-walk
- `driver_decomp` with usdinr=drift, gold_usd=drift
- (if ARIMA/ETS used for usdinr) that variant too

**Falsifiers (honest expected-null cases — report plainly if they occur):**
- If USD/INR is itself ~random-walk at h=5 (drift adds nothing), driver_decomp collapses toward
  flat-naive → null, report it.
- If the premium is unstable, the decomposition identity is noisy and may underperform → report.
- If gold_usd dominates and is random-walk, the whole thing reduces to "carry gold-USD" ≈ naive in
  INR terms after FX → report.

---

## Deliverables

1. **Code:** experiment script under scripts/ or ml/experiments/ (reuse ml/backtest.py fold logic +
   ml/metrics.py — do NOT duplicate fold generation). New non-trivial functions get mocked-data unit
   tests (norm #11).
2. **Committed artifact:** data/experiments/phi10a_driver_decomp.json — per variant:
   {name, usdinr_method, gold_usd_method, mae_variant, mae_naive, pct_improvement, wilcoxon_p,
   n_folds_ge30ctx, non_bull_signed_improvement, beats_naive: bool}. Mark clearly; this is evidence.
3. **PR-description summary:** which variants beat naive (if any), the non-bull-subset behavior, and
   a recommendation: promote-to-ADR (a real production-model proposal) / iterate (try ARIMA, longer
   horizon) / close-negative. Negatives are first-class (ADR 005).

---

## Do-NOT-reopen (evidenced dead ends — STOP and report if tempted)

MCX, TFT/N-BEATS, synthetic seed, HMM/regime, LightGBM. (CURRENT_STATE dead-ends + ADR 009/010.)
This experiment uses the EXISTING macro layer (yfinance USD/INR + gold-USD, now on 1.4.1 post-Φ12)
+ simple forecasting of those drivers — not a resurrection of any retired model.

---

## PR plan

Single PR — **PR-Φ10A** (driver-decomposition experiment). Analysis only.

## Acceptance gates

- `gh pr checks <N>` green incl lint (norm #2); strip `[skip ci]` (norm #13).
- NO production-path file touched (analysis only).
- Pre-registered gate applied exactly as written — no post-hoc threshold changes. Non-bull-subset
  reported for any variant that passes 1-4.
- Every variant gets an honest beats_naive verdict incl. negatives (ADR 005).
- Macro-alignment flag-and-stop resolved + reported BEFORE building.
- PROGRESS Decision Log appended (norm #10).
- Report back: the flag-and-stop overlap/premium findings FIRST, then per-variant gate results +
  recommendation. If anything passes the full gate (incl. non-bull), that is a promote-to-ADR
  candidate — present it for consultant review, do NOT auto-wire to production.
