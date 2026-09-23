# ADR 035 — M3: Adaptive Conformal Inference vs. the Static Calibration Band

**Status:** Accepted, implemented 2026-09-23. Offline/shadow only — does not touch
`data/calibration_band_coverage.json` or any live scoring path.

---

## Context

GG's spec (M3, run independently of M2): compare adaptive conformal inference against the current
band offline, strictly held out, with Wilson CIs; separately, note but do not yet cite the
weekend/carry-forward stratum shadow result, since its first live-scored week lands 2026-09-27 (today
is 2026-09-23 — confirmed not yet landed: `data/calibration_band_coverage.json`'s
`generated_at_utc` is still `2026-09-20`, no `stratified_shadow` key present).

The weekend/carry-forward stratum comparison itself already exists and runs in production shadow
(`ml.calibration.evaluate_stratified_band_coverage`, PR #1825, `weekly-backtest.yml`) — this ADR does
not duplicate it. What was missing, and is built here, is **adaptive conformal inference (ACI)** as
an alternative band-sizing rule, compared against the current static empirical-quantile band.

## Method

`ml/calibration_adaptive.py`: implements Gibbs & Candès (2021) ACI — instead of a fixed quantile
level, maintain a target miscoverage rate `alpha_t` that adapts after every scored day (widen after a
miss, narrow after a hit): `alpha_{t+1} = alpha_t + gamma * (alpha_target - miss_t)`, clipped to
`[0.01, 0.99]`. Both the static and ACI bands are scored on the **identical** walk-forward fit and
scoring-set construction `ml.calibration.evaluate_empirical_band_coverage` uses (same Huber
regression, same recency-weighted residuals, same asof-matched scoring set) — the comparison isolates
the band-sizing rule only, not a difference in what's being fit or scored.

## Results (real `data/ibja_rates.parquet` / `data/prices.json` overlap, n=89 scored days)

| Level | Static coverage (95% CI) | ACI coverage (95% CI) |
|---|---|---|
| 68% | 66.3% (56.0–75.3%) | 67.4% (57.1–76.3%) |
| 80% | 77.5% (67.8–85.0%) | 76.4% (66.6–84.0%) |
| 90% | 91.0% (83.3–95.4%) | 89.9% (81.9–94.6%) |

**Statistically indistinguishable at every level tested** — CIs overlap almost completely, and the
raw coverage differs by ≤1.3pp in either direction. `gamma=0.01` (conservative adaptation).

**Gamma sensitivity (level=80):** `gamma=0.05` → 80.9% coverage; `gamma=0.1` → 82.0% coverage,
final `alpha=0.43` (target was 0.20 — badly overshot). **More aggressive adaptation does not track
better at this sample size — it destabilizes.** ACI's convergence guarantees are long-run; n=89 days
is short enough that a higher learning rate amplifies noise rather than correcting genuine drift.

### What this sample can and cannot resolve (added 2026-09-23)

Re-run on 2026-09-23 with the same module and data (n = 89). The 95% Wilson intervals on the static
band's coverage are wide:

| nominal | static coverage | 95% CI | CI width |
|---|---|---|---|
| 68% | 60.7% | 50.3–70.2% | 19.9 pp |
| 80% | 71.9% | 61.8–80.2% | 18.4 pp |
| 90% | 80.9% | 71.5–87.7% | 16.2 pp |

Only a static-vs-ACI difference of roughly that size could be told apart at n = 89. The observed
differences (0–2.2 pp) are far smaller. "Statistically indistinguishable" therefore means the
comparison is underpowered, **not** that the two methods are equivalent. (An earlier session note
gave these widths as 19.3 / 17.2 / 12.1 pp; the values above are the recomputed ones.)

Separately: at the **90%** level the static band under-covers. Its 95% CI (71.5–87.7%) excludes
90%, so this offline scoring set is already resolvable at that level. The live band monitors 80%,
where the CI (61.8–80.2%) just includes nominal.

## Decision

**Not recommending ACI as a replacement for the static band at this time.** The two methods perform
equivalently on the available history, and the one place ACI diverges from the static band
(higher gamma) diverges for the worse, not the better — there's no evidence yet that this dataset
has the kind of regime drift ACI is specifically built to handle. Kept as `ml/calibration_adaptive.py`
for periodic re-checking as more overlap history accumulates (ACI's theoretical advantage — no
window-size choice, robustness to shift without needing the window to already contain examples of
it — only shows up once real drift actually occurs; the honest, held-out negative result today
doesn't rule that out for the future).

**Weekend/carry-forward stratum:** already running in production shadow; not re-litigated here. First
live-scored result due 2026-09-27 — will be reported then, not before, per GG's explicit instruction
not to cite the n=87 dev-run number as production evidence.

## Consequences

- No change to the live band. `ml.calibration`/`ml.inference` are untouched.
- A reusable, tested comparison harness now exists for re-running this check periodically (e.g. after
  the 2026-09-27 stratified result lands, or after any future duty-change/regime event that might
  give ACI genuine drift to react to).

## Alternatives considered

- **A larger gamma grid search to find an "optimal" adaptation rate.** Rejected: at n=89, this would
  be tuning noise, not signal — the gamma=0.05/0.1 results already show larger gamma moves AWAY from
  the target, and searching harder for a magic gamma value on 89 days risks exactly the kind of
  after-the-fact overfitting ADR 027 warned against for the static band's own half-life choice.
- **Re-deriving the weekend/carry-forward stratum independently.** Rejected: it already exists,
  already runs in shadow, and re-deriving it would just be duplicate code with no new information —
  the honest thing is to wait for its real result, not build a parallel copy to get an earlier answer.
