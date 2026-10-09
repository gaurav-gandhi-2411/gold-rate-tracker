# Challenger size calibration, round 2 (ADR 072 Amendment 5)

Seed 42, 10,000 paths of 180 decision days per cell, real record of 146 days, allowance 1.67%. All gains are set by hand (VERIFIED: this script). Procedure: see the module docstring (pre-registered before running).

## Result
| Challenger | calibrated k (base 1.5) | proof on held-out resamplers | power at true 10% | at 20% |
|---|---|---|---|---|
| ensemble | 2.0 | PASS | 30.90% -> 15.90% | 100.00% -> 99.97% |
| p3_monday | 2.25 | PASS | 99.99% -> 99.70% | 100.00% -> 100.00% |

## ensemble: boundary rate by k (calibration resamplers)

| k | block10 | block20 | block30 | block40 | block60 | stationary20 | ar1_phi0.2 | ar1_phi0.3 | ar1_phi0.4 | ar1_phi0.5 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1.50 | 0.58% | 0.73% | 1.36% | 0.88% | 0.00% | 0.57% | 0.70% | 1.16% | 1.98% | 3.15% |
| 1.75 | 0.31% | 0.31% | 0.75% | 0.50% | 0.00% | 0.23% | 0.39% | 0.72% | 1.21% | 1.96% |
| 2.00 | 0.14% | 0.09% | 0.36% | 0.16% | 0.00% | 0.07% | 0.23% | 0.38% | 0.80% | 1.37% |

### ensemble: held-out proof at k = 2.0
| Resampler | rate | upper 95% | rate at base k | passes |
|---|---|---|---|---|
| block25_seed11 | 0.28% | 0.40% | 1.20% | True |
| stationary30_seed12 | 0.15% | 0.25% | 0.81% | True |
| ar1_phi0.25_seed13 | 0.39% | 0.53% | 1.11% | True |
| ar1_phi0.35_seed13 | 0.65% | 0.83% | 1.72% | True |
| ar1_phi0.45_seed13 | 1.24% | 1.48% | 2.78% | True |

## p3_monday: boundary rate by k (calibration resamplers)

| k | block10 | block20 | block30 | block40 | block60 | stationary20 | ar1_phi0.2 | ar1_phi0.3 | ar1_phi0.4 | ar1_phi0.5 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1.50 | 0.76% | 3.27% | 2.82% | 2.40% | 2.10% | 1.74% | 0.70% | 1.16% | 1.98% | 3.15% |
| 1.75 | 0.42% | 2.87% | 2.59% | 2.28% | 2.10% | 1.34% | 0.39% | 0.72% | 1.21% | 1.96% |
| 2.00 | 0.30% | 1.58% | 1.92% | 1.50% | 1.45% | 0.91% | 0.23% | 0.38% | 0.80% | 1.37% |
| 2.25 | 0.24% | 0.99% | 1.21% | 0.75% | 0.79% | 0.59% | 0.11% | 0.24% | 0.49% | 1.00% |

### p3_monday: held-out proof at k = 2.25
| Resampler | rate | upper 95% | rate at base k | passes |
|---|---|---|---|---|
| block25_seed11 | 1.15% | 1.38% | 3.20% | True |
| stationary30_seed12 | 0.60% | 0.77% | 2.26% | True |
| ar1_phi0.25_seed13 | 0.26% | 0.38% | 1.11% | True |
| ar1_phi0.35_seed13 | 0.47% | 0.62% | 1.72% | True |
| ar1_phi0.45_seed13 | 0.80% | 0.99% | 2.78% | True |
