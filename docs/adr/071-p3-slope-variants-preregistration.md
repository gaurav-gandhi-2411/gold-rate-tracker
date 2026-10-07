# ADR 071: pre-registration: do two slope corrections beat the live P3 forecast?

**Status:** Proposed 2026-10-05 (GG brief item 6b/6c). Pre-registration. This text and the code it
names (`ml/nextfix.py` `predict_p3_roll60`, `predict_p3_monday`, `data/nextfix_p3_variants_oos.json`,
`scripts/analysis_p3_variants.py`, `tests/test_p3_variants.py`) are frozen by the commit that adds
this file, **before any variant has been scored on any day**. Results are appended below a
`## Results` heading. Research and forward shadow only; promotion of a variant is GG's decision in a
separate PR.

## Context (what was seen before this freeze: disclosed)

Descriptive diagnostics on the 143-day record (2025-07-17..2026-09-30), run 2026-10-05 and NOT a test:
- Calibration slope of realised on forecast return is **1.64** for both P3 (95% block-bootstrap
  interval [0.98, 2.28]) and the ensemble ([1.11, 2.20]): forecast moves are too small. For P3 the
  walk-forward slope `b` grew from 0.06 to 0.28 (mean 0.19) while the full-sample slope is 0.262:
  an expanding window lags a rising slope.
- In-sample slope of next-fix move on world move, by decision weekday: Monday 0.133 (n 41), Tuesday
  0.528, Wednesday 0.276, Thursday 0.238, Friday 0.274. Monday's feature spread is wide (sd 0.0166)
  because the "day D" world move runs from the Friday close and includes the weekend gap, most of
  which Monday's own midday fix already reflects.
- P3 vs holding by weekday (out of sample, Rs./g): Monday +7.8% worse (n 29), Tuesday -5.6%,
  Wednesday -8.0%, Thursday -9.0%, Friday -18.4%.
- All 29 Monday decisions have a one-day gap to the next fix: Monday is **not** a weekend gap
  between fixes; the gap is inside the feature.

The variants below were designed after seeing these numbers, so any retrospective comparison is
**exploratory and biased in the variants' favour**. Only forward results (decision day on or after
2026-10-05, `retro: false`) count.

## Decision: the variants (frozen)

Notation as ADR 067: `x_glob` the world move over decision day D, `y` the next fix's log return.
All three models forecast `ret = b x x_glob`; they differ in how `b` is fitted, on pairs whose target
fix is already known at D's decision time (`d1 <= d0`). P(up) = Phi(ret / s) as for P3.

| Id | Slope `b` |
|---|---|
| **P3** (live) | least squares through the origin, all resolved pairs (expanding window) |
| **V1 `p3_roll60`** | same, on the 60 most recent resolved pairs (fewer if fewer exist) |
| **V2 `p3_monday`** | on Monday decisions, least squares through the origin on resolved MONDAY pairs only, when at least 15 exist; otherwise, and on every other weekday, as P3 |

## Decision: the test (frozen)

- **Data:** forward folds only, logged every check-price run in `data/nextfix_p3_variants_oos.json`
  (one fold per resolved decision day, as for P3's own record). The first scored read is when
  `n_forward >= 40` decision days (about 8 weeks); not before.
- **Metrics, V against P3 on the same days:** MAE in Rs./g of 22K IBJA; the paired mean absolute-
  error difference with a moving-block bootstrap (blocks of 5, 2000 draws, seed 42) 95% interval of
  the relative change; one-sided Diebold-Mariano with Newey-West (4 lags), H1 "V has lower error";
  effective n. Direction accuracy on days the fix moved and the calibration slope of realised on
  forecast (with a bootstrap interval) are reported, not tested.
- **Multiplicity:** two variants: alpha = 0.05 / 2 = 0.025 one-sided per variant.
- **"V clearly beats P3":** V's MAE is at least 2% lower (the repo's champion-challenger margin) AND
  the one-sided DM p < 0.025 AND V's direction accuracy is not significantly lower (exact McNemar,
  p >= 0.025).
- **V2 additionally** is scored on its own stratum (Monday decisions), reported separately and not
  used as a second chance: the decision rule above uses all days.
- **Decision rule:** if exactly one variant clearly beats P3, recommend it to GG for promotion
  (demotion rules of ADR 068 apply to it from day one); if both do, recommend the one with the lower
  MAE; if none does, P3 stays. Nothing ships from this ADR.
- **Retrospective numbers** (the 143-day record) may be computed after this freeze and are labelled
  EXPLORATORY with the bias above; they carry no weight in the decision.

## Limits

- 40 forward days with autocorrelated errors: wide intervals; "not clearly better" is not "equal".
- Monday has about 8 forward folds at the first read: V2's Monday effect cannot be established
  there (V2's whole-sample test mostly measures noise on the other weekdays, where V2 = P3).
- A rising slope may be a regime that reverts; V1 would then lose.
- The hourly "since the fix" variant (ADR 066) attacks the same cause directly and is tested under
  its own pre-registration.

## Results

Pre-registration hash (sha256 of this file as committed in `d9f7d4aa`, before this section):
`4bfd3895d7408108f44f0a470918bdc934b76e18afc6c99bf7ac25e2ad698b03`. Artifact:
`reports/model_audit_2026-10/p3_variants.json` (script `scripts/analysis_p3_variants.py`).

**Forward: n_forward = 0 (decision days from 2026-10-05); not scored. Decision: P3 stays.**

**Retrospective, EXPLORATORY (biased in the variants' favour, carries no weight; 143 days,
2025-07-17..2026-09-30, VERIFIED):**

| Variant | MAE (P3: 107.27) | vs P3, 95% CI | one-sided DM p | Direction (P3 62.9%) | Calibration slope (P3: 1.64) | Monday stratum (n 29; P3 MAE 92.28) |
|---|---|---|---|---|---|---|
| V1 `p3_roll60` | 108.09 | +0.8% [-3.7, +5.4] | 0.64 | 62.9% | **1.01** | 101.2: +9.7% [+2.4, +17.5] |
| V2 `p3_monday` | 106.42 | -0.8% [-1.9, +0.2] | 0.072 | 62.9% | 1.79 | 88.09: -4.5% [-10.0, -0.2] |

Reading (not part of the registered test):
- **The "forecast moves are too small" finding (slope 1.64) is a calibration property, not an
  accuracy lever.** The rolling slope removes it (slope 1.01) and does not lower the error (+0.8%).
  The expanding-window slope lags a rising true slope, but a larger forecast is not a better one
  while the day-to-day slope is this noisy.
- **Monday:** a Monday-only slope helps Mondays by about 4.5% (interval just below zero, 29 days)
  and the whole sample by 0.8%: under the 2% margin. Consistent with the feature carrying the
  weekend gap that Monday's fix already reflects, but 29 days cannot confirm it.
- Neither correction is worth shipping on this evidence. The larger lever in the brief's own data is
  the hourly world-price variant (ADR 066: backtest -24% to -39% error in the two hold windows,
  pre-registered check on 2026-10-16).
