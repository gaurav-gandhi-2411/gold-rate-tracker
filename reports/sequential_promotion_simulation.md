# Sequential promotion rule: simulation (ADR 072 Amendment 1)

Seed 42, 10000 paths per scenario, stationary bootstrap (mean block 6) of the CENTRED retrospective loss differences between P3 and each challenger (146 shared days, 2025-07-17 to 2026-10-07). Those retrospective folds give variance and autocorrelation only; they are NOT evidence of any gain, and the gain in each row is set by hand. The 145 folds are not consecutive trading days, so real daily autocorrelation may differ. Rule hash `2499a124d6e0`. Horizon: 180 decision days (holidays ignored). Per-challenger level alpha/3 = 0.0167; family-wise alpha 0.05.

Forward folds with retro false at amendment time: p3 1, ensemble 0, p3_roll60 1, p3_monday 1.

- ensemble: sd of daily e = 0.238 x champion MAE, long-run sd 0.196, autocorrelation lags 1-4 -0.11, -0.17, +0.04, +0.04
- p3_roll60: sd of daily e = 0.208 x champion MAE, long-run sd 0.262, autocorrelation lags 1-4 +0.11, +0.19, +0.16, +0.15
- p3_monday: sd of daily e = 0.087 x champion MAE, long-run sd 0.087, autocorrelation lags 1-4 -0.03, +0.05, -0.02, +0.01

## New rule (confidence sequence), per challenger

| Challenger | True gain | Promoted <=180d | 95% MC interval | Retired | Mean days to decision | Median days | Median days if promoted |
|---|---|---|---|---|---|---|---|
| ensemble | 0% | 0.0% | 0.0% to 0.0% | 100.0% | 180.0 | 180 | n/a |
| p3_roll60 | 0% | 0.0% | 0.0% to 0.0% | 100.0% | 180.0 | 180 | n/a |
| p3_monday | 0% | 0.0% | 0.0% to 0.0% | 100.0% | 180.0 | 180 | n/a |
| ensemble | 5% | 0.4% | 0.3% to 0.6% | 99.6% | 179.4 | 180 | 34 |
| p3_roll60 | 5% | 1.4% | 1.2% to 1.7% | 98.6% | 178.2 | 180 | 26 |
| p3_monday | 5% | 0.7% | 0.6% to 0.9% | 99.3% | 178.9 | 180 | 21 |
| ensemble | 10% | 30.4% | 29.6% to 31.4% | 69.5% | 153.9 | 180 | 94 |
| p3_roll60 | 10% | 46.2% | 45.3% to 47.2% | 53.8% | 132.6 | 180 | 68 |
| p3_monday | 10% | 100.0% | 100.0% to 100.0% | 0.0% | 42.6 | 36 | 36 |
| ensemble | 20% | 100.0% | 100.0% to 100.0% | 0.0% | 39.2 | 33 | 33 |
| p3_roll60 | 20% | 99.9% | 99.9% to 100.0% | 0.1% | 36.4 | 24 | 24 |
| p3_monday | 20% | 100.0% | 100.0% to 100.0% | 0.0% | 20.0 | 20 | 20 |
| ensemble | 40% | 100.0% | 100.0% to 100.0% | 0.0% | 20.4 | 20 | 20 |
| p3_roll60 | 40% | 100.0% | 100.0% to 100.0% | 0.0% | 20.9 | 20 | 20 |
| p3_monday | 40% | 100.0% | 100.0% to 100.0% | 0.0% | 20.0 | 20 | 20 |

Days are forward decision days (a retired path counts as decided at the horizon). At 5% true gain the rule is at its boundary: that row is the size of the test. At 0% true gain the row is the wrongful-promotion rate.

Any of the three promoted (same days for all three, family-wise):

| True gain | Share | 95% MC interval |
|---|---|---|
| 0% | 0.0% | 0.0% to 0.0% |
| 5% | 2.1% | 1.9% to 2.5% |
| 10% | 100.0% | 100.0% to 100.0% |
| 20% | 100.0% | 100.0% to 100.0% |
| 40% | 100.0% | 100.0% to 100.0% |

## Old rule (version 1, fixed n), same paths

| Challenger | True gain | Promoted <=180d | 95% MC interval | Promoted <=600 decision days | Median days if promoted (<=600) | Not decided in 600 days |
|---|---|---|---|---|---|---|
| ensemble | 0% | 2.7% | 2.4% to 3.0% | 2.7% | 62 | 97.3% |
| p3_roll60 | 0% | 3.8% | 3.5% to 4.2% | 4.2% | 91 | 95.8% |
| p3_monday | 0% | 0.0% | 0.0% to 0.1% | 0.0% | 53 | 100.0% |
| ensemble | 5% | 75.0% | 74.1% to 75.8% | 87.4% | 99 | 12.6% |
| p3_roll60 | 5% | 60.1% | 59.1% to 61.0% | 83.8% | 136 | 16.2% |
| p3_monday | 5% | 80.7% | 79.9% to 81.4% | 89.5% | 40 | 10.5% |
| ensemble | 10% | 99.9% | 99.8% to 100.0% | 100.0% | 85 | 0.0% |
| p3_roll60 | 10% | 85.1% | 84.4% to 85.8% | 100.0% | 121 | 0.0% |
| p3_monday | 10% | 100.0% | 100.0% to 100.0% | 100.0% | 40 | 0.0% |
| ensemble | 20% | 99.9% | 99.9% to 100.0% | 100.0% | 85 | 0.0% |
| p3_roll60 | 20% | 85.5% | 84.8% to 86.2% | 100.0% | 120 | 0.0% |
| p3_monday | 20% | 100.0% | 100.0% to 100.0% | 100.0% | 40 | 0.0% |
| ensemble | 40% | 99.9% | 99.9% to 100.0% | 100.0% | 85 | 0.0% |
| p3_roll60 | 40% | 85.5% | 84.8% to 86.2% | 100.0% | 120 | 0.0% |
| p3_monday | 40% | 100.0% | 100.0% to 100.0% | 100.0% | 40 | 0.0% |

## Is the asymptotic sequence anti-conservative at small n? (variance inflation)

False-promotion rate within the horizon, by variance plug-in (floor = also at least the plain sample variance; x = inflation factor). The frozen rule is marked.

| Variance used | True gain | ensemble | p3_roll60 | p3_monday | any of three |
|---|---|---|---|---|---|
| NW x 1.0 | 5.0% | 7.8% (7.3% to 8.3%) | 10.4% (9.8% to 11.0%) | 3.2% (2.9% to 3.6%) | 17.2% (16.5% to 18.0%) |
| NW x 1.0 | 0.0% | 0.2% (0.1% to 0.3%) | 0.1% (0.0% to 0.2%) | 0.0% (0.0% to 0.0%) | 0.3% (0.2% to 0.4%) |
| NW x 1.5 | 5.0% | 3.8% (3.4% to 4.1%) | 4.1% (3.7% to 4.5%) | 1.5% (1.3% to 1.8%) | 7.7% (7.2% to 8.3%) |
| NW x 1.5 | 0.0% | 0.0% (0.0% to 0.1%) | 0.0% (0.0% to 0.1%) | 0.0% (0.0% to 0.0%) | 0.1% (0.0% to 0.2%) |
| max(NW, iid) x 1.0 | 5.0% | 2.2% (1.9% to 2.5%) | 5.5% (5.1% to 6.0%) | 1.6% (1.3% to 1.8%) | 8.0% (7.5% to 8.5%) |
| max(NW, iid) x 1.0 | 0.0% | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.1%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.1%) |
| max(NW, iid) x 1.25 | 5.0% | 0.9% (0.8% to 1.1%) | 2.8% (2.5% to 3.1%) | 1.0% (0.8% to 1.2%) | 4.0% (3.7% to 4.4%) |
| max(NW, iid) x 1.25 | 0.0% | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.1%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.1%) |
| max(NW, iid) x 1.5 (frozen) | 5.0% | 0.4% (0.3% to 0.6%) | 1.5% (1.3% to 1.7%) | 0.6% (0.5% to 0.8%) | 2.2% (1.9% to 2.5%) |
| max(NW, iid) x 1.5 (frozen) | 0.0% | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) |
| max(NW, iid) x 2.0 | 5.0% | 0.1% (0.0% to 0.1%) | 0.6% (0.5% to 0.8%) | 0.3% (0.2% to 0.4%) | 0.8% (0.7% to 1.0%) |
| max(NW, iid) x 2.0 | 0.0% | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) |

## Demotion monitor: would the same bound detect a failing live model sooner?

P3's centred retrospective (model error minus hold error) series, same bootstrap. Current = ml/demotion.py error rule (last 40 days, one-sided HAC p < 0.05, 3 checks in a row). Sequential = lower confidence bound of (model - hold) above 0 at level 0.05 from day 20. 180 days. ml/demotion.py is NOT changed by this work.

| Scenario | Rule | Fired | Fired before change | Median days to fire | Median days after change |
|---|---|---|---|---|---|
| model better than holding by 20% (false-alarm check) | current_rolling_40_persist_3 | 0.0% | n/a | n/a | n/a |
| model better than holding by 20% (false-alarm check) | sequential_bound | 0.0% | n/a | n/a | n/a |
| model equal to holding (boundary) | current_rolling_40_persist_3 | 81.9% | n/a | 70 | n/a |
| model equal to holding (boundary) | sequential_bound | 3.9% | n/a | 69 | n/a |
| model worse by 10% from day 1 | current_rolling_40_persist_3 | 99.9% | n/a | 32 | n/a |
| model worse by 10% from day 1 | sequential_bound | 79.0% | n/a | 44 | n/a |
| model worse by 20% from day 1 | current_rolling_40_persist_3 | 100.0% | n/a | 32 | n/a |
| model worse by 20% from day 1 | sequential_bound | 99.6% | n/a | 23 | n/a |
| model worse by 40% from day 1 | current_rolling_40_persist_3 | 100.0% | n/a | 32 | n/a |
| model worse by 40% from day 1 | sequential_bound | 100.0% | n/a | 20 | n/a |
| good (-20%) for 60 days, then worse by 20% | current_rolling_40_persist_3 | 100.0% | 0.0% | 93 | 33 |
| good (-20%) for 60 days, then worse by 20% | sequential_bound | 2.1% | 0.0% | 174 | 114 |
