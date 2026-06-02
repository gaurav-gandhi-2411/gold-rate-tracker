# ADR 018 — Drift-Naive Evaluated; Held (Not Promoted) Pending Out-of-Regime Evidence

**Status:** Accepted 2026-06-02
**Author:** Gaurav Gandhi / external consultant / CC
**Type:** "We decided NOT to promote X (yet)" — norm #3

---

## Context

Batch Φ7 (PRs #60–#62) ran three pre-registered backtest experiments against the flat-naive
headline, using the existing walk-forward harness. The promotion gate was fixed **before**
results existed (mirrors ADR 003 + ADR 012): ≥2% MAE improvement over flat-naive, Wilcoxon
signed-rank p < 0.05 on paired per-fold absolute errors, holding on ≥30-context folds only,
with ≥30 such folds.

Results (full record in `data/experiments/phi7_results.json`, 12 entries):

| Variant | Horizon | Result | Gate |
|---|---|---|---|
| drift-naive, EWMA span 5/10/20 | h=5 | −15% to −35% vs flat-naive | **false** |
| premium-carry (flat-carry international) | h=5 | exactly 0.00% (algebraic identity) | **false** |
| Chronos | h=5 / h=10 / h=20 | −11.04% / −9.35% / −6.07% | **false** (all) |
| **drift-naive, EWMA span 20** | **h=20** | **+5.17%, p=0.0014, 129 folds** | **true** |

Three of these are clean, useful negatives: short-horizon drift overshoots in a trend;
flat-carrying the international components collapses premium-carry to flat-naive by construction
(confirmed null); and the Chronos-minus-naive gap narrows with horizon but never crosses the
gate, falsifying the "model for the month" feature hypothesis.

**One variant passed every pre-registered criterion: `drift_naive_span20` at h=20.** Under our
own rules, that is a win. This ADR records why it is nonetheless **held, not promoted.**

## The problem: regime confounding

The full dataset (2022–2026) is **one sustained bull regime** (~Rs.85,000 → Rs.145,000). A
20-day drift term at a 20-day horizon is mechanically "assume the recent trend continues." In a
single uninterrupted uptrend, that variant and the regime are confounded — extrapolating the
trend wins because the trend never broke in-sample.

The p=0.0014 is therefore answering a narrower question than the one we care about. It measures
"is span-20 drift reliably better **in this bull regime**," not "is it better in general." There
is no bear or sideways stretch in the data for the variant to be wrong in, so the backtest cannot
falsify it. A gate that passes only because the falsifying case is absent from the sample has not
truly been passed.

This is the directional cousin of the failure ADR 005 exists to prevent. Promoting a
trend-following headline on the strength of the largest gold bull run in a decade is a window-
selection artefact wearing a statistical disguise. If the trend flattens or reverses, span-20
drift at h=20 inverts from best-in-class to worst-in-class — and a **headline** forecast that is
structurally worst during a reversal is a worse failure mode than flat-naive's honest flatness.

## Decision

**Do not route `drift_naive_span20` (or any drift variant) into the production headline at this
time.** Flat-naive remains the headline forecast (ADR 012 unchanged).

The Φ7 finding is recorded as **evaluated and held**: the variant passed the pre-registered gate,
and is deliberately not promoted because the gate was satisfied inside a single regime with no
out-of-regime folds to falsify it.

## Falsifiable promotion condition (re-evaluation trigger)

Re-evaluate drift-naive for headline promotion when **both** hold:

1. The dataset contains a genuine out-of-regime stretch — a ≥10% peak-to-trough drawdown **or** a
   sustained sideways period (≥30 trading days with |net drift| below the daily-delta noise band)
   — producing a non-bull fold set.
2. `drift_naive_span20` still passes all four gate criteria **after excluding the pure-uptrend
   folds** (i.e. it holds on the non-bull subset, or on the full set with the bull-only folds
   removed). At minimum it must not invert to worst-in-class on the non-bull subset.

Until then, the honest position is: drift-following is a bet on trend continuation, and we have
no evidence it survives a regime where the trend stops.

## Horizon caveat (independent of regime)

The pass is at **h=20**, but the production headline horizon is **h=5** (where drift loses badly,
−15% to −35%). Even setting regime aside, adopting span-20 drift would mean shipping a 20-day
headline — a different product decision, not a drop-in replacement. A 20-day-horizon product may
be worth considering on its own merits (it is arguably more honest about the uncertainty users
actually face), but that is a separate UX/product ADR, not a consequence of this result.

## Consequences

**Positive:**
- The level-forecast frontier is now mapped with pre-registered rigour: nothing cheap beats
  flat-naive at the production horizon out-of-regime. Effort can redirect with confidence.
- The held finding has a concrete, testable promotion path rather than being silently discarded.
- Records *why* a gate-passing variant was withheld — a more sophisticated honesty than the
  numeric gate alone encodes.

**Negative / accepted:**
- We may be leaving a real edge on the table **if** the bull regime persists indefinitely. Accepted:
  a headline that is excellent in one regime and worst in another is not a headline we can stand
  behind under ADR 005.
- The re-evaluation trigger depends on market conditions we do not control and cannot schedule.

## Alternatives considered

**Promote span-20 drift now (the gate passed).** Rejected: the gate passed inside a single
confounded regime; promotion would violate the spirit of ADR 005. The pre-registered gate is a
floor, not the whole judgement.

**Discard the finding as a pure artefact.** Rejected: it is a real, statistically significant
in-regime result with a clean promotion path. Discarding it loses information; holding it with a
falsifier preserves it.

**Ensemble flat-naive with drift (e.g. 0.5/0.5).** Rejected for the same reason ADR 012 Alt-4
rejected Chronos+naive: ensembling a regime-dependent variant with the benchmark adds complexity
without an out-of-regime guarantee, and obscures which component drives the signal.

## Follow-up (queued, not part of this ADR)

A diagnostic backtest re-running span-20 drift at h=20 on the **non-bull subset only** (whatever
drawdown/sideways folds exist, even if <30 — explicitly below gate power) to observe the predicted
sign flip. This does not promote anything; it converts "we believe this is a regime artefact" into
"we showed drift loses outside the uptrend." Result to be recorded in the Decision Log, not as a
gate evaluation.
