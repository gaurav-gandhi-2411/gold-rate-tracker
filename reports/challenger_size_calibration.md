# Challenger size calibration (ADR 072 Amendment 3)

Seed 42, 10,000 paths of 180 decision days per cell, real record of 146 days. Allowance per challenger 1.67%. All gains are set by hand (VERIFIED: this script). Procedure: see the module docstring.

## Result
| Challenger | calibrated k (base 1.5) | proof on held-out resamplers | power at true 10% (before -> after) | power at 20% (before -> after) |
|---|---|---|---|---|
| ensemble | 1.5 | FAIL | 30.90% -> 30.90% | 100.00% -> 100.00% |
| p3_roll60 | 3.0 | PASS | 47.84% -> 17.23% | 99.84% -> 96.80% |
| p3_monday | 2.0 | FAIL | 99.99% -> 99.85% | 100.00% -> 100.00% |

## ensemble: boundary wrongful-promotion rate by k (calibration resamplers)

| k | block10 | block20 | block40 | block60 | stationary20 |
|---|---|---|---|---|---|
| 1.50 | 0.58% | 0.73% | 0.88% | 0.00% | 0.57% |

### ensemble: held-out proof at k = 1.5
| Resampler | rate | upper 95% | rate at base k | passes |
|---|---|---|---|---|
| block30_seed7 | 1.61% | 1.88% | 1.61% | True |
| stationary40_seed8 | 0.52% | 0.68% | 0.52% | True |
| ar1_phi0.2_seed9 | 0.62% | 0.79% | 0.62% | True |
| ar1_phi0.3_seed9 | 1.23% | 1.47% | 1.23% | True |
| ar1_phi0.4_seed9 | 2.07% | 2.37% | 2.07% | False |
| ar1_phi0.5_seed9 | 3.16% | 3.52% | 3.16% | False |

## p3_roll60: boundary wrongful-promotion rate by k (calibration resamplers)

| k | block10 | block20 | block40 | block60 | stationary20 |
|---|---|---|---|---|---|
| 1.50 | 2.41% | 7.22% | 7.18% | 7.11% | 4.37% |
| 1.75 | 1.74% | 5.16% | 5.63% | 5.66% | 3.25% |
| 2.00 | 1.18% | 3.99% | 3.64% | 3.69% | 2.42% |
| 2.25 | 0.93% | 3.42% | 2.93% | 2.92% | 1.92% |
| 2.50 | 0.69% | 2.62% | 1.57% | 1.43% | 1.19% |
| 2.75 | 0.55% | 1.90% | 1.57% | 1.43% | 1.04% |
| 3.00 | 0.44% | 1.11% | 0.93% | 0.73% | 0.75% |

### p3_roll60: held-out proof at k = 3.0
| Resampler | rate | upper 95% | rate at base k | passes |
|---|---|---|---|---|
| block30_seed7 | 1.17% | 1.40% | 6.92% | True |
| stationary40_seed8 | 0.59% | 0.76% | 4.95% | True |
| ar1_phi0.2_seed9 | 0.02% | 0.07% | 0.62% | True |
| ar1_phi0.3_seed9 | 0.07% | 0.14% | 1.23% | True |
| ar1_phi0.4_seed9 | 0.17% | 0.27% | 2.07% | True |
| ar1_phi0.5_seed9 | 0.47% | 0.62% | 3.16% | True |

## p3_monday: boundary wrongful-promotion rate by k (calibration resamplers)

| k | block10 | block20 | block40 | block60 | stationary20 |
|---|---|---|---|---|---|
| 1.50 | 0.76% | 3.27% | 2.40% | 2.10% | 1.74% |
| 1.75 | 0.42% | 2.87% | 2.28% | 2.10% | 1.34% |
| 2.00 | 0.30% | 1.58% | 1.50% | 1.45% | 0.91% |

### p3_monday: held-out proof at k = 2.0
| Resampler | rate | upper 95% | rate at base k | passes |
|---|---|---|---|---|
| block30_seed7 | 1.98% | 2.27% | 3.11% | False |
| stationary40_seed8 | 1.20% | 1.43% | 2.01% | True |
| ar1_phi0.2_seed9 | 0.19% | 0.30% | 0.62% | True |
| ar1_phi0.3_seed9 | 0.39% | 0.53% | 1.23% | True |
| ar1_phi0.4_seed9 | 0.81% | 1.01% | 2.07% | True |
| ar1_phi0.5_seed9 | 1.51% | 1.77% | 3.16% | True |
