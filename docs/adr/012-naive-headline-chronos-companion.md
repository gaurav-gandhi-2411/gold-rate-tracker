# ADR 012 — Naive Flat-Hold as Headline Forecast; Chronos as Directional Companion

**Status:** Accepted 2026-05-19
**Author:** Gaurav Gandhi / CC

---

## Context

Phase 3 PR F.5 extended `data/ibja_rates.parquet` from 21 rows to 177 rows via Wayback Machine IBJA backfill, enabling a statistically meaningful walk-forward backtest (165 folds, expanding window, h=5).

**Backtest results (2026-05-19):**

| Metric | Value |
|--------|-------|
| MAE Chronos | Rs.275.5 |
| MAE Naive (flat hold) | Rs.249.5 |
| Gap | Chronos 10.4% worse |
| Wilcoxon signed-rank p | 0.0089 (statistically significant) |
| Direction accuracy h=5 | 55.8% (naive: 50.0%) |
| Last-30-fold direction accuracy | 63.3% |
| Folds with ≥30-row context | 143 of 165 |

The evidence is unambiguous: Chronos-Bolt-Tiny does not outperform naive hold on IBJA-916-PM MAE at this data volume and this series structure. The dominant driver is the 2025-2026 uptrend (~Rs.85,000 → Rs.145,000), which makes flat-hold extremely competitive. Deploying Chronos as the headline level forecaster would violate ADR 005 (honest-baseline reporting).

However, Chronos does show non-trivial directional signal (55.8% average, 63.3% on the last 30 folds), which is above the 50% naive direction baseline and above the T1/T2 activation gate (55%). Abandoning Chronos entirely would discard this directional information.

---

## Decision

**Naive flat-hold is the production headline forecast.**

`predicted_22k` in `forecast.json` is computed as:
```
predicted_22k = most_recent_ibja_pm_916 × premium_factor
```
where `premium_factor` is the HuberRegressor calibration coefficient from `data/calibration.json`.

This is what the naive baseline already computed. Making it explicit and named is an honest declaration, not a regression.

**Chronos is retained as a directional companion probe.**

`data/chronos_probe.json` continues to be written each CI cycle. It is the input for:
- T1/T2 notification triggers (directional signal + momentum agreement + direction accuracy gate)
- Future Phase 4 experimentation

The `chronos_lean` field (direction + strength) from the probe is the only Chronos output surfaced to users — framed explicitly as a directional signal, not a price forecast.

**Promotion criterion for Chronos to become headline forecaster:**

Chronos graduates to headline forecaster only when a new backtest (≥250 rows, expanding window) shows:
- `mae_5d_avg_chronos < mae_5d_avg_naive` (Chronos MAE beats naive)
- Wilcoxon signed-rank p < 0.05
- The improvement holds on ≥30-context folds only (sub-30-context folds are excluded to avoid sparse-data artefacts)

At the current accumulation rate (~20–25 new rows/month), 250 rows will be available approximately 2026-09-2026-10.

---

## Consequences

**Positive:**
- The production forecast is now honest by construction — it never claims Chronos is better than it is
- The notification system (ADR 011 T1/T2) still uses Chronos's directional signal, which is the one verified positive finding from the backtest
- No model complexity in the headline path — simpler, faster CI execution
- ADR 003's 2% promotion gate is satisfied by definition (naive IS the benchmark; Chronos must beat it by ≥2%)
- The promotion criterion is concrete and testable

**Negative:**
- The dashboard shows "naive flat-hold" as the forecast — which may read as a limitation to non-technical reviewers. Mitigated by honest labelling in the PWA
- Chronos probe still runs every 6h CI cycle; it adds ~5–15s wall-clock time and PyTorch install overhead (cached after first run)
- If the 2025-2026 uptrend reverses sharply, naive will underperform and directional signal from Chronos may matter more — but the backtest will catch this and enable promotion

## Alternatives Considered

**Alt 1: Deploy Chronos as headline despite worse MAE.** Rejected: violates ADR 005. A model 10.4% worse than naive, verified at p=0.0089, must not be deployed as the primary forecast.

**Alt 2: Abandon Chronos entirely.** Rejected: direction accuracy (55.8% average, 63.3% last 30 folds) is a verified positive finding. Removing Chronos would eliminate the T1/T2 directional notification capability without evidence that it contributes zero value.

**Alt 3: Phase 4 first (collect 250 rows before deciding).** Rejected: the decision to use naive as headline can be made now with the evidence available. Phase 4 does not depend on this decision. The promotion criterion defined here IS Phase 4.

**Alt 4: Use ensemble (0.5 × Chronos + 0.5 × naive).** Rejected: ensembling a worse model with the benchmark cannot outperform the benchmark in expectation. This would add complexity without improving accuracy, and would not be honest about which component is driving the signal.
