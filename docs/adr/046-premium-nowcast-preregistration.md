# ADR 046 — Pre-registration: does the derived Indian premium add information?

**Status:** Accepted 2026-09-24 (GG decision G4d). Pre-registration only: the text and
`scripts/analysis_premium_nowcast.py` are frozen in this PR **before the script is run on any
data**. Research; nothing a user sees changes.

**Builds on:** #2004. That PR adds the CBIC duty table (`data/duty_cbic.json`) and the derived
premium (`scripts/analysis_derived_premium.py`, `reports/derived_premium.json`).

## Context

The premium is IBJA 999 PM divided by landed parity (COMEX × USD/INR × duty), minus 1. #2004
described it on 2022–2026 IBJA:
- mean −0.92%;
- day-to-day AR(1) 0.30;
- a third of its variance is timing noise;
- after removing that noise, the residual AR(1) is 0.59;
- a large, slow response to the 2026-05-13 duty hike (+0.0% → −3.1%).

**Those descriptives were seen before this registration.** Any test on 2022–2026 IBJA is therefore
**exploratory**. The confirmatory test uses only days after this registration.

Of the three targets in the brief (direction, nowcast, range), the premium bears most directly on
the **next IBJA fix**. Parity says where the global price has moved overnight. The premium says how
far the domestic price sits from it and whether that gap closes. Direction was already shown to
have no detectable edge at this sample size (ADR 040, #1992, ADR 045). A range test would need
many more days than will accrue.

## The frozen test

**Target.** IBJA 999 PM on a publication day t, predicted before t's fixes. A day is scored only
when the previous publication day t′ is consecutive, using ADR 042's rule
(`np.busday_count(t′, t) <= 2`). Stale repeats (a price identical to the previous row) are dropped
first.

**Inputs known before t's AM fix:**
- parity(t): COMEX and USD/INR closes of calendar day t − 1, and the duty in force on t, from
  `data/duty_cbic.json`;
- IBJA(t′) and the premium p(t′).

**Predictions:**

| id | prediction | meaning |
|---|---|---|
| B0 | IBJA(t′) | no change (what the site assumes today) |
| B1 | parity(t) × (1 + p(t′)) | yesterday's premium held, the global move passed through |
| C | parity(t) × (1 + μ + ρ·(p(t′) − μ)) | the premium drifts back toward its mean |

μ and ρ are the mean and AR(1) coefficient of the premium, fitted on consecutive premium pairs
completed before t. The window is expanding, at least 30 pairs are required, and ρ is clipped to
[0, 1].

**Loss.** Absolute error in ₹/g.

**H1 (primary).** C has lower loss than B1: the premium's pull toward its mean adds information
beyond the global move. The test is a one-sided paired HAC Diebold-Mariano test at lag 1, with
α = 0.05.

**Secondary tests**, with Holm correction over the two:
- H2: C has lower loss than B0.
- H3: B1 has lower loss than B0 (whether the overnight global move helps at all).

**Confirmatory set.** Days with t after **2026-09-24** (the script default). μ and ρ still use
every earlier pair; that is training, not testing.

**When it is read.** At the first run where the confirmatory set has at least **120 scored days**.
At about 0.66 consecutive IBJA days per calendar day, that is roughly six months, around April
2027. Earlier runs log n, MAE and p only, and are not confirmatory. If IBJA capture has gaps, the
date moves later; the n does not change.

**Also reported, labelled exploratory:** the same script with `--since 2021-12-31`, i.e. all
2022–2026 days. It is run after this ADR merges and never counts toward H1.

## What would change what

- **H1 holds.** The premium's mean reversion becomes a candidate input for the next-day estimate
  and for the R2 nowcast. Any user-facing use needs its own promotion PR (GG).
- **H3 holds but H1 does not.** The overnight global move helps and the premium adds nothing
  beyond it.
- **Neither holds.** The premium stays a descriptive series. Duty shocks remain the only structure
  seen in it, and they are too rare to model.

## Known limits, stated before the run

- **Duty is charged on COMEX, not on CBIC's tariff value.** The tariff-value history isn't
  machine-readable. The error is a slow-moving level offset, which μ absorbs.
- **GC=F is a futures price** and carries contango. That puts a small sawtooth at each roll, which
  adds noise to both B1 and C equally.
- **The 2026 duty rows come from a third-party mirror.** If CBIC's own text disagrees, the table is
  corrected and the correction is logged before the read.
- **COMEX and USD/INR come from Yahoo**, which carries the same terms risk as the live pipeline
  (see the G4c data-source review). The test reads them; it does not publish them.

## Alternatives considered

- **Test on 2022–2026 history now.** Rejected as confirmatory, because its descriptives have
  already been seen. It is kept as the exploratory run.
- **A regression of IBJA returns on the lagged premium.** Rejected. It answers a different
  question, which is not the price a buyer sees. The nowcast error in ₹/g is the product's own
  metric.
- **Include the festival and wedding calendar.** Rejected. Only 5 dense days fall in a festival
  window, and the wedding-season difference is confounded with the 2026 duty period.
