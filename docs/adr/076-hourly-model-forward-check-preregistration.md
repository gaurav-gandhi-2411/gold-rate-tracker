# ADR 076: Hourly world-price model, a second and forward-only check (pre-registration)

Status: pre-registered by CC on 2026-10-17, before any data it covers is scored; accepted by GG and amended
once by GG on 2026-10-17 (Amendment 1, rule version 2, with a frozen hash). Frozen from now on: a further
change needs a new dated amendment and a new hash, made before the look.

## Context

ADR 066 set one check, on 2026-10-16, with four conditions (14 calendar days and 8 resolved base fixes;
backtest CI below 0 with DM p below 0.05; shadow agrees; range coverage). It ran once, unmodified
(`adr066-check.yml`, run 38941136174, repository sha `2aa549f0df`) and returned **too early** for both
windows:

| Window | Resolved base fixes | Calendar days | Failed conditions |
|---|---|---|---|
| after_morning_rate | 6 of 8 | 11 of 14 | 1 (length), 3 (shadow -60.4% vs backtest -41.3%, CI [-53.0, -27.7]) |
| after_afternoon_rate | 7 of 8 | 12 of 14 | 1 (length) |

ADR 066 and `docs/ADR066_CHECK_HOWTO.md` define that check for 2026-10-16 only. Neither defines a re-check
after a `too early` verdict. Running it again every few days until it passes would be optional stopping:
conditions 2 to 4 are tests, and each extra look is another chance to pass by luck. So the 2026-10-16
outcome is **final** (the hourly model did not join the challenger pool; the ADR 072 table row "the day
its own 2026-10-16 check passes" is closed as not passed), and any further check needs this new
pre-registration.

The numbers above were read on 2026-10-16 at about 17:38 UTC. That information is spent: it cannot be
part of a new test.

## Decision

One further check, written down before any of its data is scored.

1. **Data.** Only shadow entries in `data/nextfix_intraday_shadow.json` logged strictly after
   2026-10-16T17:38:00Z, for the windows `after_morning_rate` and `after_afternoon_rate`, resolved by
   `ml.nextfix_intraday.score` (not logged at or after their target fix was published). Entries already
   read in the first check are not reused. The cached hourly bars (about 60 days) overlap the first
   check's period, so the backtest is reported but is **not** a condition (see 3).
2. **One look.** On the first Monday on or after 2026-11-16 on which a window has at least 20 resolved base
   fixes and at least 28 calendar days of entries. This is a count, not a result: before that day no
   effect size, CI or p-value for these entries is computed, printed or read. The look is run once,
   unmodified, as a separate manually dispatched run, and its outcome is final whatever it says.
3. **Conditions, per window, all required** (two windows are tested, so alpha is 0.025 per window,
   Bonferroni):
   a. the 95% bootstrap CI (seed 42, 10,000 resamples of resolved fixes) for the change in mean absolute
      error against holding the fix lies entirely below 0;
   b. a Diebold-Mariano test of the same errors, one-sided, p below 0.025 (small-sample correction as in
      `scripts/check_adr066_promotion.py`);
   c. range coverage: the Wilson 95% interval of the 80% band's coverage on these entries contains 0.80;
   d. beta is fixed in advance at 1.0 (the value the live predictor uses). The first check's data-driven
      beta choice is not repeated.
   Direction stays out: a direction line additionally needs `decide_direction_signal` on the window's own
   record, as in ADR 066.
4. **Consequences of the outcome.** A window that passes is eligible for an ADR 072 amendment (GG's
   decision; new rule hash; the `after_us_close` window question left open in ADR 066 still has to be
   settled there). A window that fails, or is still short of 20 fixes at its look date, is final: the
   model stays shadow-only and nothing further is scored without another ADR.
5. **Implementation.** `scripts/check_adr066_promotion.py` gets options for the first-entry cut-off, the
   minimum fixes and days, alpha and the fixed beta, with its current defaults unchanged, in a code PR
   merged **before** 2026-11-16 and tested on synthetic data. Until then nothing is run on the real
   entries. `docs/MODEL_STATUS.md` shows only the counts.

## Amendment 1 (2026-10-17, decided by GG; recorded before any post-cut-off effect is computed)

Reading the text against the code (`scripts/check_adr066_promotion.py --adr 076`, workflow
`adr076-look.yml`) showed two gaps. GG ruled on both on 2026-10-17. This amendment replaces points 2 and 3c
above where they differ; everything else stands. Rule version 2.

1. **Coverage (3c) is calibrated on entries from before the cut-off.** The walk-forward recipe of
   `ml.nextfix.flat_record` needs 20 earlier pairs and then 20 scored points, so a window with 20 to 39 resolved
   fixes could never satisfy c. Now: the volatility-scaled 80% band is fixed from the resolved shadow
   entries of that window logged **before** 2026-10-16T17:38:00Z (the quantile of their scaled errors, never
   updated); coverage is scored only on resolved entries logged **after** the cut-off; c holds when the
   Wilson 95% interval of that coverage contains 0.80. Calibration entries are never scored by this check
   (a base fix with decisions on both sides of the cut-off counts as scored, not as calibration; an entry
   logged exactly at the cut-off is neither). At least 5 resolved calibration fixes are needed, the same
   floor `ml.nextfix_intraday.score` uses; with fewer, c is "not evaluable" and fails. The first check
   found 6 and 7 resolved pre-cut-off fixes (run 38941136174), so the band is fixed on few points: this is a
   known weakness, accepted, and the look can run at 20 post-cut-off fixes.
2. **Exactly one look, on a fixed day.** The look is the first Monday on or after 2026-11-16 on which a
   window meets both count gates (at least 20 resolved fixes and at least 28 calendar days, post-cut-off),
   counted on the entries logged before 00:00Z of that Monday. The gates are counts only: nothing about an
   effect is computed, printed or read before that Monday. A window that is short on a Monday simply waits
   for the next one; no end date was set and none is invented here. A later run of the script uses the same
   entries (those logged before 00:00Z of the look Monday) and returns the same answer, so a re-run is not a
   second look.
3. **Nothing was computed so far.** The 2026-10-16 check (run 38941136174) ran at the cut-off and used only
   earlier entries. `adr076-look.yml` has never been dispatched. One thing did compute effects on post-cut-off
   entries without anyone reading them: `ml.nextfix_intraday.run` (every check-price run) stores
   `score(all entries)` in the encrypted shadow file's summary. The weekly model scorecard prints the same
   quantities from that file but was last generated 2026-10-12 (entries up to 2026-10-09), so nothing
   post-cut-off was published. Both are changed in a separate PR to count only until the look, before the
   next weekly-backtest run on 2026-10-19.

Frozen rule, version 2: SHA-256 of the canonical JSON of `ADR076_RULE` in
`scripts/check_adr066_promotion.py` (checked by `tests/test_adr076_look.py`):

`dffcf418ecabbc08ac37bfe6760459e0084e80f5ee378954b2630f029e1f827b`

Tests: c is evaluable at exactly 20 post-cut-off fixes; no effect function runs before the look day;
calibration entries are never scored; a re-run after the look day ignores later entries.

## Consequences

- The hourly model and the P3+hourly average stay out of the pool until at least 2026-11-16 and only on a
  pass.
- Cost of the delay: about one month of a possibly better hourly model. Cost of looking again now: a pass
  that cannot be told from luck.
- The first check's backtest evidence (CI [-53.0, -27.7] morning, [-38.5, -15.2] afternoon) is not
  discarded, but it is not a condition: those bars overlap the period it already used.
- Honest limit: 20 fixes per window detect only a large effect (the shadow showed -17.6% to -60.4%, from
  6 to 7 fixes each, which is itself too few to trust).

## Alternatives

- **Re-run the first check when 14 days and 8 fixes are reached.** Rejected: ADR 066 defines no re-check;
  it is optional stopping, and the entries have already been seen.
- **Wait and judge the whole period at once with a new threshold.** Rejected: the seen entries would
  steer the choice of threshold.
- **Drop the hourly model.** Not decided here; the shadow keeps accumulating at no cost and the live
  champion is unaffected.
