"""ml.direction.stats_corrections — overlapping-horizon-aware significance,
one-sided testing, and multiplicity correction (M2, GG spec 2026-09-23,
item 3: "apply to every result in the M2 table and to everything that
follows").

Four corrections, each addressing a specific way the M2 table's original
numbers overstated evidence:

1. OVERLAPPING HORIZONS — at horizon h, consecutive folds share h-1 days
   of outcome, so treating each fold as independent overstates n.
   ml.direction.evaluate_reframed.diebold_mariano_test already uses HAC
   (Newey-West, lag=h-1) variance and now also returns `effective_n`
   (n * gamma_0 / long_run_var — shrinks toward n only when there's no
   real autocorrelation). This module adds `block_bootstrap_confirm`, an
   independent, non-parametric confirmation using block length >= h.
2. DIRECTION — `one_sided_mcnemar` and `diebold_mariano_test(...,
   alternative="less")` test specifically "model beats baseline", not
   merely "differs from it" — a row can be significant in the WRONG
   direction (worse than baseline), which a two-sided test would report
   as "significant" without saying which way. `classify_direction` labels
   every row explicitly as BETTER / WORSE / NEITHER before either p-value
   is even considered.
3. MULTIPLICITY — `bonferroni` and `benjamini_hochberg` treat every
   configuration tried as one family, per GG's instruction. Two different,
   standard corrections reported side by side (Bonferroni: conservative,
   controls family-wise error rate; BH: controls false discovery rate,
   usually less conservative) rather than picking one.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.stats import binomtest, norm


def classify_direction(mean_diff: float | None, tol: float = 1e-12) -> str:
    """BETTER (model loss < baseline loss), WORSE (model loss > baseline
    loss), or NEITHER (no measurable difference / insufficient data)."""
    if mean_diff is None:
        return "NEITHER"
    if mean_diff < -tol:
        return "BETTER"
    if mean_diff > tol:
        return "WORSE"
    return "NEITHER"


def one_sided_mcnemar(b: int, c: int, alternative: str = "greater") -> dict:
    """One-sided exact binomial test on McNemar discordant pairs.

    b = folds where the model was right and baseline wrong; c = folds
    where the model was wrong and baseline right. alternative="greater"
    tests H1: model beats baseline (b > c under the null b~Binomial(b+c,
    0.5)) — the "better than baseline" framing GG's spec asks for
    throughout, as opposed to the existing two-sided test
    (ml.direction.evaluate.compute_direction_metrics's `p_value`), which
    only asks "does the model's win rate differ from 50%" and can't by
    itself distinguish a real edge from a real DISADVANTAGE.
    """
    n = b + c
    if n == 0:
        return {"b": b, "c": c, "n_discordant": 0, "p_value_one_sided": 1.0}
    result = binomtest(b, n, 0.5, alternative=alternative)
    return {"b": b, "c": c, "n_discordant": n, "p_value_one_sided": float(result.pvalue)}


def block_bootstrap_confirm(
    loss_diff: list[float], horizon: int, n_boot: int = 2000, seed: int = 42
) -> dict:
    """Moving block bootstrap, block length = horizon, as an independent,
    non-parametric confirmation of the HAC-corrected Diebold-Mariano
    result (GG spec: "confirm with a block bootstrap (block length >= h)").

    Resamples overlapping blocks of length `horizon` from the ORIGINAL
    (not null-centered) loss-differential series, with replacement, to
    build an empirical distribution of the sample mean under the actual
    autocorrelation structure. p_value_one_sided is the fraction of
    bootstrap-resampled means that are >= 0 (null-consistent) — a simple,
    standard percentile-crossing check: strong, consistent evidence that
    the model beats the baseline should leave few resamples on the wrong
    side of zero. This is a confirmatory diagnostic, not a replacement for
    the DM test's own inference — reported alongside it, never instead of
    it.
    """
    d = np.asarray(loss_diff, dtype=float)
    n = len(d)
    block_len = max(1, horizon)
    if n < block_len:
        return {
            "p_value_one_sided": None,
            "n_boot": 0,
            "block_length": block_len,
            "observed_mean_diff": float(d.mean()) if n else None,
        }

    rng = np.random.default_rng(seed)
    n_blocks_needed = int(np.ceil(n / block_len))
    max_start = n - block_len
    boot_means = np.empty(n_boot)
    for b_i in range(n_boot):
        starts = rng.integers(0, max_start + 1, size=n_blocks_needed)
        sample = np.concatenate([d[s : s + block_len] for s in starts])[:n]
        boot_means[b_i] = sample.mean()

    observed_mean = float(d.mean())
    p_one_sided = float(np.mean(boot_means >= 0))
    return {
        "observed_mean_diff": observed_mean,
        "bootstrap_mean_of_means": float(boot_means.mean()),
        "p_value_one_sided": p_one_sided,
        "n_boot": n_boot,
        "block_length": block_len,
        "ci_95": [float(np.percentile(boot_means, 2.5)), float(np.percentile(boot_means, 97.5))],
    }


def bonferroni(p_values: list[float], alpha: float = 0.05) -> dict:
    """Family-wise error rate control: reject H0 only where p <= alpha/m."""
    m = len(p_values)
    threshold = alpha / m if m > 0 else alpha
    return {
        "method": "bonferroni",
        "m": m,
        "alpha": alpha,
        "threshold": threshold,
        "significant": [p is not None and p <= threshold for p in p_values],
    }


def benjamini_hochberg(p_values: list[float], alpha: float = 0.05) -> dict:
    """False discovery rate control (Benjamini-Hochberg step-up procedure).

    Ranks p-values ascending; finds the largest rank k such that
    p_(k) <= (k/m) * alpha; rejects H0 for all ranks <= k. Typically less
    conservative than Bonferroni — reported alongside it (GG's spec: both),
    not as a replacement.
    """
    m = len(p_values)
    valid = [(i, p) for i, p in enumerate(p_values) if p is not None]
    indexed = sorted(valid, key=lambda x: x[1])
    significant = [False] * m
    max_k = 0
    for rank, (_idx, p) in enumerate(indexed, start=1):
        threshold = (rank / len(indexed)) * alpha if indexed else alpha
        if p <= threshold:
            max_k = rank
    for rank, (idx, _p) in enumerate(indexed, start=1):
        if rank <= max_k:
            significant[idx] = True
    return {
        "method": "benjamini_hochberg",
        "m": m,
        "alpha": alpha,
        "significant": significant,
        "n_significant": max_k,
    }


def power_for_observed_effect(
    effective_n_val: float, mean_diff: float, std_diff: float, alpha: float = 0.05
) -> float:
    """Post-hoc statistical power to detect an effect of the OBSERVED size,
    at the OBSERVED effective n, one-sided at level alpha. Used only for
    reporting "how much data would be needed" context (GG spec item 4's
    n-for-80%-power requirement) — never as evidence the effect is real
    (post-hoc power computed from an observed effect is a well-known
    statistical trap when used that way; here it answers a forward-looking
    design question — how big would a FUTURE confirming sample need to be —
    not a backward-looking validity claim about the number already in hand).
    """
    if std_diff <= 0 or effective_n_val <= 0:
        return float("nan")
    se = std_diff / math.sqrt(effective_n_val)
    z_alpha = float(norm.ppf(1 - alpha))
    z_effect = abs(mean_diff) / se - z_alpha
    return float(norm.cdf(z_effect))


def n_for_power(
    mean_diff: float, std_diff: float, power: float = 0.80, alpha: float = 0.05
) -> float:
    """Effective n required to detect an effect of this size at the given
    power, one-sided at level alpha (standard normal-approximation sample
    size formula: n = ((z_alpha + z_power) * std / mean_diff)^2)."""
    if mean_diff == 0 or std_diff <= 0:
        return float("inf")
    z_alpha = float(norm.ppf(1 - alpha))
    z_power = float(norm.ppf(power))
    return float(((z_alpha + z_power) * std_diff / abs(mean_diff)) ** 2)
