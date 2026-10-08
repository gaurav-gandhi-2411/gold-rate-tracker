# ADR 073: the leak guard runs inside the live next-fix path

**Status:** Accepted 2026-10-08 (implementation of the decision "enforce a leak-guard assertion at
RUN time in the live path"). Builds on ADR 061 (`ml/leak_guard.py`, `ml/known_at.py`), ADR 069
(P3 live) and ADR 068/072 (fail-closed demotion and champion handling).

## Context

An independent review (VERIFIED by reading the imports) found that `ml.nextfix` imported neither
`ml.known_at` nor `ml.leak_guard`. The live forecast was leak-safe by structure: the training set is
`resolved[resolved.d1 <= row.d0]`, the features are read at the row dated `d0`, and the forecast is
only produced after the US close of `d0`. The guard itself ran only offline, in
`scripts/audit_nextfix_leak.py`, over the walk-forward record. A future edit that broke one of the
structural filters (a loader that takes the latest macro row, a filter that drops the embargo, a
clock that moves) would pass every test that checks values and publish a leaked forecast, with the
offline audit noticing only the next time somebody ran it.

## Decision

`ml/runtime_leak_guard.py` runs the ADR 061 contract (`known_at < issue time`, strict) every time a
forecast or an out-of-sample fold is built, using the `ml.known_at` clocks (USD/INR at the measured
20:00 UTC, COMEX 13:30 New York, IBJA AM/PM at 12:00/17:00 IST).

1. **Forecast time** (`ml.nextfix._model_forecast`, called from `forecast`). Checked against the
   issue time `now`: the decision-day row (the IBJA PM fix of `d0` that is the base, and the two
   closes the world move `x_glob` is built from: gold in USD and USD/INR, each for `d0` and the day
   before). Checked against the earlier of `now` and the US close of `d0`: every training pair's
   features and its target fix.
2. **Record time** (`ml.nextfix.update_oos`, so the live P3 record, the shadow ensemble and the
   ADR 071 variants): the same check per new fold against that day's US close, before the fold is
   predicted. A fold that fails is not appended and the walk stops, so nothing built from a late
   input is written to `data/nextfix_*oos.json`.
3. **Hold windows** (`forecast`): the base fix a hold is anchored on (AM or PM) must itself be known
   before `now`.
4. **Date of the value, not the calendar day.** `global_series` records the date of the macro row
   (or seed row) behind each value (`Series.attrs["source_date"]`) and `build_pairs` copies it into
   `g0_src` / `gprev_src`. A value that came from a row dated after the decision day is therefore
   dated by that row and is caught, even though the calendar index says `d0`.
5. **Fail closed.** On any violation the model is skipped and the published block is the hold
   figure (the latest PM fix, mode `after_afternoon_rate`, `p_up` null, reason `leak_guard`) with an
   ERROR log naming the input, its known-at and the issue time. If the hold's own base fix fails the
   check, the block is inactive with reason `leak_guard`. A bug inside the guard (missing date,
   unknown input, any exception) is recorded as a `guard_error` violation: "could not verify" is a
   deny (rule 98a). Nothing is raised out of `run()`, and nothing is persisted from a failed fold.
6. **Published.** `forecast.json` `next_fix.leak_guard` = `{"checked": true, "n_inputs": N,
   "violations": []}`; a violation entry has `input`, `source`, `known_at`, `issue_time`,
   `late_by_s`, `context`, `count` (aggregated per input name, at most 10 listed). No rates or
   prices. `checked: false` appears only when the run never reached a forecast (no IBJA data).
   `n_inputs` counts the forecast's own inputs, not the historical pairs a track-record rebuild
   checks, so it does not change between a rebuild and a normal run.

## What is checked, and what is not

Checked (known-at against the issue time, every run): IBJA PM of `d0` (the base); gold USD and
USD/INR at the source date of the `d0` and `d0 - 1` world values; for every training pair the same
five inputs plus its target PM fix; the base fix of a hold forecast (AM or PM).

Structural only, NOT checked at run time:
- the rolling basis history behind `bdev` (`BASIS_WINDOW` earlier pairs) and `x_prev` (the previous
  IBJA PM): earlier rows of series already checked at `d0`, assumed known earlier;
- the label seed (`history_seed_inr22k_label.parquet`) is treated as gold-times-rupee dated by its
  index with the COMEX and USD/INR clocks of that date; its true capture times are not recorded;
- whether the macro cache's own dates are honest (a row labelled D that holds D+1's close): the
  guard trusts the date it is given. It catches the wrong row being SELECTED, not a mislabelled row;
- the IBJA publish times are the repo convention (12:00 / 17:00 IST, ADR 058, not independently
  verified). A fix really published earlier than the convention, read in the minutes between,
  would be flagged;
- whether the value USED came from the row the recorded source date names: the guard dates an
  input from the `g0_src` / `gprev_src` columns it is handed, so a bug that changes the value
  used (for example reading the next day's value) while leaving the recorded source date
  unchanged passes (found by the independent review, 2026-10-08, with a constructed mutation);
- the fold targets that feed the spread behind `p_up` and the conformal band (`_resid_sd`),
  which are not individually checked;
- freshness: a day with no macro row passes the guard with the previous close forward-filled
  (the guard checks 'known before', not 'fresh'); the forecast is then base-plus-zero, as on
  master;
- the shadow ensemble's forecast in `forecast()` (same inputs as the live model, so a violation
  there would have been caught for the live model first); violations found while updating the
  shadow or variant records are logged and keep those records from growing but do not hold the live
  forecast;
- the shop price and calibration used by `ml.nextfix.to_retail` and `ml.inference` (current 22K,
  slope, `scraped_at`), which are outside the next-fix model;
- anything after the forecast (retail mapping, notifications).

## Consequences

- A live forecast built from an input that was not yet known now degrades to the hold figure and
  says why. It cannot ship silently. The offline audit stays as the replay over the whole record.
- The guard runs the same convention the audit does, so a clock change in `ml.known_at` (for
  example the USD/INR measurement being redone) changes what the live path accepts. The tests move
  the measured clock past `now` as the negative control and the forecast holds.
- Cost (measured 2026-10-08 on the committed data, 197 training pairs, a busy shared machine): the
  forecast-time check takes 34 ms median (105 ms worst of 30) against a 3-4 s `run()`; checking a
  full 137-fold record rebuild takes about 0.2 s per record (the clocks are computed once per
  pair set, then each fold is an array slice).
- New output key `leak_guard` in `forecast.json`; existing keys and the forecast numbers are
  unchanged (verified by running before/after on the committed data, PR description).
- Failure mode to know: a fold the guard rejects stops the record from growing until the cause is
  fixed, and a violation holds the live forecast for that run. Both self-heal on the next clean run;
  nothing sticky is written (unlike demotion). While a record is frozen forward scoring and the
  track record stop growing and nothing alerts: the only signal is the ERROR log and
  `next_fix.leak_guard.violations`.
- Wording corrections (independent review, 2026-10-08): (1) an exception inside the guard, or a
  base fix that fails its check, gives an INACTIVE next-fix block with reason `leak_guard`, not
  the hold figure (the hold figure is the result when the model's own inputs fail); (2) the
  'never raises out of `run()`' claim was false for one path (the USD/INR clock cache was built
  outside the try); fixed on 2026-10-08, with a test; (3) the ERROR text for shadow and variant
  record violations says 'failing closed to the hold figure' although those violations do not
  hold the live forecast; (4) the base-fix check rests on the assumed IBJA publish times: since
  2025-01-10 no row (0 of 206) was fetched before 06:30Z (AM) or 11:30Z (PM), so it has not
  triggered on real data.

## Alternatives

- **Raise in `run()`.** Rejected: `forecast.json` must always be written (norm #8), and the
  demotion monitor already established fail-closed-to-hold as the pattern for this page.
- **Keep the offline audit only.** Rejected by the review: it checks the past record, not the
  forecast being published now, and only when somebody runs it.
- **Check only the maximum known-at across all inputs.** Cheaper, but a violation then cannot name
  the offending input; the per-input check is vectorised and its cost is small.
- **Sticky demotion on a leak.** Rejected: a leak caused by a transient bad row should clear on the
  next good run; a person is alerted by the ERROR log and the published `violations`.
