# ADR 015 - Multi-Sample Chronos Probe with Consensus Gating

**Status:** Accepted 2026-05-22
**Author:** Gaurav Gandhi

---

## Context

Chronos-Bolt-Tiny's `predict_quantiles` is stochastic across calls because the underlying
PyTorch random state advances with each invocation. A single call to `forecast_ibja` produces
one sample from the model's predictive distribution.

**Documented direction instability (PR E observation, CURRENT_STATE.md "Known issues"):**
A probe run on the same IBJA context produced "DOWN 2.29%" in one cycle and "UP 3.73%" 24 hours
later with no meaningful change in the underlying data. This is a known consequence of
single-sample stochastic inference, not a data quality issue.

**Reference ADR 012 — Chronos as directional companion:**
ADR 012 documents Chronos direction accuracy at 55.8% on the full backtest and 63.3% on the
last 30 folds. At this accuracy level, a single-sample direction draw is near-coin-flip quality.
The T1/T2 notification gates (ADR 011) fire on the direction signal. A single stochastic draw
that incorrectly claims "up" or "down" causes a false-fire with no option to self-correct until
the next 6-hour cron cycle.

**The core problem:** a single sample from a 55-63% accurate stochastic model fires T1/T2
notifications that are visible to end users. False fires erode trust faster than false negatives.

---

## Decision

The probe (`run_probe`) now calls `forecast_ibja` `DEFAULT_NUM_SAMPLES = 5` times per cycle,
collecting 5 independent forecast DataFrames.

**Per-sample direction classification:**
- Compute `median(p50 across horizon)` for the sample.
- `delta_pct = (median_p50 - ibja_last) / ibja_last`
- If `abs(delta_pct) < 0.001` (0.1%), label "neutral".
- Otherwise label "up" (positive) or "down" (negative).

**Aggregation:**
- `majority_direction` = most-frequent label among the 5 samples.
- `direction_consensus` = count(majority) / 5, rounded to 3 decimal places.

**T1/T2 gate (implemented in pass 2/2, ml/notifications.py):**
- Fire T1/T2 only when `majority_direction in {"up", "down"}`
  AND `direction_consensus >= 0.6` (i.e., at least 3 of 5 samples agree).

**Threshold rationale:**
- 3-of-5 gives consensus = 0.6 exactly (meets gate).
- 2-of-5 gives 0.4 (rejects gate).
- A 2-2-1 split gives max 0.4 per label (rejects gate).
- 0.6 was chosen as the smallest threshold that enforces a simple majority while
  still rejecting all tie cases.

**Wall-clock budget:**
- Probe wall-clock measured at probe-run time: pipeline_load ~7.5s (local Windows) / ~10s (Ubuntu CI),
  forecast ~100ms for 5 samples (was ~15ms for 1). Total ~7.7s local / ~10s CI.
- The pre-Phi4 baseline (committed `chronos_probe.json` written by CI cron on 2026-05-21) showed
  pipeline_load=9893ms, forecast=15ms, total=9932ms -- so the dominant cost is model deserialization,
  not inference. Phi4's incremental cost is ~85ms of additional forecast time; the absolute <2s budget
  mentioned in spec.md was based on the spec author's assumption of ~1s model load, which is not what
  we observe in practice.

**Schema bump:**
- `schema_version` incremented from 1 to 2.
- New fields in `chronos_probe.json`: `num_samples`, `sample_directions`,
  `majority_direction`, `direction_consensus`.
- Failure paths (insufficient_context, model_load_failed, forecast_failed) default
  these fields to 0 / [] / "neutral" / 0.0 so consumers can rely on field presence
  regardless of probe status.

---

## Consequences

**Positive:**
- T1/T2 false-fire rate from direction noise should drop substantially. The gate moves
  from "fires on any single sample with 0.5%+ lean" to "fires only when 3+ of 5 independent
  samples agree on direction."
- `sample_directions` array is logged in `chronos_probe.json` and provides an audit trail
  for observed consensus rates over time. This data will inform future threshold tuning.
- The ~10s total wall-clock is dominated by model_load (pipeline_load=9893ms in the pre-Phi4 baseline
  committed on 2026-05-21). This cost is pre-existing and not introduced by Phi4.
- Phi4 itself adds ~85ms of additional forecast inference (5 samples vs 1 sample). This is negligible
  relative to the ~10s model-load baseline.
- The 6-hour cron cadence makes a ~10s probe operationally fine; there is no SLA concern.
- Follow-up: the pipeline_load cost was never empirically measured before this PR. Future investigation
  may find that switching from torch.load-based deserialization to safetensors lazy loading, or
  pre-caching the pipeline at module import, could reduce cold-start latency. This is out of scope
  for Phi4.

**Negative:**
- 5x inference cost per probe cycle (~100ms vs ~15ms for forecast alone). The dominant cost remains
  model_load (~10s), which is unchanged by Phi4.
- T1/T2 may suppress notifications for real moves when Chronos is genuinely uncertain
  (e.g., 3-of-5 disagree at a trend inflection). This false-negative risk is accepted:
  in a notification context, false positives are more damaging than false negatives.
- Stochasticity is NOT eliminated. A probe could produce 5 different directions across
  5 samples (1-1-1-1-1 split, max consensus 0.2). That case fires no notification, which
  is the correct and desired behavior.
- `schema_version` bump to 2 requires downstream readers to handle both v1 and v2 schemas
  during the transition cycle (one cron run after deployment regenerates the file as v2).

---

## Alternatives Considered

**Longer T1/T2 cooldown (24h to 72h):**
Reduces notification frequency but does not address the root cause: a single stochastic
sample can still fire in any given 72h window. Rejected.

**Deterministic seed for Chronos:**
Passing a fixed seed to PyTorch would make every call identical, defeating the purpose of
running multiple samples to estimate direction stability. A deterministic single sample is
no better than the current single-sample probe. Rejected.

**Single longer-horizon forecast:**
A single forecast over h=15 or h=30 days is still one sample from the predictive distribution.
It does not reduce stochastic direction variance across probe cycles. Rejected.

**Larger num_samples (e.g., 10):**
Doubles inference cost (130ms). Consensus resolution improves from 0.2 steps (5 samples) to
0.1 steps (10 samples), which provides marginal benefit at the current direction accuracy level.
5 samples was chosen as the smallest number that gives 0.6 gate semantics (3-of-5). Rejected.

**Higher consensus threshold (e.g., 0.8 = 4-of-5):**
Would require 4 of 5 samples to agree before firing T1/T2. At current direction accuracy of
55-63%, requiring 4-of-5 would suppress too many real directional moves. The 0.6 threshold
was chosen to require a simple majority. The threshold can be tuned upward once more
direction-accuracy data accumulates. Rejected for initial deployment.
