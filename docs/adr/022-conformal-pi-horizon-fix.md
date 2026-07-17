# ADR 022 — Conformal PI Recalibrated to h=1 (Next Trading Day), Matching What Coverage Actually Tests

Status: Accepted — §3's 84.7% validation claim corrected by ADR 023 (2026-07-18)
Date: 2026-07-17

**Note (2026-07-18):** the h=1 recalibration below is unchanged and correct. ADR 023 corrects
§3's "84.7% validated" framing — that figure's fit window and check window overlap almost
entirely, so it is a retrospective sanity check, not independent out-of-sample evidence. See
ADR 023 for the corrected claim and where genuine OOS confirmation will come from.

## Context

The displayed price range (`headline.lower/upper`, shown on the hero and described in the
methodology panel) is `current_price ± conformal_pi_half`, where `conformal_pi_half` is the
80th percentile of the last 30 backtest folds' naive flat-hold absolute errors
(`ml/inference.py::_compute_conformal_pi`). Since the naive forecaster's error was always read
at horizon index 4 (the 5th day out, h=5), the band was sized for a 5-day-ahead move.

Separately, `ml/metrics.py::compute_band_coverage` — added in PR #192 to track the band's real
empirical coverage — checks `lower <= actual_next_22k <= upper`, where `actual_next_22k` is
always the very next trading day's price (`_get_trading_window(...)[0]`, i.e. h=1), regardless
of the recorded decision type. The UI copy (`renderMethodology`) even mislabeled this as
"n=X resolved 5-day windows" — it was never testing 5-day windows.

A band sized for 5-day drift will trivially cover a 1-day move almost every time. Measured on
the 30 backtest folds:

| Horizon | 80th-pct naive abs error |
|---|---|
| h=1 (what coverage actually tests) | ₹262.6 |
| h=5 (what the band was calibrated to) | ₹590.3 |

This alone explains the 98.3% empirical coverage (n=59 live decisions) against an 80% nominal
target — no sample-noise story is needed. Diagnosed as part of a broader review of modeling work
that doesn't depend on the direction model's small-n limits (n=93 folds, MDE ~21pp — see the
direction-signal work this ADR is unrelated to and does not touch).

`ml/volatility.py::compute_vol_context` is a separate, legitimately-5-day quantity — a
realized-volatility estimate (20-day trailing log-return std, scaled by √5) used for the
good-price card's "moves about ±₹X over 5 days" note. It takes `static_pi_half` purely as a
floor (50% of it) and a degrade-path fallback; it does not compute its own value from
`conformal_pi_half` in the normal case. This ADR does not change `compute_vol_context`'s method
— only which reference value floors/backstops it (see Decision 2).

## Decision

### 1. `_compute_conformal_pi` is parameterized by horizon; the displayed band uses h=1

`_compute_conformal_pi(backtest, horizon_idx=4)` — default preserved for callers that still want
h=5. `ml.inference.main()` now calls it twice: `horizon_idx=0` (h=1) for `headline.lower/upper`
and `headline.conformal_pi_half`/`naive_mae_recent_30` (the band actually shown and tested);
`horizon_idx=4` (h=5, unchanged) for a separate `conformal_pi_half_5d` reference, used only to
floor `compute_vol_context`. The two quantities are exposed distinctly in `forecast.json`:
`headline.conformal_pi_half` (h=1) and `headline.vol_context.static_pi_half` (h=5) — not
collapsed into one field, so `compute_vol_context`'s genuinely-5-day estimate never silently
inherits the h=1 band's magnitude.

Rejected: leaving `compute_vol_context`'s floor wired to the (now h=1) `conformal_pi_half`. That
would floor a 5-day realized-vol estimate at roughly half of a next-day error — far too tight,
and a second, newly-introduced horizon mismatch in the exact same code path this ADR fixes.

### 2. The band represents "where tomorrow's price likely sits" — h=1, not h=5

Confirmed before changing anything: nothing in the UI needs a 5-day-ahead *point* range next to
today's price — the "5-day movement" concept the product actually wants to communicate is
already served by the separate, genuinely-5-day `vol_context` note. The displayed band's own job
is answering "how far might the next reading differ from today's price," which is exactly h=1.
Copy that claimed otherwise ("5-day range", "resolved 5-day windows", "covers the whole 5-day
window, not just tomorrow") was changed to match h=1, not the other way around (`app.js`,
`renderMethodology` and the good-price card's degraded-fallback volatility note).

### 3. Validated against live decision history before shipping — honesty floor is 80%, not a target

Re-applying the h1-calibrated half-width (₹262.6) to the 59 real resolved decisions in
`metrics_history.json` (not just the 30 backtest folds used to derive it) gives **84.7%
coverage** — above the 80% nominal floor. Per this project's honest-baseline norm (ADR 005): a
band that came out *below* 80% on this check would not have shipped, full stop, regardless of
how much tighter it made the range. Over-covered is safe; under-covered is a lie.

### 4. `coverage_metrics.json`'s reported number will lag the fix — disclosed, not hidden

`compute_band_coverage` has no time window; `n` only grows, and historical entries keep their
recorded (pre-fix, wide) `lower`/`upper` forever — correctly so, rewriting history would be
dishonest. This means the live coverage % will keep reading close to the old ~98% for a while
after this ships, gradually converging toward ~85% as new decisions resolve under the corrected
band. `ml/metrics.py::save_coverage_metrics` now writes an explicit `note` field disclosing this,
and `renderMethodology`'s copy says so directly, rather than letting the number look silently
inconsistent with the claimed methodology during the transition.

## Consequences

**Positive:**
- Displayed range is ~2.24x tighter (₹1180 → ₹526 width at today's price) while remaining
  honestly calibrated to what's actually measured — a materially more useful number next to the
  current price, with zero new data required (same 30 backtest folds, same 59 live decisions).
- The band and its own coverage claim now agree — the mismatch that caused the earlier 98.3%
  reading is closed, not just re-labeled.
- `compute_vol_context`'s existing, correct 5-day realized-vol estimate is unaffected in the
  normal (non-degraded) case, and its degrade/floor path is fixed to reference the correct h=5
  quantity instead of silently drifting onto the new h=1 number.

**Negative/accepted:**
- `coverage_metrics.json`'s displayed % will look unchanged (~98%) for a while post-ship, purely
  because old wide-band decisions are still counted with no time window. Disclosed in both the
  JSON payload and the UI copy rather than reset or hidden.
- Two conformal-PI calls per inference run instead of one (negligible cost — same 30-fold input,
  O(1) extra percentile computation).

## Alternatives considered

**Keep the band at h=5, fix `compute_band_coverage` to test against a real 5-day-ahead price
instead.** Rejected: the product question the band is meant to answer, next to today's price, is
about the near-term next reading, not a 5-day-forward point-in-time check; the good-price card
already carries the genuinely-5-day volatility statement separately. Narrowing to h=1 is also the
option users get more value from — a materially tighter, still-honest range — versus keeping a
wide band whose coverage claim would need reframing to justify its own width.

**Reset `coverage_metrics.json`'s `n`/history at the recalibration point.** Rejected: the metric
is an honest historical track record of whatever band was actually shown to users at each point
in time; resetting it after a methodology change is itself a form of overclaiming (hiding how the
number got here), not honesty. Disclosure over erasure, per ADR 005.
