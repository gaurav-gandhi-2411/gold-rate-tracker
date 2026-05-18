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

## Phase Log

### Phase 1 — Pre-engagement Audit  ✅ COMPLETE — 2026-05-18

**Goal:** Discovery only. Map the project, identify strengths and gaps.

**Deliverable:** Full audit report delivered in-session (see PR description for full text).

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

### Phase 2 — Strategic Direction  🟡 PENDING GG INPUT

**Open questions for GG (blocking):**
1. **Target horizon** — The code trains on next-reading delta (≈6h or ≈1d after daily resample) but `NEXT_SESSION.md` proposes "decision accuracy" as primary metric: did price drop ≥₹100 in the next 5 trading days? These are different tasks. Which is the real goal?
2. **Use case priority** — Dashboard display (show predicted next-day price) vs buy-signal alert (notify when model says "wait, price will drop") vs both. Affects how model quality should be evaluated.
3. **Synthetic seed decision** — Keep 444 synthetic rows as auxiliary training data (current behaviour), use them for backtest only, or drop entirely and accept a smaller but cleaner corpus?

**Consultant's proposed pivot** (awaiting GG sign-off):
- Adopt **Chronos-Bolt-Tiny** (Amazon, open weights on Hugging Face, CPU-runnable in ~1s) as zero-shot primary forecaster. Rationale: 71 real readings is far below LightGBM's effective sample for a 44-feature tabular model; it is well within Chronos's zero-shot operating range.
- Retire TFT/N-BEATS gating logic and remove committed ONNX artifacts from `models/production/` (both will be stale by the time their data gates would open — N-BEATS needs 1,000 real readings, TFT needs 2,000; at 4/day that is Q1 2027 and Q2 2028 respectively).
- Keep LightGBM as an optional residual-correction head on macro features only, trained and promoted only when it clears the champion/challenger gate vs Chronos baseline.
- Keep all existing engineering discipline: honest baseline, walk-forward backtest, ADRs, drift monitoring, conformal prediction intervals.

**Status of pivot:** awaiting GG decision on Q1–Q3 above before any implementation begins.

---

### Phase 3 — Implementation Plan  ⏸️ NOT STARTED
Will be filled in after Phase 2 sign-off.

### Phase 4 — Build  ⏸️ NOT STARTED

### Phase 5 — Validate  ⏸️ NOT STARTED

### Phase 6 — Promote  ⏸️ NOT STARTED

---

## Decision Log

| Date | Decision | Made by | Rationale |
|------|----------|---------|-----------|
| 2026-05-18 | Phase 1 read-only audit | Consultant | Establish evidence base before any changes |

---

## Risks Register

| Risk | Severity | Owner | Mitigation status |
|------|----------|-------|-------------------|
| Model 34.6% worse than naive on backtest (MAE ₹225.33 vs ₹167.36, 69 folds) | Blocker | Consultant | Pivot plan drafted in Phase 2; awaiting GG sign-off |
| 86% synthetic training data (444/~515 combined rows from Yahoo Finance formula) | Major | GG (decision) | Open question Q3; no action until GG answers |
| Unpinned deps: yfinance ≥0.2.40 has prior schema instability needing workarounds | Major | CC | Lockfile (`pip-compile` or `uv lock`) in next sprint |
| `inference.py` (live CI hot path) has no dedicated test; `continue-on-error` masks failures | Major | CC | Smoke test for `main()` in next sprint |
| Regime feature (2-state HMM) dead-weight in all models; root cause unresolved | Minor | CC | Diagnose with 5-line local print; fix or remove |
| Sentry DSN is a placeholder; JS errors in PWA are not captured | Minor | GG | Replace placeholder DSN when Sentry project created |
| ADR 006 missing (numbering gap between ADR 005 and ADR 007) | Minor | GG | Confirm whether deleted or skipped; renumber if needed |
| WANDB env vars in `.env` but wandb not in any requirements file | Minor | CC | Remove stale vars; confirm wandb is not in use |

---

## Glossary / Pointers
- **Naive baseline:** predict next price = last observed price (delta = 0).
- **Walk-forward backtest:** `ml/backtest.py`, 90-day window, 69 folds, expanding train window.
- **Honest-baseline ADR:** `docs/adr/005-honest-baseline-reporting.md`.
- **Warmup flag:** `forecast.json:warmup = true` while `real_readings_count < 100`; PWA shows banner.
- **Production model artifacts:** `models/production/lgbm.txt` + `lgbm-p10.txt` + `lgbm-p90.txt` (live, retrained every 6h); `tft.onnx` + `nbeats.onnx` (gated, not used in CI, committed 2026-05-11).
- **Minimal_v2 feature set:** 8 features — `lag_1`, `lag_7d`, `roll_7d_mean`, `roll_30d_mean`, `gold_usd`, `usd_inr`, `regime`, `dow` — currently active in CI inference.
- **Synthetic seed:** `data/history_seed.json`, 444 daily rows 2024-05-14 to 2026-05-14, estimated from Yahoo Finance GC=F × INR=X with time-varying India retail premium.
- **Groq key:** free tier, `llama-3.3-70b-versatile`; used for 2–3 sentence commentary only, not for forecasting.
- **Data gates for neural models:** N-BEATS needs 1,000 real readings; TFT needs 2,000 real readings (at 4/day: Q1 2027 and Q2 2028 respectively).
