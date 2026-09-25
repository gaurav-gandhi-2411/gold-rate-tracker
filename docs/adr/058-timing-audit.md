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

---

*Everything below was appended after the freeze.* The frozen text above is unchanged. It is
commit `0aee0e9e`, and `sha256(docs/adr/058-timing-audit.md)` at the freeze is
`de7aace1abb8d6b8ebe4d6c867b8ab969b2a0f358e0e79e845d7107365a12a0c`.

## Part 2 — result (run at the frozen commit `0aee0e9e`, `reports/timing_audit/fomc_aligned.json`)

| Cell | n event (eff. n) | n normal | mean \|move\| event / normal | ratio of means (medians) | HAC p, one-sided | block-bootstrap p | Bonferroni (≤ 0.0167) | BH | verdict |
|---|---|---|---|---|---|---|---|---|---|
| C1 COMEX, next settlement (**contaminated**) | 109 (110.2) | 2,399 | 1.37% / 0.72% | **1.89** (2.16) | 6.0e-9 | < 0.0005 | yes | yes | passes: a replication, not a confirmation |
| C2 GLD, decision-day close | 109 (119.1) | 2,402 | 0.93% / 0.72% | **1.30** (1.38) | 0.0008 | 0.0005 | yes | yes | **passes** (partly contaminated, see pre-reg) |
| C3 IBJA, first PM after | 7 | 127 | 0.80% / 0.83% | 0.96 (1.02) | — | — | — | — | **not testable** (n < 8) |
| retail Tanishq, IST D+1 board (descriptive) | 4 | 113 | 0.89, 1.05, 0.41, 0.39% vs normal median 0.49% | — | — | — | — | — | descriptive only |

Window 2013-01-01..2026-09-24, 109 priced decisions (after adding 2019-09-18 and excluding 3
unscheduled). The C1 diff CI95 is [0.42, 0.86] pp. For C2 it is [0.08, 0.35] pp.

**Read plainly:**
- **Correctly aligned, FOMC decisions DO move gold more than a normal day.** On COMEX the next
  session moves about 1.9x a normal day (ADR 050's misaligned row said 0.67x). On GLD, whose
  close lands 2 hours after the decision, the same day moves 1.3x. ADR 050's "FOMC days move
  less" is reversed, not just invalid.
- **C2 is the cleanest evidence.** Its numbers were never computed before. It is not blind,
  because C1-type knowledge existed, which is why the pre-reg calls it partly contaminated.
- **The Indian markets cannot be tested yet.** IBJA has 7 usable decisions; retail has 4.
  - IBJA's point estimate (0.96x) sits *below* 1 on n = 7. That is no evidence either way.
  - Whether the COMEX spike shows up in the Indian price a buyer pays is **unmeasured**.
- **Product:** per the pre-registration, nothing is surfaced. C2 passing licenses a *new* F4 card
  pre-registration, which must be measured on the Indian price, because the card speaks in Rs/g.
  The forward C1/C3 cells are registered above.

## Part 1 — the audit

### Findings table

The full rows, with inputs, clocks, fixes and "result at risk", are in
`reports/timing_audit/audit_table.json`. They are also copied into
`reports/timing_audit/audit.json`.

| # | Analysis | Issue | Severity | Could a published result change? |
|---|---|---|---|---|
| A1 | INR proxy **feature** series (ADR 030) | none; conservative, ~17 h (COMEX) and ~30 h (FX) stale at the PM fix | none | no |
| A2 | INR proxy **label** series (ADR 032) | COMEX settle D is 6-7 h *after* IBJA PM D. The lag sweep used daily closes only | medium | **yes -> R1: changes** |
| A3 | `ml/features.py` macro join | **leak**: joined on the reading's UTC date, so values arrive 7-18 h late. No consumer found | high if reused | no (dormant) |
| A4 | `ml/macro.py` forward-fill -> feature-store `*_asof_date` | staleness mask (e.g. `gold_usd_asof_date` 2026-09-19 is a Saturday). 1-day change is 0 and 5-day vol is biased low on filled days | medium | none found |
| A5 | feature-store backfill (114/221 rows) vs live rows | **leak** plus train/serve skew. Backfill macro is 6-9 h after the IBJA fix; live rows hold an intraday bar | medium | no ("no edge" stands a fortiori) |
| A6 | COMEX direction dataset | partial leak: US closes of i-1 are 2.5-3.5 h after the anchoring settle | low-med | no (same reason) |
| A7 | F4 FOMC (ADR 050) | the decision lands in bar D+1 | **high** | **yes -> Part 2: reverses** |
| A8 | F4 CPI / jobs | aligned (08:30 ET, before the settle) | none | no |
| A9 | F4 India Budget | the FX reaction lands on D+1 in the label series | low-med | possibly (n 17, p 0.167) |
| A10 | F4 calendar | 2019-09-18 regular meeting missing. Unscheduled rows use non-14:00 ET times | low | marginal |
| A11 | Day of week (ADR 052) | aligned (the Monday settle maps to the Indian Tuesday) | none | no |
| A12 | F1 markup (#2022) | aligned: the fix in force at each reading's IST time. Publish times are assumed | low | no |
| A13 | R2 nowcast "global move" (#1957), scorecard (#2015) | for a Saturday board the move is settle(Fri)/settle(Thu), so it double-counts and misses hours | medium | **yes -> R2: reverses** |
| A14 | derived premium (#2004), ADR 046 | premium = PM(t) / parity(settle t-1). Timing noise inflates the premium's mean reversion | medium | **yes -> R3: changes** the meaning of ADR 046 H1 |
| A15 | accuracy band / calibration | evaluation uses same-date PM; a live morning viewer gets the previous PM | low-med | coverage optimistic for mornings (not re-run) |
| A16 | `ml/drivers.py` "why did the price move" (**live**) | post-fix gold moves are attributed to "premium / local factors" | medium | live copy |
| A17 | weekly / next-day range, stale-day, 5-day vol | single clock | none | no |
| A18 | fusion benchmark (shadow) | IST vs UTC day keys (agree 118/121) | low | no |

### Re-runs (EXPLORATORY re-measurements; `reports/timing_audit/audit.json`, commit `924ee298`)

Aligned prices come from Yahoo 1-hour bars of GC=F x INR=X, read with
`ml.timing_alignment.asof_intraday`: the close of the last bar that ended by the instant. 1-hour
history starts 2024-05-25, so every re-run is limited to 2024-05-25..2026-09-24.

**R1: ADR 032 "timing misalignment does NOT explain the ~33% disagreement". Overturned.**
Setup: IBJA PM direction agreement, new-value consecutive rows, as ADR 032 did.

| Candidate | n | agreement [Wilson 95%] | corr. of changes |
|---|---|---|---|
| daily close, same day (ADR 032 label, lag 0) | 207 | 67.1% [60.5, 73.2] | 0.74 |
| daily close, previous day (ADR 030 feature, lag -1) | 207 | 67.1% [60.5, 73.2] | 0.70 |
| **COMEX x FX known at the PM fix (11:30 UTC)** | 208 | **90.4% [85.6, 93.7]** | **0.93** |

- McNemar, one-sided, aligned beats lag 0: 58 vs 10 discordant, **p = 1.2e-9**.
- On consecutive-business-day pairs only: 90.0% (n 180) vs 63.7% (n 179).
- **Consequences:**
  - Most of the proxy's "one day in three wrong" was the clock, not local premium noise.
  - The ADR 032 dead-zone rule (trust direction only above ~100 Rs/g) was fitted to a
    timing artefact where 1-hour data exists.
  - The daily-close proxy before 2024-05 cannot be fixed this way. Yahoo keeps only 730 days of
    hourly bars.

**R2: R2 nowcast "a COMEX x USD/INR adjustment makes weekend days worse". Reversed.**
Setup: M4 = M0 x move. The published move is settle(day before truth) / settle(day before IBJA
date). The aligned move is price when the scored Tanishq board was first seen / price at the IBJA
PM fix.

| Days | n | M0 | M4 as published | M4 aligned | aligned vs M0, DM one-sided (eff. n) |
|---|---|---|---|---|---|
| carried-forward (weekend/holiday) | 28 | Rs 96.1/g | 122.8 | **43.0** | **p = 0.0026** (18.7) |
| all | 90 | 61.2 | 69.5 | **44.7** | p = 0.0065 (60.9) |

- Same-day rows have move = 1, so the all-days gain comes from the carried-forward days.
- **Caveats:**
  - Post hoc; n 28, 2026-04-17..09-24.
  - "First seen" is an upper bound on the board time. On weekday holidays this can include up to
    one scrape interval (~3 h) of look-ahead. On weekends it cannot, because markets are closed
    after Friday 21:00 UTC.
  - Not compared here with yesterday's Tanishq (Rs 46.0 on the #2015 weekend stratum).
  - It needs a forward pre-registered shadow before anyone relies on it.

**R3: derived premium / ADR 046. The premium's mean reversion was mostly timing noise.**
- The premium's AR(1) is **0.30** on the published t-1-close parity. With parity at the fix it is
  **0.85** (184 pairs), and its SD falls from 1.26% to 0.82%.
- On ADR 046's H1 (C mean-reversion vs B1 "yesterday's premium + global move"), exploratory, same
  152 days:
  - **Published convention:** C Rs 130.1 vs B1 148.6/g (999, per g), p = 0.024.
  - **Aligned** (premium at the fix; parity at 11:30 IST before the AM fix): C 58.04 vs B1 58.03,
    **p = 0.50**.
- **ADR 046's registered H1 would mostly measure the clock.** Amend it before its forward read
  (~2027-04): change the parity timing, or state that a C win under the t-1 convention is not
  evidence of premium dynamics.

Multiplicity across the three re-runs (Bonferroni, m = 3, 0.0167): R1 and R2's carried-forward
cell pass. R3 is a disappearance, not a claim.

### Proposed fixes (for separate PRs; none made here)

1. **ADR 032 / `ml/inr_proxy_labels.py`:** from 2024-05, build labels from COMEX x FX known at
   each IBJA fix, and re-derive the dead-zone threshold. Record in ADR 032 that finding (a) is
   withdrawn (R1).
2. **R2 nowcast:** register a forward shadow of M4 with the aligned move, against M0 **and**
   yesterday's Tanishq on weekend/holiday days (R2). No live change until it confirms.
3. **ADR 046:** amend the parity timing (or the H1 interpretation) before the forward read (R3).
4. **ADR 050:** mark the FOMC row as reversed by ADR 058. Fix the calendar: add 2019-09-18, and
   add a release-time field and a scheduled/unscheduled flag.
5. **`ml/drivers.py` (live):** use COMEX x FX known at each IBJA fix at both window ends (A16).
   This is user-facing, so it is GG's call.
6. **`ml/macro.py` / feature store:** add a per-ticker genuine-observation mask. Write the true
   last-observation date to `*_asof_date`. Compute 1-day change and 5-day vol on genuine days only
   (A4).
7. **Feature-store backfill:** fill macro with values known by 11:30 UTC on D, and tag each row's
   clock. Live and backfill rows then share one convention (A5).
8. **`ml/features.py`:** replace the UTC-date join with "latest row known by ts", or delete the
   macro path, since nothing consumes it (A3).
9. **COMEX direction dataset:** lag US-close drivers so they are known by the anchoring settle
   (A6).
10. **Accuracy band:** score with the IBJA fix in force at each reading's time (A15).
11. **Scrapers:** record IBJA's actual publish time per fix (A12, A15). Every IBJA clock in this
    repo is still an assumption.

### Limits

- Intraday evidence covers only Yahoo's retention window: 60 days of 5-minute bars, 730 days of
  1-hour bars.
- The GC=F settlement clock is VERIFIED only for recent months. It is assumed stable back to
  2013. The ADR 050 audit's D+1 pattern on 2000-2026 is consistent with that.
- The INR=X clock is not pinned down.
- Hourly bars are a vendor aggregate, not an official fix.
- The COMEX x FX series in the re-runs is not roll-adjusted. Rolls fall on few days, and their
  effect on direction agreement was not separately measured.
