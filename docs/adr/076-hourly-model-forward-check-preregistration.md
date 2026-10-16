# ADR 076: Hourly world-price model, a second and forward-only check (pre-registration)

Status: pre-registered by CC on 2026-10-17, before any data it covers is scored. GG may amend it until
the first scoring date; after that it is frozen.

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
