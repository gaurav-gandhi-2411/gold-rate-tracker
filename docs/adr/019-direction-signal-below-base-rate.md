# ADR 019 — Chronos Direction Signal Does Not Beat the Bull-Regime Base Rate; No Calibrated Probability

**Status:** Accepted 2026-06-02
**Author:** Gaurav Gandhi / external consultant / CC
**Type:** "We decided NOT to ship X" — norm #3. Honest-baseline correction (ADR 005).

---

## Context

Batch Φ8B set out to turn the Chronos directional signal into an honest, calibrated probability
("~X% chance higher over next 5d"), validated on a reliability diagram + Brier vs a base-rate
baseline, per a pre-registered honesty gate (beat the bull-regime base-rate Brier out-of-sample).

Investigating the data surfaced a correction to a claim carried as settled since ADR 011/012:
**the relevant naive direction baseline in this regime is the base rate, not 50%.** Measured on
the existing 165-fold backtest:

| Window | Base rate P(actual up) | Chronos p50-direction acc | Beats base rate? |
|---|---|---|---|
| All 165 folds | 69.7% | 55.8% | No |
| ge30ctx (143) | 75.5% | 52.4% | No |
| Last-30 folds | 70.0% | 63.3% | No |

Chronos direction accuracy is **below the base rate on every window.** A constant "always predict
up" predictor beats it on direction in this regime. The previously-cited "55.8% / 63.3%, above the
50% naive floor" (ADR 011, ADR 012) compared against the wrong baseline: 50% is the base rate of a
*balanced* series, not of a series that rose on ~70% of horizons.

Brier score (ge30ctx, full-sample two-bucket calibration — i.e. the most favourable possible setup,
calibration and evaluation on the same data):

- Model: 0.1841
- Base-rate constant: 0.1849
- Improvement: **0.0008** over 143 folds — eight ten-thousandths, not meaningful.

Full-sample (in-sample) improvement of 0.0008 means out-of-sample improvement is, with very high
confidence, zero or negative. The pre-registered honesty gate is not met.

(Note: a separate degeneracy in the consensus mechanism is addressed in ADR 020. This ADR concerns
the direction signal's accuracy vs the correct baseline, which holds regardless of consensus.)

## Decision

**Do not ship a calibrated direction probability.** `forecast.json.chronos_companion` carries
`direction_prob_basis: "base_rate_fallback"` and no fabricated percentage. The honest statement is:
at current data and regime, the Chronos direction signal carries no information beyond the base
rate.

This is the spec's pre-registered fallback, not a failure. It is honest-baseline reporting (ADR
005) functioning exactly as intended: a model that does not beat the correct baseline is not shipped
as if it did.

## Re-evaluation condition (falsifiable)

Revisit a calibrated direction probability when BOTH hold:

1. **A mixed-regime fold set exists** — enough non-up folds that the base rate moves back toward
   50% and there is genuine directional uncertainty for a signal to resolve. (In a ~70%-up regime,
   "beat the base rate" is a very high bar that even a good signal struggles to clear; this is as
   much a property of the regime as of the model.)
2. **A genuinely discriminative direction signal exists** — see ADR 020. The current Chronos path
   is both below base rate (this ADR) and degenerate in its consensus (ADR 020).

Until then, the honest position: we cannot quote a calibrated probability, and we do not.

## Consequences

**Positive:**
- The production surface stops implying a directional edge that the data does not support.
- The 55.8%/63.3% figures are now correctly contextualised against the 69.7%/70% base rate
  wherever they appear (README, PWA "how good is this" panel, ADR 011/012 references).
- Φ8C's trust surface is built on an honest footing: track record + plain "flat-hold because
  nothing beats it" framing, no fake confidence percentage.

**Negative / accepted:**
- The companion's headline directional claim is materially weaker than previously documented. This
  is a correction, not a regression — the edge was never there against the right baseline.
- T1/T2 notifications lose their Chronos justification; their replacement is decided in ADR 020.

## Alternatives considered

**Ship a probability anyway from the 0.0008 in-sample edge.** Rejected: in-sample, sub-noise,
guaranteed not to survive out-of-sample. Textbook overclaim; violates the pre-registered gate and
ADR 005.

**Run the walk-forward Brier on a held-out window for completeness before deciding.** Considered.
Rejected as the deciding step: 0.0008 full-sample over 143 folds already determines the out-of-
sample outcome; spending a backtest cycle to confirm a known-negative is not required to make the
decision (it may still be done later as a shareable artifact, but it does not gate this ADR).

**Keep comparing to 50%.** Rejected: 50% is the wrong baseline for a trending series. Honest-
baseline reporting requires the realistic alternative (the base rate), per ADR 005.
