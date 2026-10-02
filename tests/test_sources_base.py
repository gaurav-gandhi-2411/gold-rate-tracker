"""Tests for ml.sources.base.validate_observed_at — the shared fail-closed guard.

Every adapter that derives ``observed_at`` from a source-provided field
(kalyan, malabar, ibja) routes it through this function before returning a
reading; these tests cover the guard itself, independent of any one adapter.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from ml.sources.base import SourceStructureError, validate_observed_at


def test_plausible_observed_at_passes_through():
    ts = datetime(2026, 9, 24, 10, 30, tzinfo=UTC)
    assert validate_observed_at(ts, source="kalyan") == ts


def test_epoch_placeholder_raises_structure_error():
    # The exact corrupt value found in data/fusion_snapshots.parquet: Kalyan's
    # "01 Jan 1970 00:00" IST sentinel, converted to UTC.
    epoch_ist_as_utc = datetime(1969, 12, 31, 18, 30, tzinfo=UTC)
    with pytest.raises(SourceStructureError, match="implausible observed_at"):
        validate_observed_at(epoch_ist_as_utc, source="kalyan")


def test_year_just_below_threshold_raises():
    ts = datetime(2019, 12, 31, tzinfo=UTC)
    with pytest.raises(SourceStructureError, match="before 2020"):
        validate_observed_at(ts, source="malabar")


def test_year_at_threshold_passes():
    ts = datetime(2020, 1, 1, tzinfo=UTC)
    assert validate_observed_at(ts, source="ibja") == ts


def test_far_future_raises():
    ts = datetime.now(UTC) + timedelta(days=30)
    with pytest.raises(SourceStructureError, match="future"):
        validate_observed_at(ts, source="kalyan")


def test_within_future_skew_passes():
    ts = datetime.now(UTC) + timedelta(hours=1)
    assert validate_observed_at(ts, source="kalyan") == ts
