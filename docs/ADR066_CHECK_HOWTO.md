# ADR 066 promotion check: how to run it on 2026-10-16

`scripts/check_adr066_promotion.py` evaluates the pre-registered promotion rule of
`docs/adr/066-nextfix-intraday-shadow.md` mechanically, for `after_morning_rate` and
`after_afternoon_rate`. `after_us_close` is the live model's window (ADR 065) and is out of scope.
It is read-only: it never edits `ml/` or `data/`; its only write is `reports/adr066_check_<date>.json`.

## Steps (venv: `C:\Users\gaura\ml-projects\grt-venv`)

1. `git fetch` and check out current `master` (the shadow log and backtest JSON are committed by
   the check-price bot, so they must be current). Once the ADR 060 migration has run, the shadow log
   `data/nextfix_intraday_shadow.json` is committed only as ciphertext: put your offline copy of the
   key in the environment (never on the command line) and run
   `python scripts/data_crypt.py decrypt --all` first (the check also reads `data/ibja_rates.parquet`,
   which ADR 060 encrypts too). The backtest JSON stays public. Without the decrypt the script
   reports `no data` for every window.
2. Refresh the cached hourly bars so the backtest is recomputed (needed for conditions 4 and 5):
   `python -c "from ml import macro; macro.update_intraday_cache()"` (writes the gitignored
   `data/macro_intraday.parquet`; needs network). Without that file the script falls back to
   `data/nextfix_intraday_backtest.json`, says so, and conditions 4 and 5 report "not evaluable"
   (a fail, never a pass).
3. `python scripts/check_adr066_promotion.py`
4. Read the per-window verdict: `promotable (...)`, `not yet (fails: ...)`, `too early (n=...)` or
   `no data`. Attach `reports/adr066_check_2026-10-16.json` (carries repo SHA, sha256 of each data
   file, run time) to whatever is reported.

## Definitions the ADR left open (fixed in the script, not chosen after the fact)

- Chosen beta: the beta in {0.5, 1.0} with the lower backtest MAE in that window (ties to 0.5). A
  window passes condition 2 only on that beta; the other beta is printed too.
- Condition 1 "calendar days": inclusive span from the first to the last shadow entry in the
  window. "Resolved": entries kept by `ml.nextfix_intraday.score`, i.e. not logged at or after
  their target fix was published (count reported as `n_excluded_logged_after_target`).
- Condition 4: `ml.nextfix.flat_record` (NOMINAL 0.80, CONFORMAL_WINDOW, MIN_CONFORMAL, EWMA vol)
  with the chosen beta's forecast as the band centre, over one decision per base fix; passes if
  the Wilson 95% interval of coverage contains 0.80 (over-coverage fails too).
- Condition 5 (direction): `decide_direction_signal` on the window's own hard up/down calls
  (sign of `x_since_fix` vs realised direction). Required for any direction line; it does not
  affect the range verdict.

## After the check

Promotion is GG's decision and, per the ADR, a separate PR. The script never promotes anything.

## A live predictor now exists in shadow (2026-10-10)

`ml.nextfix.predict_hourly` and a shadow record (`hourly_shadow` in
`data/nextfix_p3_variants_oos.json`) were added so that a pass can be acted on quickly. They do not
change this check: the script reads `data/nextfix_intraday_shadow.json` and the cached bars, never the
`hourly_shadow` record, and it never promotes. See ADR 066, "Live predictor built, 2026-10-10", for
what it takes to bring the model into the challenger pool (an ADR 072 amendment).
