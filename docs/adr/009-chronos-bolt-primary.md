# ADR 009: Chronos-Bolt-Tiny as primary forecaster (replacing LightGBM)

**Status:** Accepted, 2026-05-19

## Context

Phase 1 audit (2026-05-18) found the LightGBM ensemble 34.6% worse than the naive
baseline on a 69-fold walk-forward backtest (MAE ₹225.33 vs naive ₹167.36). Root causes:

1. **92% synthetic training data.** `data/history_seed.json` contained 444 synthetically
   generated rows created before live scraping was established. These rows contaminated
   the training distribution and provided false coverage. Archived in PR B (ADR 010).

2. **Insufficient real-data volume for supervised learning.** At ~70 real readings, there
   is not enough signal for the lag/rolling features that LightGBM requires. The model
   learns residual patterns from synthetic data that do not generalise to live prices.

3. **Feature engineering debt.** Regime, MCX basis, and WANDB tracking were partially
   implemented and later removed (PRs B–D). Each removal reduced signal without replacing it.

In this data regime (~25–100 real daily readings accumulating at 4 readings/day), a
**zero-shot foundation model** that requires no training data is the appropriate choice.
Chronos-Bolt-Tiny (Amazon, 2024) is a 9M-parameter T5-based model pre-trained on 100,000+
time series. It produces calibrated probabilistic forecasts from a short context window
without any fine-tuning.

## Decision

Adopt **Chronos-Bolt-Tiny** (`amazon/chronos-bolt-tiny`, pinned revision
`a0e552de83495b5c28c14c71c374f3e33280b340`) as the primary 5-day forward forecaster for
the IBJA-916-PM INR/g series.

**Deployment strategy (phased):**

- **PR E (this ADR):** Parallel probe path. Chronos runs every CI cycle and writes to
  `data/chronos_probe.json`. Legacy LightGBM continues to write `data/forecast.json`.
  `FORECAST_ENGINE=legacy` (default). No live-forecast change.

- **PR F:** Walk-forward backtest at h=5 using Chronos. Establishes the new MAE baseline.

- **PR H:** `FORECAST_ENGINE=chronos` flipped. Legacy path removed. Chronos writes
  `data/forecast.json` directly. LightGBM deleted.

**Implementation details:**

- Input: IBJA-916-PM daily series (INR/g), all available history (currently ~25–30 days).
- Output: 5-step ahead p10/p50/p90 quantile forecast, IBJA level (INR/g).
- Calibration: `ml/calibration.py` HuberRegressor converts IBJA-level → Tanishq-22K level.
  Calibration is `valid: false` at PR E merge; applied automatically when 30 pairs exist.
- Context length: Chronos-Bolt supports variable context length. We feed the full available
  history; no truncation or padding. Minimum 8 observations enforced by `ml/chronos_forecast.py`.
- Inference: CPU-only. `torch==2.12.0+cpu` in `ml/requirements-inference.lock`.
  Cold-start ~20s (first CI run after cache miss); subsequent runs ~5s (cached weights ~8.65 MB).
- Revision pinning: SHA `a0e552de83495b5c28c14c71c374f3e33280b340` ensures reproducible
  inference regardless of future model updates on HuggingFace Hub.

## Alternatives considered

| Alternative | Reason not chosen |
|---|---|
| **LightGBM (keep as primary)** | 34.6% worse than naive; root cause is data quality (synthetic contamination), not model design. Kept as legacy fallback only during PR E–G transition. |
| **TFT (Temporal Fusion Transformer)** | Retired in PR B. Requires ~2,000 real readings before outperforming naive on this target. Data gate would not open until late 2027 at current accumulation rate. |
| **N-BEATS** | Retired in PR B. Requires ~1,000 real readings. Same data-gate problem. |
| **Chronos (full-size, non-Bolt)** | `amazon/chronos-t5-small` is 46M params / 186 MB. Accuracy improvement over Bolt-Tiny is marginal at short context lengths. Latency and CI cost are significantly higher. Upgrade path: change `CHRONOS_BOLT_TINY_MODEL_ID` constant. |
| **Chronos-2** | Multivariate; requires exogenous features. Deferred as Phase 4 upgrade if 5d MAE plateaus after Chronos-Bolt establishes a baseline. |
| **NeuralForecast (NHITS/TimesNet)** | Would require model training (defeats the zero-shot advantage at this data volume). Considered for Phase 4 if real-data corpus reaches 500+ readings. |

## Consequences

**Good:**
- Zero-shot: no training data required. Immediately operational on 25–30 real readings.
- Calibrated probabilistic output: p10/p50/p90 quantiles enable honest prediction intervals
  in the PWA (PR H wires these into `forecast.json`).
- 8.65 MB model weights: GitHub Actions runner cache keeps inference fast after first download.
- Probe-only mode in PR E validates the full path (load → forecast → calibration → JSON output)
  before any live-forecast change.

**Bad / risks:**
- Chronos-Bolt-Tiny is a univariate model. It cannot use macro features (USD/INR, crude oil,
  DXY) that the LightGBM ensemble used. This is acceptable at current data volume but may
  limit accuracy at longer horizons.
- Cold-start latency ~20s on first CI run (GitHub Actions runner, no cache). Acceptable for
  a 6-hour CI cadence; `actions/cache` mitigates to ~5s after the first run.
- Revision pinning means we will not automatically benefit from future model improvements.
  Deliberate policy: stability over freshness. Update the SHA in a dedicated chore commit
  when a new revision improves accuracy on our backtest.
- Gold is close to a random walk over 5-day horizons. Chronos may not beat the naive
  baseline on MAE either. This is expected and will be measured honestly in PR F.
  ADR 005 (honest baseline reporting) applies.
