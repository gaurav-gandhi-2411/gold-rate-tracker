# ADR 030 — Long-History INR 22K Proxy for Model Pretraining (M1)

**Status:** Accepted, implemented 2026-09-23

**Context for:** M2 (direction model — pretrain on proxy, evaluate only on real forward folds) and
M4 (Chronos companion — covariate/ensemble variants). Neither is implemented by this ADR.

---

## Context

Real IBJA history (`data/ibja_rates.parquet`) only goes back to 2022-01-19 (265 rows as of this
writing). The direction model's own diagnosis (`data/direction_baseline.json`) shows a majority-class
collapse at n=159 folds — not enough real history to reliably distinguish a working model from a
lucky one, let alone to test reframed targets (dead-zone labels, detrended direction, 5/10-day
horizons) that need even more folds per configuration. GG's spec (2026-09-23) asked for a proxy
history long enough to pretrain on, evaluated only on real data thereafter, with **direction
agreement between proxy and real IBJA changes** as the metric that actually matters — a proxy whose
level tracks IBJA well but whose day-to-day direction doesn't is worse than useless for label
pretraining, since the model would learn to move the wrong way.

## Decision

Built `ml/inr_proxy.py`: COMEX gold futures (`GC=F`) × USD/INR (`INR=X`) → 22K INR/10g, adjusted for
the import duty/cess schedule (`data/duty_events.json`, already backfilled by `#1881`) and a
walk-forward-calibrated residual premium against real IBJA. Output: `data/history_seed_inr22k_proxy.parquet`,
2013-01-01 to present (5,014 rows, 13.7 years — comfortably past the "10+ years" target; duty
schedule data itself starts 2013-01-01, so that's the natural lower bound).

### Three leakage traps, handled explicitly

1. **Time alignment.** COMEX/Globex trades almost continuously through the IST evening — well past
   both of IBJA's AM/PM fixes — so a same-day COMEX/FX close is not something the real fix could have
   seen. Every day's proxy uses only the **prior calendar day's (T-1)** close (`shift(1)` on both
   series). Verified by `tests/test_inr_proxy.py::TestLeakageAlignment` (a same-day price spike must
   not appear in that day's proxy, only the next day's).
2. **Futures rolls.** `GC=F` is a front-month continuous series; monthly contract rollovers can
   produce a price discontinuity unrelated to the spot gold price. Detected via divergence from `GLD`
   (SPDR Gold Shares — tracks physical spot gold minus expense ratio, and does not roll): a day where
   `GC=F`'s log-return diverges from `GLD`'s by more than 6σ of the trailing 60-day divergence is
   ratio-back-adjusted (that day and every subsequent day multiplied by the ratio needed to remove
   exactly the excess divergence), so genuine shared market moves are never touched. Only **1 day**
   out of 5,014 was flagged over the full 13.7-year history — gold's low cost-of-carry makes roll
   jumps small relative to genuine price moves most months; this is a conservative threshold (6σ) and
   could be revisited if a later backtest surfaces residual roll artifacts.
3. **Premium calibration.** Fit walk-forward (expanding window, no leakage) using the same
   `HuberRegressor` + recency-weighting primitives `ml/calibration.py` already uses for the
   IBJA→Tanishq fit (`_fit_robust`/`_recency_weights`, reused directly rather than reimplemented) —
   never a single full-period fit. Dates before the first 30-pair fold (pre-overlap, or the first ~30
   overlap days) use that first fold's frozen parameters: a fixed, non-adaptive transform, so no
   later information leaks backward into earlier proxy values.

### Validation (walk-forward OOS only, n=227 real IBJA days, 2022-01-19 onward)

| Metric | Value |
|---|---|
| MAE | Rs 1,090.88 /10g (0.98%) |
| Level correlation | 0.9988 |
| **Direction agreement** (n=225 real new-value days) | **67.1%** [95% CI 60.7–72.9%, Wilson] |
| Baseline (always-predict-up) | 54.2% |

The direction-agreement CI's lower bound (60.7%) sits clearly above the always-up baseline (54.2%) —
the proxy's day-to-day direction is a genuinely useful (not just level-accurate) training signal, not
only a smoothed level series that happens to look right. `mae_rs_per_10g`/`correlation_level` above
are reported for completeness; per GG's spec, direction agreement is the metric that actually gates
usefulness for M2/M4 pretraining.

One numerical caveat, stated plainly rather than silently absorbed: `_fit_robust`'s `HuberRegressor`
failed to converge on the very first walk-forward fold (30 samples) and fell back to weighted OLS —
this is the exact small/near-collinear-window failure mode `ml/calibration.py`'s own docstring already
documents and designed the fallback for; not a new issue introduced here.

### Indian demand calendar extended (`ml/calendar_events.py`)

Added, additively, alongside the existing festival-window logic (Akshaya Tritiya/Dhanteras/
Diwali/Navratri, untouched):
- `get_wedding_season_info` — two broad solar-calendar windows (mid-Nov–mid-Feb, mid-Apr–mid-Jul)
  approximating Indian wedding-demand seasonality. Explicitly **not** a muhurat calendar (which needs
  a panchang and shifts yearly with the lunar calendar) — a coarse seasonality flag only.
- `get_budget_window_info` — ±12/14 days around the fixed Feb 1 Union Budget date, when gold duty
  changes are announced more often than any other time of year (`data/duty_events.json`:
  2019-07-06/2021-02-02/2022-07-01 are Budget-adjacent).
- `get_duty_event_proximity` — production version of the `duty_change_active` helper that previously
  existed only inside `tests/test_duty_and_calendar.py` (test-local, never wired into any feature).
- `get_demand_calendar_features` — single entry point combining all four flag groups for future
  feature-matrix wiring (not yet wired into `ml/features.py`'s `FEATURE_COLS` — same "accumulate
  before wiring" discipline `docs/FEATURE_STORE.md` documents for `india_vix`).

### Not done by this ADR (explicitly out of scope)

- **FRED DFII10** (US 10-year TIPS real yield, the series GG's spec named specifically) needs a free
  FRED API key (signup required, no cost) — **listed as a GG action item**, not blocked on. The `TIP`
  ETF already in `ml.macro.TICKER_MAP` is an imperfect but already-wired interim proxy for the same
  real-rate-expectations signal.
- Wiring the proxy into `ml.direction.dataset` for actual M2 pretraining — that's M2's job, next.
- Wiring `get_demand_calendar_features` into `ml/features.py`'s live feature matrix.

## Consequences

- M2/M4 can now pretrain on 13.7 years of a validated, leakage-controlled proxy instead of ~3.7 years
  of real IBJA overlap alone, with a documented, honest ceiling on how much to trust it (67.1%
  direction agreement, not 100%).
- `data/history_seed_inr22k_proxy.parquet` is committed to the repo (like `data/ibja_rates.parquet`,
  unlike the CI-regenerated `data/macro_cache.parquet`) since it's a foundational, slow-changing
  training input, not something that needs regenerating every pipeline run. Refresh cadence: manual,
  via `python -m ml.inr_proxy --build`, whenever a new duty event lands in `data/duty_events.json` or
  the proxy needs extending to a later end date — no CI job wired up yet (M2/M4 will determine actual
  refresh needs once they consume this).
- The 1-in-5,014 roll-adjustment rate is worth re-checking once M4's Chronos-companion backtest runs
  against this data — a systematically-missed roll would show up there as an unexplained MAE spike
  clustered around month boundaries.

## Alternatives considered

- **Fit calibration on the full period, not walk-forward.** Rejected: would let 2026 IBJA data
  calibrate 2013 proxy values, which is look-ahead bias — the exact class of error ADR 023/027 already
  had to correct for the Tanishq calibration fit. Not worth reintroducing here.
- **A hardcoded COMEX futures-expiry calendar for roll detection**, instead of the GLD-divergence
  method. Rejected: contract codes/expiry conventions have changed historically and a hardcoded
  calendar needs maintenance; GLD-divergence is self-correcting (works the same way regardless of
  which contract is currently front-month) and needs no calendar at all.
- **Fetching an independent LBMA/spot gold benchmark** instead of GLD as the roll-detection reference.
  Rejected for now: not available free/simply via yfinance; GLD tracks spot gold closely enough
  (expense-ratio drag is ~0.4%/year, negligible at daily-divergence-detection resolution) and is
  already free and reliable via the same `yfinance` pipeline already in use.
