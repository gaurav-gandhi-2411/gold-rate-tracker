"""Basis factor utilities — stateless, no file I/O.

Basis is defined as ``IBJA_price / MCX_price``. A stable basis means the local
(IBJA) and international (MCX/COMEX via GC=F + conversion) prices move together.
A significant drift signals import duty changes, currency moves, or local demand
shocks that may warrant a model refit.

These functions are purposely stateless and free of pandas/file I/O so they can
be called inline without side effects.
"""

from __future__ import annotations


def compute_basis_factor(ibja_price: float, mcx_price: float) -> float:
    """Return ``ibja_price / mcx_price``.

    Both prices must be in the same unit (e.g. both Rs/10g or both USD/oz).

    Raises:
        ValueError: if ``mcx_price`` is zero.
    """
    if mcx_price == 0.0:
        raise ValueError("mcx_price must be non-zero")
    return ibja_price / mcx_price


def apply_basis_adjustment(
    forecast_inr: float,
    current_ibja: float,
    current_mcx: float,
) -> float:
    """Scale a forecast by the current basis factor.

    Useful when the model was trained on IBJA prices but the current
    ibja/mcx basis has drifted from the training-period mean. The
    adjustment nudges the forecast toward the current price relationship.

    Args:
        forecast_inr:  Raw model forecast in INR.
        current_ibja:  Latest IBJA price (same unit as ``forecast_inr``).
        current_mcx:   Latest MCX/GC=F price (same unit).

    Returns:
        ``forecast_inr * (current_ibja / current_mcx)``.
    """
    return forecast_inr * compute_basis_factor(current_ibja, current_mcx)


def should_refit(
    current_basis: float,
    reference_basis: float,
    threshold: float = 0.02,
) -> bool:
    """Return True when the basis has drifted beyond the given threshold.

    Threshold is relative: ``|current - reference| / reference > threshold``.
    Default 0.02 means a >2% drift triggers a refit recommendation.

    Raises:
        ValueError: if ``reference_basis`` is zero.
    """
    if reference_basis == 0.0:
        raise ValueError("reference_basis must be non-zero")
    return abs(current_basis - reference_basis) / reference_basis > threshold
