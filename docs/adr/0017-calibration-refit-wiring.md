# ADR 017 — Calibration Refit Wiring and CI Integration

**Status:** Accepted 2026-06-01  
**Author:** Gaurav Gandhi / CC

---

## Context

Phase 3 (PR D) built a full calibration layer (`ml/calibration.py`): `fit_calibration()`, `should_refit()`, `save_calibration()`, and `load_calibration()`. All functions were unit-tested. The system was designed to automatically refit `data/calibration.json` as Tanishq/IBJA overlap pairs accumulated, flipping `valid: true` once 30 pairs were available.

**The wiring was never added.** A 2026-05-31 modelling assessment (read-only pass, `docs/MODELLING_ASSESSMENT_2026-05-31.md`) confirmed: `fit_calibration()` is called only in tests, never from any CI step. `check-price.yml` has no calibration refit step. The "self-flip at 30 pairs" described in `CURRENT_STATE.md`, `ADR 014`, and `ADR 016`'s H5 deferral rationale was documented as automatic but was not implemented.

**State at assessment date:** 29 overlap pairs (1 short of the 30-pair threshold); `calibration.json` frozen at its Phase 3 stub (`valid: false`, `n_observations: 21`, `fit_date: 2026-05-19`).

The same assessment also identified two secondary issues:
- `data/chronos_probe.json` was stale (13 days behind the parquet) because an intermediate probe commit step created a race condition with the main data commit.
- `data/drift_metrics.json` new entries lacked `baseline_mae`; `app.js` was falling back to the legacy LightGBM entry's `225.65` for the "Accuracy drift" ratio.

---

## Decision

### 1. Add calibration refit to `check-price.yml`

A new step — **Refit calibration if needed** — runs after `Append IBJA rates` and before `Run Chronos probe`:

```yaml
- name: Refit calibration if needed
  continue-on-error: true
  run: python -m ml.calibration
```

`continue-on-error: true` ensures a refit failure does not block the rest of the CI run (same pattern as all other ML steps).

### 2. Add `run_refit_if_needed()` to `ml/calibration.py`

New public function exposed as the `python -m ml.calibration` entry point. Logic:

- Loads `ibja_rates.parquet` and `prices.json`.
- Computes actual overlap pair count (IBJA trading dates ∩ Tanishq UTC calendar dates).
- Triggers refit under two conditions:
  - **Initial unlock:** `calibration.valid == False` AND `overlap_count >= 30`. Runs on the first CI cycle that reaches the threshold.
  - **Periodic refit:** `calibration.valid == True` AND `should_refit()` returns True (10+ new pairs since last fit).
- On trigger: calls `fit_calibration(ibja_df, tanishq_df)` → `save_calibration(params)`.
- Exits silently (returns False) when no refit is needed or when overlap < 30.
- Raises on refit failure so `continue-on-error: true` catches it and the CI step exits non-zero (visible in logs).

### 3. Consolidate probe commit — fix probe staleness

Remove the intermediate `Commit Chronos probe data` step (which ran after the probe but before the main data commit, creating a race condition). Add `data/chronos_probe.json` to the main end-of-run `git add` list alongside `data/calibration.json`.

Effect: probe and calibration are now committed atomically with all other data files. The one-run lag for probe data (probe generated in run N, used by inference in run N+1) is unchanged; the race condition that left probe.json 13 days stale is eliminated.

### 4. Add `baseline_mae` to drift entries (`ml/drift.py`)

Each new `drift_metrics.json` entry now includes `baseline_mae: bt["mae_5d_avg_naive"]` read from `data/backtest.json`. `app.js` uses `withBase.baseline_mae` for the "Accuracy drift" ratio; this anchors the ratio to the current naive baseline (Rs.249.53) rather than the legacy LightGBM entry (Rs.225.65) which will age out within 30 days. Omitted gracefully if `backtest.json` is absent.

---

## Consumer Path Verification (calibration → live forecast)

All links were traced before implementation:

| Step | File | What happens | Status |
|---|---|---|---|
| 1 | `check-price.yml` | `run_refit_if_needed()` → writes `calibration.json` with `valid: true` | **Added** |
| 2 | `ml/inference.py` | reads `calibration.json`; if valid, applies `slope*v+intercept` to `horizon_p10/p50/p90`; sets `calibration_applied: true` | Pre-existing ✓ |
| 3 | `data/forecast.json` | `chronos_companion.calibration_applied: true`, arrays in Tanishq retail units | Pre-existing ✓ |
| 4 | `app.js` | `cc.calibration_applied ? "Yes" : "Not yet"` — flips correctly | Pre-existing ✓ |
| 5 | `ml/notifications.py` | `_check_t6()` reads `calibration.json` directly; fires T6 once-ever when `valid=True` AND `last_t6_fired_date_ist == ""` | Pre-existing ✓ |

**Timing:** On the run where refit writes `valid=true` (step 1): notifications reads `calibration.json` directly and T6 fires in the same run. Inference (which runs earlier in the CI step order) will see `valid=true` on the next run and write a calibrated `forecast.json`. The 6-hour one-run lag between T6 firing and the calibrated companion appearing in the PWA is acceptable.

---

## Consequences

**Positive:**
- Calibration will now unlock automatically on the next CI run after the 30th overlap pair accumulates (currently 29; ETA: 2026-06-02 or 2026-06-03 pending 2026-06-01 IBJA append).
- Chronos companion horizon arrays will be calibrated to Tanishq retail prices once the flag flips, improving the accuracy of the "5-day outlook" directional range.
- T6 (calibration-unlock notification) will fire as designed.
- The `app.js` "Adjusted to Tanishq prices" display will flip to "Yes" correctly.
- Probe staleness eliminated: `chronos_probe.json` commits atomically with the main data file set.
- Drift "Accuracy drift" ratio corrected to use the current naive MAE baseline.

**Negative / honest limits:**
- The first refit will use 29–31 overlap pairs (Apr–Jun 2026, ~6 weeks of data). The calibration is statistically valid (≥30 pairs, HuberRegressor is robust to outliers), but `residual_std` may be non-trivial given the IBJA lag artefacts documented in §3.1.3 of PROGRESS.md. Monitor `calibration.json.residual_std` after the first successful refit.
- Inference sees calibration one run after T6 fires. This is a known one-run lag, matching the existing probe pattern.
- `calibration_just_unlocked` in `forecast.json.chronos_companion` will always be `False` in practice: T6 fires in the run before inference sees `valid=True`, so `last_t6_fired_date_ist` is already set when inference evaluates the flag. This is a pre-existing logic issue in `inference.py`; it does not affect T6 delivery or the calibrated forecast. Out of scope for this ADR.

---

## H5 Un-blocked (future decision, not implemented here)

ADR 016 deferred the IBJA-calibrated fallback reading (H5) explicitly because "invalid calibration = noise." Once calibration flips `valid: true` (this ADR), that deferral rationale no longer holds. H5 becomes a legitimate future option: when the Tanishq scraper fails, an IBJA-derived reading could be written to `prices.json` with `"source": "ibja_calibrated"` rather than leaving a gap.

**This ADR does not implement H5.** It flags that H5 is now un-blocked and should be evaluated in a dedicated ADR once calibration has been observed to be stable in production.

---

## Alternatives Considered

**Alt: Manual one-time flip of `calibration.json`.** Rejected: explicitly prohibited in `CURRENT_STATE.md` ("Don't manually flip; the fit function handles it"). The correct fix is to wire the function, not bypass the gate.

**Alt: Trigger refit only via a separate `calibration-refit.yml` workflow.** Rejected: calibration data changes each CI cycle (new IBJA and Tanishq readings). Checking for a refit inside the main 6-hour loop is the correct cadence. A separate workflow would require additional coordination state.

**Alt: Separate intermediate commit for calibration (like the old probe pattern).** Rejected: the intermediate probe commit was the root cause of the probe staleness bug. Consolidating both into the main data commit is the cleaner fix.
