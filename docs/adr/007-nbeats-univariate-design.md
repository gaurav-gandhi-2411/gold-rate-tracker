# ADR 007: N-BEATS trained univariate; TFT and LightGBM multivariate

**Status:** Accepted

## Context

The ensemble has three models. Each tests a distinct hypothesis about what signal is
extractable from Indian gold price data. Forcing all three to share the same feature set
would make the ensemble a popularity contest among similar models on identical inputs, which
provides less information than letting each architecture test its native strength.

## Decision

N-BEATS is trained univariately (target series only, no `past_covariates`, no
`future_covariates`). LightGBM uses the full tabular feature set (macro + lags + rolling
stats + calendar + regime). TFT uses multivariate `past_covariates` (macro + lags) and
`future_covariates` (calendar + regime).

This means the ensemble compares three distinct hypotheses:

- **LightGBM:** rich tabular features matter
- **TFT:** attention over multivariate sequences matters
- **N-BEATS:** decomposition of price-only history matters

## Consequences

**Good:**

- Each model tests a genuinely different hypothesis, so the ensemble is more informative.
- N-BEATS likely won't beat the naive baseline on MAE, because it works with strictly less
  information than naive's implicit "yesterday's value matters" assumption already encodes.
  That is acceptable: if N-BEATS still gets weight in the inverse-MAE ensemble, it is because
  it captures decomposable structure (trend + seasonality stacks) that the other models miss.
- This is more architecturally honest than forcing all three to share features, and produces a
  clearer story for evaluation.

**Bad:**

- The README must explicitly explain this choice in the model-comparison section, or readers
  will assume feature_count=1 is a bug.

## Alternatives considered

- **Force N-BEATS to use `past_covariates` like TFT:** rejected. darts `NBEATSModel` supports
  `past_covariates`, but doing so would make N-BEATS a weaker version of TFT on the same
  inputs. The ensemble would gain a third vote that is correlated with TFT, not independent of
  it. Less useful.
- **Drop N-BEATS from the ensemble entirely:** rejected. The univariate decomposition
  hypothesis is worth testing. A model that learns seasonality and trend from price-only data
  may still be complementary to TFT and LightGBM even if its standalone MAE is higher.
