# ADR 046 — Pre-registration: does the derived Indian premium add information?

> ## Superseded in part by ADR 058 (2026-09-25)
>
> *Added after the fact. The frozen pre-registration below is unchanged and is still the
> registered test. Source: [ADR 058](058-timing-audit.md), re-run R3,
> `reports/timing_audit/audit.json` at `924ee298` (PR #2051).*
>
> **No longer holds: the reading of the premium as mean-reverting.** The premium here is IBJA PM
> on day t divided by parity built from COMEX and USD/INR *closes*. The COMEX close is a settle
> 12-18 hours old at the fix it is set against. So day-to-day "reversion" in the premium is
> mostly the clock:
>
> - AR(1) of the premium: **0.30** with the t-1-close parity (as published in #2004 and the
>   Context below) vs **0.85** with parity read at the fix (1-hour bars, 184 pairs). Its SD falls
>   from 1.26% to 0.82%.
> - H1 re-run on the same 152 days (exploratory; the days where 1-hour data exists):
>   - published convention: C Rs 130.1/g vs B1 148.6/g (999), one-sided p = 0.024;
>   - premium at the fix, parity at 11:30 IST before the AM fix: C 58.04 vs B1 58.03, **p = 0.50**.
> - The exploratory result below (C beats B1, p = 0.004, n = 163) is on a slightly different day
>   set. Its sign agrees with the 152-day published-convention run. It should now be read as "C
>   partly repairs B1's stale-parity error", not as evidence about premium dynamics.
> - **Exploratory.** Post hoc, 2024-05 onward only (Yahoo keeps 730 days of 1-hour bars).
>
> **What this means for the registered forward test (read ~April 2027).** As registered, H1
> compares C with B1 using parity from day t-1 closes. It would mostly measure timing noise: a C
> win is expected whether or not the premium has any real dynamics. H2 and H3 use the same
> t-1-close parity, so they are affected too. Before the forward read, either the parity timing or the meaning of H1 must change.
> A dated addendum proposing this is at the end of this file. **It is not adopted.** Adopting it
> is GG's or the orchestrator's call.
>
> **Has any forward data been examined?** No, as far as the repo shows (checked 2026-09-25 on
> master `51769bd6`). The confirmatory set starts with days after 2026-09-24. The only later IBJA
> row (2026-09-25) has an AM value and no PM value. No committed report and no workflow runs the
> confirmatory set. ADR 058's R3 used only pre-registration days. So amending before the read is
> still legitimate, provided it is adopted before any day after 2026-09-24 is scored.

---

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

## Exploratory result (2026-09-24, after registration; does not count toward H1)

**Provenance:** `reports/premium_nowcast_exploratory.json`, from `scripts/analysis_premium_nowcast.py
--since 2021-12-31` at `da0389a2`, on `data/ibja_rates.parquet` to 2026-09-24. There are
n = 163 scored days, 2025-05-12 to 2026-09-24. The 30-pair warm-up uses up the earlier dense days.

| prediction | mean absolute error, ₹/g (999) |
|---|---|
| B0 no change | **117.9** |
| B1 global move, premium held | 151.5 |
| C premium drifts back to its mean | 128.7 |

One-sided HAC Diebold-Mariano p-values:

| comparison | p | effective n | result |
|---|---|---|---|
| H1: C vs B1 | **0.004** | 173 | C better |
| H2: C vs B0 | 0.87 | — | Holm: no |
| H3: B1 vs B0 | 0.998 | — | Holm: no |

**Reading, exploratory only.** Passing the previous evening's COMEX and USD/INR move through to the
next fix makes the estimate *worse* than assuming no change. That matches the timing noise #2004
found: the COMEX close is hours stale by the time IBJA fixes. Letting the premium drift back to its
mean undoes part of that damage, but not enough to beat "no change". If the forward test repeats
this, H1 will hold and H2/H3 will not. The premium would then carry information only relative to
a parity-based estimate, and "no change" would stay the best next-fix estimate.
The confirmatory set had n = 0 on 2026-09-24.

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

## Addendum A1 — proposed 2026-09-25, NOT adopted (ADR 058 R3)

*Status: proposal only. The frozen test above stays the registered test unless this addendum is
adopted. It must be adopted, if at all, before any day after 2026-09-24 is scored.*

**Why.** ADR 058 found the parity in this test runs on the wrong clock. The COMEX close of day t-1
is the settle at 13:30 ET on t-1, 12-13 hours before IBJA's AM fix on t. The premium p(t')
inherits the same error. On 152 pre-registration days, fixing the clock moves the premium's AR(1) from 0.30
to 0.85, and H1 from p = 0.024 to p = 0.50 (exploratory, see the top of this file).

**Proposed change (option A, recommended).** Keep B0, B1, C, the loss, H1-H3, alpha, Holm, the
confirmatory start (days after 2026-09-24) and the n >= 120 read rule. Change only the clock of
two inputs:
- parity(t) = COMEX GC=F x USD/INR INR=X read from Yahoo 1-hour bars as the Close of the last bar
  that ended by 06:30 UTC on t (IBJA AM ~12:00 IST), times the duty in force on t;
- p(t') = IBJA PM(t') / parity read the same way at t''s PM fix (11:30 UTC) - 1.

Because Yahoo drops 1-hour bars after 730 days, the scoring script must also save the bars it
used for each scored day as a frozen artifact.

**Alternative (option B).** Keep the inputs as registered. State now that a C win over B1 is not
evidence of premium dynamics, only that C corrects stale parity, and that such a result cannot
license any user-facing use of the premium.

**How to adopt.** Commit the chosen option with a short note "adopted <date> by <GG/orchestrator>",
record `sha256(docs/adr/046-premium-nowcast-preregistration.md)` at that commit, and freeze the
amended `scripts/analysis_premium_nowcast.py` in the same commit, all before its first run on
any day after 2026-09-24.
