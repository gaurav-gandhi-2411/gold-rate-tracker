# Direction Signal — Honest Status

*Auto-measured by `ml/direction/evaluate.py` (weekly via `.github/workflows/eval-direction.yml`). Latest run embedded below; live numbers are in `data/direction_baseline.json` (per-horizon, with embedded gate verdicts) and the trend in `data/direction_eval_history.jsonl`.*

## Verdict (as of 2026-08-05)

**DARK at every horizon and for both signal types.** No model beats the always-up base rate out-of-sample with significance, so neither a calibrated direction probability nor a buy/wait/sell timing signal is shown to users. This is the gate (`ml/direction/gate.py`) working as designed (ADR 019, honest-baseline ADR 005), not a failure.

We evaluate two short horizons, both leak-free (features at day *t*, label from IBJA strictly after *t*):
- **h=1** — direction to the next IBJA trading day.
- **h=2** — direction two IBJA trading days out.

Calibration (Expected Calibration Error, ECE) is the **primary** quality metric: a well-calibrated "58% up" is honest and useful even at base-rate accuracy. The gate still requires beating the base rate *with significance* before anything ships.

| Horizon | N folds | Base rate (always-up) | Model | OOS accuracy | Brier | ECE | Significant (p) | Prob gate | Timing gate |
|---|---|---|---|---|---|---|---|---|---|
| **h=1** | 130 | 50.8% | logistic | 48.5% | 0.264 | 0.134 | no (p=0.63) | **dark** | **dark** |
| | | | lightgbm | 50.8% | 0.292 | 0.208 | no | | |
| **h=2** | 128 | 57.8% | logistic | 60.9% | **0.242** | **0.038** | no (p=0.45) | **dark** | **dark** |
| | | | lightgbm | 57.8% | 0.265 | 0.190 | no | | |

Dataset: 150 labelled rows (h=1) / 148 (h=2), 2025-01-09 → 2026-08-03, from the PIT feature store. n_test_folds = kept rows − min_train_size(20).

### What the numbers say (honestly)
- **h=1 is both inaccurate and poorly calibrated.** Logistic accuracy (48.5%) is *below* the base rate, and ECE 0.134 means its probabilities are off by ~13pp on average. Nothing to ship.
- **h=2 is the interesting one.** The logistic model is **well-calibrated** (ECE 0.038, comfortably below our 0.10 bar — improved further now that n has grown) and its **Brier (0.242) beats the base rate's (0.421)** — its probabilities are honest. But its 0.5-threshold **accuracy (60.9%) does not beat the base rate (57.8%) with significance** (p=0.45). So a calibrated probability exists, but it carries no *statistically demonstrated* edge over "gold usually goes up." Per the gate, that stays dark.
- This is exactly the case the gate is built for: a probability can be well-calibrated yet still fail to beat the trivial baseline with significance. We do not ship calibration alone as if it were an edge.
- n grew from 93/92 folds to 130/128 folds in this run — see "Data-accumulation bug" below for why that jump happened all at once rather than gradually.

## Website-ready copy (same honest voice as the flat-hold section)

> **Can it call tomorrow's move?** Not yet — and we won't fake it. Every week we test
> next-day and 2-day direction models against the simplest honest benchmark: "gold
> usually rises, so just say up." Over ~130 out-of-sample days, the models land around
> 49–61% accuracy versus that benchmark's 51–58%. They don't beat it, and the gap isn't
> statistically meaningful. The 2-day model's *probabilities* are actually fairly honest
> (well-calibrated), but honest probabilities with no edge over "usually up" aren't worth
> showing as a signal. So we keep it off and keep measuring as data grows.

(Pill variant: *"Direction signal: off — no model beats the base rate yet (h1 ~49% vs 51%, h2 ~61% vs 58%). Revisit as data grows."*)

## The two gates (both dark)

1. **Probability gate** (`decide_direction_signal`) — ships a "% up" only when, OOS: ≥30 folds, significant (p<0.05), Brier < base-rate Brier, accuracy > base rate, AND ECE ≤ 0.10. Currently fails on accuracy + significance at both horizons.
2. **Timing gate** (`decide_timing_signal`, *stricter* — a buy/wait/sell signal implies an action) — requires the probability gate to pass AND ≥60 folds, accuracy edge ≥5pp, ECE ≤ 0.05, p < 0.01. Dark by definition while the probability gate is dark.

## Majority-class collapse (G3, session dated 2026-08-28)

Beyond "not significant yet" above, the logistic model's own *predictions*
— not just the labels' base rate — have stopped varying. In its most
recent 30 folds, at **both** horizons, it predicted "up" every single
time, exactly matching the always-up baseline's own prediction every fold:

| Horizon | N folds | Trailing-30 "up" fraction | Majority-class collapse |
|---|---|---|---|
| h=1 | <!--METRIC:data/direction_baseline.json#horizons.h1.n_test_folds:int-->148<!--/METRIC--> | <!--METRIC:data/direction_baseline.json#horizons.h1.trailing_30_fold_up_fraction:pct1-->100.0%<!--/METRIC--> | <!--METRIC:data/direction_baseline.json#horizons.h1.majority_class_collapse:raw-->True<!--/METRIC--> |
| h=2 | <!--METRIC:data/direction_baseline.json#horizons.h2.n_test_folds:int-->146<!--/METRIC--> | <!--METRIC:data/direction_baseline.json#horizons.h2.trailing_30_fold_up_fraction:pct1-->100.0%<!--/METRIC--> | <!--METRIC:data/direction_baseline.json#horizons.h2.majority_class_collapse:raw-->True<!--/METRIC--> |

(`majority_class_collapse` fires at a trailing-30-fold fraction >= 0.95
either direction — see `ml/direction/evaluate.py`'s
`MAJORITY_CLASS_COLLAPSE_THRESHOLD` docstring for the full reasoning.
Computed and written by `ml.direction.evaluate` every eval run; the table
above is live, not hand-typed.)

**Why this matters for the significance test above:** a model that always
agrees with a trivial baseline can never generate a *new* discordant pair
against it. This is not just "not yet significant" — the gate's p-value is
currently **structurally frozen** at either horizon, regardless of how many
more weekly runs pass, for as long as this trailing-window behavior
persists.

**The arithmetic** (point-in-time, measured 2026-08-28 — a derived
calculation across multiple discordant-pair counts and an extrapolation
assumption, not a single stored field, so it is presented as a dated
analysis rather than a live-injected number; the `n_test_folds`/
`trailing_30_fold_up_fraction`/`majority_class_collapse` figures above
*are* live-injected and will update on their own): at the observed
discordant-pair counts (h1: b=7, c=10, 17 of 147 folds; h2: b=10, c=6, 16
of 145 folds), reaching p<0.05 at the *current* b:c ratio held constant
would need roughly 125 discordant pairs (h1) / 65 (h2) — about 108 / 49
*more* than exist today. At the full-history average accrual rate (0.116
discordant pairs/fold for h1, 0.110/fold for h2), that is **~934 more
folds for h1 (~18.0 years at this eval's weekly cadence) and ~444 more
folds for h2 (~8.5 years)**. But the full-history average is not the
*current* rate: in the most recent 30 folds, the accrual rate is 0/fold at
both horizons — the model has matched the baseline every single time. Under
that behavior, the gate cannot reach significance at *any* finite number of
additional folds; it would first need to resume predicting "down" at least
occasionally. **The gate is not "waiting for more data" right now — it is
structurally unresolvable until the model's own behavior changes.**

This finding does not change the verdict (still DARK, correctly) and does
not modify the model or the gate logic — it is a diagnostic addition,
recorded so a future reader of this doc (or the weekly eval output) sees
*why* the p-value looks frozen rather than reading a flat "p=0.63, not
significant" as "inconclusive, check back later."

## Re-run cadence

- **Weekly** (Mon 04:00 UTC) and on `ml/direction/**` changes, via `eval-direction.yml`.
- Each run rewrites `data/direction_baseline.json` (both horizons, with embedded gate verdicts) and appends one record to `data/direction_eval_history.jsonl`.
- Expected to stay dark until the regime offers genuine directional uncertainty and a model earns its gate. Both gates flip automatically — no manual edit — when the conditions are met.

## Revisit trigger — data accumulation, not feature/target iteration (as of 2026-08-05)

A 2026-07-17 diagnostic (Monte Carlo power sim matching the actual sign-test gate)
found the DARK verdict is explained by sample size, not missing signal: at n=93
folds, only an accuracy edge of **~21 percentage points** is reliably detectable
(80% power) — far beyond anything plausible for daily direction on a globally
arbitraged commodity. Two enrichment experiments (momentum/volatility features,
a "relative cheapness vs. trailing mean" reframed target — both committed at
`ml/experiments/direction_enrichment.py`) were tested against the unmodified gate
and came back negative, consistent with that power ceiling. **Conclusion: do not
iterate further on features or targets until n grows.** No agentic feature/target
search either — n is nowhere near enough for that to be meaningful.

### Data-accumulation bug (2026-06-07 → 2026-08-05, ~8 weeks) — found and fixed

The 2026-07-17 revisit-date table below (2026-10-23 for n=250) turned out to be **invalid**,
not just optimistic. Root cause: `ml.feature_store.append_snapshot` kept exactly one row per IST
calendar day, first-writer-wins. `check-price.yml` runs 8x/day; the run that first crossed IST
midnight (~00:40–02:30 IST) routinely captured *before* IBJA's own daily publish (~17:00 IST) and
permanently locked in the prior day's close for that `as_of_date` — every later same-day run,
which *would* have had the fresh reading, was silently a no-op. `ml.direction.dataset` correctly
excludes any row where `ibja_pm_916_asof_date < as_of_date` as a leakage guard, so **every one of
these rows was silently dropped** from the direction-model training set: n stayed frozen at 93
h1 folds across five weekly eval runs (2026-07-16 → 2026-07-27) while the raw feature-store parquet
kept growing normally underneath it (113 → 171 rows). `ml.notifications` trigger T10 — the guard
built specifically to catch capture gaps — stayed green the entire time, because a row genuinely
landed every day; T10 checks that *something* arrived, not whether it was usable. This is why the
2026-07-17 "verified capture rate" audit below, which measured raw row arrival, did not catch it.

**Fixed 2026-08-05:**
- `ml.feature_store.append_snapshot` now allows one same-day *upgrade*: once a later same-day
  capture has the genuine same-day IBJA reading, it replaces a stale same-day row (never
  downgrades). See its docstring for the full mechanism.
- `ml.feature_store_backfill.repair_stale_ibja` repaired 38 of the 51 affected historical rows —
  the ones with a genuine same-day IBJA publish already sitting in `ibja_rates.parquet` (IBJA's own
  capture job was never broken; only the feature-store join was). This was a same-repo join
  repair, not a re-fetch of revised third-party data, so it's fully PIT-honest. The remaining 13
  were genuine non-trading days (weekends) or not-yet-published — correctly excluded, not a bug.
  Macro backfill via yfinance for the gap was considered and **declined**: `feature_store_backfill.py`
  already documents that yfinance returns revised adjusted closes, not the value known at capture
  time, so it cannot honestly reconstruct that window — and it wasn't needed anyway, since the
  affected rows' macro/Tanishq/calendar fields were already captured correctly live; only the IBJA
  join was wrong.
- New guard: `ml.notifications` trigger **T13** (`_check_t13_usable_snapshot_stall`) fires once per
  IST day when the most recent *usable* (same-day-IBJA) snapshot is ≥2 calendar days old,
  independent of T10. T10 answers "did a row land"; T13 answers "is the dataset actually growing."
  See `docs/RUNBOOK.md`'s matching section for the full incident writeup.
- The repair immediately recovered the dataset from 113 to 150 kept rows (93 → 130 h1 folds, 92 →
  128 h2 folds) in the 2026-08-05 eval run — see the Verdict table above.

**Verified capture rate** (audited 2026-07-17, still accurate for *raw* capture): the feature store
held 152 rows at a genuine **1.00 raw snapshot/calendar-day** rate (a live 2026-07-13→07-15 gap was
bug #4, `bot-pr-sync` failing GH006 pushes, fixed by PR #183 — unrelated to the staleness bug above).
What that audit did not measure, and what the revisit dates below correct for, is that raw capture
and *usable* capture are different rates: IBJA only publishes on trading days (~5/7), so even with
the capture-timing bug fixed, at most ~5/7 of raw-captured days become usable rows. Measured directly
from the post-fix `live_pit` window (2026-06-07 → 2026-08-05, 60 calendar days): 57 raw rows (0.95/day,
matching the earlier audit) but only 38 usable-after-repair rows (**0.63 usable rows/calendar-day**) —
that is the rate the revisit dates below are computed from.

**Computed revisit dates** (from n=150 / 130 h1 folds as of 2026-08-03, at the verified 0.63 usable
row/calendar-day rate):

| Target n (kept rows) | h1 test folds at that n | Rows needed | Revisit date |
|---|---|---|---|
| 250 | ~230 | 100 | **2027-01-07** |
| 300 | ~280 | 150 | **2027-03-27** |

(Theoretical ceiling if usable capture ever tracked raw capture 1:1 — not achievable under IBJA's
known publish cadence, since it doesn't publish on weekends, but useful as a bound: 2026-11-16 /
2027-01-07 respectively.)

**What to re-run at each checkpoint:**
1. `python -m ml.direction.evaluate` (already runs weekly, unmodified gate — no action needed, just read the new `data/direction_baseline.json`).
2. `python -m ml.experiments.direction_enrichment` (committed, not wired into production) — re-check whether the momentum-feature and relative-cheapness variants clear the gate at the larger n. At n≈250, edges ≥~8-10pp become detectable per the power sim (vs. ~21pp at n=93) — still a high bar, but no longer categorically unprovable.
3. Only if either shows a **real, gate-clearing** edge: revisit the Phase 3 agentic-search design (held-out set, purged walk-forward CV, multiple-testing correction) — not before.
