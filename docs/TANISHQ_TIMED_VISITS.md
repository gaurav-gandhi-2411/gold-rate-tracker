# Tanishq timed visits (GG decision E3)

## Pre-registration (written before the final analysis run)

Disclosure: before this section was written, one exploratory listing of the
consecutive-capture change intervals in `data/prices.json` was printed (IST
start/end of each change interval). No estimator, schedule optimum,
staleness figure or bootstrap had been computed.

**Data.** Every live Tanishq capture: `data/prices.json` entries whose
`source` is not a history backfill, plus entries present in any earlier
git revision of that file and later removed. Timestamp convention: the
`timestamp` field is the UTC capture instant set by `scraper/scrape.js`
(`new Date()` after a successful parse). A *change* is any difference in
the 22K, 24K or 18K rate between two consecutive captures; Tanishq updated
somewhere in the open-closed interval (previous capture, this capture].
Republishing an unchanged rate is not observable and is not an "update"
for staleness purposes (its staleness is zero by definition).

**Estimator.** Nonparametric Turnbull NPMLE (self-consistency EM) of the
update time-of-day distribution on the 24 h IST circle, 1-minute bins,
uniform initialisation (so mass inside an innermost interval stays
uniform: within-interval placement is not identifiable). Intervals of
24 h or longer carry no time-of-day information and are dropped (counted).
Per day type (weekday / Saturday / Sunday) an interval is used only when
both ends fall on the same day type; otherwise it counts as "mixed" and
enters the pooled estimate only.

**Uncertainty.** Nonparametric bootstrap over change intervals, B = 500,
seed 42, 95% percentile intervals. Monte Carlo staleness with 20,000 draws
per evaluation, common random numbers across schedules.

**Objective.** Choose K in {5, 6} daily IST visit times on a 5-minute grid
minimising mean staleness (time from a real update to the capture that
first sees it), by coordinate descent from 20 seeded starts, under:
(i) at least one visit in 07:00–09:00 IST (fresh price by morning);
(ii) variant A: every circular gap between consecutive visits at most
460 min (the site's 8 h Tanishq freshness gate, `_STALE_THRESHOLD_H` in
`ml/inference.py`, minus a 20 min lateness margin), so the live
price-source gate never flips overnight; variant B: no gap constraint.
Lateness = measured dispatch-to-capture delay of `workflow_dispatch` runs;
a visit fails independently with probability q, evaluated at q = 0 and at
the measured share of scheduled runs that waited more than 30 min for the
runner.

**Current-schedule baseline.** Replay of the actual capture instants since
the self-hosted workflow split (2026-09-04), days resampled in the
bootstrap. Metrics for every schedule: mean staleness, share of updates
captured within 30 min and within 60 min.

**Decision rule for (c) "can the data choose?"** Re-optimise in 200 of the
bootstrap replicates. Regret of the point-estimate schedule in replicate b
= its mean staleness under replicate b's distribution minus replicate b's
own optimum. The data resolve the choice if the 90th percentile of regret
is at most 15 min. The non-identifiable within-interval placement is
bounded separately by evaluating the chosen schedule with all mass at the
left and at the right edge of each innermost interval; that range is
reported, not used in the rule. If the rule fails, a bounded measurement
window is proposed (STOP for GG) instead of a schedule.
