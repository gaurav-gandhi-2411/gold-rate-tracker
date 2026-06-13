# Direction Signal — Honest Status

*Auto-measured by `ml/direction/evaluate.py` (weekly via `.github/workflows/eval-direction.yml`). Latest run embedded below; the live numbers are in `data/direction_baseline.json` and the trend in `data/direction_eval_history.jsonl`.*

## Verdict (as of 2026-06-13, 93 OOS folds)

**DARK — no calibrated direction probability is shown to users.** No model beats the always-up base rate out-of-sample with significance. This is the gate (`ml/direction/gate.py`) working as designed (ADR 019, honest-baseline reporting ADR 005), not a failure.

| Model | OOS accuracy | Brier | vs always-up baseline | Significant (p<0.05)? |
|---|---|---|---|---|
| Always-up base rate | **53.8%** | 0.462 | — | — |
| Logistic (primary) | 49.5% | 0.267 | below on accuracy | no (p=0.42) |
| LightGBM | 52.7% | 0.282 | below on accuracy | no (p=1.0) |
| Persistence | 46.2% | 0.538 | below on both | no |

- **N = 93** expanding-window walk-forward folds, dataset 2025-01-09 → 2026-06-05 (113 labelled rows from the PIT feature store).
- The logistic model has a **better Brier** (0.267 vs 0.462 — its probabilities are better calibrated) but a **worse 0.5-threshold accuracy** and the difference vs the baseline is **not significant** (14 discordant folds, p=0.42). The gate requires *all four* of: ≥30 folds, significance, lower Brier, AND higher accuracy. Two fail. So: dark.
- **Regime note:** the base rate here is **53.8%**, not the 70% cited in ADR 019. The dataset now spans the Apr–Jun 2026 correction, so the regime is more balanced — one of ADR 019's two re-evaluation conditions (a mixed-regime fold set) is now closer to met. The model still does not beat even this lower bar.

## Website-ready copy (same honest voice as the flat-hold section)

> **Can it call the direction?** Not yet — and we won't pretend otherwise. We test a
> direction model every week against the simplest honest benchmark: "gold usually goes
> up, so just say up." Over the last 93 days of out-of-sample checks, our model lands at
> ~49–53% accuracy versus that benchmark's ~54%. It does not beat the benchmark, and the
> gap isn't statistically meaningful. So we don't show a "% chance higher" number, because
> any number we showed would be guessing. We keep measuring as more data comes in and will
> turn this on only when a model genuinely clears the bar.

(Shorter pill variant: *"Direction signal: off — no model beats the base rate yet (~50% vs ~54% over 93 days). Revisit as data grows."*)

## Re-run cadence

- **Weekly** (Mon 04:00 UTC) and on `ml/direction/**` changes, via `eval-direction.yml`.
- Each run rewrites `data/direction_baseline.json` (latest, incl. the embedded `gate` verdict) and appends one record to `data/direction_eval_history.jsonl` (the trend).
- Expected to stay dark until ~300 labelled rows (est. 2027) and/or a genuinely discriminative signal emerges (ADR 019 / ADR 020). The gate flips automatically — no manual edit — when a model clears all four conditions.
