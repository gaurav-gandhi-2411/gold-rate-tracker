"""ml.direction.leak_checks -- known_at inputs for the direction walk-forwards (ADR 061).

Prediction moment of a fold whose test row is dated D: ist_day_end(D), the first instant after
IST day D. build_dataset keeps only rows whose IBJA PM is dated D, so every forecast in these
harnesses is issued after D's PM (17:00 IST) and before IST midnight; the latest such instant is
used. The walk-forwards' own date embargo (label_date < as_of_date) is stricter by one day at
h1 -- the guard is a necessary condition, not a replacement for it.

A label dated L is IBJA's PM on L, known at ml.known_at.ibja_known_at(L, "pm").
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import pandas as pd

from ml.direction.dataset import PROVENANCE_COLS
from ml.known_at import (
    IBJA_FIELDS,
    MACRO_DAILY_CLOCKS,
    ibja_known_at,
    ist_day_end,
    snapshot_field_known_at,
)
from ml.leak_guard import KnownInput, LeakGuard

TIMED_FEATURES: frozenset[str] = frozenset({*MACRO_DAILY_CLOCKS, *IBJA_FIELDS, "tanishq_22k"})


def fold_prediction_moment(as_of_date: Any) -> pd.Timestamp:
    return ist_day_end(as_of_date)


def training_label_inputs(label_dates: Iterable[Any], label_col: str) -> list[KnownInput]:
    """One KnownInput per training label (and the persistence baseline, which copies one)."""
    return [
        KnownInput(f"{label_col}@{pd.Timestamp(d).date()}", "ibja_pm_label", ibja_known_at(d, "pm"))
        for d in label_dates
    ]


def feature_inputs_for_row(
    row: Mapping[str, Any] | pd.Series, feature_cols: Iterable[str]
) -> list[KnownInput]:
    """KnownInputs for the timed features of a test row (calendar features are known in
    advance and not listed). Raises KeyError when the row lacks provenance columns."""
    for col in PROVENANCE_COLS:
        if col not in row:
            raise KeyError(f"test row has no {col!r}; build_dataset provenance missing")
    out = []
    for col in feature_cols:
        if col not in TIMED_FEATURES:
            continue
        known = snapshot_field_known_at(row, col)
        if known is not None:
            out.append(KnownInput(col, f"feature:{col}", known))
    return out


def check_fold(
    labels_guard: LeakGuard,
    features_guard: LeakGuard | None,
    test_row: Mapping[str, Any] | pd.Series,
    train_label_dates: Iterable[Any],
    label_col: str,
    feature_cols: Iterable[str],
) -> None:
    """Run both guards for one fold. A features guard on a row without provenance (synthetic
    test datasets) counts the fold as unchecked instead of failing: it is a report-mode guard."""
    t = fold_prediction_moment(test_row["as_of_date"])
    ctx = f"as_of={test_row['as_of_date']}"
    labels_guard.check(t, training_label_inputs(train_label_dates, label_col), context=ctx)
    if features_guard is None:
        return
    try:
        feats = feature_inputs_for_row(test_row, feature_cols)
    except KeyError:
        features_guard.n_unchecked += 1
        return
    features_guard.check(t, feats, context=ctx)
