# ADR 043 — The Weekly Price Range: Historical Simulation, Calibrated on Real IBJA

**Status:** Accepted for shadow, 2026-09-24. Research and forward shadow only. Nothing a user sees
changes until GG approves a separate promotion PR.

**Builds on:** ADR 041 (R1: historical simulation is the best short-horizon range on real IBJA).
Also ADR 042's consecutive-day rule.

---

## Context

The next user-facing model is one sentence: *"The price will very likely stay between ₹X and ₹Y
over the next 7 days — right about 8 times out of 10."* The brief set four conditions:

- Build it from the method that measured best on real IBJA.
- Calibrate it to 80%, strictly held out.
- Handle weekends and holidays explicitly.
- Take "8 times out of 10" from measured coverage, rounded down.

"Stay between" is a claim about the **whole path**, not just the last day. So the target is:
every IBJA PM price published in the 7 calendar days after the as-of day is inside the range.
That is usually 5 publication days, or 4 in a holiday week. The 1-day target is the next
publication day.

## Decision

`ml/weekly_range.py` implements this. The design was fixed before any result was seen.

- **Shape:** historical simulation on the INR proxy. Take the last 500 proxy days known at t. Use
  the 10th percentile of 5-day path minima and the 90th percentile of path maxima (for 1 day, the
  quantiles of 1-day returns).
- **Scale:** split conformal on real IBJA. Each earlier window scores the smallest multiple of the
  base range that contains its path. The scale is the ⌈(n+1)·0.8⌉/n quantile of the latest 250
  scores. Only windows whose last day is before t count, and at least 30 are needed.
- **Weekends and holidays:** ranges are issued on publication days, and the window end date is part
  of the forecast. A Saturday or holiday viewer therefore sees the last publication day's range,
  ending on a real date. A holiday is not known at issue time, so the range is always built for 5
  days; a 4-day holiday week is scored against it. A window that crosses a hole in the IBJA record
  is never scored.
- **Truth:** IBJA PM 916. The page would show the range as percentages on its retail 22K estimate.
  That conversion is not separately validated.

## Results

**Provenance:** `reports/weekly_range_results.json`, from `scripts/analysis_weekly_range.py` on
`data/ibja_rates.parquet` as of 2026-09-23.

| horizon | view | n | calibrated: coverage [Wilson 95%] | width | raw historical simulation | width |
|---|---|---|---|---|---|---|
| 1 day | walk-forward | 161 | **85.1%** [78.8, 89.8] | 3.37% | 80.7% [74.0, 86.1] | 3.08% |
| 1 day | hold-out (scale frozen from the first half) | 80 | 80.0% [70.0, 87.3] | 3.50% | 80.0% [70.0, 87.3] | 3.52% |
| 7 days | walk-forward, overlapping | 119 | **84.0%** [76.4, 89.5] | 8.89% | 78.2% [69.9, 84.6] | 8.03% |
| 7 days | non-overlapping (every 5th window) | 24 | 87.5% [69.0, 95.7] | 8.86% | 79.2% [59.5, 90.8] | 8.00% |
| 7 days | hold-out | 56 | 87.5% [76.4, 93.8] | 9.54% | 80.4% [68.2, 88.7] | 8.78% |

Kupiec's test does not reject 80% for any cell (smallest p = 0.094: calibrated, 1 day,
walk-forward). The walk-forward runs from 2025-05-12 (1 day) and 2025-07-15 (7 days) to
2026-09-22.

**In plain words:**

- **The brief's premise did not hold here.** R1 measured historical simulation at 85.6% on IBJA.
  With the path target and the consecutive-day rule, raw historical simulation already sits at
  about 80% at 1 day. The difference from R1's figure has not been traced. R1 used endpoint returns
  and a calendar-gap segment rule (INFERRED cause).
- **Calibration did not narrow the range.** It moved the 7-day coverage from 78% to 84%, at 11%
  more width. At 1 day it made the range wider for no coverage gain in the hold-out.
- **Both 7-day versions are consistent with 80%.** The data cannot separate them. The honest n
  for 7 days is about 24 non-overlapping weeks.
- **The weekly range is wide.** 8.9% of the price is about ₹1,250/g at ₹14,000/g, roughly ±₹620.
  That is the real size of a week's moves in this period, and the copy has to live with it.

**Candidate for the shadow:** the calibrated range. It is the pre-specified method, and it
errs on the side the sentence promises ("very likely"). Its measured figure rounds down to
"8 times out of 10" in every view. Raw historical simulation is logged alongside it for
comparison.

## Page design (for the promotion PR — GG decides)

The page already shows one range: the "likely range" around today's estimate. That range is about
how far today's shop price may be from our estimate. It aims at 80% and measures 70.9%
(n = 86, 95% CI 60.6–79.5%, `data/calibration_band_coverage.json`, 2026-09-20), so it
under-covers. A
second range about next week's movement would compete with it. The proposal is to show **one**
range statement, the weekly one. Today's estimate would carry a plain accuracy note ("usually
within ₹N of the shop price") instead of its own band. The copy and layout go in the promotion
PR, following #1962's plain-language standard. The "N times out of 10" figure is read from the
forward shadow's measured coverage, rounded down, never typed by hand.

## Consequences

- The forward shadow (`scripts/run_weekly_range_shadow.py`, next PR) scores only as-of days after
  its merge date. The promotion PR waits for enough forward weeks for a CI that excludes 70%.
  At about 5 new windows a week, that is roughly 8–10 weeks.
- If the forward coverage falls below 80%, the copy must say a lower "N times out of 10". The rule
  is mechanical (`times_out_of_ten`), so no one gets to choose it.

## Alternatives considered

- **Endpoint-only week range** (only the 7th day's price). Rejected: "stay between" promises the
  path. An endpoint range would be narrower and would break the promise on days in between.
- **IBJA-only historical simulation** (no proxy). Rejected for now: the dense IBJA record since
  2025-04 has about 170 days, too few for a 5-day path distribution. Worth revisiting after a
  year more data.
- **Tuning the calibration** (window length, recency weighting) after seeing these results.
  Rejected. Any change is now test-set tuning, and it could only be validated on the forward
  shadow.
