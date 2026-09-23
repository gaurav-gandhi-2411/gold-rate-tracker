# ADR 032 — M1 Follow-up: Separating the Proxy's Label and Feature Roles

**Status:** Accepted, implemented 2026-09-23. **Numbered 032** (031 is #1892's ADR, still open) to
avoid a collision once both land.

---

## Context

ADR 030 (`ml/inr_proxy.py`) validated the M1 proxy against real IBJA at 67.1% direction agreement
(n=225, 95% CI 60.7–72.9%) — wrong about one day in three. GG's follow-up separates two requirements
that build conflated: **FEATURES** must be leakage-safe (only values known before each IBJA fix — the
T-1 lag ADR 030 applies); **LABELS** only need to track the real price's moves faithfully — they
describe something that already happened, so leakage protection doesn't apply to them, and using the
feature-safe (T-1-lagged) series as a label source may be needlessly conservative.

## What was measured (`ml/inr_proxy_labels.py`)

### (a) Agreement by lag — timing misalignment ruled out as the cause

Swept the SAME-DAY (unlagged) raw proxy at lags -2..+2 against real IBJA changes (n=252 candidate
pairs; day-over-day, new-value days only):

| Lag | n | Agreement | 95% CI |
|---|---|---|---|
| -2 | 252 | 52.4% | 46.2–58.5% |
| **-1 (= current T-1 feature lag)** | 252 | 68.25% | 62.3–73.7% |
| **0 (same-day)** | 252 | 68.65% | 62.7–74.1% |
| +1 | 252 | 47.2% | 41.1–53.4% |
| +2 | 252 | 42.6% | 36.7–48.8% |

**Finding: the peak is essentially flat across lag -1 and 0 (68.25% vs. 68.65%, CIs almost fully
overlapping) and craters sharply one day the other way (+1: 47.2%, near coin-flip).** COMEX/FX
genuinely leads IBJA's own domestic pricing by 0–1 days, not more — the currently-used T-1 feature
lag was already sitting at (or immeasurably close to) the empirical peak. **Timing misalignment does
NOT explain the ~33% disagreement.** Confirmed at the overall/calibrated level too: the realigned
(lag=0) label series' OOS direction agreement is 67.11% (151/225) — the *exact same integer count* as
ADR 030's original T-1-lagged series. Realignment alone buys nothing measurable here.

### (b) Agreement by move size — the real, actionable finding

Bucketed the same 252 pairs by |real IBJA move| (Rs/gram):

| Move size | n | Agreement | 95% CI |
|---|---|---|---|
| <20 Rs/g | 36 | 44.4% | 29.5–60.4% |
| 20-50 Rs/g | 63 | 54.0% | 41.8–65.7% |
| 50-100 Rs/g | 48 | 68.75% | 54.7–80.1% |
| 100-200 Rs/g | 60 | **85.0%** | 73.9–91.9% |
| 200-500 Rs/g | 40 | **85.0%** | 70.9–92.9% |
| 500+ Rs/g | 5 | 100% | 56.6–100% (n=5, wide CI) |

**Clean, monotonic relationship: agreement climbs from near-coin-flip on tiny moves to 85%+ on moves
≥100 Rs/gram.** This directly confirms GG's hypothesis — small day-to-day noise is genuinely
unpredictable from this formulaic proxy (expected: local demand/dealer-spread/GST-timing noise
dominates at that scale), but the proxy tracks *real, meaningful* moves reliably. **A dead-zone label
(only trust the proxy's direction when its own implied move exceeds ~100 Rs/gram) is well-supported
and should be preferred over a raw every-day direction label built from this proxy.**

### (c) Realigned label series

`ml/inr_proxy_labels.build_label_series` — same construction as ADR 030's `build_proxy_history`
(roll-adjustment, duty schedule, walk-forward Huber calibration reusing the same primitives) but
WITHOUT the T-1 lag, since labels don't need it. Written to
`data/history_seed_inr22k_label.parquet`. Per (a) above, its overall accuracy is statistically
indistinguishable from the original feature-safe series — kept anyway, because it is now the
*structurally correct* choice for label use (no reason to leave leakage protection on a component
that doesn't need it, even where the measured cost of not doing so turned out to be small).

## Decision: the proxy's three roles, stated plainly

| Role | Which series | Why | Evidence |
|---|---|---|---|
| **Feature source** | `data/history_seed_inr22k_proxy.parquet` (`ml.inr_proxy`, T-1-lagged) | Must be leakage-safe; a model consuming this as an input feature must never see same-day information | ADR 030's 3 leakage-trap tests; this ADR's lag sweep confirms no accuracy is being left on the table by keeping the lag |
| **Label source** | `data/history_seed_inr22k_label.parquet` (`ml.inr_proxy_labels`, same-day), **thresholded to a dead-zone** (only trust direction on |implied move| ≥ ~100 Rs/gram) | Labels describe an already-realized outcome; magnitude bucketing shows raw every-day labels from this proxy are only slightly better than a coin flip below that threshold | This ADR's magnitude-bucket table: 85%+ agreement at ≥100 Rs/g vs. 44-54% below 50 Rs/g |
| **Pretraining corpus** | Feature series (T-1-lagged) + dead-zone-thresholded label series, joined on date | Combines the leakage-safe input a live model would actually have with a label reliable enough to be worth learning from | Both of the above; M2's own `reframed_targets.add_deadzone_binary` (PR #1892) is the direct consumer of this pattern already |

## Consequences

- M2's future pretraining-on-the-proxy step (still not done — that's the next M2 sub-task) should use
  the feature series for inputs and the **dead-zone-thresholded label series** for targets, not the
  raw every-day label from either series.
- The lag question is closed — no further re-tuning of the feature lag is warranted from this
  evidence.
- `data/history_seed_inr22k_label.parquet` is committed alongside the feature parquet (same rationale
  as ADR 030: a foundational, slow-changing training input, not a CI-regenerated cache).

## Alternatives considered

- **Re-fit the feature lag to the empirical peak (lag=0) instead of keeping T-1.** Rejected: lag=0
  would reintroduce exactly the same-day leakage ADR 030's feature-safety design exists to prevent,
  for a measured gain (0.4pp) that isn't distinguishable from noise. Not worth the leakage risk.
- **A continuous move-size weighting instead of a hard dead-zone cutoff.** Simpler to reason about
  and matches the dead-zone label already implemented in `ml/direction/reframed_targets.py`
  (`add_deadzone_binary`) — reusing an existing, already-tested pattern rather than inventing a new
  weighting scheme.
