# ADR 052 — Pre-registration: is gold reliably cheaper on a particular weekday?

**Status:** Accepted 2026-09-24. Pre-registration only. The text and
`scripts/analysis_dow_prereg.py` are frozen in this commit, **before the script is run on the
confirmatory data**. Nothing a user sees changes. Until this test passes, the product never
advises waiting for a particular day.

## Context

GG noticed that some weekdays seem cheaper, for example that Friday beats Tuesday. An exploratory
analysis, re-verified on 2026-09-24 on `data/history_seed_inr22k_label.parquet` (the INR proxy,
weekdays 2013-01-01 to 2026-09-23, 3,582 weekday rows):

| figure | brief | re-measured | note |
|---|---|---|---|
| joint weekday test (HAC) | p 0.35 | **p 0.341** | no weekday effect |
| Friday cheaper than the same week's Tuesday | 44.5% of 715 weeks | **44.1%** of 715 full weeks | |
| … average | Friday ~0.16% dearer | **0.163% dearer** | |
| … 2022–26 | 39%, ~0.39% dearer | **38.2%** of 246 weeks, **0.397% dearer** | |
| price lower after 2 trading days | 47% | **47.0%** (n 3,580) | |
| average cost of waiting 2 days | ~0.09% | **0.102%** | small difference, same sign |
| typical 2-day move | ± ₹220/g | median \|move\| **₹120/g**; ₹220 is **1 sd** (₹218.6) at ₹14,000/g | "typical ±₹220" overstates the typical move |
| Tanishq, 148 days | Fri cheaper in 12 of 20 weeks, ~₹23/g dearer | **12 of 20**, mean **+₹23.0**, median −₹42.5 | 20 weeks is too few to read |

That analysis used 2013–2026 data, so it can only be exploratory. The proxy is built from the
**previous day's** COMEX close. An Indian "Friday" in it is therefore COMEX Thursday.

## The frozen test

**Confirmatory data, never used for any weekday question in this project:**
- COMEX GC=F from 2000-08-30 to 2012-12-31: `reports/vol_regime_prereg_gcf_2000_2012.csv`, the
  snapshot ADR 044 froze. Its SHA-256 is in ADR 044's result. ADR 044 used it only for the
  volatility-regime question.
- GLD from 2004-11-18 to 2012-12-31, the roll-free check, from the same ADR.

**Mapping.** An Indian buyer's day reflects the previous US session. So Indian Tuesday means the
COMEX Monday close, and Indian Friday means the COMEX Thursday close.

**P1 (primary).** Daily log returns are regressed on weekday dummies, with Monday as the base. A
Newey-West (lag 5) Wald test asks whether all weekday means are equal. α = **0.025** (Bonferroni
over P1 and P2).

**P2 (primary).** Weekly log(Thursday close / Monday close) within the same ISO week. H1: the mean
is below 0 (an Indian Friday is cheaper than that week's Tuesday). One-sided HAC t-test, lag 1,
α = **0.025**. The share of weeks cheaper is also reported, with a Wilson interval.

**Secondary.** The same two tests on GLD.

**Forward arm.** Real IBJA PM 916: Friday against the same week's Tuesday, for weeks after
**2026-09-24** only. One-sided HAC test, α = 0.05. It is read only once it has **≥ 52 weeks**.

**Product rule (frozen).** The site may mention a weekday only if all three hold:
- P1 or P2 passes on GC=F;
- the forward IBJA arm passes at ≥ 52 weeks;
- the sign agrees.

Otherwise it never advises waiting for a particular day. Whatever the tests find, the copy states
the measured odds, never a recommendation.

## Deviation log

- **2026-09-24, fix after the freeze at `f8afd8db`.** The first run crashed inside `weekly_pair`
  on the empty forward IBJA set (n = 0 weeks), before any result was printed or written. The fix
  returns an empty series instead of raising. It changes no test, threshold or data; a test covers
  it. No result was seen before the fix.

## Result (2026-09-24, run at `e887b01f`, `reports/dow_prereg_results.json`)

The snapshot SHA-256 starts `2db071aa`, matching ADR 044's record.

**Not confirmed. No weekday is reliably cheaper, and an Indian Friday is not cheaper than the same
week's Tuesday.**

| test | data | n | result | p | passes |
|---|---|---|---|---|---|
| P1 any weekday differs | GC=F 2000–2012 | 3,088 days | mean daily return by COMEX weekday (bp): Mon +6.1, Tue −0.2, Wed +4.4, Thu +2.7, Fri +16.7 | 0.163 | no |
| P2 Indian Fri < Tue | GC=F 2000–2012 | 559 weeks | Friday **+0.02% dearer** [−0.15, +0.19]; cheaper in **45.3%** of weeks [41.2, 49.4] | 0.61 | no |
| P1 (secondary) | GLD 2004–2012 | 2,042 days | Fri +20.7 bp | 0.037 (> 0.025) | no |
| P2 (secondary) | GLD 2004–2012 | 370 weeks | Friday +0.08% dearer; cheaper in 44.1% of weeks [39.1, 49.1] | 0.77 | no |
| forward IBJA | after 2026-09-24 | 0 weeks | not readable before 52 weeks | — | no |

**Verdict: the site never advises waiting for a particular day.** If anything, the Indian Friday is
the dearer day slightly more often than the cheaper one: 45% cheaper on untouched COMEX, 44% on
the exploratory proxy. That matches a small drift upwards, not a weekday pattern. GLD's Friday
return (+20.7 bp, secondary, p 0.037) falls on the COMEX Friday. That is an Indian *Monday*, so it
would make Monday dearer, not Friday cheaper. It is not significant after correction.

The forward IBJA arm keeps running and is read at 52 weeks, as registered.

## Consequences

- The earliest a weekday claim could ever appear is about October 2027, after 52 forward weeks.
- A pass on COMEX alone is not enough. The effect must also appear in the Indian price buyers
  pay.
- If P1 and P2 fail, "some days are cheaper" is recorded as noise, and the forward arm keeps
  running only as a check.

## Alternatives considered

- **Re-test on 2013–2026 data.** Rejected: that is the data that suggested the question.
- **Test every pair of weekdays.** Rejected: that is 10 pairs and a much weaker correction. P1
  covers "any weekday differs", and P2 covers the specific claim GG raised.
- **Use Tanishq data.** Rejected as confirmatory: it has 20 weeks. It is reported only as
  context.
