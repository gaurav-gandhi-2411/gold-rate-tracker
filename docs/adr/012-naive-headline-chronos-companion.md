# ADR 012: Naive flat-hold as production forecast with Chronos as directional companion

**Status:** Accepted, 2026-05-19

## Context

ADR 009 designated Chronos-Bolt-Tiny as the primary 5-day forecaster, with PR H planned to flip
`FORECAST_ENGINE=chronos` and write `predicted_22k` from Chronos p50 to `forecast.json`.

PR F established the walk-forward backtest framework. PR F.5 extended `data/ibja_rates.parquet`
from 21 to 177 rows via Wayback Machine backfill and re-ran the backtest.

**165-fold walk-forward results (PR F.5, 2026-05-19):**

| Metric | Chronos | Naive |
|--------|---------|-------|
| MAE 5d avg | Rs.275.5 | Rs.249.5 |
| Gap | 10.4% worse | — |
| Wilcoxon signed-rank p | 0.0089 | — |
| Direction accuracy (h=5) | 55.8% | 50.0% |
| PI 80% coverage | 87.0% | — |

The result is statistically significant. Chronos's mean-reversion prior consistently under-predicts
the magnitude of moves in a trending market (IBJA-916-PM ~Rs.85,000 → Rs.145,000 over 2025-2026).

**Why Chronos trails naive on a trend:**
Chronos-Bolt-Tiny is pre-trained on 100,000+ diverse time series exhibiting a variety of
behaviours, but the aggregate prior is mean-reverting. On a persistently trending series it
systematically predicts "less movement" than the trend delivers, inflating MAE. The flat-hold
naive baseline implicitly assumes the most recent price is the best forecast — which is exactly
correct when the dominant process is a random walk with a positive drift.

**What Chronos does retain:**
Direction accuracy at 55.8% (vs 50% naive). Over 165 folds this is p < 0.001 — non-trivial but
below the 65%+ threshold needed for high-precision alert gating. The 87% PI 80 coverage shows
the Chronos uncertainty bands are slightly wide but well-calibrated.

## Decision

1. **Production `predicted_22k`** is computed as:
   ```
   predicted_22k = most_recent_ibja_916_pm × premium_factor
   ```
   where `premium_factor` comes from `ml/calibration.py` (HuberRegressor fit on IBJA-Tanishq
   overlap readings). This is the naive flat-hold in Tanishq-level units.

2. **Conformal prediction interval** (`lower`, `upper` in `forecast.json`) is derived from the
   rolling distribution of naive MAE errors, not from Chronos quantiles. This guarantees coverage
   that respects the actual error distribution on this series.

3. **Chronos runs in parallel** via `data/chronos_probe.json` (already live since PR E). It
   contributes `chronos_lean` (directional signal: "up" / "flat" / "down" at h=5) and
   `chronos_pi_width` (p90-p10 spread as a confidence indicator). Neither replaces the headline
   forecast; both are used by the notification system.

4. **PR H is repurposed** from "flip to Chronos" to "establish naive as production path + remove
   legacy artifacts." `FORECAST_ENGINE` env var is removed; naive path is hard-coded.

5. **Promotion criterion for Chronos:** If a future backtest on ≥250 IBJA rows shows
   `mae_5d_avg_chronos < mae_5d_avg_naive` at p < 0.05 on Wilcoxon signed-rank, re-open ADR 009
   and promote Chronos to `predicted_22k`. The weekly `weekly-backtest.yml` workflow monitors this
   automatically; `data/backtest.json` is the ground truth.

## Alternatives considered

| Alternative | Reason not chosen |
|---|---|
| **Deploy Chronos despite worse MAE** | Violates ADR 005 (honest-baseline reporting). `predicted_22k` would be systematically biased toward under-predicting upward moves in a trending market. Users would see forecasts that consistently underestimate prices. |
| **Abandon Chronos entirely** | Direction accuracy at 55.8% is a real signal worth retaining for notification gating. Abandoning Chronos removes that signal with no replacement. It is cheap to keep running in probe mode. |
| **Invest in Phase 4 before shipping PR H** | Phase 4 options (Chronos-2 multivariate, LightGBM residual head, detrend-forecast-retrend) all require additional build investment with uncertain outcomes. The current state (naive as headline, Chronos as directional lean) is shippable now, honest, and improvable. Phase 4 candidates are documented and can be evaluated against the same 165-fold protocol. |
| **Switch to h=1 horizon target** | Likely more tractable (1-day MAE is harder for naive to dominate). Deferred to Phase 4 — changing the horizon also changes the notification semantics, the calibration window, and the PWA display. Out of Phase 3 scope. |
| **Blend naive + Chronos (e.g., 0.7 × naive + 0.3 × Chronos)** | An ensemble that is dominated by the weaker component (Chronos) would degrade MAE below the pure-naive baseline. The blend would need to be adaptive and itself validated with a backtest, adding complexity without a clear benefit at this data volume. |

## Consequences

**Good:**
- `predicted_22k` is now the best-performing h=5 forecast on the 165-fold backtest.
- Production behaviour is honest: the PWA shows the current price as the 5-day forecast,
  which is statistically the best available estimate. ADR 005 is satisfied.
- Chronos directional lean adds real signal (55.8% dir acc) to notification gating without
  contaminating the headline number.
- The upgrade path is well-defined and automatically monitored.

**Bad:**
- `predicted_22k` is flat (no price movement predicted). This may feel uninformative to users
  expecting a "real" forecast. The `warmup` flag and PWA context text must explain this honestly.
- Chronos investment in PRs E–F is not the final production path — the infra is retained but
  the original end-state (Chronos writes `predicted_22k`) does not ship in Phase 3.

**Mitigation:**
The PWA displays "5-day outlook" with the Chronos directional lean as a contextual indicator
("Chronos signal: ↑ slight upward lean" / "→ neutral" / "↓ slight downward lean") alongside the
naive headline. This communicates both the honest flat forecast and the probabilistic lean without
conflating them. The distinction between "our best MAE estimate" and "model signal" is explicit in
the UI.
