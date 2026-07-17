# Direction Signal — Honest Status

*Auto-measured by `ml/direction/evaluate.py` (weekly via `.github/workflows/eval-direction.yml`). Latest run embedded below; live numbers are in `data/direction_baseline.json` (per-horizon, with embedded gate verdicts) and the trend in `data/direction_eval_history.jsonl`.*

## Verdict (as of 2026-06-13)

**DARK at every horizon and for both signal types.** No model beats the always-up base rate out-of-sample with significance, so neither a calibrated direction probability nor a buy/wait/sell timing signal is shown to users. This is the gate (`ml/direction/gate.py`) working as designed (ADR 019, honest-baseline ADR 005), not a failure.

We evaluate two short horizons, both leak-free (features at day *t*, label from IBJA strictly after *t*):
- **h=1** — direction to the next IBJA trading day.
- **h=2** — direction two IBJA trading days out.

Calibration (Expected Calibration Error, ECE) is the **primary** quality metric: a well-calibrated "58% up" is honest and useful even at base-rate accuracy. The gate still requires beating the base rate *with significance* before anything ships.

| Horizon | N folds | Base rate (always-up) | Model | OOS accuracy | Brier | ECE | Significant (p) | Prob gate | Timing gate |
|---|---|---|---|---|---|---|---|---|---|
| **h=1** | 93 | 53.8% | logistic | 49.5% | 0.267 | 0.121 | no (p=0.42) | **dark** | **dark** |
| | | | lightgbm | 52.7% | 0.282 | 0.211 | no | | |
| **h=2** | 92 | 62.0% | logistic | 60.9% | **0.243** | **0.086** | no (p=1.0) | **dark** | **dark** |
| | | | lightgbm | 58.7% | 0.269 | 0.199 | no | | |

Dataset: 113 labelled rows (h=1) / 112 (h=2), 2025-01-09 → 2026-06-05, from the PIT feature store.

### What the numbers say (honestly)
- **h=1 is both inaccurate and poorly calibrated.** Logistic accuracy (49.5%) is *below* the base rate, and ECE 0.121 means its probabilities are off by ~12pp on average. Its reliability curve is overconfident (a "74% up" bin came true 29% of the time). Nothing to ship.
- **h=2 is the interesting one.** The logistic model is **reasonably well-calibrated** (ECE 0.086, below our 0.10 bar) and its **Brier (0.243) beats the base rate's (0.380)** — its probabilities are honest. But its 0.5-threshold **accuracy (60.9%) does not beat the base rate (62.0%)**, and the difference is not significant (p=1.0). So a calibrated probability exists, but it carries no directional *edge* over "gold usually goes up." Per the gate, that stays dark.
- This is exactly the case the gate is built for: a probability can be well-calibrated yet still fail to beat the trivial baseline. We do not ship calibration alone as if it were an edge.

## Website-ready copy (same honest voice as the flat-hold section)

> **Can it call tomorrow's move?** Not yet — and we won't fake it. Every week we test
> next-day and 2-day direction models against the simplest honest benchmark: "gold
> usually rises, so just say up." Over ~90 out-of-sample days, the models land around
> 49–61% accuracy versus that benchmark's 54–62%. They don't beat it, and the gap isn't
> statistically meaningful. The 2-day model's *probabilities* are actually fairly honest
> (well-calibrated), but honest probabilities with no edge over "usually up" aren't worth
> showing as a signal. So we keep it off and keep measuring as data grows.

(Pill variant: *"Direction signal: off — no model beats the base rate yet (h1 ~50% vs 54%, h2 ~61% vs 62%). Revisit as data grows."*)

## The two gates (both dark)

1. **Probability gate** (`decide_direction_signal`) — ships a "% up" only when, OOS: ≥30 folds, significant (p<0.05), Brier < base-rate Brier, accuracy > base rate, AND ECE ≤ 0.10. Currently fails on accuracy + significance at both horizons.
2. **Timing gate** (`decide_timing_signal`, *stricter* — a buy/wait/sell signal implies an action) — requires the probability gate to pass AND ≥60 folds, accuracy edge ≥5pp, ECE ≤ 0.05, p < 0.01. Dark by definition while the probability gate is dark.

## Re-run cadence

- **Weekly** (Mon 04:00 UTC) and on `ml/direction/**` changes, via `eval-direction.yml`.
- Each run rewrites `data/direction_baseline.json` (both horizons, with embedded gate verdicts) and appends one record to `data/direction_eval_history.jsonl`.
- Expected to stay dark until the regime offers genuine directional uncertainty and a model earns its gate. Both gates flip automatically — no manual edit — when the conditions are met.

## Revisit trigger — data accumulation, not feature/target iteration (as of 2026-07-17)

A 2026-07-17 diagnostic (Monte Carlo power sim matching the actual sign-test gate)
found the DARK verdict is explained by sample size, not missing signal: at n=93
folds, only an accuracy edge of **~21 percentage points** is reliably detectable
(80% power) — far beyond anything plausible for daily direction on a globally
arbitraged commodity. Two enrichment experiments (momentum/volatility features,
a "relative cheapness vs. trailing mean" reframed target — both committed at
`ml/experiments/direction_enrichment.py`) were tested against the unmodified gate
and came back negative, consistent with that power ceiling. **Conclusion: do not
iterate further on features or targets until n grows.** No agentic feature/target
search either — n=93 is nowhere near enough for that to be meaningful.

**Verified capture rate** (audited 2026-07-17): the feature store
(`data/feature_store/snapshots.parquet`) held 152 rows, 93 h1-usable folds. A
live 2026-07-13→07-15 gap (3 missed calendar days) was traced to bug #4
(`bot-pr-sync` failing GH006 branch-protection pushes) — **not** a silent
regression of the Φ25 capture pipeline itself. Fixed by PR #183 (merged
2026-07-17T00:10 UTC); every `check-price.yml` run has succeeded since. The
clean 30-day window immediately before the outage (2026-06-13 → 2026-07-12) shows
the underlying rate is **1.00 snapshot/calendar-day (30/30, zero gaps)** — that is
the number the revisit dates below are computed from, not the outage-diluted
30-day blended rate (0.93/day).

**A new guard now watches this directly**: `ml.notifications` trigger **T10**
fires via `NTFY_TOPIC` (once per IST day) if the feature store goes
≥2 calendar days without a new snapshot — independent of price/forecast
staleness (T9), since the scraper can be healthy while the commit path is
broken (exactly what happened in bug #4). See `ml/notifications.py::_check_t10`.

**Computed revisit dates** (from n=152 / 93 h1 folds on 2026-07-17, at the
verified 1.0 row/day rate):

| Target n (feature-store rows) | h1 test folds at that n | Rows needed | Revisit date |
|---|---|---|---|
| 250 | ~230 | 98 | **2026-10-23** |
| 300 | ~280 | 148 | **2026-12-12** |

(Conservative fallback if the rate regresses toward the outage-blended 0.93/day:
2026-10-30 / 2026-12-22 respectively.)

**What to re-run at each checkpoint:**
1. `python -m ml.direction.evaluate` (already runs weekly, unmodified gate — no action needed, just read the new `data/direction_baseline.json`).
2. `python -m ml.experiments.direction_enrichment` (committed, not wired into production) — re-check whether the momentum-feature and relative-cheapness variants clear the gate at the larger n. At n≈250, edges ≥~8-10pp become detectable per the power sim (vs. ~21pp at n=93) — still a high bar, but no longer categorically unprovable.
3. Only if either shows a **real, gate-clearing** edge: revisit the Phase 3 agentic-search design (held-out set, purged walk-forward CV, multiple-testing correction) — not before.
