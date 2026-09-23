"""Tests for ml.direction.price_units — INR-denominated constants must never
be applied to a USD series (the 2026-09-23 COMEX buyer_decision_h1 incident:
a ₹50/g dead band read as a >$500/oz dip, label ~always 0, "100% accuracy")."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from ml.direction.evaluate_reframed import TARGET_BUILDERS, run_walk_forward_reframed
from ml.direction.price_units import (
    INR_PER_10G,
    PRICE_UNITS_ATTR,
    USD_PER_TROY_OZ,
    PriceUnitsError,
    declare_units,
    require_inr_per_10g,
)
from ml.direction.reframed_targets import (
    add_buyer_decision_binary,
    add_buyer_decision_binary_pct,
)


def _usd_frame() -> pd.DataFrame:
    # Realistic GC=F magnitudes: a $40/oz (1.6%) dip — a real buyer's dip.
    return pd.DataFrame({"current_pm916": [2500.0], "window_min_pm916_h1": [2460.0]})


class TestInrConstantRejectsUsd:
    def test_usd_declared_series_raises(self) -> None:
        with pytest.raises(PriceUnitsError, match="USD_per_troy_oz"):
            add_buyer_decision_binary(declare_units(_usd_frame(), USD_PER_TROY_OZ), 1)

    def test_undeclared_series_fails_closed(self) -> None:
        with pytest.raises(PriceUnitsError, match="None"):
            add_buyer_decision_binary(_usd_frame(), 1)

    def test_usd_magnitudes_mislabelled_as_inr_raise(self) -> None:
        with pytest.raises(PriceUnitsError, match="implausibly low"):
            add_buyer_decision_binary(declare_units(_usd_frame(), INR_PER_10G), 1)

    def test_genuine_inr_series_passes(self) -> None:
        df = declare_units(
            pd.DataFrame({"current_pm916": [70000.0], "window_min_pm916_h1": [68000.0]}),
            INR_PER_10G,
        )
        require_inr_per_10g(df, "test")  # no raise
        assert add_buyer_decision_binary(df, 1).iloc[0] == 1.0

    def test_walk_forward_on_usd_dataset_with_default_builders_raises(self) -> None:
        # The exact incident path: evaluate_reframed's default TARGET_BUILDERS
        # applied to a USD dataset must stop, not score a degenerate label.
        rng = np.random.default_rng(42)
        n = 40
        price = 2500.0 + np.cumsum(rng.normal(0, 15, n))
        ds = pd.DataFrame(
            {
                "as_of_date": pd.date_range("2025-01-01", periods=n).strftime("%Y-%m-%d"),
                "current_pm916": price,
                "window_min_pm916_h1": np.roll(price, -1),
                "label_date_h1": pd.date_range("2025-01-02", periods=n).strftime("%Y-%m-%d"),
                "f1": rng.normal(size=n),
            }
        )
        declare_units(ds, USD_PER_TROY_OZ)
        with pytest.raises(PriceUnitsError):
            run_walk_forward_reframed(ds, "buyer_decision", 1, feature_cols=["f1"])


class TestUnitFreeVariant:
    def test_pct_variant_scores_usd_dip(self) -> None:
        df = declare_units(_usd_frame(), USD_PER_TROY_OZ)
        # 40/2500 = 1.6% dip > 0.5%
        assert add_buyer_decision_binary_pct(df, 1, dip_pct=0.5).iloc[0] == 1.0
        assert add_buyer_decision_binary_pct(df, 1, dip_pct=2.0).iloc[0] == 0.0

    def test_pct_variant_nan_propagates(self) -> None:
        df = pd.DataFrame({"current_pm916": [2500.0], "window_min_pm916_h1": [None]})
        assert pd.isna(add_buyer_decision_binary_pct(df, 1, dip_pct=0.5).iloc[0])

    def test_target_builders_override_swaps_in_pct_variant(self) -> None:
        builders = dict(TARGET_BUILDERS)
        builders["buyer_decision"] = lambda d, h: add_buyer_decision_binary_pct(d, h, 0.5)
        assert builders["buyer_decision"] is not TARGET_BUILDERS["buyer_decision"]


class TestDeclarations:
    def test_unknown_units_rejected(self) -> None:
        with pytest.raises(ValueError):
            declare_units(pd.DataFrame(), "INR_per_gram")

    def test_attr_survives_filter_and_copy(self) -> None:
        df = declare_units(pd.DataFrame({"a": [1, 2, 3]}), INR_PER_10G)
        assert df[df["a"] > 1].reset_index(drop=True).copy().attrs[PRICE_UNITS_ATTR] == INR_PER_10G
