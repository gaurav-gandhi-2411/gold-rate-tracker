"""ml.direction.price_units — explicit price-unit declarations for direction datasets.

`ml.direction.dataset.build_dataset` (IBJA, INR per 10 g) and the COMEX daily
dataset (GC=F, USD per troy oz) share one column schema (`current_pm916`,
`window_min_pm916_hN`, `delta_per_gram_hN`) so the same M2 machinery runs on
both — but the NAMES are INR-flavoured and the numbers are not. Code that
applies an INR-denominated constant (the /10 per-10g→per-gram conversion, a
₹/gram dead band) to those columns silently produces garbage on a USD series:
`add_buyer_decision_binary`'s ₹50/g threshold became "a >$500/oz dip" on
COMEX, the label was ~always 0, and the run reported a meaningless 100%
accuracy (2026-09-23).

A dataset states its units in `df.attrs["price_units"]`; INR-constant code
calls `require_inr_per_10g` first. Undeclared counts as a mismatch — this
fails closed, never defaults to INR.
"""

from __future__ import annotations

import pandas as pd

PRICE_UNITS_ATTR = "price_units"
INR_PER_10G = "INR_per_10g"
USD_PER_TROY_OZ = "USD_per_troy_oz"

# Second, independent guard against a WRONG declaration: 22K IBJA per 10 g has
# been above ~₹25,000 for the whole 2013+ history this repo uses, and GC=F
# has never traded above ~$5,000/oz. 10,000 sits >2x from both.
_INR_PER_10G_MIN_PLAUSIBLE_MEDIAN = 10_000.0


class PriceUnitsError(ValueError):
    """An INR-denominated constant was about to be applied to a series that
    is not (or is not declared as) INR per 10 g."""


def declare_units(df: pd.DataFrame, units: str) -> pd.DataFrame:
    if units not in (INR_PER_10G, USD_PER_TROY_OZ):
        raise ValueError(f"unknown price units: {units!r}")
    df.attrs[PRICE_UNITS_ATTR] = units
    return df


def require_inr_per_10g(df: pd.DataFrame, caller: str) -> None:
    units = df.attrs.get(PRICE_UNITS_ATTR)
    if units != INR_PER_10G:
        raise PriceUnitsError(
            f"{caller} applies INR-per-10g constants, but the dataset declares "
            f"price_units={units!r}. Use a unit-free (percentage) variant for other "
            f"series, or declare units via ml.direction.price_units.declare_units."
        )
    if "current_pm916" in df.columns:
        median = df["current_pm916"].median()
        if pd.notna(median) and median < _INR_PER_10G_MIN_PLAUSIBLE_MEDIAN:
            raise PriceUnitsError(
                f"{caller}: dataset is declared {INR_PER_10G} but median current_pm916 "
                f"is {median:,.2f}, implausibly low for INR/10g (looks like USD/oz)."
            )
