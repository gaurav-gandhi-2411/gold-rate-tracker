# Demotion monitor error test: size, wrongful demotion, catch rate (old vs new)

Produced by `python scripts/simulate_demotion_size.py` (seed 42, 5000 paths per sequential
scenario, 20000 per single-check cell), raw numbers in `demotion_test_size.json` (same folder).
OLD = normal-tail Newey-West, 4 lags. NEW = Student-t tail, 4 degrees of freedom (same variance).
Window 40, alpha 0.05, persistence 3, 121 daily checks. Thresholds unchanged.
Source series: `data/nextfix_p3_oos.json` (145 folds; model MAE / hold MAE = 0.9266, mean loss
difference -8.41, sd 36.33, lag-1 autocorrelation 0.096).

## Single-check size at nominal 5% (n = 40, true mean difference zero)

| Null | OLD | NEW |
|---|---|---|
| t(4), AR(1) 0.0 | 7.41% | 3.26% |
| t(4), AR(1) 0.3 | 9.07% | 4.54% |
| t(4), AR(1) 0.5 | 11.77% | 6.68% |
| real P3 loss series, demeaned (model exactly as good as holding) | 12.79% | 7.22% |

## Full rule over 121 daily checks (persistence 3)

"Day" is the daily check number (check 1 uses the first 30 folds).

| Scenario | Test | Demoted within 40 | Mean day (within 40) | Demoted within 121 | Mean day (within 121) |
|---|---|---|---|---|---|
| WRONGFUL: model WITH P3's effect (real series, bootstrapped) | OLD | 2.68% | 17.6 | 6.28% | 53.3 |
| | NEW | 1.02% | 18.4 | 2.20% | 51.0 |
| No-skill: exactly as good as holding (real series, demeaned) | OLD | 33.72% | 15.3 | 65.96% | 45.5 |
| | NEW | 22.20% | 16.6 | 47.78% | 49.7 |
| No-skill: 10% worse than holding (real series) | OLD | 87.70% | 9.2 | 99.44% | 15.4 |
| | NEW | 78.66% | 10.7 | 98.12% | 21.3 |
| Synthetic null t(4), AR 0.0 | OLD | 21.44% | 16.3 | 48.16% | 51.0 |
| | NEW | 11.28% | 17.4 | 27.62% | 54.8 |
| Synthetic null t(4), AR 0.3 | OLD | 26.44% | 16.0 | 56.28% | 49.0 |
| | NEW | 15.06% | 17.8 | 36.28% | 53.7 |
| Synthetic null t(4), AR 0.5 | OLD | 30.58% | 15.4 | 62.00% | 47.0 |
| | NEW | 19.42% | 16.1 | 43.52% | 51.4 |

## Candidate tests compared (single-check size, n = 40)

| Test | AR 0.0 | AR 0.3 | AR 0.5 | real P3 demeaned |
|---|---|---|---|---|
| old: normal tail, 4 lags | 7.48% | 9.30% | 11.48% | 13.23% |
| Student-t, df = n-1 = 39 | 6.99% | 8.71% | 10.94% | 12.70% |
| variance x n/(n-1), then t(39) | 6.80% | 8.47% | 10.58% | 12.41% |
| Student-t, df 5 | 4.01% | 5.46% | 7.39% | 8.78% |
| **Student-t, df 4 (chosen)** | 3.24% | 4.68% | 6.39% | 7.63% |
| Student-t, df 3 | 2.12% | 3.35% | 4.81% | 5.87% |

(The candidate columns use their own random draws, so they differ slightly from the first table.)

## Direction and range rules (exact binomial, no simulation)

Exact probability of a breach at the edge of the null (true accuracy 0.55; true coverage 0.75):
direction n=40 4.05%, n=30 3.34%; range n=60 2.98%, n=30 2.16%. All at or under 5%: these two rules
are not inflated and are unchanged.
