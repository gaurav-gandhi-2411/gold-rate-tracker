"""ml.data_sanity -- turn non-positive / non-finite rates into NaN at every live-path read.

A rate (IBJA pm_916/am_916, gold_usd, usd_inr) is a price: it is never <= 0. A 0 (an unpublished
fix stored as 0, a failed bar) passes ``dropna`` and ``np.isfinite`` and then divides by zero in
``math.log(a / b)`` or poisons a log-return with -inf. Mapping it to NaN lets the existing
``dropna`` / ``isfinite`` guards do their job.
"""

from __future__ import annotations

from typing import TypeVar

import numpy as np
import pandas as pd

IBJA_RATE_COLUMNS = ("pm_916", "am_916")

_T = TypeVar("_T", pd.Series, pd.DataFrame)


def positive_or_nan(obj: _T) -> _T:
    """Return a copy of a Series/DataFrame with every value <= 0 or non-finite set to NaN."""
    num = obj.astype(float)
    return num.where(np.isfinite(num) & (num > 0))


def sanitize_ibja(df: pd.DataFrame) -> pd.DataFrame:
    """Copy of an ibja_rates frame with pm_916 / am_916 non-positive values set to NaN."""
    out = df.copy()
    for col in IBJA_RATE_COLUMNS:
        if col in out.columns:
            out[col] = positive_or_nan(out[col])
    return out
