# ADR 039 — Pre-registration: R3 buyer "wait or buy" policy and R4 selective direction

**Status:** Accepted, 2026-09-23. **Frozen before any scoring.** No R3 or R4 policy, threshold or
model has been evaluated on any test-period data when this ADR is merged. Shadow research only: nothing
here changes the gate, the site, or any published number. Promotion of anything to users needs a
promotion PR and GG's approval (ADR 036).

**Why pre-register.** Direction models have repeatedly looked good on the data that selected them
and then not held up (ADR 034 → ADR 038 amendment A1). Here the rules, parameter grids, selection
procedure, test periods, metrics, baselines, α and correction families are fixed in writing first.
Any later deviation must be logged as a dated amendment, with its reason, *before* the affected
data is scored.

Common to both:

- **Series.** 22K ₹/gram = INR proxy `label_22k_per_10g` / 10 (`ml.inr_proxy_labels`), and real
  IBJA `pm_916` / 10 (`data/ibja_rates.parquet`). Carried-forward non-publication days are dropped,
  so each row is a real trading day. COMEX: roll-adjusted GC=F daily (`ml.direction.comex_daily`).
- **Selection period** (all parameter and threshold choices): proxy and COMEX up to 2021-12-31.
- **Test periods** (scored once, at the end): proxy 2022-01-01 → latest matured day; real IBJA
  2022-01-20 → latest matured day; COMEX 2022-01-01 → latest matured day. **Real IBJA is the
  product-relevant (primary) test set.**
- **Forward-only.** Every decision at day *t* uses data through *t* only. Any model is refit at
  most every 21 trading days on an expanding window. The embargo is ≥ the horizon: a training row is
  used only if its outcome had matured by *t*.
- **Significance.** One-sided, HAC (Newey-West, lag = horizon − 1), effective n reported. α = 0.05.
  Bonferroni and Benjamini-Hochberg are both reported over the family stated in each section.
  Seed 42.

## R3 — "Should I wait?" judged in rupees

**Decision.** Each trading day *t*, a buyer must buy 1 g of 22K within **N ∈ {5, 10}** trading days.
A policy says **buy today**, or **wait** and buy at a trigger, else on the deadline day *t+N*.
Buying on the day at its price is assumed; no store premium or transaction cost is modelled.

**Metric.** Saving_t = P_t − P_paid, in ₹/g, against "always buy today". Report:
- the mean saving per decision, with a HAC 95% CI;
- the share of decisions that saved or cost money;
- the **oracle** upper bound (buy at the minimum over *t..t+N*);
- "always wait until the deadline", for context.

**Policies and grids (exactly these):**

| id | rule | grid |
|---|---|---|
| P1 limit order | set L = P_t × (1 − k·σ_t), σ_t = EWMA(λ = 0.94) daily volatility at *t*; buy the first day *s* ∈ (*t*, *t+N*] with P_s ≤ L, at P_s; else at P_{t+N} | k ∈ {0.25, 0.5, 1.0, 1.5} |
| P2 stretch-wait | z_t = (P_t − MA20_t) / SD20_t. If z_t > z\*, wait and buy the first day *s* with z_s ≤ 0, else at the deadline; if z_t ≤ z\*, buy today | z\* ∈ {0.5, 1.0, 1.5} |
| P3 model-wait | logistic regression (C = 0.1, standardized) predicts D_t = 1[min_{s∈(t,t+N]} P_s ≤ P_t(1 − 0.5%)] from returns (1/5/20 d), volatility (5/20 d), z_t, day of week and month. If p̂ > θ, wait with a limit at P_t(1 − 0.5%), else buy today | θ ∈ {0.5, 0.6, 0.7} |

**Selection rule.** For each policy and N, the grid value with the highest mean saving on the
proxy selection period is frozen. P3 is walk-forward there too. Ties go to the smaller k, larger
z\*, or larger θ (the more conservative choice).

**Test and family.** One-sided H1: mean saving > 0.
- Primary family: 3 policies × 2 N on real IBJA = 6 tests (Bonferroni threshold 0.0083).
- Full family for BH: × {real IBJA, proxy} = 12.

**Success:** a policy is Bonferroni-significant on real IBJA *and* positive on the proxy test.

**Power caveat, stated now.** Real IBJA has ~250 test days. At N = 10, overlapping windows leave
roughly 25 independent blocks. A saving of a few ₹/g can't be detected; only a saving of order
₹20–40/g or more could be.

## R4 — Selective direction: predict only when confident

**Targets.** Direction up vs not-up over **h ∈ {1, 5}** trading days, on three series:
- INR proxy (selection data up to 2021; test proxy 2022+);
- real IBJA (test only; the model is trained on the proxy);
- COMEX GC=F (selection up to 2021; test 2022+).

**Model.** Logistic regression (C = 0.1, standardized) on stationary features only: returns 1/5/20
d, volatility 5/20 d, z-score vs 20-day mean, day of week, month. Price levels are excluded; the
direction diagnosis found them to be a source of overfitting. Refit every 21 days, expanding, with
embargo ≥ h.

**Selection rule (frozen).** Confidence c_t = |p̂_t − 0.5|. For target coverage **κ ∈ {10%, 25%,
50%, 100%}**, the threshold at each refit is the (1 − κ) quantile of c over **out-of-sample**
predictions on the most recent 250 matured training days. Those predictions come from a model fit
on the training data before those 250 days; the model is then refit on everything for the next
block. Test days are never used to set a threshold.

**Metric.** At each κ:
- realised coverage;
- precision (accuracy on the selected days);
- the baseline **on the same selected days**: the per-fold training-majority class, and also
  always-up.

A one-sided HAC DM test (lag h − 1) compares 0/1 loss on the selected days against that same-days
majority baseline.

**Family.** 4 κ × 2 h × 3 test series = 24 tests (Bonferroni 0.00208), plus BH.

**Success:** a κ < 100% is Bonferroni-significant on real IBJA or COMEX, with precision above
the same-days baseline.

## What would count as a deviation

Any change to a policy, grid, feature list, threshold procedure, test period, metric or family.
Fixing a bug that makes the code differ from this text is not a deviation: the text is the spec.
Such a fix is logged in the PR with the before/after behaviour, before test data is scored.

## R4 results (scored 2026-09-23, `scripts/analysis_selective_direction.py` at `05b755c6`, Actions run 35889514765, `reports/r4_selective_direction_results.json`)

**Pre-registered verdict: R4 does not meet its success criterion.** No κ < 100% is significant on
real IBJA or COMEX. Family size: 23 testable cells (real IBJA h1 at κ = 10% selected no days);
Bonferroni threshold 0.00217.

Precision on the selected days vs the training-majority class **on the same days**; one-sided HAC
p (lag h − 1):

| series | h | κ = 10% | κ = 25% | κ = 50% | κ = 100% |
|---|---|---|---|---|---|
| INR proxy 2022+ | 1 | **61.3% vs 47.9%, n = 163, p = 0.0010 (Bonferroni ✓)** | 58.7% vs 49.9%, p = 0.0044 | 57.6% vs 52.0%, p = 0.011 | 55.2% vs 53.5%, p = 0.19 |
| INR proxy 2022+ | 5 | 65.4% vs 64.4%, p = 0.28 | 61.1% vs 60.9%, p = 0.45 | 59.5% vs 59.4%, p = 0.47 | 56.9% vs 58.5%, p = 0.81 |
| real IBJA (dense) | 1 | 0 days selected | 3 days | 33.3% vs 53.3%, n = 15, p = 0.92 | 49.3% vs 49.3%, n = 73 |
| real IBJA (dense) | 5 | 75.0% vs 75.0%, n = 8 | 80.0% vs 80.0%, n = 20 | 73.0% vs 64.9%, n = 37, p = 0.11 | 63.1% vs 60.0%, n = 65, p = 0.33 |
| COMEX 2022+ | 1 | 57.6% vs 48.9%, n = 139, p = 0.059 | 57.6% vs 53.0%, p = 0.11 | 53.8% vs 54.6%, p = 0.62 | 53.6% vs 54.5%, p = 0.67 |
| COMEX 2022+ | 5 | 64.3% vs 64.3%, n = 182 | 57.6% vs 56.5%, p = 0.39 | 55.9% vs 57.2%, p = 0.68 | 53.7% vs 57.7%, p = 0.94 |

**The one Bonferroni-significant cell is on the proxy, and is very likely an artifact.** ADR 040
(D4) found the INR proxy has strong day-to-day reversal (lag-1 autocorrelation −0.13), which real
IBJA doesn't show (+0.07 on dense days). A confident-day model can learn to bet on that reversal.
It then scores on the proxy and nowhere else, which is exactly the pattern above: real IBJA at
h = 1 selects almost no days and has no edge. ADR 039 names real IBJA or COMEX as the success sets
for this reason. The proxy is not a success set.

**Power caveat.** Only two real-IBJA dense segments are long enough after the 20-day feature
warm-up: 73 test days at h = 1, 65 at h = 5. That can't detect realistic edges (ADR 040, D3).

**What this closes.** Selective direction, the last pre-registered direction test, finds no usable
edge on real prices. The direction signal stays in shadow. Model effort goes to R1 (range) and R2
(nowcast).
