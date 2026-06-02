# ADR 020 — Supersede ADR 015: Chronos Consensus Is Degenerate; Re-point T1/T2 to Momentum (Option b)

**Status:** Accepted 2026-06-02
**Supersedes:** ADR 015 (Multi-Sample Chronos Probe with Consensus Gating)
**Author:** Gaurav Gandhi / external consultant / CC
**Type:** "We decided to undo a prior decision" — norm #3.

---

## Context

ADR 015 (2026-05-22) introduced 5-sample majority-consensus gating for T1/T2 notifications, to fix
observed inter-run direction instability ("DOWN 2.29%" then "UP 3.73%" 24h apart). It assumed
`predict_quantiles` is stochastic across calls and that 5 samples + a `direction_consensus >= 0.6`
gate would damp false fires.

**That assumption is false.** Investigation during Φ8B (2026-06-02):

- `predict_quantiles` on ChronosBolt-Tiny is a **deterministic quantile-regression head.** Calling
  `forecast_ibja()` 5× on the same context returns byte-identical results.
- Empirical confirmation: of 84 historical `chronos_probe.json` commits, 40 carry
  `direction_consensus`; **100% show exactly 1.0, never once below.** Zero variance across 6+ months
  of 3h-cadence CI runs.

**Two consequences:**

1. **The consensus gate is inert.** `direction_consensus` is always 1.0, so `>= 0.6` is always true.
   The gate ADR 015 added to suppress false fires has never suppressed anything. The 5-sample loop is
   5× redundant inference producing one repeated value.

2. **ADR 015 misdiagnosed the root cause.** The real inter-run direction flips are caused by the
   **context window changing between runs** (new scraped readings shift the input), not by sampling
   stochasticity. Five identical samples cannot damp variance that originates upstream of the model.

Separately, ADR 019 establishes that the Chronos direction signal is below the bull-regime base rate
on every window. So T1/T2 were gating a below-base-rate signal behind an always-true gate.

## Decision

**Supersede ADR 015. Adopt Option (b): re-point T1/T2 onto a transparent momentum / base-rate signal;
keep Chronos as a probe artifact only.**

Cleanup (to be implemented in a Φ8B-revised PR):

1. **Drop the 5-sample loop to a single call** in `chronos_forecast.run_probe()`. The model is
   deterministic; `DEFAULT_NUM_SAMPLES = 1`. Removes redundant inference. Keep `direction_consensus`
   in the schema for backward-compat but set it to a constant 1.0 with a comment pointing here, OR
   remove it in a schema_version bump — implementer's call, flag if non-trivial (norm #1).

2. **Remove the `direction_consensus >= 0.6` gate from T1/T2** (`ml/notifications.py`). It is inert;
   deleting it changes nothing in behaviour but removes a misleading guard.

3. **Re-point T1/T2 onto momentum / base-rate**, replacing the Chronos lean as the trigger basis. The
   signal: recent realised price trend (e.g. sign and magnitude of N-day momentum on Tanishq 22K /
   IBJA). Chronos lean may still be *reported* in the probe artifact but no longer *gates* alerts.

4. **Chronos probe stays live** as an artifact (`chronos_probe.json` keeps being written) for future
   re-evaluation under ADR 019's mixed-regime condition. It is demoted from notification gate to
   recorded observation.

## The honesty constraint on the momentum signal (do not repeat Φ7D)

A momentum "lean up" signal is right ~70% of the time in this bull regime **for the same reason flat-
naive and drift-naive look good: the trend has not stopped.** It will be wrong at the reversal —
precisely when a directional alert matters most. This is the Φ7D / ADR 018 regime-confounding lesson
applied to notifications.

Therefore (b) is NOT a claim that we found a good direction *predictor*. It is: we replace a below-
base-rate signal (Chronos) with an honest description of the base rate / recent trend. T1/T2 copy MUST
be framed as a **description, not a forecast**:

- Acceptable: "Prices have been trending up over the past N days."
- NOT acceptable: "Model predicts prices will rise" / any implied turn-prediction / any confidence %.

This keeps T1/T2 useful (a user is informed of the trend) without overclaiming predictive power the
signal does not have. Per ADR 005 and the norm #16 honesty discipline.

## Consequences

**Positive:**
- Removes 5× redundant inference per probe (minor compute, real clarity).
- Eliminates an inert, misleading gate that suggested false-fire protection that never existed.
- T1/T2 now fire on a signal that is at least honestly the base rate, framed as description.
- Corrects the engagement record: ADR 015's mechanism is documented as non-functional rather than
  silently carried forward (norm #3, norm #10).
- This is a sixth instance of the "computed-but-wired-but-inert" bug class; the Φ8A integration +
  schema tests now guard against shipping a metric that is structurally constant.

**Negative / accepted:**
- T1/T2 will be silent at trend inflections and wrong at reversals (regime-confounded, as above).
  Accepted and stated honestly in the copy; a turn-predicting signal does not exist at current data.
- `schema_version` handling for `direction_consensus` (constant vs removed) requires a transition
  decision; flagged to the implementer.

## Alternatives considered (the a/b/c choice)

- **(a) Keep Chronos as-is with honest "below base rate" labelling.** Rejected: retains 5× redundant
  inference and an inert gate, and continues gating notifications on a below-base-rate signal. Honest
  labelling alone does not justify keeping non-functional machinery.
- **(b) Re-point T1/T2 to momentum/base-rate; Chronos as probe artifact. CHOSEN.** The momentum/base-
  rate signal is at least honestly the thing that works in-regime, framed as description not forecast.
- **(c) Retire Chronos from the live path entirely.** Rejected *for now*: the probe artifact costs
  little, and it is the natural input for re-evaluation if a mixed regime arrives (ADR 019). Retiring
  it fully would discard that option. Revisit (c) if Chronos provides no value after a mixed-regime
  test.

## Future

- If ADR 019's mixed-regime condition arrives and Chronos still adds nothing, execute (c): retire the
  probe.
- A small dedicated momentum model (the long-discussed direction classifier) could replace the raw
  momentum description if it is ever shown to beat the base rate out-of-sample on a mixed-regime fold
  set — same pre-registered-gate discipline as Φ7/Φ8B. Not now.
