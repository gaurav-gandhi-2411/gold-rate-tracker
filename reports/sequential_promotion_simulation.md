# Sequential promotion rule: simulation (ADR 072 Amendment 1)

Seed 42, 10000 paths per scenario, stationary bootstrap (mean block 6) of the CENTRED retrospective loss differences between P3 and each challenger (145 shared days, 2025-07-17 to 2026-10-05). Those retrospective folds give variance and autocorrelation only; they are NOT evidence of any gain, and the gain in each row is set by hand. The 145 folds are not consecutive trading days, so real daily autocorrelation may differ. Rule hash `1bbc5dd3eeae`. Horizon: 128 weekday decision days in 180 calendar days (holidays ignored). Per-challenger level alpha/3 = 0.0167; family-wise alpha 0.05.

Forward folds with retro false at amendment time: p3 0, ensemble 0, p3_roll60 0, p3_monday 0.

- ensemble: sd of daily e = 0.237 x champion MAE, long-run sd 0.195, autocorrelation lags 1-4 -0.11, -0.17, +0.04, +0.04
- p3_roll60: sd of daily e = 0.208 x champion MAE, long-run sd 0.262, autocorrelation lags 1-4 +0.12, +0.19, +0.16, +0.15
- p3_monday: sd of daily e = 0.087 x champion MAE, long-run sd 0.087, autocorrelation lags 1-4 -0.03, +0.05, -0.02, +0.01

## New rule (confidence sequence), per challenger

| Challenger | True gain | Promoted <=180d | 95% MC interval | Retired | Mean days to decision | Median days | Median days if promoted |
|---|---|---|---|---|---|---|---|
| ensemble | 0% | 0.0% | 0.0% to 0.0% | 100.0% | 128.0 | 128 | n/a |
| p3_roll60 | 0% | 0.0% | 0.0% to 0.0% | 100.0% | 128.0 | 128 | n/a |
| p3_monday | 0% | 0.0% | 0.0% to 0.0% | 100.0% | 128.0 | 128 | n/a |
| ensemble | 5% | 0.4% | 0.3% to 0.6% | 99.6% | 127.6 | 128 | 36 |
| p3_roll60 | 5% | 1.5% | 1.2% to 1.7% | 98.5% | 126.7 | 128 | 20 |
| p3_monday | 5% | 0.7% | 0.5% to 0.8% | 99.3% | 127.3 | 128 | 21 |
| ensemble | 10% | 20.6% | 19.8% to 21.4% | 79.4% | 115.5 | 128 | 67 |
| p3_roll60 | 10% | 37.4% | 36.5% to 38.4% | 62.6% | 102.4 | 128 | 54 |
| p3_monday | 10% | 99.5% | 99.3% to 99.6% | 0.5% | 42.1 | 35 | 35 |
| ensemble | 20% | 99.8% | 99.7% to 99.8% | 0.2% | 39.2 | 33 | 32 |
| p3_roll60 | 20% | 98.7% | 98.4% to 98.9% | 1.3% | 36.2 | 24 | 24 |
| p3_monday | 20% | 100.0% | 100.0% to 100.0% | 0.0% | 20.0 | 20 | 20 |
| ensemble | 40% | 100.0% | 100.0% to 100.0% | 0.0% | 20.4 | 20 | 20 |
| p3_roll60 | 40% | 100.0% | 100.0% to 100.0% | 0.0% | 20.9 | 20 | 20 |
| p3_monday | 40% | 100.0% | 100.0% to 100.0% | 0.0% | 20.0 | 20 | 20 |

Days are forward decision days (a retired path counts as decided at the horizon). At 5% true gain the rule is at its boundary: that row is the size of the test. At 0% true gain the row is the wrongful-promotion rate.

Any of the three promoted (same days for all three, family-wise):

| True gain | Share | 95% MC interval |
|---|---|---|
| 0% | 0.0% | 0.0% to 0.0% |
| 5% | 2.1% | 1.8% to 2.4% |
| 10% | 99.5% | 99.4% to 99.6% |
| 20% | 100.0% | 100.0% to 100.0% |
| 40% | 100.0% | 100.0% to 100.0% |

## Old rule (version 1, fixed n), same paths

| Challenger | True gain | Promoted <=180d | 95% MC interval | Promoted <=600 decision days | Median days if promoted (<=600) | Not decided in 600 days |
|---|---|---|---|---|---|---|
| ensemble | 0% | 2.7% | 2.4% to 3.0% | 2.8% | 63 | 97.2% |
| p3_roll60 | 0% | 3.0% | 2.7% to 3.4% | 4.3% | 91 | 95.7% |
| p3_monday | 0% | 0.0% | 0.0% to 0.0% | 0.0% | n/a | 100.0% |
| ensemble | 5% | 63.4% | 62.4% to 64.3% | 87.3% | 100 | 12.7% |
| p3_roll60 | 5% | 39.0% | 38.0% to 39.9% | 83.8% | 136 | 16.2% |
| p3_monday | 5% | 77.1% | 76.2% to 77.9% | 90.0% | 40 | 10.0% |
| ensemble | 10% | 93.2% | 92.7% to 93.7% | 100.0% | 86 | 0.0% |
| p3_roll60 | 10% | 54.2% | 53.2% to 55.2% | 100.0% | 122 | 0.0% |
| p3_monday | 10% | 100.0% | 100.0% to 100.0% | 100.0% | 40 | 0.0% |
| ensemble | 20% | 93.3% | 92.8% to 93.8% | 100.0% | 85 | 0.0% |
| p3_roll60 | 20% | 54.8% | 53.8% to 55.7% | 100.0% | 121 | 0.0% |
| p3_monday | 20% | 100.0% | 100.0% to 100.0% | 100.0% | 40 | 0.0% |
| ensemble | 40% | 93.3% | 92.8% to 93.8% | 100.0% | 85 | 0.0% |
| p3_roll60 | 40% | 54.8% | 53.8% to 55.7% | 100.0% | 121 | 0.0% |
| p3_monday | 40% | 100.0% | 100.0% to 100.0% | 100.0% | 40 | 0.0% |

## Is the asymptotic sequence anti-conservative at small n? (variance inflation)

False-promotion rate within the horizon, by variance plug-in (floor = also at least the plain sample variance; x = inflation factor). The frozen rule is marked.

| Variance used | True gain | ensemble | p3_roll60 | p3_monday | any of three |
|---|---|---|---|---|---|
| NW x 1.0 | 5.0% | 7.1% (6.6% to 7.6%) | 10.0% (9.4% to 10.6%) | 3.3% (3.0% to 3.7%) | 16.6% (15.9% to 17.4%) |
| NW x 1.0 | 0.0% | 0.2% (0.1% to 0.3%) | 0.1% (0.0% to 0.1%) | 0.0% (0.0% to 0.0%) | 0.2% (0.2% to 0.3%) |
| NW x 1.5 | 5.0% | 3.2% (2.8% to 3.5%) | 4.1% (3.8% to 4.5%) | 1.7% (1.4% to 1.9%) | 7.2% (6.7% to 7.7%) |
| NW x 1.5 | 0.0% | 0.0% (0.0% to 0.1%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.1%) |
| max(NW, iid) x 1.0 | 5.0% | 2.1% (1.8% to 2.4%) | 4.7% (4.3% to 5.1%) | 1.8% (1.6% to 2.1%) | 7.1% (6.6% to 7.6%) |
| max(NW, iid) x 1.0 | 0.0% | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.1%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.1%) |
| max(NW, iid) x 1.25 | 5.0% | 0.8% (0.7% to 1.0%) | 2.5% (2.2% to 2.8%) | 1.1% (0.9% to 1.4%) | 3.6% (3.3% to 4.0%) |
| max(NW, iid) x 1.25 | 0.0% | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.1%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.1%) |
| max(NW, iid) x 1.5 (frozen) | 5.0% | 0.4% (0.3% to 0.6%) | 1.6% (1.3% to 1.8%) | 0.8% (0.6% to 0.9%) | 2.2% (2.0% to 2.5%) |
| max(NW, iid) x 1.5 (frozen) | 0.0% | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) |
| max(NW, iid) x 2.0 | 5.0% | 0.1% (0.0% to 0.1%) | 0.9% (0.8% to 1.1%) | 0.4% (0.3% to 0.5%) | 1.1% (0.9% to 1.4%) |
| max(NW, iid) x 2.0 | 0.0% | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) |

## Demotion monitor: would the same bound detect a failing live model sooner?

P3's centred retrospective (model error minus hold error) series, same bootstrap. Current = ml/demotion.py error rule (last 40 days, one-sided HAC p < 0.05, 3 checks in a row). Sequential = lower confidence bound of (model - hold) above 0 at level 0.05 from day 20. 128 days. ml/demotion.py is NOT changed by this work.

| Scenario | Rule | Fired | Fired before change | Median days to fire | Median days after change |
|---|---|---|---|---|---|
| model better than holding by 20% (false-alarm check) | current_rolling_40_persist_3 | 0.0% | n/a | n/a | n/a |
| model better than holding by 20% (false-alarm check) | sequential_bound | 0.0% | n/a | n/a | n/a |
| model equal to holding (boundary) | current_rolling_40_persist_3 | 67.9% | n/a | 60 | n/a |
| model equal to holding (boundary) | sequential_bound | 3.1% | n/a | 60 | n/a |
| model worse by 10% from day 1 | current_rolling_40_persist_3 | 99.1% | n/a | 32 | n/a |
| model worse by 10% from day 1 | sequential_bound | 69.5% | n/a | 39 | n/a |
| model worse by 20% from day 1 | current_rolling_40_persist_3 | 100.0% | n/a | 32 | n/a |
| model worse by 20% from day 1 | sequential_bound | 98.0% | n/a | 23 | n/a |
| model worse by 40% from day 1 | current_rolling_40_persist_3 | 100.0% | n/a | 32 | n/a |
| model worse by 40% from day 1 | sequential_bound | 100.0% | n/a | 20 | n/a |
| good (-20%) for 60 days, then worse by 20% | current_rolling_40_persist_3 | 97.4% | 0.0% | 93 | 33 |
| good (-20%) for 60 days, then worse by 20% | sequential_bound | 0.0% | 0.0% | n/a | n/a |
