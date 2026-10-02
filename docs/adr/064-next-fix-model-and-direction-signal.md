# ADR 064: next official-rate forecast from the global move after the fix; direction signal on

**Status:** Accepted 2026-10-02 (GG: "use neural nets or any other highly efficient models",
"make the interval smaller and then test", "ensure we get a direction signal ... no pushback").
Promotion record: `data/direction_promotion_record.json`.

## Context

- The site's next-day figure was flat-hold ("assume no change") with an 80% band from flat-hold's
  past errors. Measured coverage was 75.7% (n=70), below its own target.
- Chronos-Bolt (a neural forecaster on IBJA's own history) was 17% worse than flat-hold. The
  ml/direction classifiers scored 48–56% against a 51–58% always-up baseline. Both only saw
  information that IBJA's fix had already priced in.
- IBJA fixes its PM rate at ~17:00 IST (11:30 UTC). Global gold and USD/INR trade until the US
  close, ~21:00–22:00 UTC. The global move between the fix and the close is known hours before the
  next fix, and the next fix follows it. Measured: corr(next fix return, global close-to-close
  return of the fix day) = 0.364 over 203 day pairs.
- Found on the way: the history seed (`data/history_seed_inr22k_proxy.parquet`) is dated one day
  late. Its row dated D is the close of the trading day before D (Monday's row is Friday's close).
  Verified against the feature store: log-ratio spread 0.0037 when shifted, 0.0145 unshifted.
  `ml.nextfix.global_series` shifts it. Other consumers of the seed were not changed here.

## Decision

`ml/nextfix.py` forecasts the next IBJA PM fix. The decision time is after the US close of the
latest fix day D (22:15 UTC) and before IBJA publishes a newer AM fix (06:30 UTC).

**Features.** All are known at that time:
- the global move over day D (close to close);
- the previous fix's move;
- the gap between the global close and today's fix, relative to its 20-day mean.

**Models:**
- **Point:** the mean of a ridge regression and an ensemble of five one-layer neural networks.
- **P(up):** the mean of a logistic regression and the two point models' implied probabilities.
- **Range:** split-conformal on the model's own out-of-sample errors, scaled by recent volatility
  (EWMA, half-life 10), with an 80% target.

**Track record.** `data/nextfix_oos.json` holds one out-of-sample forecast per day. Each is trained
only on pairs whose target fix was on or before that decision day. Every check-price run appends
new days and re-scores the record.

**Headline.** While the forecast is active, `forecast.json`'s headline (`predicted_22k`,
`lower`/`upper`) is the model's, mapped to the shop price by the calibration slope. Outside the
window, it stays flat-hold. `ml.metrics --record` scores it like before.

**Direction.** It is shown only when all three hold:
- the forecast is active;
- `decide_direction_signal` ships on the model's own track record, re-checked every run;
- the promotion record exists.

Within 5 points of 50% the page says "too close to call". The timing gate (buy/wait/sell) does
not pass and stays dark.

## Evidence (walk-forward, 137 days, 2025-07-17 .. 2026-09-22)

| | Flat-hold | Ridge | Neural nets | LightGBM | Shipped (ridge + NN) |
|---|---|---|---|---|---|
| MAE Rs./g (IBJA 22K) | 115.8 | 106.9 | 105.2 | 113.4 | **105.2 (−9.2%, Wilcoxon p=0.004)** |
| Direction accuracy | — | 62.8% | 64.2% | 59.9% | **65.7%** (always-up 48.9%) |

- **Stable across halves:**
  - 2025-07..2026-06: −5.5% MAE, 66.2% direction accuracy (always-up 54.4%);
  - 2026-06..09: −12.5% MAE, 65.2% direction accuracy (always-up 43.5%).
- **Direction gate:** acc 0.657 > 0.489, Brier 0.219 < 0.511, ECE 0.064 ≤ 0.10, p = 0.004, n = 137.
  It ships. The timing gate fails on ECE (0.064 > 0.05).
- **80% range:**
  - flat-hold, as shown: 76.1% coverage, mean width Rs. 360;
  - flat-hold, sized to really reach 80%: Rs. 404;
  - the new range: **80.3% coverage, mean width Rs. 356**. It is narrower in calm weeks; on
    2026-10-01 it was Rs. 271 against the old Rs. 416.
- **Production code path:** reproduces these numbers exactly.

## Consequences

- The window (about 03:45–12:00 IST) covers the daily scored decision (first run after 00:00 UTC).
  Outside it the band reverts to flat-hold's, so the band changes width during the day.
  Extending the window with intraday global prices (`macro_intraday.parquet`) is untested and
  left for later.
- Each check-price run fits two small models (about 1 s). The first run after a gap catches up
  one fold per missed IBJA day.
- If the record stops passing the direction gate, the signal hides itself on the next run. The
  promotion record stays; it authorizes showing a passing signal, not a failing one.
- Rollback: delete `data/direction_promotion_record.json` (direction off), or make
  `_next_fix_block` return inactive (headline back to flat-hold).
