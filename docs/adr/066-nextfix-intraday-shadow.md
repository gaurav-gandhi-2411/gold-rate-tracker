# ADR 066: hourly world-price shadow for the windows where the forecast holds a fix

**Status:** Accepted 2026-10-02 (GG: "keep the model on all day by using hourly world prices after
the morning rate ... about 2 weeks of logged forecasts before it can be validated"). Shadow only.
Nothing reaches the page or `forecast.json`.

## Context

ADR 065 holds the latest IBJA fix in two windows:
- after an AM fix, until that day's PM fix;
- after a PM fix, until the US close.

Daily data had no edge there. The world price keeps moving between fixes, though, and
`ml.macro` already caches 1-hour GC=F / USD-INR bars (`data/macro_intraday.parquet`, last 60
days).

## Decision

`ml/nextfix_intraday.py` runs after inference on every check-price run.

**Forecast.** The next fix is `B x (G(now) / G(t_B)) ** beta`, for beta in {0, 0.5, 1}:
- B is the latest fix, published at t_B;
- G(t) is gold_usd x usd_inr read as the close of the last 1-hour bar that ended by t (the
  ADR 058 convention). A reading more than 6 h old is not used;
- beta = 0 is the live forecast (hold the fix).

**Shadow log** (`data/nextfix_intraday_shadow.json`):
- one entry per run;
- each entry is resolved when IBJA publishes the next fix: the same day's PM after an AM fix, or
  the next AM after a PM fix;
- entries are kept for 120 days.

**Backtest** (`data/nextfix_intraday_backtest.json`): every hour covered by the cached bars,
replayed and scored against the fix that followed. It is recomputed each run.

**Scoring.** It is per window, with one row per base fix (the latest decision before the target),
so a day with many runs counts once. For each beta it reports:
- MAE;
- the change against holding, with a 95% moving-block bootstrap CI;
- a Diebold-Mariano p-value;
- the direction hit rate of the world move since the fix.

The every-hour backtest score is also reported, to show where in the window the edge appears.

## Promotion rule (set before any data)

A window may switch from "hold the fix" to the pass-through only if **all** of these hold, in a
separate PR:
1. At least 14 calendar days of shadow entries, with at least 8 resolved base fixes in that window.
2. In the backtest, the chosen beta's 95% CI for the change against holding lies entirely below 0,
   with a DM p-value below 0.05.
3. The shadow agrees: same sign, and its point estimate is inside the backtest's CI.
4. The window's range is re-fitted on the pass-through errors and keeps 80% coverage, as in
   ADR 065.

Direction in that window also needs `decide_direction_signal` to ship on that window's own
record.

## Limits

- The bars cover about 60 days, so the backtest has roughly 40 base fixes per window. That is
  enough to see a large effect, not a small one.
- IBJA's exact fixing instants are not recorded. The repo convention (AM 06:30 UTC, PM 11:30 UTC)
  is used, as in ADR 058.

## Scoring correction, 2026-10-05 (not a change to the promotion rule)

`score()` kept, per base fix, the latest shadow entry even when that entry was logged **after its
target fix was published** (2026-10-05 07:13Z: the AM fix, published 06:30Z, was the target of an
entry whose base was still the previous PM fix, because check-price then ran inference before the
IBJA append; fixed in #2390). Such an entry is not a forecast. `ml.nextfix_intraday.logged_after_target`
now drops it before scoring and each window reports `n_excluded_logged_after_target`. The rule above
(14 days, 8 resolved fixes, CI below 0 with DM p < 0.05, shadow agrees, range refit) is unchanged.
This is a scoring correctness fix made before the 2026-10-16 check; no result had been read.

## Live predictor built, 2026-10-10 (not a change to the rule or the check)

GG asked for the hourly model to be switchable the day its check passes. The check itself is
untouched: `ml/nextfix_intraday.py`, `scripts/check_adr066_promotion.py` and the rule above are not
edited by this change, and no result had been read when it was written.

What exists now, **in shadow only** (nothing reaches the page or `forecast.json` unless the pool is
opened):
- `ml.nextfix.predict_hourly`: for the decision day D, the next PM fix is `pm0 x exp(x_hourly)`, with
  `x_hourly = ln(G(US close of D) / G(PM fix of D))`, G read from the cached 1-hour bars by
  `nextfix_intraday.value_at` (last bar that ended by the instant, within 6 h). It is the beta = 1.0
  pass-through the scorecard already reports as "the hourly pass-through". Nothing is fitted. P(up)
  and the volatility come from the training pairs exactly as for P3, so the range and direction
  machinery are the same.
- Its own record, one fold per decision day, in the `hourly_shadow` key of
  `data/nextfix_p3_variants_oos.json` (already encrypted, ADR 060). It is deliberately **not** under
  `variants`, so no scorer, status page or promotion step reads it as a registered challenger.
  Folds before `HOURLY_FORWARD_FROM` (2026-10-12, the first decision day the merged code issues) are
  walk-forward re-runs on the cached bars and carry `retro: true`; they are never counted.
- Runtime leak guard (ADR 073): both bar readings must have ended strictly before the decision
  moment (the US close), a missing bar end is a deny, and a violation stops the record walk and, if
  the model is ever live, holds the fix. Negative control in `tests/test_nextfix_hourly.py`.
- `CHAMPION_REGISTRY["hourly"]` and `nextfix.run` support the hourly model as champion, with its own
  sticky demotion state (`model_demotion_state__nextfix_hourly_v1.json`) under the unchanged ADR 068
  rules. Test: opening the pool in a test runs it live and creates that file.

What it does **not** do: it is not in `ml.promotion.LIVE_CAPABLE`, not in `RULE["registered"]`, and
the frozen rule (hash `e4efabec2193...`, version 7) is unchanged, so it cannot be promoted. A
champion file naming it fails closed to P3. If the 2026-10-16 check passes for a window, entering the
challenger pool is a rule amendment (new ADR 072 amendment and hash: add it to `LIVE_CAPABLE`,
`registered`, and the family size / alpha split), which is GG's decision; this change makes that
amendment the only missing piece. Two things the amendment must settle, because the 2026-10-16 check
does not cover them: the check's windows are `after_morning_rate` and `after_afternoon_rate`, while
the P3-style record here is the `after_us_close` window (target the next PM fix); and the beta (here
1.0, not chosen by the check's beta rule).
