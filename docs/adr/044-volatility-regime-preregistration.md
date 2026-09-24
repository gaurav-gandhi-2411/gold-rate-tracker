# ADR 044 — Pre-Registration: "Momentum When Calm, Reversal When Volatile" on Unseen COMEX History

**Status:** Registered 2026-09-24. This ADR is written and committed **before any of the test data
is downloaded**, and the commit is the timestamp. It is analysis only. Nothing a user sees depends
on it.

**Tests:** ADR 040 D4's observation. On COMEX 2013–2026, lag-1 structure looked like momentum on
low-volatility days (variance ratio 1.36, p = 0.003) and reversal on high-volatility days (0.64,
p = 0.013).

---

## Why a new test is needed

ADR 040 found the effect by splitting 2013–2026 at the **full-sample** median of volatility. That
split uses information from the future. The same data both suggested the effect and measured it.
A fair test needs data that played no part, and a rule that can be applied on the day.

**Unseen data.** Every COMEX analysis in this repo starts at 2013-01-01
(`ml/direction/comex_daily.py`, `COMEX_START_DATE`; ADR 040 "2013–2026"). GC=F daily history from
**2000-08-30 to 2012-12-31** has not been used anywhere in this project.

## The frozen test

**Data (primary).** Yahoo Finance `GC=F` daily Close, `auto_adjust=True`, from 2000-08-30 to
2012-12-31 inclusive, downloaded once. The download is saved as
`reports/vol_regime_prereg_gcf_2000_2012.csv`, and its SHA-256 goes in the results. Every re-run
uses the saved file, never a new download (ADR 040's reproducibility note).

- r_t = ln(C_t / C_{t−1}) over consecutive rows of the file.
- Roll adjustments are **not** applied. GC=F rolls are small relative to daily moves; the
  robustness check below uses a roll-free series.

**Regime, known on the day.**
- vol_t = sample std (ddof 1) of the 20 returns r_{t−19} … r_t.
- Day t is **calm** if vol_t is below the median of the previous 252 values vol_{t−252} … vol_{t−1}.
  Otherwise it is **volatile**.
- The first 272 returns are warm-up and are not scored.

**Rule.** Predict whether r_{t+1} > 0.
- Calm: predict the same sign as r_t (momentum).
- Volatile: predict the opposite sign (reversal).
- r_t = 0 predicts "up".

**Baseline.** Always up (gold's drift makes this the right baseline, as in ADR 038/040). The rule
against 50% is reported as well.

**Primary test (P1), the only confirmatory one.**
- One-sided Diebold-Mariano on 0/1 loss: rule vs always-up.
- H1: the rule's mean loss is lower.
- Newey-West long-run variance with lag 5 (`diebold_mariano_test(..., horizon=6)`).
- α = 0.05. One hypothesis, so no multiplicity correction.

**Secondary (S1–S6).** Benjamini-Hochberg at q = 0.05 across all six. All one-sided.
- S1: lag-1 autocorrelation of returns after calm days > 0 (Fisher z).
- S2: lag-1 autocorrelation after volatile days < 0 (Fisher z).
- S3: on calm days only, the rule vs always-up (same DM test).
- S4: on volatile days only, the rule vs always-up (same DM test).

**ADR 040's own statistic, on the unseen data (S5–S6, also in the BH family).** D4's evidence was a
20-day variance ratio, not tomorrow's direction. Its lag-1 autocorrelation on calm days was
+0.008. D4 was built in a way that could manufacture a regime difference:

- the split used the full-sample median of 20-day volatility;
- each day's volatility window includes that day's own return;
- VR(20) was computed on the calm or volatile days concatenated, so 20-day sums run across regime
  boundaries.

S5 and S6 recompute D4 **exactly as it was built** (`scripts/analysis_direction_diagnosis.py`
`shard_series`: rolling 20-day std, full-sample median, Lo-MacKinlay VR(20) with the robust z)
on the 2000–2012 data:

- S5: VR(20) on low-volatility days > 1.
- S6: VR(20) on high-volatility days < 1.

This asks whether the statistic that suggested the effect replicates at all, whatever its
construction problems.

**Found before any data was touched: D4's construction produces the effect from pure noise.** On 40
simulated series of n = 3,450 with no structure at all:

| simulated null | calm-day VR(20), median [5–95%] | calm-day false positives at 5% | volatile-day VR(20), median [5–95%] | volatile-day false positives at 5% |
|---|---|---|---|---|
| iid normal | 1.16 [0.99, 1.46] | 17 of 40 | 0.91 [0.72, 1.04] | 8 of 40 |
| GARCH(1,1)-like, 0.05 / 0.93 | 1.13 [0.97, 1.35] | 17 of 40 | 0.94 [0.79, 1.17] | 3 of 40 |

The nominal false-positive rate is 5%. D4's 1.36 and 0.64 sit in or near these null ranges. **ADR
040's "momentum when calm, reversal when volatile" is therefore mostly, perhaps entirely, a
measurement artifact.** S5 and S6 stay in the family as registered, but they can confirm nothing
either way; `test_d4_construction_manufactures_a_regime_effect_from_pure_noise` documents this.
P1, which uses a regime known on the day and contiguous next-day outcomes, is the only valid test. P1 asks the product question: does the regime known on the day tell you
tomorrow's direction?

**Robustness (reported, not tested for confirmation).** P1 on `GLD` (roll-free, spot-tracking), from
its first day, 2004-11-18, to 2012-12-31. It is saved as
`reports/vol_regime_prereg_gld_2004_2012.csv`.

**Reported regardless of outcome:**
- n and effective n;
- accuracy for the rule, always-up and 50%;
- p-values;
- the share of calm days;
- the edge in accuracy points.

**What the outcomes mean, decided now:**
- **P1 significant:** the regime effect replicates out of sample on COMEX. It becomes a candidate for
  its own pre-registered test on INR/IBJA data. It still would not reach users without the ADR 036
  gate. For scale: ADR 040 D3 found the pipeline cannot see edges below ~60% accuracy on a
  160-day INR set.
- **P1 not significant:** ADR 040's regime observation does not replicate on unseen data. The
  "what could change the verdict" list loses this item.
- **No re-runs with other windows, thresholds or lags.** If a variant is tried later, it is
  labelled exploratory and cannot confirm anything on this data.

**Power, stated in advance.** There are about 3,100 returns, minus 272 of warm-up, so n ≈ 2,800.
The rule and always-up disagree on about half the days, so the loss difference has a std of about
0.71. With effective n ≈ 2,800, a one-sided 5% test has 80% power against a 3.3-point accuracy edge
over always-up. It has about 44% power against a 2-point edge. A null result therefore rules out large edges, not
small ones.

The runner is `scripts/analysis_vol_regime_prereg.py`, committed with this ADR. It downloads only
if the snapshot files do not exist.

## Result (run 2026-09-24, after this ADR merged as #1987)

**Provenance:** `reports/vol_regime_prereg_results.json` at `b9bcd7e8`. Snapshot SHA-256: GC=F
`2db071aa…`, stored in the results. The first download of these series happened after the
registration merged.

**Not confirmed. The regime rule is worse than always predicting "up".**

| test | n | result | one-sided p |
|---|---|---|---|
| **P1** rule vs always-up, GC=F 2001-10-04 → 2012-12-28 | 2,816 (effective 2,900) | 50.4% vs 54.2% (**−3.8 points**) | 0.999 |
| S1 lag-1 autocorrelation after calm days > 0 | 1,347 | −0.026 | 0.83 |
| S2 lag-1 autocorrelation after volatile days < 0 | 1,469 | +0.016 | 0.73 |
| S3 rule vs always-up, calm days | 1,347 | 48.1% vs 55.6% | 1.00 |
| S4 rule vs always-up, volatile days | 1,469 | 52.6% vs 53.0% | 0.58 |
| S5 D4's VR(20), low-volatility days > 1 | 1,535 | 0.93 | 0.70 |
| S6 D4's VR(20), high-volatility days < 1 | 1,534 | 0.84 | 0.13 |
| Robustness: P1 on GLD 2005-12-16 → 2012-12-28 | 1,770 | 49.7% vs 54.4% (−4.7 points) | 0.999 |

No secondary passes Benjamini-Hochberg. The calm share is 47.8%.

Every piece points the wrong way or nowhere:

- Calm days show slight reversal, not momentum.
- D4's own statistic does not reproduce: it gives 0.93 on calm days, where D4 reported 1.36.
- Of the effect D4 reported, one half fails to replicate (S5). The other half (S6, 0.84) is
  inside the null range its construction produces from pure noise.

**Consequence for ADR 040 (proposed, for GG to confirm — ADR 040 itself is not edited here):** "weak regime structure: momentum when calm, reversal when volatile"
should be withdrawn. It came from a measurement construction that produces the effect without any
structure, and it does not replicate on 11 years of unseen COMEX data. The list of things that
could change ADR 040's verdict loses this item. The remaining items are new information (see
the data-source review) and years more IBJA days.
