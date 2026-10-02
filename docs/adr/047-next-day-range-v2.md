# ADR 047 — Pre-registration: rebuild the displayed next-day range on the ADR 043 method

**Status:** Accepted for pre-registration, 2026-09-24. `ml/next_day_range.py`,
`scripts/run_next_day_range_shadow.py` and their tests are frozen in this PR **before the
retrospective is run on any data**. Research and forward shadow only. Nothing a user sees
changes until GG approves a separate promotion PR.

**Builds on:** ADR 043 (the calibrated 1-day range on real IBJA, ml.weekly_range) and ADR 022
(the current displayed naive flat-hold conformal band, and its `min_decision_date` fix).

## Context

The site shows a "tomorrow's range" alongside the current estimate: `[lower, upper]` on
`current_22k`, built by ADR 022's naive flat-hold conformal method. It is logged forward,
decision by decision, in `data/metrics_history.json` (`decision_date`, `current_22k`, `lower`,
`upper`, `actual_next_22k`). Measured on the 63 decisions made on or after ADR 022's fix date
(`ml.metrics.ADR_022_FIX_DATE = 2026-07-18`, the standard cutoff `ml.metrics.compute_band_
coverage` already uses so pre-fix, differently-calibrated decisions are never averaged in):

- **Overall: 46/63 = 73.0%** [Wilson 95% 61.0%, 82.4%] against an 80% target.
- **Mon-Thu decisions: 24/36 = 66.7%.** **Fri-Sun decisions: 22/27 = 81.5%.**

ADR 043 built and measured a different, better-calibrated 1-day range for a different purpose
(the weekly-range product item): historical simulation on the INR proxy for shape, split
conformal on real IBJA for scale. On IBJA's own publication-day series it measures 85.1% walk-
forward (n = 161) and 80.0% chronological hold-out (n = 80) — see `docs/adr/043-weekly-price-
range.md`. That is the best-measured short-horizon method on real IBJA available in this
codebase (ADR 041, R1). This ADR asks a narrower, different question from ADR 043's: **does the
same method, applied at the displayed decision day and scored against the same truth the
displayed range is scored against, beat the displayed range on its own days?**

**Those 73.0% / 66.7% / 81.5% figures were seen before this registration.** Any comparison on
the 63 existing decision days is therefore run with that caveat stated plainly (see "Confirmatory
set" below) — it is a real backtest of v2 (v2 itself has never been computed on these days
before this PR), but it is not a blind forward test, because the target it is being compared
against was already known.

## Decision

`ml/next_day_range.py` implements this, reusing `ml.weekly_range`'s primitives exactly (no new
statistics, no retuning):

- **For a decision day `d`** with displayed price `P_d` (`current_22k`): filter the INR proxy
  series and the IBJA series to `index < d` (strictly before `d` — the proxy price on `d` itself
  is not treated as known at decision time, matching the displayed range's own T-1 discipline).
- **Base range (shape):** `ml.weekly_range.base_range` on the filtered proxy history, `k=1` —
  10th/90th percentile of 1-day historical-simulation log returns, unchanged from ADR 043.
- **Conformal scale:** `ml.weekly_range.complete_windows` + `conformal_scale` on the filtered
  IBJA history's "1d" windows. Because the IBJA series is filtered to `index < d` before
  `complete_windows` ever sees it, every window's as-of day and outcome (last publication) day
  are automatically strictly before `d` — "the conformal scale fitted on IBJA windows whose
  outcome date < d", the same no-leak rule ADR 043 froze, applied at `d` instead of at an IBJA
  row. This is exactly `scripts/analysis_weekly_range.py`'s "1d" walk-forward
  (`ml.weekly_range.walk_forward`), evaluated at one as-of date instead of over the whole series.
- **Range:** `[P_d * (1 + lo), P_d * (1 + hi)]`, where `lo, hi = ml.next_day_range.
  next_day_range_pct(proxy, ibja, d)` are the base range scaled by the conformal scale, expressed
  as percentages of `P_d` (`exp(s * lo_base) - 1`, `exp(s * hi_base) - 1`).
- **Truth:** `actual_next_22k` from `data/metrics_history.json` — the identical number the
  displayed range is scored against, so v2 and the displayed range are compared on the same
  target, not on IBJA's own PM price (which ADR 043 uses and which is a different series from the
  retail 22K estimate the site shows).

`scripts/run_next_day_range_shadow.py` recomputes v2 for every resolved decision day (same
filter as `ml.metrics.compute_band_coverage`: outcome resolved, `decision_date >=
ADR_022_FIX_DATE`) and writes `data/next_day_range_shadow.json`. Full recompute every run, not
append-only: both the retrospective and forward blocks are pure functions of `metrics_history.
json` plus historical proxy/IBJA prices, and `next_day_range_pct` enforces its own no-look-ahead
internally (filtering to `index < d` before either primitive runs), so there is no accumulation
step that could leak future data backward into an already-scored day.

### Frozen evaluation protocol

- **Primary metric:** coverage on the same resolved decision days the displayed range is scored
  on, with a Wilson 95% CI.
- **Secondary metric:** mean width in ₹.
- **Strata:** Mon-Thu vs Fri-Sun decision days (the same split that showed the displayed range's
  weakest cell above).
- **Success** (retrospective and forward, evaluated separately): v2's coverage Wilson 95% CI
  contains 80% **AND** v2's coverage ≥ the displayed range's coverage on the same days **AND**
  v2's mean width is not more than 25% wider than the displayed range's mean width.
- **Exact one-sided binomial p vs 80%** is reported alongside every coverage figure (`H0`: true
  coverage = 80%; `H1`: true coverage < 80% — the concern here is under-coverage, not
  over-coverage; `scipy.stats.binomtest(k, n, 0.80, alternative="less")`).
- **Confirmatory set:** decision days with `decision_date` strictly after this ADR's freeze date,
  **2026-09-24** (`scripts/run_next_day_range_shadow.py`'s `FREEZE_DATE`, matching the PR's merge
  date). This is the only set that is a genuine blind forward test.
- **Retrospective set:** the 63 (and growing, up to the freeze date) decisions since ADR 022's
  fix date, already resolved as of the freeze. **These are reported as a held-out-but-already-
  seen-for-the-displayed-range retrospective, not a confirmatory result** — v2 itself was never
  computed on them before this PR, but the number it is being compared against (the displayed
  range's 73.0% / 66.7% / 81.5%) was known before this method was chosen or frozen. Stated
  plainly so the retrospective is never mistaken for the confirmatory read.

Nothing about the method (window length, percentiles, conformal target, `k`) is tuned after
seeing either the retrospective or the forward result.

## Results

**Provenance:** `data/next_day_range_shadow.json`, from `scripts/run_next_day_range_shadow.py`
at freeze commit `e05695759cc7a55723ca1db5b361e50d9761c0b8` (this PR), run immediately after that
commit was pushed. `min_decision_date = 2026-07-18`, `frozen_at = 2026-09-24`.

### Retrospective (n = 63, 2026-07-18 to 2026-09-18 — already-seen-for-the-displayed-range days)

| | n | k | coverage | Wilson 95% | one-sided p (H1: coverage < 80%) | mean width (₹) |
|---|---|---|---|---|---|---|
| **v2** | 63 | 53 | **84.1%** | [73.2%, 91.1%] | 0.835 | 545.5 |
| displayed (naive flat-hold) | 63 | 46 | 73.0% | [61.0%, 82.4%] | 0.112 | 417.5 |

By stratum:

| | n | v2 coverage [Wilson] | displayed coverage [Wilson] |
|---|---|---|---|
| Mon-Thu | 36 | 77.8% [61.9%, 88.3%] | 66.7% [50.3%, 79.8%] |
| Fri-Sun | 27 | 92.6% [76.6%, 97.9%] | 81.5% [63.3%, 91.8%] |

**Success criteria:**

| criterion | result |
|---|---|
| v2's Wilson 95% CI contains 80% | **pass** ([73.2%, 91.1%] contains 80%) |
| v2's coverage ≥ displayed's coverage | **pass** (84.1% ≥ 73.0%) |
| v2's mean width ≤ 1.25 × displayed's mean width | **fail** — v2 is 545.5 / 417.5 = **1.31×** displayed, i.e. 30.6% wider, above the 25% ceiling |
| **overall** | **fail** (width criterion) |

**In plain words.** v2 clears the coverage bar on every measure — it covers noticeably more of
the same 63 days than the displayed range does (84.1% vs 73.0%), including on the displayed
range's weakest cell (Mon-Thu: 77.8% vs 66.7%). It does this the way ADR 043 already found it
would: by being wider. At 30.6% wider than the displayed range's mean width, it misses the
pre-registered width ceiling. The trade is real and was not free: v2 buys its higher coverage
with a materially wider stated range, and the frozen success bar (all three conditions, not just
coverage) correctly reports this as **not a clean win**, not a partial pass rounded up.

### Forward (confirmatory; n = 0 as of the freeze)

No decision has yet been made after the freeze date (2026-09-24 is also `metrics_history.json`'s
latest, still-pending decision day). This block will accrete one row per resolved decision once
`scripts/run_next_day_range_shadow.py` runs weekly (see the workflow line below) — it is the
only genuinely blind read of this method, and the one the promotion question should ultimately
be decided on, not the retrospective above.

### What this means

The retrospective does not clear this ADR's pre-registered bar, on the width condition
specifically. Two honest readings, stated in the open:

- If coverage alone were the bar, v2 would already look better than the range in production. It
  is not, on purpose — width is part of the promise ("very likely ... between ₹X and ₹Y"), and a
  range that is 31% wider is a real cost to a viewer even when it is right more often.
- The forward shadow is the next real test. Because it is a genuinely blind read (v2 was never
  tuned against it), it can move this verdict either way without the seen-the-target caveat the
  retrospective carries. No promotion PR is warranted from the retrospective alone.

## Consequences

- Nothing a user sees changes. `ml/inference.py` and every UI file are untouched.
- `scripts/run_next_day_range_shadow.py` is meant to run weekly (`weekly-backtest.yml`, wired in
  a follow-up PR) so the forward block accretes one row per resolved decision day. The promotion
  PR (if any) waits for the forward set to reach a size where its own Wilson CI is informative —
  the retrospective is evidence toward starting the shadow, not evidence toward promotion.
- If the forward coverage undercuts the retrospective, or the success criteria are not met, this
  method is not promoted, and the finding is written up (positive or negative) the same way ADR
  043 wrote up its own honest results.

## Alternatives considered

- **Score v2 against IBJA's own PM price** (ADR 043's original truth), rather than against
  `actual_next_22k`. Rejected: the displayed range is scored against `actual_next_22k`, and the
  entire point of this ADR is a same-truth, same-days comparison against the range users
  currently see — scoring against a different truth would answer ADR 043's question again, not
  this one.
- **Re-fit the conformal scale on `metrics_history.json`'s own decisions** instead of reusing
  ADR 043's IBJA-fitted scale. Rejected: `metrics_history.json` has only ~63-130 rows, far below
  `ml.weekly_range.MIN_CAL = 30` matured windows needed for a stable quantile, and refitting would
  make this a new, untested method rather than a reuse of ADR 043's already-measured one.
- **Retune `HS_WINDOW`/`CAL_WINDOW`/percentiles for this use case** after seeing the retrospective
  result. Rejected — same reasoning as ADR 043: any change after seeing a result is test-set
  tuning, and could only be validated on the forward shadow, never on the days already used to
  motivate the change.
