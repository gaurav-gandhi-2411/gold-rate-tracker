# ADR 040 — Why gold direction forecasts are noise: the diagnosis

**Status:** Accepted, 2026-09-23. Research finding; nothing user-facing changes. The direction signal
stays in shadow. R4 (ADR 039) is its last pre-registered test.

**Evidence:** `scripts/analysis_direction_diagnosis.py` at `bd0a2467`, run on GitHub Actions as run
**35870505399**. Report: `reports/direction_diagnosis_run_35870505399.json`. Datasets:
- **COMEX daily:** roll-adjusted GC=F, "up tomorrow?", 3,451 days (2013–2026), 3,201 scored.
- **INR:** real IBJA 22K, "up in 2 IBJA days?", 182 labelled days, 160 scored.

Every walk-forward is forward-only with an embargo ≥ the horizon.

## The answer in plain language (for GG)

**Mostly "there is genuinely little to find", plus a real limit on what our setup can see.**

1. **It is not underfitting.** Giving the models more power doesn't help. Simple models find almost
   nothing even on the data they were trained on. Powerful models memorise their training days
   perfectly and then do no better than a coin flip on new days.
2. **Overfitting happens, but fixing it only gets us back to "no edge".** The best-behaved models
   land exactly at the simple baseline, never above it.
3. **More data won't fix it at the daily horizon.** As training data grows from 500 to 2,000 to
   ~3,000 days, the models get *closer* to the baseline but never pass it.
4. **The price series itself has almost no day-to-day memory.** Gold's daily moves are close to a
   random walk. Their memory is small enough that even a perfect use of it would be right only about
   51% of the time. The little structure there is changes with the market's mood: short runs of
   momentum when it is calm, snapping back when it is volatile.
5. **Our pipeline can't see small edges.** When we planted a fake pattern of known strength in the
   real data, the pipeline reliably found it only when a perfect predictor would have been right
   about **60%** of the time (a 10-point edge). It missed patterns worth 55% or less. On the INR set
   (160 days) it missed even very strong planted patterns until they reached about 80%. So a *small*
   real edge could exist and we wouldn't see it. A large one almost certainly doesn't.
6. **No feature family helps on unseen days.** Price history, macro, India VIX and calendar were
   each tried alone. None beat the baseline; the best was price history, with a Brier skill of
   +0.001 and p = 0.40.

**What would change this verdict:**
- a genuinely new kind of information (e.g. Indian import premium or discount, MCX–COMEX basis,
  wedding-season demand data);
- a pre-registered test of the volatility-regime effect in D4;
- years more real IBJA days for the INR set;
- targets that do have structure: **how much** the price will move (R1), not which way.

## Evidence

### D1 + D6 — train vs validation, from simple to high-capacity (COMEX, n = 3,201 validation days)
| model | train acc | val acc | val majority | one-sided p (acc) | train Brier | val Brier | climatology Brier |
|---|---|---|---|---|---|---|---|
| majority | 51.7% | 51.5% | 51.5% | — | 0.2496 | 0.2500 | 0.2500 |
| logistic, strong L2 | 55.3% | 52.0% | 51.5% | 0.32 | 0.2445 | 0.2513 | 0.2500 |
| logistic | 56.7% | 52.0% | 51.5% | 0.34 | 0.2414 | 0.2595 | 0.2500 |
| GBM stumps | 56.7% | 51.6% | 51.5% | 0.46 | 0.2437 | 0.2510 | 0.2500 |
| GBM small | 76.4% | 50.5% | 51.5% | 0.82 | 0.1968 | 0.2660 | 0.2500 |
| GBM default | 98.8% | 50.5% | 51.5% | 0.80 | 0.0528 | 0.3037 | 0.2500 |
| GBM large | 100% | 51.1% | 51.5% | 0.65 | 0.0000 | 0.4073 | 0.2500 |
| random forest | 100% | 50.7% | 51.5% | 0.75 | 0.0351 | 0.2608 | 0.2500 |

On the INR set (n = 160; majority 59.4%), training accuracy rises from 63% to 100% with capacity.
Validation stays at 49–56%, below the majority, in every model. No model's validation Brier beats
climatology on either dataset.

### D2 — learning curves (COMEX, fixed last 1,000 days from 2022-09-29)
Validation Brier as the training window grows 500 → 1,000 → 2,000 → all:
- logistic: 0.2752 → 0.2604 → 0.2523 → 0.2537
- small GBM: 0.2807 → 0.2679 → 0.2583 → 0.2587

Climatology is 0.248–0.249 throughout. The models approach the baseline and never cross it, and
accuracy never significantly exceeds the majority (one-sided p ≥ 0.82).

### D3 — signal injection (planted signal of known strength q; 5 seeds)
With probability q, a day's real label is replaced by a deterministic function of that day's
features. A perfect predictor then scores ≈ 0.5 + q/2. "Detected" means one-sided p < 0.05 vs
majority.

| set / signal / features | q = 0.05 (oracle ≈ 52.5%) | q = 0.1 (≈ 55%) | q = 0.2 (≈ 60%) | q = 0.4 (≈ 70%) |
|---|---|---|---|---|
| COMEX, linear, all features | 0/5, 0/5 | 1/5, 0/5 | 4/5, 4/5 | 5/5, 5/5 |
| COMEX, linear, stationary features only | 0/5, 0/5 | 0/5, 1/5 | 5/5, 5/5 | 5/5, 5/5 |
| COMEX, nonlinear (interaction) | 0/5, 0/5 | 0/5, 0/5 | 3/5, 5/5 | 4/5, 5/5 |
| INR (n = 160), level signal | — | 0/5, 0/5 | 0/5, 1/5 | 1/5, 1/5 (q = 0.6: 5/5, 5/5) |
| INR, change signal (levels-only features cannot express it) | — | 0/5, 0/5 | 0/5, 0/5 | 0/5, 0/5 (q = 0.6: 0/5) |

Cells are logistic, then small GBM. At q = 0.2 the models realise only 52.5–55.5% accuracy against
an oracle of ~60%. Removing the price-*level* features improves recovery (logistic 52.5% → 53.4%;
GBM 53.5% → 55.5%), so the level features cost some sensitivity.

### D4 — predictability of the series itself
| series | n | lag-1 autocorr | Ljung-Box p (10 lags) | ceiling from lag-1 | variance ratio, 20 d (p) |
|---|---|---|---|---|---|
| COMEX daily | 3,451 | −0.034 | 0.052 | ~51% | 0.83 (0.15) |
| COMEX, low-volatility days | 1,716 | +0.008 | 0.052 | ~50% | **1.36 (0.003)**, momentum |
| COMEX, high-volatility days | 1,716 | −0.043 | 0.052 | ~51% | **0.64 (0.013)**, reversal |
| INR proxy daily | 3,637 | −0.129 | < 0.001 | ~54% | 0.68 (0.008) |
| real IBJA daily | 252 | +0.050 | 0.08 | ~52% | 1.22 (0.44) |

"Ceiling from lag-1" is the sign-prediction accuracy that lag-1 autocorrelation alone allows,
0.5 + arcsin(ρ)/π, whichever sign is exploited.

The proxy's strong reversal does **not** appear in real IBJA. It is most likely the proxy's own
measurement noise (noise creates negative autocorrelation), not a tradable pattern.

Sub-periods (COMEX, 2013–17 / 2018–21 / 2022–26): lag-1 autocorrelation −0.05 / −0.01 / −0.04. None
is stable enough to trade.

### D5 — which feature families carry signal on unseen days
- **One family only** (strong-L2 logistic and stumps, COMEX): price history BSS +0.001 (p = 0.40),
  macro −0.001, India VIX −0.001, calendar −0.002. None beats climatology.
- On INR, every family scores below the majority.
- **Permutation within 63-day windows** (COMEX, logistic): price history ΔBrier +0.007
  (95% CI +0.003 to +0.010, all 3 repeats). India VIX is borderline (+0.001, lower CI bound 0.000
  in 2 of 3 repeats); macro and calendar CIs include 0. With the small GBM, nothing is significant.
- The model does lean on recent price moves, but not enough to beat climatology.

*Methodological correction:* a first run shuffled each family across the whole 2014–2026 test
period. That mixes price levels from different years, so it measured fragility to regime shifts,
not signal: logistic ΔBrier was +0.08 on a model already worse than climatology. It was replaced by
the within-window shuffle above.

### Reproducibility note
The INR results are identical across two runs. The COMEX dataset is re-downloaded from yfinance on
every run, and small history revisions move results slightly (logistic 51.98% vs 51.95%, GBM
default 51.0% vs 50.5%; identical row counts). The conclusions don't change. Future COMEX work
should freeze a dataset snapshot as a run artifact.

## Consequences
- Direction stays in shadow. R4 (selective direction, ADR 039) is its last pre-registered test. The
  model effort moves to R1 (range/volatility), R2 (nowcast) and R3 (buyer policy in rupees).
- "Our models can't find it" is now bounded: on COMEX daily, edges below ~5 points of oracle
  accuracy are invisible to this pipeline, and on the 160-day INR set almost anything is.
- Level features overfit. New direction or volatility features should be stationary (returns,
  changes, volatility-scaled).
