# ADR 057: Pre-registration: does an unusually high Tanishq markup revert, and does waiting pay?

**Status:** Accepted 2026-09-25 as a pre-registration. Sections 1–4 and
`ml/markup_reversion.py`, `scripts/analysis_markup_reversion.py` and
`scripts/run_markup_reversion_shadow.py` are frozen in this commit. The step-2 outcome code had
**not been run on any data** when this commit was made. Results are appended below the freeze line
in a later commit. This is shadow only: nothing a user sees changes, and promotion is GG's call.

## Context

F1 (#2022, `ml/markup.py`, `reports/markup_analysis.json`) measured Tanishq's listed 22K rate at
an average of 1.45% above IBJA 916 (sd 1.55%, n = 148 IST days, 2026-04-14 to 2026-09-24). It
also found an AR(1) of 0.76 on adjacent daily rows, which implies a half-life of about 2.5 days.
If that persistence were real, an unusually high markup would be a plausible "wait a few days"
signal for a buyer. It would be the first predictive edge in this project that does not depend on
forecasting the gold price itself.

**Markup definition (reused unchanged from #2022).** `ml/markup.py` is copied byte-identical onto
this branch.

- `markup_pct(d) = (T(d) / I(d) − 1) × 100` and `markup_rs(d) = T(d) − I(d)` (Rs/g).
- `T(d)` is the **last** Tanishq 22K reading of IST day `d`.
- `I(d)` is the IBJA 916 fix "in force" at that reading, under F1's rule:
  - a reading at 17:00 IST or later uses that day's PM fix;
  - a reading from 12:00 to 17:00 uses that day's AM fix;
  - a reading before 12:00 uses the previous day's PM fix.
- If the wanted fix does not exist (weekend, holiday, or not yet published), the rule falls back
  to the latest earlier fix and flags the row `stale`.

**Timestamp conventions.**

- `data/prices.json` `timestamp` is the UTC scrape time, converted to IST for the calendar day.
  25 rows tagged "(history backfill)" (2026-04-14 to 2026-05-08) have a **synthetic** timestamp of
  06:30 UTC (12:00 IST), set by `scraper/backfill-history.js`. They are not real observation times.
- `data/ibja_rates.parquet` `date` is the IST publish date, weekdays only. `am_916`/`pm_916` are in
  Rs/10g (divided by 10 here). There is no per-fix publish timestamp. `fetched_at` records when our
  scraper wrote the row, not when IBJA published.
- On the data side, 104 of the 123 non-backfill days have their last reading at or after
  17:00 IST, so they pair with that day's PM fix. Tanishq changes its rate within the day on
  86 of 123 days, most often between 11:00 and 13:00 IST (46 of 104 intraday changes).

## 1. Step 1: persistence diagnostic

This step is about data construction, not outcomes, so it was allowed before the freeze. It was
run before this commit and is labelled exploratory. Command: `python
scripts/analysis_markup_reversion.py --step 1`, which writes
`reports/markup_reversion/step1_persistence.json`.

The concern: on a weekend or holiday (and on Monday before 12:00), the IBJA side is a repeated
Friday PM fix. Consecutive markups then share the same denominator, which by construction produces
autocorrelation.

**Variants:**

- **(a)** Every daily row, as F1 used them.
- **(b)** Same-day pairs only: the IBJA fix used is the one wanted on that IST day (`stale` is
  False). This removes every weekend and holiday row, and any row whose IBJA fix was not yet
  published.
- **(c)** Variant (b), minus days where Tanishq's value repeats the previous calendar day's value
  exactly (a possible stale page on the retailer side), and minus the synthetic-timestamp backfill
  rows.

Rows: (a) 148, (b) 92, (c) 73.

AR(1) of `markup_pct`, with a 95% Fisher-z CI and a 95% moving-block bootstrap CI (block 5,
2,000 draws, seed 42). n_eff is the AR(1) effective n for a mean, n(1−r)/(1+r). Half-life is in
pair steps. **VERIFIED** (run on the committed data at `db8a4a0e`, 2026-09-25).

| variant | pairing | n pairs | AR(1) | Fisher CI | block-bootstrap CI | Spearman | half-life | n_eff |
|---|---|---|---|---|---|---|---|---|
| (a) all rows | adjacent rows (F1) | 147 | **0.76** | [0.69, 0.82] | [0.40, 0.79] | 0.57 | 2.6 | 19.7 |
| (a) all rows | 1 calendar day apart | 137 | 0.77 | [0.69, 0.83] | [0.40, 0.79] | 0.58 | 2.6 | 17.8 |
| (a) all rows | next IBJA business day | 94 | 0.48 | [0.31, 0.62] | [0.16, 0.62] | 0.49 | 0.9 | 33.1 |
| (b) same-day | adjacent rows | 91 | **0.43** | [0.25, 0.59] | [0.11, 0.56] | 0.47 | 0.8 | 35.8 |
| (b) same-day | 1 calendar day apart | 67 | 0.39 | [0.16, 0.57] | [−0.00, 0.57] | 0.43 | 0.7 | 29.6 |
| (b) same-day | next IBJA business day | 81 | 0.41 | [0.22, 0.58] | [0.06, 0.56] | 0.45 | 0.8 | 33.5 |
| (c) no carry-forward | adjacent rows | 72 | **0.24** | [0.01, 0.45] | [−0.11, 0.43] | 0.29 | 0.5 | 44.1 |
| (c) no carry-forward | 1 calendar day apart | 49 | 0.03 | [−0.25, 0.31] | [−0.28, 0.25] | 0.16 | 0.2 | 46.0 |
| (c) no carry-forward | next IBJA business day | 59 | 0.10 | [−0.16, 0.35] | [−0.24, 0.30] | 0.15 | 0.3 | 48.3 |

In `markup_rs` the figures are within 0.04 of these (see the JSON).

**Reading:**

1. **Most of F1's 0.76 is manufactured by weekend IBJA carry-forward.** On same-day pairs,
   persistence falls to about 0.4 per business day, a half-life under one business day. It is
   still clearly above 0 (Fisher CI lower bound 0.22 on business-day pairs).
2. Removing Tanishq repeat days as well (variant c) takes it to about 0.1 to 0.24, and every
   CI includes or touches 0. This is ambiguous about the buyer. A Tanishq "repeat" can be a stale
   scrape, which would be an artifact. It can also be a real day on which Tanishq did not reprice
   while the market moved. That is a real retail price and exactly the lag a waiting buyer could
   exploit. So variant (c) is a sensitivity check, not the primary series.
3. The bootstrap CIs sit well below the Fisher CIs, and Spearman is below Pearson in (a). A few
   extreme markup days (the sd of 1.55% against a p10–p90 range of only 0.38–2.21%) inflate the
   Pearson AR(1). This is why the signal below uses a **robust** z (median/MAD).
4. **Implication for the brief's premise.** "Half-life of about 2.5 days, so a markup 2 sd high
   recovers about a 2-day market move" does not survive step 1. What remains is weaker, faster
   persistence (under one business day) on a much smaller effective sample.

## 2. Hypothesis

H1: when Tanishq's same-day markup is unusually high against its own trailing history, Tanishq's
retail price falls **relative to IBJA** over the next N IBJA business days, by more than it does on
other days. H0: the markup change after a signal day is no lower than after other days.

## 3. Pre-registered test (frozen)

**3.1 Series.** The primary series is variant (b), same-day-fresh pairs (weekdays only by
construction). The sensitivity series are variant (c) and F1's variant (a). Both are exploratory
and outside the gated family. Variant (a) uses "N rows later" as its horizon and adds a
weekday/weekend split.

**3.2 Signal (walk-forward, no look-ahead).**

- `z(d) = (markup_pct(d) − median(W)) / (1.4826 × MAD(W))`.
- W is up to 60 rows of the primary series **strictly before** d, with a minimum of 20. Otherwise z
  is undefined.
- A day is a signal day if `z(d) ≥ z*`.
- The decision moment is d's last Tanishq reading, which is almost always after that day's PM fix.

**3.3 Grid (a family of 4, fixed).**

- `z* ∈ {1.0, 1.5}` × `N ∈ {1, 3}` IBJA business days.
- How the grid was chosen: step 1 computed the **signal frequency only** (a feature, not an
  outcome). z ≥ 1.0 fires on 11 of 72 days (15.3%), z ≥ 1.5 on 8 (11.1%), and z ≥ 2.0 on
  1 (1.4%). z = 2.0 was dropped from the planned grid because it could never reach any sample floor.
- N was chosen from the step-1 half-life (under one business day). N = 1 captures most of the
  implied reversion. N = 3 is the "wait a few days" buyer horizon.

**3.4 Outcome and test.**

- Target day = the N-th IBJA business day after d, where the business-day calendar is the set of
  dates on which IBJA published any 916 fix. The outcome is defined only when the target day is
  itself in the primary series.
- **Primary outcome:** `y = markup_rs(target) − markup_rs(d)` in Rs/g. A negative y means the
  retail price fell relative to the market.
- **Test:**
  - Fit OLS `y = a + b·signal` over all days with both z and y defined.
  - H1 is `b < 0`, one-sided, using Newey-West (Bartlett) SEs with **lag = N + 2**. That is
    N − 1 for the overlapping outcome plus 3 for signal persistence, so it is always ≥ N − 1.
    p comes from the normal approximation.
- **Baseline:** the unconditional mean of y over the same days, reported with its HAC CI. b is
  the difference between signal days and non-signal days.
- **Effective n for signal days:** `n_signal × (SE_OLS / SE_HAC)²`. Also reported: the HAC effective
  n of y on signal days (n·γ0/LRV).

**3.5 Buyer value (reported for every cell, not gated in the historical run).**

- **Saving net of market:** the mean of −y on signal days, with a HAC CI.
- **Gross saving** `T(d) − T(target)`: the Rs/g a buyer who waits N days saves, on signal days,
  with a HAC CI.
- **Excess gross saving:** signal days vs non-signal days, from the same HAC regression. This
  removes the market drift common to all days.
- **Probability of paying more:** P(gross saving < 0) and P(y > 0), each with a Wilson CI.
- **Decomposition:** the mean Tanishq change vs the mean IBJA change on signal days, which shows
  whether the reversion came from the retailer side (which helps the buyer) or from the market side.

**3.6 Historical gate (per cell).** Multiplicity is corrected across the 4 cells with **Bonferroni
(α/4 = 0.0125) and BH (q = 0.05)**, both reported.

- **PASS:** b < 0 is significant under both Bonferroni and BH, **and** the mean saving net of
  market on signal days is ≥ Rs 20/g, **and** n_signal ≥ 15, **and** signal effective n ≥ 8.
- **INCONCLUSIVE:** n_signal < 15, or effective n < 8, or no signal days.
- **NEGATIVE:** every other case, including b ≥ 0.
- Rs 20/g is about 0.14% of a Rs 14,000/g price, or Rs 200 on a 10 g purchase. It is roughly a
  third of the live nowcast MAE (Rs 61.2/g, #2015 scorecard).

**3.7 Status of the historical run: exploratory, or at best semi-confirmatory.** F1 computed AR(1)
on this same data, and step 1 above looked at persistence on this same data before the freeze.
The data is therefore not fresh for this question. From the signal counts alone (11 and 8 signal
days before losing any to missing targets), **both z* rows are expected to be INCONCLUSIVE under
the n ≥ 15 floor**. This expectation was set before any outcome was computed. The floor is **not**
lowered to get a verdict. Numbers are reported descriptively.

## 4. Forward confirmatory test (frozen)

- **One cell, no multiplicity:** z* = 1.0, N = 3 (`FORWARD_PRIMARY`). It was chosen now, before
  any outcome, on signal-to-noise grounds. With AR(1) of about 0.41, about 93% of an excess reverts
  by N = 3 against about 59% by N = 1, while the variance of ΔM grows more slowly. It also matches
  the buyer's "wait a few days" horizon.
- **Sample:** decision days d ≥ **2026-09-25** (IST), logged prospectively by
  `scripts/run_markup_reversion_shadow.py` into `reports/markup_reversion/shadow.json`. Each entry
  carries a `logged_at_utc` stamp, and its signal fields are never rewritten. Trailing windows may
  use pre-freeze rows, because those are features only. The script is not wired into any workflow;
  wiring it is GG's call. Without daily runs, the evaluation recomputes the same walk-forward
  signals from the committed data, which is identical by construction because the data is
  append-only. That fallback is disclosed if it is used.
- **Minimum n:** 25 resolved signal days **and** signal effective n ≥ 12.
- **Decision date:** the first Monday on or after 2027-03-31 on which the minimum n is met.
  Expected accrual is about 3.9 same-day rows per week × 15% ≈ 0.6 signal days per week, which puts
  the date at around June 2027. **Hard stop 2027-09-30:** if the minimum n is still unmet, the
  verdict is INCONCLUSIVE and the idea is closed. There are no interim looks.
- **Gate:** HAC one-sided p ≤ 0.05 for b < 0 (lag 5), **and** mean saving net of market on signal
  days ≥ Rs 20/g, **and** a positive point estimate of excess gross saving (the buyer really pays
  less, not just less relative to IBJA).
  - All three met: CONFIRMED, which makes the signal eligible for a GG promotion review.
  - Otherwise: NEGATIVE.
- **What counts as a negative:** the forward gate failing on the minimum n. A historical PASS
  without a forward confirmation is **not** evidence enough to ship anything user-facing.

---

*Freeze line. Everything below was added after the pre-registration commit.*

## 5. Results

Not yet run at the freeze.
