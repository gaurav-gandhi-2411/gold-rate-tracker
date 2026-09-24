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

**Secondary (S1–S4).** Benjamini-Hochberg at q = 0.05 across the four. All one-sided.
- S1: lag-1 autocorrelation of returns after calm days > 0 (Fisher z).
- S2: lag-1 autocorrelation after volatile days < 0 (Fisher z).
- S3: on calm days only, the rule vs always-up (same DM test).
- S4: on volatile days only, the rule vs always-up (same DM test).

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
