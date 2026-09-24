# ADR 058 — Timing audit: which clock does each series run on? + FOMC re-run with correct alignment

**Status:** Proposed 2026-09-25 (branch `feat/timing-audit`). Analysis-only; no live-pipeline
change. Part 2 below is a **pre-registration**, frozen at the commit that first carries this file,
before `scripts/analysis_fomc_aligned.py` was run on any price data. Part 1 (the audit table and
re-runs) is appended after the freeze and is exploratory by nature (it re-measures published
results; it does not confirm anything).

## Clock evidence (checked before the freeze; timestamps and shapes only, no event outcomes)

| Series | Label | What the value is, and when it is known | Evidence |
|---|---|---|---|
| Yahoo `GC=F` daily Close | NY trading date D | **COMEX settlement, 13:30 ET on D** (17:30 UTC summer / 18:30 UTC winter) — not the 17:00 ET last trade | VERIFIED: 49 days of Yahoo 5-min bars (2026-07-17..09-24). Daily Close vs price at 13:30 ET: median gap 1.2 bp. At 14:00 ET: 14.7 bp. At 16:00 ET: 34.6 bp. Last bar of the day: 42.6 bp |
| Yahoo `GLD` daily Close | NY date D | NYSE close, 16:00 ET on D | VERIFIED: 59 days of 5-min bars; median gap 0.7 bp at 15:55–16:00 ET, 21 bp at 14:00 ET |
| Yahoo `INR=X` daily Close | date D | A single snapshot, not a fixing. Close == Open on 91.3% of 1,229 days (2022-2026). Closest to the quote around 00:00–02:00 UTC on D (median 3.9 bp, vs ≥7 bp for later hours; 1-hour bars, n≈480–720). **No exact clock claimed** | Partly verified; 5-min bars (sparse, 59 days) were inconclusive |
| IBJA AM / PM | IST date D | ~12:00 / ~17:00 IST on D (06:30 / 11:30 UTC). Weekdays only | Repo convention (`ml/sources/ibja.py` uses 11:30 UTC for PM; `ml/markup.py` uses 12:00/17:00). Publish times not independently verified; `fetched_at` shows only when the pipeline fetched |
| Tanishq board (`prices.json`) | UTC ISO timestamp per scrape | The board changes once or twice a day. New prices are first seen at 11:00–13:00 IST (38 of 55 tightly-bracketed changes), some at ~18:00 IST. Changes Mon–Sat, never Sunday | VERIFIED on `data/prices.json` (686 live scrapes, 2026-05-09..09-24) |
| FOMC statement | ET date D | 14:00 ET for every scheduled meeting since 2013 (14:15 before 2013; 12:30 on 2011–12 press-conference days). 18:00/19:00 UTC = 23:30/00:30 IST | Fed practice; per-event times not in the ADR 050 calendar |

Consequence: a 14:00 ET decision on D is in COMEX bar **D+1** (settles 13:30 ET D+1). It is in
GLD bar **D**. It is in IBJA's **next IST publication** (AM of IST D+1 at the earliest), and in
Tanishq's **IST D+1** board.

## Part 2 — pre-registration: F4 FOMC re-run with correct alignment (FROZEN)

**Question (size only, never direction):** is |move| larger on the first session whose price
could reflect a 14:00 ET FOMC decision than on normal sessions, in each market?

**Events.** FOMC rows of ADR 050's calendar (`reports/timing_audit/event_dates_adr050.json`, a
dates-only copy of `data/events_calendar.json` at the recorded commit of `feat/event-watch-model`).
Window 2013-01-01..2026-09-24, where every scheduled statement is released at 14:00 ET.
- **Excluded:** unscheduled actions with a different or unverifiable release time: 2019-10-11,
  2020-03-03 (10:00 ET), 2020-03-15 (Sunday).
- **Added:** 2019-09-18. The ADR 050 calendar misses it. federalreserve.gov
  `fomchistorical2019.htm` lists "September 17-18 Meeting - 2019" (regular). Checked before the
  freeze. This is a calendar defect, reported in Part 1.

**Event session** (`ml/timing_alignment.first_bar_after`): the first observation whose publication
time is strictly after 14:00 ET on the decision date, using each series' clock above. A first
session more than 4 calendar days after the decision is treated as a data gap and dropped.

| Cell | Series | Event session | Previous value |
|---|---|---|---|
| **C1 comex** | GC=F daily settlement, roll-adjusted with the ADR 050 method (`ml.inr_proxy._detect_and_adjust_rolls` against GLD), genuine COMEX days only | next trading day's settlement | previous genuine settlement |
| **C2 gld** | GLD daily close (NYSE, 16:00 ET) | the decision day's close | previous close |
| **C3 ibja_pm** | IBJA 22K `pm_916`, `data/ibja_rates.parquet` | first PM publication after the decision (IST D+1 when published) | previous PM publication. Consecutive pairs only (≤ 2 business days, ADR 042 rule). Zero-change pairs (stale repeats) dropped from both pools |
| retail (descriptive, **not** in the family) | Tanishq 22K, last reading per IST date | first IST board after the decision (IST D+1) | previous IST day's board |

**Metric:** |ln(event value / previous value)|.

**Normal pool:** the same series' sessions in the window. Remove any session dated within ±1
calendar day of any event of any type (FOMC incl. the added and excluded dates, CPI, jobs,
Budget). This is ADR 050's rule. Also remove the event sessions.

**Test (primary):** OLS of |r| on an event dummy over the date-ordered pooled sessions. Variance
is Newey-West (Bartlett, lag 5). The test is one-sided, H1: event mean > normal mean, normal
approximation. **Effective n** = n_event × (i.i.d. variance / HAC variance) of the coefficient.
**Secondary (reported, not gating):** ADR 050's block bootstrap (event days i.i.d., normal days
circular moving blocks of 5, 2000 replicates, seed 42), and the ratio of medians.

**Family and gate:** m = 3 (C1, C2, C3). A cell **passes** only if both hold:
- HAC p ≤ 0.05/3 = 0.0167 (Bonferroni);
- ratio of means ≥ 1.2.

This is the same gate family as ADR 050 (Bonferroni + ratio ≥ 1.2). It uses HAC as the primary
test, because this brief asks for HAC p, and the family is these 3 cells. BH (q = 0.05) across the
3 is reported alongside. A cell with fewer than 8 event sessions is **"not testable"**. It is
reported, and counts as neither a pass nor a negative.

**Contamination, stated in advance:**
- **C1 is not confirmatory.** ADR 050's measurement audit already computed next-day FOMC medians
  on 2000–2026 (1.80× the all-day median, n = 206), so C1 can only be a *replication* under the
  corrected rule. Its verdict is reported with that label and cannot by itself justify surfacing
  anything.
- **C2 is partly contaminated.** Its exact numbers have never been computed. But its window
  (16:00 ET D-1 → 16:00 ET D) shares the 14:00–16:00 ET post-decision hours with C1's, so a
  large C1 effect makes a large C2 effect likely.
- **C3 is the only uncontaminated cell.** It is expected to be underpowered: IBJA is daily only
  from 2025-Q2. At most about 10 decisions fall in dense data.
- **Retail** has about 3 decisions (2026-06-17, 07-29, 09-16). It is descriptive only.

**What counts as a negative:** a testable cell with HAC p > 0.0167, or a ratio below 1.2. Neither
is evidence that FOMC days are *quieter* unless the ratio's upper CI is below 1. "Not testable" is
not a negative.

**Product consequence, fixed now:** nothing is surfaced from this ADR. At most, a passing C2 or C3
licenses a *new* F4 card pre-registration. A passing C1 alone does not. Surfacing stays GG's call.

**Forward cell (registered, not run now):** C1 and C3 on FOMC decisions *after* 2026-09-25, with
the same rules. C1 is read at n ≥ 16 (about two years). C3 is read at n ≥ 12.

**Frozen artifacts:** this section, `scripts/analysis_fomc_aligned.py`, `ml/timing_alignment.py`,
`reports/timing_audit/event_dates_adr050.json`, and their tests. Output:
`reports/timing_audit/fomc_aligned.json`, run once at the frozen commit or later with no edits to
the frozen files.
