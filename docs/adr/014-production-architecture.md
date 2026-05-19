# ADR 014 — Phase 3 Production Architecture Summary

**Status:** Accepted 2026-05-19
**Author:** Gaurav Gandhi / CC

---

## Context

Phase 3 is complete. Six ADRs were enacted (005, 009, 010, 011, 012, 013), nine PRs were merged (A through H), and the full production stack has been rebuilt from the pre-engagement LightGBM-based design.

The architectural decisions made across Phase 3 are recorded individually in their respective ADRs. This document captures the resulting production architecture as a single coherent reference and declares Phase 3 closed.

**Evidence that drove the architecture:**

| Source | Finding |
|--------|---------|
| PR F.5 backtest (165 folds) | Chronos MAE Rs.275.5 vs Naive Rs.249.5 (10.4% worse, p=0.0089) |
| PR F.5 direction accuracy | 55.8% average, 63.3% last-30-fold |
| PR D calibration fit | Tanishq/IBJA ratio median 1.017 (std 0.015, 21 pairs); valid=False until 30 pairs |
| Wayback Machine ceiling | 177 rows (103 CDX captures fully processed) |
| LightGBM walk-forward | MAE Rs.225.33 vs Naive Rs.167.36 (34.6% worse, 69 folds) |

---

## Decision

**Production stack as of PR H merge:**

### Headline forecast — naive flat-hold
`predicted_22k = current_22k` (most recent Tanishq 22K scrape).

Confidence interval: 80th-percentile conformal PI from the last 30 folds' naive h=5 absolute errors (`data/backtest.json`). Falls back to `mae_5d_avg_naive × 1.5` when fold-level data is unavailable.

### Directional companion — Chronos-Bolt-Tiny
`ml/chronos_forecast.py --probe` writes `data/chronos_probe.json` each CI cycle.
`ml/inference.py` reads it — never calls Chronos directly. The companion block in `forecast.json` carries `lean_direction`, `lean_strength_pct`, `direction_acc_30f`, and the 5-day horizon arrays.

### Data layer
- **Tanishq 22K:** Playwright scrape every 6h (`scraper/scrape.js`). Ground-truth retail series.
- **IBJA 916-PM:** Live daily scrape + 30-day PDF backfill (`ml/ibja.py`). Primary modeled series for Chronos and calibration.
- **Macro features:** yfinance USD/INR + Gold-USD (`ml/macro.py`). Retained for Phase 4 covariate use; not used in naive headline path.

### Calibration layer
`ml/calibration.py` — HuberRegressor fit on (ibja_916_pm, tanishq_22k) daily pairs. Calibration applied to Chronos companion horizon arrays only, gated on `calibration.json.valid=True` (activates when 30 overlap pairs accumulate; currently `valid=False` at 21 pairs).

### Notifications
`ml/notifications.py` — five triggers (T1–T5). T1/T2 use Chronos directional signal gated on `dir_acc_30f ≥ 0.55` and `lean_strength_pct ≥ 0.5`. T3 fires on observed moves ≥ Rs.150. T4 is weekly digest. T5 fires on `model_fallback=True`. State persisted across CI cycles via GitHub Actions cache.

### forecast.json schema
Two-block nested schema (`headline` + `chronos_companion`) with top-level PWA backward-compat aliases (`predicted_22k`, `lower`, `upper`, `model_status`, `warmup`, `target_time`, `predicted_at`, `real_readings_count`). Aliases removed in a follow-up PWA-update PR after the new schema is rendered in the UI.

### Deleted in Phase 3 (PR H)
LightGBM training, `ml/forecast.py`, `ml/regime.py`, `ml/daily_summary.py`, `ml/ensemble.py`, `ml/models/lgbm.py`, `ml/compare_feature_sets.py`, `ml/promotion.py`, tuning scripts, all associated tests, and production model artifacts (`lgbm.txt`, `lgbm-p10.txt`, `lgbm-p90.txt`, `lgbm-meta.json`).

---

## Consequences

**Positive:**
- The production forecast path is minimal: read prices.json, compute conformal PI, read chronos_probe.json, write forecast.json. No model training, no external model calls, no PyTorch in the inference step.
- Chronos runs as a separate CI step (probe-only), keeping the inference step fast and the failure blast radius small.
- All Phase 3 ADRs are enacted in code. There are no pending "to be implemented" architectural decisions.
- The notification system is live with verified state persistence across CI cycles.

**Negative:**
- The headline forecast is explicitly naive. The PWA will show a "flat hold" until Chronos earns its promotion (≥250 rows, beats naive, p<0.05).
- Calibration is `valid=False` at Phase 3 close (21/30 pairs). Chronos companion horizon arrays are uncalibrated until ~9 more trading days accumulate.
- Phase 4 multivariate upgrade path (Chronos-2 / Moirai-MoE) cannot begin until the promotion criterion is met.

## Alternatives Considered

**Alt: Delay Phase 3 close until calibration is valid.** Rejected: calibration validity is a data-accumulation gate, not an engineering gate. Phase 3 code is complete and correct; waiting for 9 more trading days is not a reason to leave Phase 3 open.

**Alt: Keep LightGBM as a fallback alongside naive.** Rejected: LightGBM is 34.6% worse than naive (69 folds). Keeping it as a fallback path adds complexity with no accuracy upside. The `model_fallback` field in forecast.json signals Chronos probe failures; a secondary LightGBM path is not needed for that purpose.

**Alt: Defer ADR 014 to Phase 4.** Rejected: the production architecture is stable at Phase 3 close and warrants a summary ADR. Phase 4 decisions will produce their own ADRs (015+).
