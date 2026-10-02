# ADR 065: the next-fix forecast all day; same-day global data; confidence intervals

**Status:** Accepted 2026-10-02 (GG: "fix for the model is only on for part of the day", "fix the
data bug", "the model results should be very highly confident"). Extends ADR 064.

## Context

- ADR 064's forecast was only active from the US close (~22:15 UTC) until IBJA's next AM fix
  (06:30 UTC). For the rest of the day, the page fell back to the old flat-hold band (₹416 wide,
  76% measured coverage against an 80% target).
- **The "data bug" was ours, not the seed's.** ADR 064 read `history_seed_inr22k_proxy.parquet`
  and shifted it by a day by hand. The proxy is lagged on purpose (ADR 030, leak control). The
  same-day series is `history_seed_inr22k_label.parquet`. Its `raw_pre_duty` on D equals the
  proxy's on D+1 to within 0.02% over 5,013 days.
- **The headline numbers had no confidence intervals.**

## Decision

1. **Data:** `ml/nextfix.py` reads the label seed with no shift. The proxy is no longer read.
   `data/README.md` now documents both seeds' dating.
2. **One forecast for the next IBJA fix, whatever the time.** Each window was tested walk-forward
   over 2025-07-17 .. 2026-09-23, and all three use the same 80% volatility-scaled split-conformal
   range:

| Window (UTC) | Forecast | Why | Range coverage (95% CI) | Mean width |
|---|---|---|---|---|
| US close .. next AM fix | ADR 064 model, next PM fix | −9.0% error vs holding the PM fix | 80.5% [72.4, 86.6], n=118 | ₹355 |
| AM fix .. that day's PM fix | Hold the AM fix | The model was **worse** here: +2.7% error, 95% CI [+0.4, +5.3], n=138 | 79.1% [71.8, 84.8], n=148 | ₹127 |
| PM fix .. US close | Hold the PM fix (target: next AM) | No edge without hourly prices: +0.7%, CI [−0.8, +2.2], p=0.38 | 79.2% [71.8, 85.0], n=144 | ₹332 |

   For comparison, the band the page showed before had 76.1% coverage at ₹360 wide.

3. **Shop-price anchor.** The forecast moves the shop price by
   `slope x (forecast fix − ref)`. Here `ref` is the latest fix published before the shop price was
   read; the estimate tiers use `ibja_asof` instead. A move the shop price already shows is not
   counted twice.
4. **Direction:** shown only in the model window, and only while that window's record passes the
   gate. While a fix is held there is no evidence for a direction, so no direction line is shown.
5. **Confidence, computed every run and published in `forecast.json` `next_fix`:**
   - moving-block bootstrap 95% CIs (blocks of 5 days) for the error change and direction accuracy;
   - a Diebold-Mariano test (Newey-West, 4 lags) against flat-hold;
   - Wilson CIs for range coverage.
6. **Copy:** "Next working day: likely ₹x – ₹y" becomes "Next price update: likely ₹x – ₹y",
   because the range is for the next official-rate update. The reliability sentence quotes the
   current window's own coverage.

## Evidence for the model window (same-day data, 138 out-of-sample days)

- **Error:** ₹105.4 vs ₹115.8 per gram, a change of **−9.0%**, 95% CI [−15.1, −3.3].
  Diebold-Mariano p = 0.006; Wilcoxon p = 0.005.
- **Direction:** **65.2%** right, 95% CI [58.0, 72.5], against 48.6% for always-up.
  Binomial p = 0.004. Brier 0.220 vs 0.514; ECE 0.068.
  - `decide_direction_signal`: ships.
  - `decide_timing_signal`: does not (ECE > 0.05).
- **Robustness:** −5.5% / −12.5% error and 66% / 65% direction in each half of the period
  (ADR 064). The IBJA AM fix as the target gives −9.8%, CI [−15.7, −3.2].

## Consequences and limits

- The page always shows a band that has been measured, and the band changes with the window. It is
  narrowest (~₹110–130) between the AM and PM fixes.
- The PM .. US-close window is where hourly world prices could help. That is the intraday shadow
  (ADR 066): logged for at least 2 weeks before any promotion.
- The confidence intervals are wide because n ≈ 140 days. They narrow as the record grows: every
  check-price run appends new days, and every number above is recomputed then.
