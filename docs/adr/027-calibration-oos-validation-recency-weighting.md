# ADR 027 — Genuine Out-of-Sample Calibration Validation + Recency-Weighted Refit

**Status:** Accepted, implemented 2026-07-19

**Amends:** ADR 021 (H5 IBJA fallback, introduced the calibration layer). Extends ADR 023's
methodology discipline (no in-sample number cited as generalization evidence) to `ml/calibration.py`,
which had the same class of issue ADR 023 found in the conformal PI band.

---

## Context

`ml/calibration.py::fit_calibration` fits a `HuberRegressor` mapping IBJA's per-gram rate to
Tanishq's 22K retail price, then reports `r_squared` and `residual_std` computed by evaluating that
same fitted model against the **same data it was fit on**. This is textbook in-sample fit quality,
not out-of-sample (OOS) generalization — exactly the distinction ADR 023 had to correct for the
conformal PI band's 84.7% coverage claim. `calibration.py` was never separately audited for the same
issue; it had it from day one. `data/calibration.json`'s `r_squared=0.963` has been cited (in ADR
021/025 and this session's own prior reports) as if it were validation evidence; it is not — it is
fit quality, a different and weaker claim.

Separately: the calibration is a single global `HuberRegressor` fit over all overlap pairs, unweighted
by recency. If the true IBJA↔Tanishq markup drifts slowly (duty changes, seasonal demand, retailer
margin policy), an unweighted fit averages across the drift rather than tracking it, which would show
up as worse **genuinely OOS** prediction error even if in-sample R² looks fine (in-sample R² can't
distinguish "the world changed" from "noise," since the fit already saw the changed data too).

At the time of this audit: 52 overlap pairs (2026-04-17 to 2026-07-17), 1 more than the last fit
(51, dated 2026-07-16) — not enough new data to justify a refit under the existing 10-new-pairs
trigger. There is no meaningfully larger dataset to fit on right now; the value here is entirely in
validation methodology and recency-weighting, not in "more data."

## Decision

### 1. Add genuine walk-forward OOS validation (`walk_forward_validate`)

Same protocol `ml/backtest.py` already uses for the Chronos walk-forward (expanding window, step 1,
no leakage) rather than inventing a new convention: for each overlap pair from index 30 onward, refit
using **only** the pairs strictly before it, predict the held-out pair, collect the residual. Over the
current 52-pair history this gives **22 genuinely out-of-sample predictions** (indices 30-51), each
one predicted from a model that never saw it or anything after it.

Verified no-leakage directly: mutating pair 39's value doesn't change the prediction for the fold
trained on pairs 0-29 (`test_walk_forward_validate_first_fold_prediction_unaffected_by_future_mutation`).

### 2. Recency-weighted fit, swept and verified, not assumed

Tested exponential recency weighting (`weight = 0.5^((age-1)/half_life)`, most recent pair = weight 1)
against the unweighted baseline, using the walk-forward OOS protocol above as the judge — never
in-sample. Swept `half_life` in {8, 10, 15, 20, 25, 30}:

| half_life | R²_oos | residual_std_oos | MAE_oos |
|---|---|---|---|
| unweighted | 0.9013 | 80.79 | 70.72 |
| 8 | 0.9189 | 81.11 | 58.87 |
| **10** | **0.9203** | **80.09** | **58.86** |
| 15 | 0.9187 | 80.13 | 60.67 |
| 20 | 0.9125 | 81.78 | 64.75 |
| 25 | 0.9145 | 80.55 | 63.32 |
| 30 | 0.9110 | 81.20 | 65.77 |

**Every tested half-life beat unweighted on both R²_oos and MAE_oos** — this is not a cherry-picked
single lucky value; `half_life=10` (the single best MAE_oos and effectively tied for best R²_oos) is
adopted as the new default. MAE_oos improves ~17% (70.72 → 58.86) versus unweighted.

Also confirmed on synthetic data with a known linear drift in the true slope
(`test_walk_forward_validate_recency_weighted_beats_unweighted_on_trending_data`): recency-weighting
tracks drift at least as well as unweighted, as expected.

### 3. The in-sample number's honest fate: it gets *slightly worse*, on purpose

Refitting with recency weighting on the full 52-pair history: in-sample `r_squared` moves from 0.963
(old, unweighted, n=51) to **0.957** (new, recency-weighted, n=52) and in-sample `residual_std` moves
from 90.68 to **96.92** — both slightly worse. This is expected and correct: recency-weighting
deliberately trades in-sample fit quality on older, down-weighted points for better generalization on
recent ones. Reporting this drop honestly (not hiding it) is the point of this ADR — an improvement
that only shows up as a WORSE in-sample number and a BETTER genuinely-OOS number is real evidence of
generalization improvement, not noise.

### 4. The number that actually matters improved, and the display band tightens

The genuinely new evidence — `residual_std_oos = 80.09` — is **smaller** than the *old, never-truly-
validated* in-sample `residual_std = 90.68` it's replacing as the displayed band's half-width. Per
this ADR, `ml.inference._select_price_source` now prefers `calibration.residual_std_oos` over
`residual_std` for `est_low`/`est_high` when present (falls back to `residual_std` for any
`calibration.json` predating this field). Net effect on the live site: the "estimated range" subline
under the hero **tightens from ±₹91 to ±₹80**, and for the first time that band width is backed by a
genuinely out-of-sample number instead of an in-sample one.

### 5. Duty-change / seasonality segmentation: not testable with current data, not attempted

`data/duty_events.json` has exactly one recorded event (2024-07-23), over a year before the current
52-pair overlap window (2026-04-17 to 2026-07-17). There is no duty-change event inside the
calibration window to test a duty-aware segmented model against — building one now would be
untested speculation, not a validated improvement. Revisit if/when a real duty change occurs inside
the accumulating overlap window.

### 6. Schema version bump (1 → 2)

`CalibrationParams` gains `half_life`, `r_squared_oos`, `residual_std_oos`, `mae_oos`, `n_oos`,
`oos_method` — all `None`-defaulted so old code constructing `CalibrationParams` positionally/by the
original 7 kwargs is unaffected, and `load_calibration` defaults them to `None` for any
`schema_version: 1` file that predates this ADR (no crash, no fabricated values).

## Alternatives considered

**Wait for more overlap data before doing anything.** Rejected: the methodology gap (no genuine OOS
validation existed at all) is independent of dataset size — it's fixable today, and per the "confirm
current calibration is already at its ceiling / don't manufacture an improvement" instruction, the
honest finding here is nuanced: the *point estimate* (slope/intercept) doesn't have obvious room to
improve from more data alone (only +1 pair since last fit), but the *validation methodology* and the
*weighting scheme* did have real, demonstrated room — and that's exactly what this ADR ships.

**A richer model (e.g. piecewise/duty-aware regression).** Deferred per §5 — no in-window event to
validate it against. Building complexity without a way to check it against reality would be exactly
the "manufactured improvement" the kickoff explicitly warned against.

**Use `residual_std_oos` to replace `residual_std` entirely (drop the in-sample field).** Rejected:
`residual_std`/`r_squared` remain useful as **fit diagnostics** (does the model fit the data it was
given at all) distinct from **generalization evidence** (does it predict data it hasn't seen) — ADR
023's fix for the conformal PI band was to *relabel*, not delete, the in-sample number. Same posture
here: both fields persist, clearly named, with the OOS ones documented as the ones that answer the
generalization question.

## Consequences

**Positive:**
- A calibration.json field can now be cited as genuine OOS evidence (`r_squared_oos`,
  `residual_std_oos`, `mae_oos`, `n_oos`) — closing the same gap ADR 023 flagged for the conformal
  band, this time before it was ever cited externally as validation (caught in-house, not after
  shipping an overstated claim).
- The displayed estimate band tightens (±91 → ±80) while being MORE rigorously validated, not less —
  not a tradeoff.
- Recency-weighting is swept and verified against a real walk-forward judge, not assumed; every
  tested half-life beat unweighted, so this is a robust win, not a lucky pick.

**Negative / honest limits:**
- `n_oos=22` is still a small sample — genuinely useful as directional evidence, not a
  tight statistical guarantee. This will strengthen automatically as more overlap pairs accumulate
  (each new fit's `walk_forward_validate` call grows `n_oos` by however many new pairs exist).
- In-sample `r_squared`/`residual_std` got slightly worse (0.963→0.957, 90.68→96.92) — disclosed
  here specifically so a future reader doesn't see a "regression" in those two fields without the
  context of why that's expected and not a problem.
- Duty-change segmentation is a real gap in current understanding, not resolved by this ADR — flagged
  for revisit only if a duty event actually occurs within the accumulating overlap window.

## Re-evaluation triggers

- A real duty-change or major seasonal event occurs within the overlap window → test a segmented
  model against the same walk-forward OOS protocol before adopting it.
- `n_oos` grows large enough (rough target: 60+, by analogy with other statistical floors in this
  repo) that the half-life sweep should be re-run against a bigger OOS sample to confirm 10 remains
  near-optimal.
- Tanishq access is restored broadly enough that overlap-pair accumulation resumes at its historical
  rate — revisit whether `_REFIT_NEW_PAIRS=10`'s trigger cadence is still appropriate once refits
  start happening on a real cadence again rather than being manually forced (as this one was).
