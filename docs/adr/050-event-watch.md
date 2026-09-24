# ADR 050 — Pre-registration: F4 "Event watch" — does move SIZE spike around known calendar events?

**Status:** Proposed 2026-09-24 (branch `feat/event-watch-model`). **Result recorded 2026-09-24:
none of the 4 event types pass the pre-registered success gate; no card is surfaced (see
Result).** Pre-registration only. The
text, `data/events_calendar.json`, `ml/event_watch.py`, `scripts/analysis_event_watch.py`, and
`tests/test_event_watch.py` are frozen at the commit that carries this sentence, **before any run**
of the analysis script on price data. The results section below is appended only after that frozen
run, and states the commit SHA it ran against.

## Context

F4 wants a single sentence on the product: *"Budget day is next week — on similar days in the
past, prices moved about Rs.X/g."* This is a claim about the **size** of a typical move around a
known, calendar-dated event (FOMC decisions, US CPI/jobs releases, the India Union Budget), never
about direction — the repo's own direction track (ADR 040, and #1992/#2000's detection-floor
finding) has repeatedly found no usable directional edge on this data; this ADR does not reopen
that question, and `ml.event_watch` never emits an up/down call.

## Event types considered, and why festivals are excluded

Source research: `sources_cot_events.md` (scratchpad, this session) plus direct re-verification
this session (see "Sources" table below). Four candidate event types have a verifiable, dated,
multi-year, official-source calendar:

1. **FOMC policy decisions** — federalreserve.gov, public domain (17 U.S.C. §105, by analogy with
   CFTC/Treasury/BLS's own explicit public-domain statements; not independently re-quoted from a
   Fed-specific policy page this session).
2. **US CPI releases** — bls.gov, public domain (bls.gov/bls/linksite.htm, quoted in the research
   note).
3. **US Employment Situation (jobs report) releases** — bls.gov, same public-domain basis as (2).
4. **India Union Budget presentation** — dates are bare facts (not copyrightable expression under
   either US or Indian law), sourced from Wikipedia's "Union budget of India" (CC BY-SA 4.0),
   independently re-fetched and cross-checked this session. `indiabudget.gov.in` itself returns
   HTTP 403 to automated fetch and its own page text carries Indian government copyright (60
   years, not public domain) — its **text** is not reproduced anywhere in this feature; only the
   **date** is extracted, which the research note's legal analysis treats as low-risk.

**Festivals (Akshaya Tritiya, Dhanteras, Diwali) are excluded from this feature.** Per the task
brief's own instruction: *"if you cannot source festival dates verifiably, leave festivals out and
say so."* Two independent problems, both confirmed this session:
- No single official Government-of-India source publishes a dated multi-year list for these three
  festivals. Dhanteras/Diwali appear only as one-off, per-year DoPT gazetted-holiday PDFs (not a
  stable dated archive back to 2000); Akshaya Tritiya is not a gazetted holiday at all.
- The one candidate secondary source with year-by-year pages (drikpanchang.com) gave **two
  mutually conflicting dates for Akshaya Tritiya 2026** in the original research pass (19 Apr vs
  15 May) — an unresolved discrepancy, not a typo, and exactly the kind of thing a from-scratch
  panchang/ephemeris computation would be needed to arbitrate, which is out of scope here.
- `ml/calendar_events.py`'s existing `ALL_FESTIVALS` anchor dates (used by the direction-model
  calendar features) carry **no source citation at all** in the module — checked directly this
  session (`git log`/file read, no comment, no ADR reference found). They are usable as an
  unverified feature input to a model that is graded empirically on walk-forward accuracy (where a
  wrong festival date just costs a little signal), but they do **not** meet this ADR's `verified:
  true` bar for a user-facing factual claim ("prices moved Rs.X/g around Diwali"). Not fixed here
  — out of scope; flagged for a future ADR if festival-day event-watch is ever wanted.

Bonferroni multiplicity below is therefore over **m = 4** event types, not 5+.

## Price series (frozen, before any row is read for this purpose)

| Event types | Series | Source | Coverage |
|---|---|---|---|
| `fomc_decision`, `us_cpi`, `us_jobs_report` | COMEX GC=F daily close, USD/troy-oz, ratio roll-adjusted | `ml.macro._download_with_retry` (yfinance, same fetch primitive `ml/direction/comex_daily.py` uses) + `ml.inr_proxy._detect_and_adjust_rolls` (same GLD-divergence roll-adjustment method already validated for M2/COMEX work) | 2000-08-30 (GC=F's actual first Yahoo Finance row, confirmed by direct fetch this session) through today |
| `india_budget` | INR proxy, `data/history_seed_inr22k_label.parquet`, `label_22k_per_10g` column | Existing repo artifact (ADR 030/032) | 2013-01-01 through today (2026-09-23 as of last regen) |

**Known limitation, stated in advance, not corrected in this ADR:** GLD (needed for roll detection)
only started trading 2004-11-18; before that, `_detect_and_adjust_rolls`'s rolling window has
insufficient GLD history to flag anything, so GC=F between 2000-08-30 and roughly Dec 2004 is
**not** roll-adjusted. Any roll artifact in that window would inflate that period's |move| for
*both* event and normal days roughly equally (a roll is a random calendar event, not correlated
with FOMC/CPI/jobs dates), so it is not expected to bias the event-vs-normal *comparison*, but it
is a real reason the 2000-2012 half's numbers carry a wider, less-clean error bar than the
2013-2026 half's. Reported, not hidden.

**Real IBJA** (`data/ibja_rates.parquet`) is used **only** to anchor "today's price" for the card's
Rs/g conversion (see below) — never as an input to the historical move statistics, per the task
brief ("real IBJA reported separately, descriptive").

## Event-day definition (frozen)

- **FOMC**: the decision day (last calendar day of the meeting range; for the one historical
  exception where the Fed's own page states the Statement was released a day after the meeting
  date — the March 2020 unscheduled emergency meeting — the **release** date is used, not the
  meeting date. Confirmed directly from federalreserve.gov's own annotation, the only such
  annotation found anywhere in the 2000-2027 archive). Event-day price = that calendar date's
  **genuine** COMEX close; if that date is not a genuine COMEX trading day (exchange holiday), roll
  forward to the next genuine trading day's close.
- **CPI / jobs**: the stated release date (always 8:30 AM ET per every row read this session) →
  same roll-forward-to-next-genuine-trading-day rule as FOMC.
- **India Budget**: the presentation date (~11:00 AM IST) → that date's INR-proxy value; the proxy
  series is defined for every calendar day (forward-filled through COMEX-closed weekends in its own
  construction), so no roll-forward is normally needed, but the same roll-forward-if-missing rule
  applies defensively.
- **Prior-day price** (the "vs previous" baseline for the log return): the last **genuine**
  trading/proxy-update day strictly before the (possibly rolled-forward) event day. "Genuine" for
  COMEX = a real (non-forward-filled) GC=F close, detected the same way
  `ml/direction/comex_daily.py`'s `is_gc_trading_day` mask is built (compare against the raw,
  pre-reindex Yahoo Finance close, not a ffilled value). "Genuine" for the INR proxy series (which
  has no separately published raw-vs-ffilled flag) is inferred as: the day's value differs from the
  immediately preceding calendar day's value at full float precision — a ffilled weekend/holiday
  repeat is bit-identical to the prior day; a real update essentially never is.
- **Metric**: `|ln(event_close / prior_close)|` — a single scalar per event occurrence, in the
  event's own price series' native units (USD/oz for the three COMEX types, INR/10g for Budget).
  Percent moves, not absolute currency amounts, are what get compared/tested (USD/oz and INR/10g
  are not directly comparable in absolute terms) — the Rs/g card figure is reconstructed afterward
  by applying the *relative* move to today's real, live, Rs/g price (see "Card figure" below).

## Normal-day set (frozen)

For a given price series (COMEX or INR proxy), the **normal-day pool** = every genuine
trading/update day in that series' full coverage range, **minus** any day within ±1 calendar day
of **any** event of **any** of the 4 types (the full `events_calendar.json`, not just the type
under test) — per the task brief's explicit instruction. This is one shared normal pool per price
series, reused across all types measured on that series (`fomc_decision`, `us_cpi`,
`us_jobs_report` share the COMEX normal pool; `india_budget` gets its own INR-proxy normal pool).

## Test (frozen)

**H1** (one-sided, per type): mean `|log return|` on that type's event days is **larger** than
mean `|log return|` on normal days.

**Method — block bootstrap** (stated per the brief's "HAC/block-bootstrap intervals (state
which)"):
- The **normal-day** `|log return|` series is resampled with a **moving block bootstrap, block
  length = 5 trading days**, circular wrap, because normal days are literally most of the trading
  calendar and carry real volatility-clustering autocorrelation that i.i.d. resampling would
  understate.
- The **event-day** `|log return|` values for a given type are resampled **i.i.d.** (no
  meaningful "block" — FOMC/CPI/jobs events are ~6-13/year, budget is ~1/year, so consecutive
  occurrences of the same event type are weeks-to-months apart and are not temporally adjacent to
  each other; there is no autocorrelation structure between them to preserve).
- 2000 replicates, `numpy.random.default_rng(seed=42)`. Each replicate pairs one bootstrap event
  mean with one bootstrap normal mean; `p_one_sided = fraction of (event_mean - normal_mean) <= 0`
  across replicates. The observed (non-bootstrapped) difference and its 95% bootstrap percentile
  interval are also reported.
- **Also reported** (not gating): ratio of MEDIAN `|move|` (event / normal) — the brief's "a ratio
  of medians".

**Multiplicity**: Bonferroni over the 4 event types (m = 4, α = 0.05, per-type threshold =
0.0125).

**Success per type** (gates whether the type is surfaced on the card): Bonferroni-significant
(`p_one_sided <= 0.0125`) **AND** ratio of **MEAN** `|move|` (event / normal) **>= 1.2** — both
conditions, per the brief's literal wording. Only types meeting both are surfaced; a type failing
either is reported in full in this ADR's Results section, not hidden.

## Consistency check (frozen, reported not gated)

Every number above is also computed separately on the **2000-01-01..2012-12-31** half and the
**2013-01-01..present** half of each type's available data, using only that half's own event days
and that half's own normal-day pool (restricted to the same half). `india_budget` has zero rows in
the 2000-2012 half (INR proxy starts 2013) — reported as "not measurable," not as a 0/0 statistic.
This is a stated-in-advance consistency check, not a second chance at significance — a type that
passes the full-sample gate but is wildly inconsistent between halves is still surfaced (the gate
is the full-sample numbers), but the inconsistency is reported plainly in the same table.

## Card figure (frozen)

For a type that passes the success gate, the card's "about Rs.X/g" = **median** `|log return|` on
that type's historical event days (not mean — the brief specifies median for the card), converted
to an absolute Rs/g amount by applying that relative move to **today's real IBJA price**
(`data/ibja_rates.parquet`, latest `pm_916` falling back to `am_916`, divided by 10 for per-gram),
with a 95% interval built from the same i.i.d. bootstrap of the event-day `|log return|` values
(2.5th/97.5th percentile of the bootstrap median, same 2000 replicates/seed=42, converted to Rs/g
the same way). This mixes a COMEX/INR-proxy-derived **relative** move with a real, live **absolute**
anchor price — an explicit, stated modeling assumption (a 1.5% move in USD gold is assumed to
transfully transfer to a ~1.5% move in India's Rs/gram price; INR/USD and import-duty effects on
that day are not separately modeled) — not a claim that COMEX and IBJA move in lockstep on every
single day.

`data/event_watch_today.json` lists every event, of a surfaced type, whose date falls in the next
14 days from the run date, with its type's X and interval, and a plain-language sentence built only
from those computed numbers (no hand-typed figure).

## Sources (verified this session)

| Claim | Source | How verified |
|---|---|---|
| FOMC meeting dates 2000-2020 | `federalreserve.gov/monetarypolicy/fomchistorical<YYYY>.htm` | Direct `curl` fetch, all 21 pages, this session |
| FOMC meeting dates 2021-2027 | `federalreserve.gov/monetarypolicy/fomccalendars.htm` | Direct `curl` fetch, this session |
| US CPI / jobs release dates 2000-2026 | `bls.gov/schedule/<YYYY>/home.htm` (26 pages) | `bls.gov` blocks direct automated fetch (HTTP 403, confirmed both with and without a browser User-Agent); fetched via the Wayback Machine (`web.archive.org`) snapshots of the same pages instead — same BLS-authored content, same public-domain status, an availability workaround only |
| India Budget presentation dates 2000-2026 | `en.wikipedia.org/wiki/Union_budget_of_India` | Direct `curl` fetch, this session; cross-checked against the pre-existing research note's independent press corroboration for the earliest rows |
| GC=F / GLD first-available date | Yahoo Finance via `yfinance` | Direct fetch this session: GC=F from 2000-08-30, GLD from 2004-11-18 |

Every row in `data/events_calendar.json` carries its own `source_url` and `verified: true/false` —
only `verified: true` rows are used by `ml.event_watch`.

## Alternatives considered

- **Use `ml/calendar_events.py`'s existing festival anchors anyway, flagged low-confidence.**
  Rejected — the brief's instruction is explicit ("leave festivals out and say so"), and an
  unverifiable date feeding a user-facing "prices moved X on this day" claim is a worse failure
  mode than simply not having the feature for festivals yet.
- **Use FRED `DFII10`/other mirrors instead of Treasury-adjacent COMEX loaders.** Not applicable —
  this feature needs a gold price series, not a real-yield series; FRED wasn't a candidate here.
- **Test difference of MEDIANS directly (not means) with the bootstrap.** Rejected in favor of
  matching the brief's literal spec ("difference of mean |return|" for the test; median is used
  separately for the ratio and the card figure) — not a free methodological choice.
- **HAC (Newey-West) instead of block bootstrap for the significance test.** Considered; block
  bootstrap was chosen because the two groups being compared (event days vs. normal days) are not
  a single paired time series the way `ml.direction.stats_corrections.block_bootstrap_confirm`'s
  loss-differential series is — HAC's standard use case there doesn't map cleanly onto a two-sample
  comparison with one group densely time-ordered and the other sparse and scattered. Both are valid
  choices; the brief explicitly allows either and asks only that the choice be stated.

## Result (run at frozen commit `e5f5d8bce33155fcc0b23e7768d6fdfc397aaba2`, 2026-09-24)

**Provenance:** `reports/event_watch_results.json` (git_sha recorded in the file matches the frozen
commit above). Nothing in `ml/event_watch.py`, `scripts/analysis_event_watch.py`,
`data/events_calendar.json`, or this ADR's frozen sections changed between the freeze push and this
run.

**Pre-registered verdict: none of the 4 event types pass the success gate.** No type is surfaced on
`data/event_watch_today.json` (empty `upcoming_events`, empty `surfaced_types`, verified by direct
inspection of the file this run produced).

| type | n events priced | n normal days | mean\|move\| event | mean\|move\| normal | ratio of means | ratio of medians | p (one-sided) | Bonferroni (≤0.0125) | ratio ≥ 1.2 | **passes gate** |
|---|---|---|---|---|---|---|---|---|---|---|
| `fomc_decision` | 210 / 225 | 4,516 | 0.00618 | 0.00916 | **0.674** | 0.713 | 1.000 | no | no | **no** |
| `us_cpi` | 317 / 329 | 4,516 | 0.00792 | 0.00916 | **0.864** | 0.914 | 0.998 | no | no | **no** |
| `us_jobs_report` | 315 / 327 | 4,516 | 0.00896 | 0.00916 | **0.978** | 1.120 | 0.665 | no | no | **no** |
| `india_budget` | 17 / 32 | 2,563 | 0.01360 | 0.00917 | **1.483** | 1.442 | 0.167 | no | yes | **no** |

(`n events priced` is out of `n events in calendar`; the gap is events before each price series'
coverage starts — COMEX from 2000-08-30, INR proxy from 2013-01-01 — dropped per the frozen
protocol, not a bug.)

**In plain words:**
- **US FOMC decisions and CPI releases move gold LESS than a normal day, not more**, on this
  22-27-year sample (ratio 0.67 and 0.86 respectively — both well below 1, the opposite direction
  from the F4 hypothesis). This is consistent across the 2000-2012 and 2013-2026 halves for both
  types (FOMC: 0.68 / 0.67; CPI: 0.88 / 0.84) — not a fluke of one sub-period. A plausible
  mechanism, not tested here: both are heavily anticipated, scheduled releases that markets
  extensively pre-position for, so the announcement itself can resolve LESS uncertainty than an
  average day carries from unscheduled news, other assets' moves, or COMEX's own technical flow.
- **US jobs reports are close to a wash** (ratio 0.98 overall) but the two halves disagree: 0.87 in
  2000-2012, 1.10 in 2013-2026 (p=0.105, the closest of the 4 to significance but still far from
  the 0.0125 Bonferroni bar). Reported as a genuine inconsistency, not smoothed over — if this
  feature is revisited, jobs-report is the type worth a fresh, larger pre-registration first.
- **India Budget shows the expected direction (ratio 1.48, biggest of the 4) but is not
  statistically significant** (p=0.167) — it is also the smallest sample by far (17 priced events,
  one per year since 2013, vs. hundreds for the COMEX-backed types), so this is underpowered, not
  disproven. The 2000-2012 half is correctly reported as "not measurable" (INR proxy coverage
  starts 2013) rather than a misleading 0/0.
- **A genuine, documented data-quality artifact, not a code bug**: 3 of 225 FOMC event-days (all in
  2000-2008, the lowest-liquidity years of the GC=F electronic contract) show an EXACT
  `|log return| = 0.0` because Yahoo Finance's own raw daily close is bit-identical across two
  consecutive days (confirmed by direct re-fetch: 2007-09-17 and 2007-09-18 both show
  `Close=715.799988`, with different Open/High/Low/Volume — a genuine vendor stale-tick, not a
  forward-fill in this code's `is_genuine` mask). This affects the event and normal pools equally
  (it is not correlated with event dates) and is one contributor, alongside the pre-Dec-2004
  roll-adjustment gap already flagged above, to the 2000-2012 half's wider error bars.
- **What this closes.** F4's headline sentence ("prices moved about Rs.X/g") cannot be shown today
  for any of the 4 verifiably-sourced event types — the pre-registered bar is not met, and for two
  types (FOMC, CPI) the honest finding is the opposite of the product hypothesis. `data/
  event_watch_today.json` correctly reflects this (empty). The code, calendar, and test harness are
  left in place (not reverted) so that (a) India Budget or jobs-report can be re-examined once more
  years of INR-proxy/COMEX history accumulate without rebuilding the pipeline, and (b) any future
  attempt is required to go through its own fresh pre-registration, per this repo's standing
  practice — this result is not grounds to lower the bar on a rerun.
