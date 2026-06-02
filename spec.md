# Spec — Φ7-followup: Non-Bull Subset Diagnostic (drift_naive_span20 @ h=20)

**Date:** 2026-06-02
**Author:** External consultant (via GG) → Orchestrator (CC)
**Status:** Draft for orchestrator execution
**Type:** Diagnostic analysis only. NO production-path change. NO gate evaluation.

---

## Purpose

ADR 018 holds `drift_naive_span20` at h=20 (Φ7's one gate pass) on the grounds that the result
is **confounded by a single bull regime** (2022–2026). This diagnostic tests our own skepticism:
re-run that exact variant on the **non-bull subset of folds only** to observe whether drift
*loses* outside the uptrend, as ADR 018 predicts.

This is explicitly **below gate power** (we expect <30 out-of-regime folds) and is **not** a
promotion evaluation. The goal is a directional observation — does the sign flip? — that turns
"we believe it's a regime artefact" into "we showed it."

---

## What to do

Reuse the Φ7 Exp-3 machinery (PR #62) and the existing `ml/backtest.py` folds. Do NOT invent new
fold logic or a new split.

1. **Define the non-bull subset.** Over the backtest window, classify each fold's evaluation
   segment by realised regime. Use a transparent, documented rule — pick ONE and state it in the
   result note:
   - **Drawdown folds:** folds whose h-step realised path sits within / after a ≥10% peak-to-trough
     drawdown from the running max, OR
   - **Sideways folds:** folds where |net drift over the horizon| is below the daily-delta noise
     band (e.g. < 0.5 × rolling-std of daily deltas), OR
   - **Non-up folds (simplest):** folds where the realised h=20 change is ≤ 0 (price flat or down).

   The simplest defensible rule (non-up folds: realised change ≤ 0) is the recommended default.
   If essentially zero such folds exist in 2022–2026, that itself is the finding — report
   "dataset contains no out-of-regime folds; predicted sign flip is untestable on current data"
   and stop. That is a legitimate, honest outcome (it confirms the confounding directly).

2. **Re-run `drift_naive_span20` and `flat_naive` at h=20 on that subset only.** Report:
   - n_folds in the non-bull subset
   - mae_drift_span20, mae_flat_naive on the subset
   - pct_improvement (signed — negative means drift LOSES, which is the predicted result)
   - the full-set numbers alongside (+5.17%) for contrast

3. **Do NOT compute a gate verdict.** Subset is below power by design. No `beats_naive` boolean
   for the subset — that field is for gate-evaluated runs only. Use a distinct field, e.g.
   `subset_signed_improvement`, so this is never mistaken for a gate pass.

---

## Deliverables

- Append a diagnostic entry to `data/experiments/phi7_results.json` clearly marked
  `"diagnostic": true, "below_gate_power": true` with the subset rule used and the signed numbers.
  Do NOT mark it as a gate evaluation.
- A one-paragraph note for the PROGRESS.md Decision Log (append-only, norm #10): the subset rule,
  n_folds, signed improvement, and whether the predicted sign flip was observed. If no out-of-regime
  folds exist, state that plainly.
- Result reported back to orchestrator → consultant for the ADR 018 Decision Log linkage.

---

## Constraints

- Analysis only. No edit to inference.py, forecast.json, app.js, notifications.py, check-price.yml.
- Reuse existing harness; no new fold logic.
- Honest reporting including a null/untestable outcome (ADR 005, norm #4). "No out-of-regime folds
  exist" is a valid and useful result — do not manufacture a subset to force a number.
- `gh pr checks <N>` green incl. lint (norm #2); strip `[skip ci]` from squash body (norm #13).
- This does NOT change ADR 018's "held" status regardless of outcome — even an observed sign flip
  only strengthens the existing hold; it does not by itself create a promotion. Promotion still
  requires the ADR 018 falsifiable condition (genuine out-of-regime folds AND gate pass on them).

---

## PR plan

Single PR — **PR-Φ7D** (diagnostic). Small. One subset rule, two MAE numbers, one Decision Log note.
