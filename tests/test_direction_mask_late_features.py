"""ml.direction.leak_checks.mask_late_features (GG decision F1, ADR 061 amendment A1)."""

from __future__ import annotations

import math

import pandas as pd
from ml.direction.leak_checks import check_fold, mask_late_features
from ml.leak_guard import LeakGuard

COLS = ["usd_inr", "sensex", "dow"]


def _row(as_of: str, source: str, usd_inr: float, usd_asof: str, capture: str | None) -> dict:
    return {
        "as_of_date": as_of,
        "source": source,
        "capture_utc": capture,
        "usd_inr": usd_inr,
        "usd_inr_asof_date": usd_asof,
        "sensex": 80000.0 + usd_inr,
        "sensex_asof_date": as_of,  # BSE closes 15:30 IST on D: known before IST midnight
        "dow": 1.0,
    }


def _backfill() -> pd.DataFrame:
    # Backfilled rows hold the INR=X close dated D, published after IST midnight of D (F1).
    return pd.DataFrame(
        [
            _row("2026-03-02", "backfill_yfinance", 83.0, "2026-03-02", None),
            _row("2026-03-03", "backfill_yfinance", 83.5, "2026-03-03", None),
            _row("2026-03-04", "backfill_yfinance", 84.0, "2026-03-04", None),
        ]
    )


def test_late_input_takes_the_previous_known_value_and_its_clock():
    out, counts = mask_late_features(_backfill(), COLS)
    assert counts == {"usd_inr": 3}
    assert math.isnan(out.loc[0, "usd_inr"])  # no earlier row: NaN, imputed later
    assert pd.isna(out.loc[0, "usd_inr_asof_date"])
    assert out.loc[1, "usd_inr"] == 83.0 and out.loc[1, "usd_inr_asof_date"] == "2026-03-02"
    assert out.loc[2, "usd_inr"] == 83.5 and out.loc[2, "usd_inr_asof_date"] == "2026-03-03"
    # inputs known in time and calendar features are untouched
    assert out["sensex"].tolist() == _backfill()["sensex"].tolist()
    assert out["dow"].tolist() == [1.0, 1.0, 1.0]


def test_input_does_not_mutate_its_argument():
    ds = _backfill()
    mask_late_features(ds, COLS)
    assert ds["usd_inr"].tolist() == [83.0, 83.5, 84.0]


def test_a_raise_mode_feature_guard_passes_after_masking_and_fails_before():
    ds = _backfill()
    out, _ = mask_late_features(ds, COLS)
    for frame, should_raise in ((out, False), (ds, True)):
        guard = LeakGuard("features", mode="raise")
        labels = LeakGuard("labels", mode="raise")
        raised = False
        try:
            check_fold(labels, guard, frame.iloc[2], [], "label_binary", COLS)
        except Exception:
            raised = True
        assert raised is should_raise


def test_live_rows_known_at_capture_are_unchanged():
    ds = pd.DataFrame(
        [
            _row("2026-09-01", "live_pit", 88.0, "2026-09-01", "2026-09-01T12:00:00Z"),
            _row("2026-09-02", "live_pit", 88.2, "2026-09-02", "2026-09-02T12:00:00Z"),
        ]
    )
    out, counts = mask_late_features(ds, COLS)
    assert counts == {}
    assert out["usd_inr"].tolist() == [88.0, 88.2]


def test_donor_must_share_the_rows_source():
    ds = pd.DataFrame(
        [
            _row("2026-06-01", "live_pit", 85.0, "2026-06-01", "2026-06-01T12:00:00Z"),
            _row("2026-06-02", "backfill_yfinance", 85.5, "2026-06-02", None),
        ]
    )
    out, counts = mask_late_features(ds, COLS)
    assert counts == {"usd_inr": 1}
    assert math.isnan(out.loc[1, "usd_inr"])  # the live row is not an allowed donor


def test_rows_without_provenance_are_returned_unchanged():
    ds = pd.DataFrame({"as_of_date": ["2026-03-02"], "usd_inr": [83.0]})
    out, counts = mask_late_features(ds, COLS)
    assert counts == {}
    assert out.equals(ds)
