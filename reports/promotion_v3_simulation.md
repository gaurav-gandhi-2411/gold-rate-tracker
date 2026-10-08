# Promotion rule v3: simulation (ADR 072, Amendment 2)

Seed 42, 10,000 paths per cell, circular block bootstrap (block 10) of the 145 real days 2025-07-17 to 2026-10-05. The gain in every cell is set by hand (VERIFIED: this script); the retrospective days are used only for noise, autocorrelation and the control variate.

## Noise in the real record
| Challenger | sd of daily e / champion mean error | record's own mean gain (NOT evidence) |
|---|---|---|
| ensemble | 0.237 | 1.3% |
| p3_roll60 | 0.208 | -0.8% |
| p3_monday | 0.087 | 0.8% |

## Chance of being promoted within the horizon (per challenger)

| True gain | Challenger | v2 (128 decision days) | v3 (180 decision days) | v3 + control variate |
|---|---|---|---|---|
| 0% | ensemble | 0.0% | 0.0% | 0.0% |
| 0% | p3_roll60 | 0.0% | 0.0% | 0.0% |
| 0% | p3_monday | 0.0% | 0.0% | 0.0% |
| 0% | any of three | 0.0% | 0.0% | - |
| 5% | ensemble | 0.6% | 0.6% | 0.2% |
| 5% | p3_roll60 | 2.9% | 3.1% | 4.9% |
| 5% | p3_monday | 0.7% | 0.7% | 0.0% |
| 5% | any of three | 3.2% | 3.5% | 5.1% |
| 10% | ensemble | 22.0% | 31.3% | 27.8% |
| 10% | p3_roll60 | 40.0% | 47.6% | 53.5% |
| 10% | p3_monday | 99.6% | 100.0% | 100.0% |
| 10% | any of three | 99.7% | 100.0% | 100.0% |
| 20% | ensemble | 99.8% | 100.0% | 100.0% |
| 20% | p3_roll60 | 98.6% | 99.8% | 99.7% |
| 20% | p3_monday | 100.0% | 100.0% | 100.0% |
| 20% | any of three | 100.0% | 100.0% | 100.0% |
| 40% | ensemble | 100.0% | 100.0% | 100.0% |
| 40% | p3_roll60 | 100.0% | 100.0% | 100.0% |
| 40% | p3_monday | 100.0% | 100.0% | 100.0% |
| 40% | any of three | 100.0% | 100.0% | 100.0% |

## Same, when the gain is proportional to each day's champion error (more conservative)

| True gain | Challenger | v2 (128 decision days) | v3 (180 decision days) | v3 + control variate |
|---|---|---|---|---|
| 5% | ensemble | 0.0% | 0.0% | 0.0% |
| 5% | p3_roll60 | 2.1% | 2.2% | 2.9% |
| 5% | p3_monday | 0.0% | 0.0% | 0.2% |
| 5% | any of three | 2.1% | 2.2% | 3.1% |
| 10% | ensemble | 5.4% | 11.3% | 6.0% |
| 10% | p3_roll60 | 20.8% | 27.7% | 40.6% |
| 10% | p3_monday | 100.0% | 100.0% | 96.3% |
| 10% | any of three | 100.0% | 100.0% | 98.3% |
| 20% | ensemble | 99.8% | 100.0% | 100.0% |
| 20% | p3_roll60 | 97.9% | 99.9% | 100.0% |
| 20% | p3_monday | 100.0% | 100.0% | 100.0% |
| 20% | any of three | 100.0% | 100.0% | 100.0% |
| 40% | ensemble | 100.0% | 100.0% | 100.0% |
| 40% | p3_roll60 | 100.0% | 100.0% | 100.0% |
| 40% | p3_monday | 100.0% | 100.0% | 100.0% |
| 40% | any of three | 100.0% | 100.0% | 100.0% |

## Longer horizons (the option; its cost is that decisions come later)
| Horizon (decision days) | Gain construction | True gain | ensemble | p3_roll60 | p3_monday |
|---|---|---|---|---|---|
| 180 | additive | 10% | 31.3% | 47.6% | 100.0% |
| 180 | additive | 15% | 96.4% | 93.2% | 100.0% |
| 180 | proportional | 10% | 11.3% | 27.7% | 100.0% |
| 180 | proportional | 15% | 94.0% | 88.2% | 100.0% |
| 270 | additive | 10% | 48.8% | 59.1% | 100.0% |
| 270 | additive | 15% | 99.9% | 98.6% | 100.0% |
| 270 | proportional | 10% | 27.2% | 39.9% | 100.0% |
| 270 | proportional | 15% | 99.9% | 98.6% | 100.0% |
| 360 | additive | 10% | 64.6% | 68.9% | 100.0% |
| 360 | additive | 15% | 100.0% | 99.8% | 100.0% |
| 360 | proportional | 10% | 48.8% | 52.7% | 100.0% |
| 360 | proportional | 15% | 100.0% | 100.0% | 100.0% |

## Median decision day, given promoted (v3)
| True gain | Challenger | median day | by day 60 | by day 90 | by day 180 |
|---|---|---|---|---|---|
| 5% | ensemble | 29 | 0.5% | 0.6% | 0.6% |
| 5% | p3_roll60 | 20 | 2.5% | 2.7% | 3.1% |
| 5% | p3_monday | 22 | 0.7% | 0.7% | 0.7% |
| 10% | ensemble | 90 | 10.5% | 15.7% | 31.3% |
| 10% | p3_roll60 | 55 | 25.5% | 32.4% | 47.6% |
| 10% | p3_monday | 36 | 78.3% | 95.1% | 100.0% |
| 20% | ensemble | 31 | 85.4% | 97.4% | 100.0% |
| 20% | p3_roll60 | 23 | 83.7% | 93.9% | 99.8% |
| 20% | p3_monday | 20 | 100.0% | 100.0% | 100.0% |
| 40% | ensemble | 20 | 100.0% | 100.0% | 100.0% |
| 40% | p3_roll60 | 20 | 100.0% | 100.0% | 100.0% |
| 40% | p3_monday | 20 | 100.0% | 100.0% | 100.0% |

## Block-length sensitivity (v3 only, any challenger, per challenger share)
| Block | True gain | ensemble | p3_roll60 | p3_monday |
|---|---|---|---|---|
| 5 | 0% | 0.0% | 0.0% | 0.0% |
| 5 | 5% | 0.2% | 0.6% | 0.2% |
| 5 | 10% | 26.7% | 43.8% | 100.0% |
| 5 | 20% | 100.0% | 99.9% | 100.0% |
| 5 | 40% | 100.0% | 100.0% | 100.0% |
| 20 | 0% | 0.0% | 0.0% | 0.0% |
| 20 | 5% | 0.7% | 9.2% | 2.6% |
| 20 | 10% | 36.6% | 53.0% | 99.9% |
| 20 | 20% | 100.0% | 99.7% | 100.0% |
| 20 | 40% | 100.0% | 100.0% | 100.0% |
| 40 | 0% | 0.0% | 0.0% | 0.0% |
| 40 | 5% | 0.8% | 8.1% | 2.5% |
| 40 | 10% | 40.9% | 54.7% | 100.0% |
| 40 | 20% | 100.0% | 99.6% | 100.0% |
| 40 | 40% | 100.0% | 100.0% | 100.0% |

## Control variate (hold error, same day)
mu_H = 114.49 Rs./g (frozen), n = 145.

| Challenger | theta | variance cut, in sample | variance cut, out of sample |
|---|---|---|---|
| ensemble | -0.0270 | 1.5% | 1.9% |
| p3_roll60 | 0.0314 | 2.7% | 0.9% |
| p3_monday | -0.0545 | 45.9% | 29.6% |

## Wrongful promotion when the volatility regime shifts (v3, per challenger)
| Regime x gain | Challenger | plain | with control variate |
|---|---|---|---|
| scale=0.7|gain=0% | ensemble | 0.0% | 0.0% |
| scale=0.7|gain=0% | p3_roll60 | 0.0% | 0.0% |
| scale=0.7|gain=0% | p3_monday | 0.0% | 0.0% |
| scale=0.7|gain=5% | ensemble | 0.6% | 0.0% |
| scale=0.7|gain=5% | p3_roll60 | 3.1% | 13.6% |
| scale=0.7|gain=5% | p3_monday | 0.7% | 0.0% |
| scale=1.0|gain=0% | ensemble | 0.0% | 0.0% |
| scale=1.0|gain=0% | p3_roll60 | 0.0% | 0.0% |
| scale=1.0|gain=0% | p3_monday | 0.0% | 0.0% |
| scale=1.0|gain=5% | ensemble | 0.6% | 0.2% |
| scale=1.0|gain=5% | p3_roll60 | 3.1% | 4.9% |
| scale=1.0|gain=5% | p3_monday | 0.7% | 0.0% |
| scale=1.3|gain=0% | ensemble | 0.0% | 0.0% |
| scale=1.3|gain=0% | p3_roll60 | 0.0% | 0.0% |
| scale=1.3|gain=0% | p3_monday | 0.0% | 0.0% |
| scale=1.3|gain=5% | ensemble | 0.6% | 0.4% |
| scale=1.3|gain=5% | p3_roll60 | 3.1% | 2.2% |
| scale=1.3|gain=5% | p3_monday | 0.7% | 12.2% |
