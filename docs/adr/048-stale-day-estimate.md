# ADR 048 — Stale-IBJA days get their own estimate and their own band

**Status:** Proposed 2026-09-24. Pre-registration only: the text and the code
(`ml/stale_day_estimate.py`, `scripts/run_stale_day_shadow.py`, `tests/test_stale_day_estimate.py`)
are frozen at the pushed commit that carries this sentence, **before any run** of
`scripts/run_stale_day_shadow.py`. Research; nothing a user sees changes -- see "What promotion
would need" below for exactly what a future promotion PR would touch, and that it stays behind a
feature flag OFF until GG turns it on.

**Builds on:** `ml.calibration.evaluate_empirical_band_coverage` (the production scoring set) and
`evaluate_stratified_band_coverage` (AP3, 2026-09-21 -- the existing freshness-stratified SHADOW
band, which widens the *band* around the same IBJA-calibrated estimate on a stale-IBJA day but never
changes the *estimate* itself).

## Context

On a weekend or holiday, IBJA does not publish and the displayed IBJA-calibrated estimate is built
from the latest IBJA row on file, which is now older than the Tanishq day it is predicting --
`ml.inference` already labels this `freshness_stratum = "carry_forward"`. Re-measuring the current
production scoring set (`evaluate_empirical_band_coverage`'s days, `data/ibja_rates.parquet` /
`data/prices.json` as of this commit):

| | n | MAE (Rs/g) | band coverage | nominal |
|---|---|---|---|---|
| stale-IBJA days, live estimate (S1) | 28 | 96.1 | 57.1% [39.1%, 73.5%] | 80% |
| stale-IBJA days, yesterday's Tanishq carried forward | 28 | 52.5 | -- | -- |
| stale-IBJA days, existing stratified shadow band (same S1 estimate, wider band) | 28 | 96.1 | 85.7% | 80% |

The existing shadow (AP3) already shows the *band* problem is fixable by sizing it separately for
stale days. It does not touch the *estimate* -- and the estimate itself is off by roughly twice as
much on a stale day (Rs 96/g) as simply carrying yesterday's real retail reading forward (Rs 52.5/g).
Those two numbers were seen before this registration (they are quoted in the task brief this ADR
implements); scoring them again here is **exploratory**, not confirmatory -- see "Confirmatory vs
exploratory" below.

**Why this might be true.** The IBJA-calibrated estimate answers "what would Tanishq's price be if it
moved the way IBJA's *stale* PM fix implies," which is a bet on a fix that is, by construction, not
the fix for this day. Carrying yesterday's actual Tanishq reading forward makes no such bet -- it
says "assume no change," which is exactly right whenever Tanishq itself does not move over a
weekend (a documented pattern: AP3's docstring notes 26 of 28 carry-forward days in its own window
were Saturday or Sunday). A national retail *consensus* built from other retailers who published
that same day (GRT, Malabar) is a third, independent way to answer the same question, worth testing
on its own but not a substitute for the primary comparison.

## Decision -- the frozen test (do not tune)

**Scoring set.** Exactly `ml.calibration.evaluate_empirical_band_coverage`'s scoring set: every
Tanishq daily reading date, asof-matched backward to the latest IBJA row within the max-age gate
(`_SCORING_MAX_IBJA_AGE_DAYS`), after the same `_MIN_FIT_OBSERVATIONS`-pair warm-up. A day is
**stale** when the matched IBJA row is dated strictly before it (`gap_days >= 1`); otherwise it is
an **IBJA day**.

For each scored day t:

- **S1 (live).** The production IBJA-calibrated estimate and band, exactly as
  `evaluate_empirical_band_coverage` / `evaluate_stratified_band_coverage`'s "production" column
  score it today: a recency-weighted Huber fit on same-day IBJA/Tanishq pairs strictly before t, band
  from the recency-weighted empirical `|residual|` quantile (`ml.calibration._weighted_percentile`)
  of that same fit's own training residuals, at `NOMINAL_LEVEL` = 80.

- **S2 (proposed).** On an IBJA day, S2 **is** S1, exactly -- there is nothing to carry forward. On a
  stale day: the estimate is the latest **actual** Tanishq reading dated strictly before t (raw
  carry-forward -- not re-run through the IBJA calibration at all), gated at
  `MAX_CARRY_FORWARD_AGE_DAYS` = 4 calendar days old; older than that, or missing, falls back to S1's
  estimate (counted as `s2_estimate_fallback`). The band is the recency-weighted (half-life =
  `ml.calibration._DEFAULT_HALF_LIFE` = 10 overlap pairs) 80th-percentile quantile
  (`ml.calibration._weighted_percentile`) of `|carry-forward error|` over **earlier stale days**
  (date < t) whose own S2 estimate was itself resolved, not a fallback -- a fallback day never had a
  genuine carry-forward error to contribute. Below `MIN_CARRY_RESIDUALS` = 8 such earlier days, the
  band falls back to S1's band for this day (counted as `s2_band_fallback`).

- **S3 (secondary).** Only attempted on a stale day where a same-day national fusion benchmark
  exists, built from `data/fusion_snapshots.parquet`'s GRT + Malabar national-level (`city` null)
  readings for that date under `ml.fusion.DEFAULT_WEIGHTS` (`fusion_benchmark_per_day` -- IBJA is
  excluded from this benchmark on purpose, since IBJA being unavailable is exactly the situation S3
  is for). The estimate is that benchmark times the recency-weighted **median**
  (`_weighted_percentile` at the 50th percentile) of Tanishq/benchmark over every earlier day
  (stale or not) where both a Tanishq reading and a benchmark exist; below `MIN_FUSION_RATIO_PAIRS` =
  8 such pairs, S3 falls back to **S2** (not S1), counted as `s3_estimate_fallback`. The band follows
  S2's rule -- the recency-weighted 80th-percentile quantile of `|S3 error|` over earlier stale days
  where S3 itself resolved, minimum 8, else falls back to S2's band (`s3_band_fallback`). A day with
  no fusion benchmark at all gets `s3_estimate = s3_half_width = None` -- S3 was never attempted for
  it, which is distinct from attempted-and-fell-back.

No look-ahead: every quantity used to score day t is built exclusively from rows strictly before it,
walking the scoring set once in date order (`ml/stale_day_estimate.py`; see its no-look-ahead test).

## Frozen hypotheses

**Primary.** On stale days, MAE(S2) < MAE(S1): one-sided paired HAC Diebold-Mariano test
(`ml.direction.evaluate_reframed.diebold_mariano_test`, `horizon=2` i.e. lag 1, `alternative="less"`),
with effective n, α = 0.05.

**Secondary**, Holm correction over the secondary family: MAE(S3) < MAE(S2), same test, on the
stale days where S3 was attempted.

**Also reported, not hypothesis tests:** band coverage of the *active* band (S1 on an IBJA day, S2 on
a stale day -- i.e. what the site would actually be showing under S2) with Wilson 95% CI and the
exact one-sided binomial P(coverage ≤ observed | n, 80%), for both strata (IBJA day / stale) and
pooled; S1's own band coverage for the same breakdown, as the baseline it's compared against; mean
half-widths; and the fallback counts above (a design that falls back often is a design that hasn't
actually been tested much yet, which the numbers should say plainly).

## Confirmatory vs exploratory

`FROZEN_AT = "2026-09-24"` in `scripts/run_stale_day_shadow.py`, this PR's freeze date. A day at or
before it is **retrospective / exploratory** -- its numbers (the Context table above, and the
`retrospective` block on this script's first run) were substantially already seen before this
registration, because they are exactly what this task brief quotes. A day strictly after it is
**forward / confirmatory** and accrues one `weekly-backtest.yml` run at a time in the `forward` block
of `data/stale_day_shadow.json`, append-style (nothing here ever recomputes an earlier day once
scored, matching `scripts/run_weekly_range_shadow.py`'s existing convention). The primary and
secondary hypotheses above are read against the **forward** block only, once it has enough stale
days to be worth reading -- there is no fixed n target here (stale days accrue roughly 2/7 of
calendar days), so read it whenever GG asks, and report `forward.strata.stale.n` alongside any
reading, not just the p-value.

## What promotion would need (NOT applied here; feature-flagged OFF)

If the forward confirmatory window holds, promoting S2 (or S3, where it also beats S2) into
`ml.inference._select_price_source` would need, and only needs, a change inside the existing tier-2
branch -- no new tier, no change to tier 1/3/4's gates:

```python
# ml/inference.py, near the other tier-2/3 constants
_STALE_DAY_ESTIMATE_ENABLED: bool = False  # ADR 048 -- OFF until GG reads the forward window and
                                            # flips this; see docs/adr/048-stale-day-estimate.md

# inside _select_price_source, right after ibja_result is unpacked:
    ibja_result = _try_ibja_calibrated(calibration, data_dir, now)
    if ibja_result is not None:
        (current, source, est_low, est_high, ibja_asof, band_method,
         nominal_coverage, freshness, quantile_source, band_half_width,
         band_unavailable_reason) = ibja_result
        if _STALE_DAY_ESTIMATE_ENABLED and freshness == "carry_forward":
            # freshness == "carry_forward" is _try_ibja_calibrated's own existing flag for
            # exactly this ADR's "stale day" -- no new staleness test needed.
            current, band_half_width, band_unavailable_reason = _stale_day_override(
                current, band_half_width, band_unavailable_reason, data_dir, now,
            )
        return (current, source, est_low, est_high, ibja_asof, None, band_method,
                nominal_coverage, freshness, quantile_source, band_half_width,
                band_unavailable_reason)
```

`_stale_day_override` (new, in `ml/stale_day_estimate.py`, exported for `ml.inference` to call) would
apply exactly the frozen S2 rule (S3 where a live fusion benchmark is available same-cycle, else S2)
using the real-time data files, with the same fallback behavior -- on a fallback it returns `current`
and `band_half_width` **unchanged**, so promotion can never make the displayed number worse than
today by construction, only leave it as-is or replace it with the forward-validated alternative. This
is a sketch of the shape, not a diff to apply: the exact signature, error handling and a live-path
integration test are a promotion PR's own scope, gated on GG's read of the forward window, same
process as ADR 036's direction-signal promotion gate.

## Known limits, stated before the run

- **The retrospective window is short and not weekday/weekend-independent.** AP3 already noted 26/28
  of its carry-forward days were weekends -- "stale IBJA" and "weekend" cannot be separated with the
  data on hand. If Tanishq itself also just re-serves Friday's number over the weekend (plausible;
  not directly verified here), S2's near-zero error on those days partly reflects that, not a genuine
  predictive edge.
- **S3's history is short.** `data/fusion_snapshots.parquet` starts 2026-07-19, well after
  `data/prices.json`; MIN_FUSION_RATIO_PAIRS and the stale-day band history both take real time to
  accrue, so S3 will under-report relative to its steady-state performance for a while.
- **The band-fallback rule can leave S2/S3's OWN coverage below 80%** even when the point estimate is
  a clear MAE win (a real possibility, not merely hypothetical -- see the Results section once it is
  filled in). A DM win on MAE and a coverage win on the band are separate claims; both are reported,
  neither is allowed to stand in for the other.
- **This is still a shadow.** Nothing here reads `ml.inference`'s live data files under production
  latency/failure conditions (a live parquet read failing mid-cycle, a fusion snapshot not yet
  written this cycle) -- a promotion PR needs its own integration test for that, not just this
  retrospective/forward scorer.

## Alternatives considered

- **Only widen the existing band (AP3), ship that.** Rejected as the sole fix: it lowers the
  under-coverage but leaves the underlying estimate exactly as wrong (Rs 96/g MAE) as it is today --
  a wider band around a worse number is not obviously the right trade for a user just trying to
  decide whether the displayed price is close to real, so the estimate itself needs testing too.
- **Always use carry-forward on a stale day, no fallback and no separately-sized band.** Rejected:
  the existing production band-coverage regression gate (`tests/test_calibration.py`) already exists
  specifically because an unmeasured, ungated band is how the site got to 57% coverage on stale days
  in the first place; a new estimator needs the same discipline from day one, not a promise to add it
  later.
- **A regression of Tanishq on GRT/Malabar/Kalyan jointly (a real fused fusion model) instead of a
  fixed-ratio scaling of a GRT+Malabar benchmark.** Rejected for this registration: the fusion
  snapshot history is short enough (present since only 2026-07-19) that a multi-parameter regression
  would be badly overfit; a single recency-weighted ratio is the simplest model the data can support,
  and Kalyan is excluded because ADR 026 already found it identical across cities (no independent
  city-level signal), so including it here would not add information over GRT+Malabar.
- **Score against `ml.inference`'s literal live output instead of re-deriving S1 from
  `ml.calibration`.** Rejected: re-deriving S1 via the exact scoring-set-parity-tested code path
  `evaluate_empirical_band_coverage` already uses is how AP3 does it too, and keeps this ADR's S1
  identical to the number already quoted in the Context table without a second, separately-audited
  code path to the same "production estimate" concept.
