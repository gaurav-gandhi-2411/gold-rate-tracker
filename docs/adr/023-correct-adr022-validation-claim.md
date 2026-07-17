# ADR 023 — Correct ADR 022 §3: the 84.7% Figure Is Not Independent Out-of-Sample Evidence

**Status:** Accepted 2026-07-18
**Amends:** ADR 022 (Conformal PI Recalibrated to h=1) — does not reverse it
**Author:** Gaurav Gandhi / CC (orchestrated audit)
**Type:** Correction of an evidentiary claim — norm #3/#10 (append-only correction, not a silent rewrite)

---

## Context

A full model-quality audit (2026-07-18) of the price/magnitude pipeline — walk-forward backtest,
IBJA→Tanishq calibration, feature-store PIT integrity, and the conformal band's validation claim —
found no production lookahead bug. It did find one validation-rigor issue in **ADR 022 §3**
("Validated against live decision history before shipping — honesty floor is 80%, not a target"),
which this ADR corrects.

ADR 022 derives the displayed h=1 band half-width (₹262.6) as the 80th percentile of naive
next-trading-day absolute errors over the **last 30 backtest folds**, whose `context_end_date`
range is **2026-05-11 → 2026-07-03**. It then reports **84.7% coverage** from re-applying that
fixed width to the **59 live resolved decisions** in `data/metrics_history.json`, whose
`decision_date` range is **2026-05-14 → 2026-07-12** — a near-total overlap with the fit window.

Because Tanishq tracks IBJA at a near-fixed premium (the calibration relationship itself), the
quantity used to *fit* the band width and the quantity used to *check* its coverage are the same
underlying daily price move over almost the same dates, up to a constant. Fitting an 80th-percentile
absolute error on a period and then checking coverage of that same period against that percentile
clears ~80% close to by construction — it is not independent confirmation that the band generalizes.
ADR 022's own framing ("not just the 30 backtest folds used to derive it") implied an independence
between the two checks that the date ranges do not actually support.

This is **not a code leak** — `_compute_conformal_pi` and `compute_band_coverage` each compute
correctly from the data they're given. It is a claim in prose (an ADR, and copy derived from it)
overstating what a correctly-computed number proves.

## Decision

### 1. The h=1 recalibration itself is unchanged and correct

Nothing here touches the band width, the horizon fix, or `_compute_conformal_pi`. ADR 022's core
decision — size the displayed band to h=1, matching what `compute_band_coverage` actually tests —
stands as correct and is not being reopened.

### 2. Reframe the 84.7% figure as a retrospective sanity check, not independent validation

ADR 022 §3's 84.7% number is relabeled, in this document and in downstream copy, as an **in-sample-
adjacent consistency check**: it confirms the new band isn't obviously broken on the data it was
built from, nothing more. It must not be cited as out-of-sample proof the band generalizes.

### 3. The genuinely out-of-sample number is the *prospective* per-decision track, going forward

`ml/metrics.py::compute_band_coverage`, run over `data/metrics_history.json`, measures each
decision's `lower`/`upper` **as it was actually recorded at decision time**, resolved against a
strictly later price — that is a real prospective, point-in-time check. ADR 022 §4 already discloses
that this track currently still reads close to the *old*, pre-fix wide-band rate (~98%) because `n`
has no time window and old decisions never get relabeled. This ADR reaffirms that disclosure and
makes explicit: **the coverage_metrics.json prospective track, once it accumulates enough
post-2026-07-17 decisions, is the only number that should be cited as genuine out-of-sample evidence
for the h=1 band — not the retrospective 84.7% figure.** Until then, there is honestly no independent
OOS confirmation of the new band's true coverage rate; that gap is disclosed, not smoothed over.

### 4. Copy and payload updates

- `ml/metrics.py::save_coverage_metrics`'s `note` field now also states that the current `coverage`
  value (while it still mixes pre- and post-fix decisions) is not yet independent evidence for the
  new band, and points back to this ADR.
- `app.js::renderMethodology`'s "How accurate is this?" panel copy is updated so the coverage-%
  sentence doesn't read as a settled validation result while the figure is still pre-fix-dominated.

## Consequences

**Positive:**
- Restores the honest-baseline norm (ADR 005): a number is only cited as evidence for what it
  actually demonstrates. Closes the gap between what ADR 022 claimed and what the two overlapping
  windows can actually support.
- Gives a concrete, checkable trigger for when a real OOS coverage claim becomes available (enough
  post-2026-07-17 decisions in the prospective track), rather than leaving "validated" as a permanent,
  unexamined claim.

**Negative / accepted:**
- There is currently no independent OOS confirmation that the h=1 band holds ≥80% coverage. This is
  disclosed, not hidden. The band itself is not suspected of being wrong (the h=1 recalibration logic
  in ADR 022 is sound), only unconfirmed by non-overlapping data yet.
- Until enough post-fix decisions accumulate, the "How accurate is this?" panel is honestly weaker
  ("early days, not yet confirmed independently") rather than citing 84.7%.

## Alternatives considered

**Leave ADR 022 as written and only fix the code path (there is nothing to fix in code).** Rejected:
the overstated claim lives in prose that is directly quoted into user-facing copy
(`renderMethodology`) and a persisted metrics payload (`coverage_metrics.json`'s `note`); leaving it
uncorrected keeps shipping the overstatement to users and to future engineering sessions reading the
ADR as ground truth.

**Recompute a genuinely non-overlapping validation now, using folds strictly outside
2026-05-11→07-03.** Deferred, not rejected: current backtest history has enough folds in total
(194, per `data/backtest.json`), but a *recency-matched, non-overlapping* window thin enough to be
informative about current market conditions doesn't yet exist — doing this properly needs more
elapsed time, not a different computation today. Revisit once the prospective track (Decision §3) has
accumulated enough n to be the real answer anyway, making a separate held-out backtest redundant.
