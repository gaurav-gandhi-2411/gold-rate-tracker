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
