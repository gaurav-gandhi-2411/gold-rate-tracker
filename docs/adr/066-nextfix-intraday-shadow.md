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
