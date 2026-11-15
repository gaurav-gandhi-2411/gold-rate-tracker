"""A 0 (not NaN) rate must never reach a division on the live path (P0, 2026-11-12).

Since 2026-11-12 13:10Z the next-fix model failed every run with ``float division by zero`` and one
run published Rs.0. ``dropna`` / ``np.isfinite`` let a stored 0 through. Hypothesis (INFERRED, the
real data is encrypted): a 0 in pm_916 / am_916 or in the world-price series. Every test pins its
clock; none reads the real one.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from ml import inference as inf
from ml import nextfix
from ml import nextfix_intraday as nfi
from ml.data_sanity import positive_or_nan, sanitize_ibja

from tests.test_inference_main import _disable_fusion, _make_backtest, _make_probe


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(nextfix, "MLP_MODELS", 1)
    monkeypatch.setattr(nextfix, "MLP_EPOCHS", 20)
    monkeypatch.setattr(nextfix, "MIN_TRAIN", 40)


def _synthetic(n_days: int = 120, seed: int = 3) -> tuple[pd.DataFrame, pd.Series]:
    """Weekday IBJA fixes (pm_916/am_916 per 10 g) that follow the world price, plus that price."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2026-01-05", periods=n_days)
    glob_close = 125000 * np.exp(np.cumsum(rng.normal(0, 0.01, n_days)))
    pm = 13000 * np.exp(0.5 * np.log(glob_close / glob_close[0]))
    ibja = pd.DataFrame({"date": days, "pm_916": pm * 10.0, "am_916": pm * 10.0 * 0.999})
    glob = pd.Series(glob_close, index=days)
    glob = glob.reindex(pd.date_range(days.min(), days.max(), freq="D")).ffill()
    return ibja, glob


def _with_zeros(ibja: pd.DataFrame) -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp]:
    out = ibja.copy()
    pm_day, am_day = out["date"].iloc[80], out["date"].iloc[90]
    out.loc[out["date"] == pm_day, "pm_916"] = 0.0
    out.loc[out["date"] == am_day, "am_916"] = 0.0
    return out, pm_day, am_day


def test_positive_or_nan_maps_zero_negative_and_inf_to_nan():
    s = pd.Series([1.0, 0.0, -2.0, np.inf, np.nan, 5.0])
    out = positive_or_nan(s)
    assert out.isna().tolist() == [False, True, True, True, True, False]
    assert out.iloc[[0, 5]].tolist() == [1.0, 5.0]
    assert s.iloc[1] == 0.0  # the input is not mutated


def test_sanitize_ibja_only_touches_the_rate_columns():
    df = pd.DataFrame({"date": ["2026-01-05"], "pm_916": [0.0], "am_916": [-1.0], "x": [0.0]})
    out = sanitize_ibja(df)
    assert out[["pm_916", "am_916"]].isna().all(axis=None) and out["x"].iloc[0] == 0.0


def test_loaders_turn_a_zero_pm_and_a_zero_am_into_nan(tmp_path: Path):
    ibja, pm_day, am_day = _with_zeros(_synthetic()[0])
    ibja.to_parquet(tmp_path / "ibja_rates.parquet")
    full = nextfix.load_ibja_full(tmp_path / "ibja_rates.parquet")
    assert np.isnan(full.loc[full["date"] == pm_day, "pm"].iloc[0])
    assert np.isnan(full.loc[full["date"] == am_day, "am"].iloc[0])
    pm_only = nextfix.load_ibja(tmp_path / "ibja_rates.parquet")
    assert pm_day not in set(pm_only["date"])  # no PM fix that day
    assert (pm_only["pm"] > 0).all()
    events = nextfix.fix_events(full)
    assert all(e[3] > 0 for e in events)


def test_global_series_drops_a_zero_in_the_macro_product_and_the_label_seed(tmp_path: Path):
    _, glob = _synthetic()
    macro = pd.DataFrame({"gold_usd": glob.to_numpy() / 100.0, "usd_inr": 100.0}, index=glob.index)
    macro.iloc[40, 0] = 0.0  # gold_usd 0
    macro.iloc[50, 1] = 0.0  # usd_inr 0
    label = pd.DataFrame(
        {"raw_pre_duty": [0.0, 120000.0]}, index=pd.to_datetime(["2025-12-01"] * 2)
    )
    label.to_parquet(tmp_path / "label.parquet")
    s = nextfix.global_series(macro, tmp_path / "label.parquet")
    assert (s.dropna() > 0).all() and np.isfinite(s.to_numpy()).all()


def test_run_still_records_and_stays_active_with_zero_rows(tmp_path: Path):
    ibja, pm_day, _ = _with_zeros(_synthetic()[0])
    glob = _synthetic()[1]
    ibja.to_parquet(tmp_path / "ibja_rates.parquet")
    macro = pd.DataFrame({"gold_usd": glob.to_numpy() / 100.0, "usd_inr": 100.0}, index=glob.index)
    macro.iloc[60, 0] = 0.0
    d0 = ibja["date"].iloc[-1]
    now = datetime(d0.year, d0.month, d0.day, 23, tzinfo=UTC)  # pinned clock
    out = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    assert out["forecast"]["active"] is True
    assert out["eval"]["n"] > 0
    p3 = nextfix.load_oos(tmp_path / "nextfix_p3_oos.json")
    assert p3 and pm_day.strftime("%Y-%m-%d") not in {f["d0"] for f in p3}


def _bars(hours: int = 48) -> pd.DataFrame:
    idx = pd.date_range("2026-08-03", periods=hours, freq="h", tz=UTC)  # bar START times
    return pd.DataFrame({"gold_usd": 4200.0, "usd_inr": 96.0}, index=idx)


def test_load_bars_drops_a_zero_bar(tmp_path: Path):
    bars = _bars()
    bars.iloc[10, 0] = 0.0
    bars.iloc[20, 1] = 0.0
    bars.to_parquet(tmp_path / "macro_intraday.parquet")
    g = nfi.load_bars(tmp_path)
    assert len(g) == len(bars) - 2 and (g > 0).all()


def test_forecast_at_is_none_for_a_zero_fix_bar():
    t_fix = datetime(2026, 8, 3, 11, 30, tzinfo=UTC)
    events = [(t_fix, "pm", pd.Timestamp("2026-08-03"), 13000.0)]
    ends = pd.date_range("2026-08-03 09:00", periods=8, freq="h", tz=UTC)
    g = pd.Series(4200.0 * 96.0, index=ends)
    g.loc[pd.Timestamp("2026-08-03 11:00", tz=UTC)] = 0.0  # the bar the fix reads
    assert nfi.forecast_at(events, g, datetime(2026, 8, 3, 14, 0, tzinfo=UTC)) is None
    g.loc[pd.Timestamp("2026-08-03 11:00", tz=UTC)] = 4200.0 * 96.0
    assert nfi.forecast_at(events, g, datetime(2026, 8, 3, 14, 0, tzinfo=UTC)) is not None


def _prices(n: int, last_22k: int) -> list[dict]:
    start = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    rows = [
        {
            "timestamp": (start + timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "22k": 14400 + (i % 7) * 20,
            "source": "smoke-test",
        }
        for i in range(n)
    ]
    rows[-1]["22k"] = last_22k
    return rows


def test_inference_refuses_to_write_a_non_positive_headline(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)
    (tmp_path / "prices.json").write_text(json.dumps(_prices(40, 14400)))
    (tmp_path / "backtest.json").write_text(json.dumps(_make_backtest()))
    (tmp_path / "chronos_probe.json").write_text(json.dumps(_make_probe("success")))
    (tmp_path / "calibration.json").write_text(json.dumps({"valid": False}))
    _disable_fusion(monkeypatch)
    # The 2026-11-11 incident: a price tier handed back 0 (ibja_calibrated, current_22k = 0).
    zero_tier = (0, "ibja_calibrated", None, None, None, None, None, None, None, None, None, None)
    monkeypatch.setattr(inf, "_select_price_source", lambda *a, **k: zero_tier)
    sentinel = json.dumps({"predicted_22k": 14400}) + "\n"
    (tmp_path / "forecast.json").write_text(sentinel)
    now = datetime(2026, 2, 9, 12, 0, tzinfo=UTC)  # pinned
    with pytest.raises(inf.NonPositiveForecastError):
        inf.main(now=now)
    assert (tmp_path / "forecast.json").read_text() == sentinel  # previous forecast untouched


def test_assert_positive_forecast_accepts_good_and_rejects_zero_negative_nan():
    inf._assert_positive_forecast(a=1, b=2.5, c=None)
    for bad in (0, -1, float("nan"), math.inf):
        with pytest.raises(inf.NonPositiveForecastError):
            inf._assert_positive_forecast(a=14000, b=bad)
