"""ml.nextfix_intraday (ADR 066): hourly world-price shadow for the windows where ml.nextfix holds a fix."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from ml import nextfix
from ml import nextfix_intraday as nfi


def _bars(start: str, hours: int, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=hours, freq="h", tz=UTC)  # bar START times
    gold = 4200 * np.exp(np.cumsum(rng.normal(0, 0.002, hours)))
    return pd.DataFrame({"gold_usd": gold, "usd_inr": 96.0}, index=idx)


def _ibja_following(bars: pd.DataFrame, days: pd.DatetimeIndex, k: float = 0.0323) -> pd.DataFrame:
    """IBJA fixes equal to k x G at the fix instant (perfect pass-through), per 10 g."""
    end = bars.index + pd.Timedelta(hours=1)
    gv = pd.Series((bars["gold_usd"] * bars["usd_inr"]).to_numpy(), index=end)
    rows = []
    for d in days:
        vals = {}
        for kind, hm in (("am", nextfix.IBJA_AM_PUBLISH_UTC), ("pm", nextfix.IBJA_PM_PUBLISH_UTC)):
            t = pd.Timestamp(datetime(d.year, d.month, d.day, hm[0], hm[1], tzinfo=UTC))
            pos = gv.index.searchsorted(t, side="right") - 1
            vals[f"{kind}_916"] = float(gv.iloc[pos]) * k * 10 if pos >= 0 else np.nan
        rows.append({"date": d, **vals})
    return pd.DataFrame(rows)


def _setup(tmp_path: Path, days: int = 30) -> tuple[pd.DataFrame, list]:
    bars = _bars("2026-08-01", 24 * (days + 3))
    bars.to_parquet(tmp_path / "macro_intraday.parquet")
    ib = _ibja_following(bars, pd.bdate_range("2026-08-03", periods=days))
    ib.to_parquet(tmp_path / "ibja_rates.parquet")
    return bars, nextfix.fix_events(nextfix.load_ibja_full(tmp_path / "ibja_rates.parquet"))


def test_value_at_reads_the_last_bar_that_ended_and_rejects_stale_readings(tmp_path: Path):
    _bars("2026-08-01", 10).to_parquet(tmp_path / "macro_intraday.parquet")
    g = nfi.load_bars(tmp_path)
    v, end = nfi.value_at(g, datetime(2026, 8, 1, 3, 30, tzinfo=UTC))
    assert end == datetime(2026, 8, 1, 3, 0, tzinfo=UTC)  # bar 02:00-03:00
    assert v == pytest.approx(g.loc[pd.Timestamp("2026-08-01 03:00", tz=UTC)])
    assert nfi.value_at(g, datetime(2026, 8, 1, 17, 0, tzinfo=UTC)) is None  # > 6 h since last bar


def test_forecast_at_passes_the_world_move_through_and_tags_the_window(tmp_path: Path):
    _, events = _setup(tmp_path)
    g = nfi.load_bars(tmp_path)
    fc = nfi.forecast_at(events, g, datetime(2026, 8, 5, 9, 15, tzinfo=UTC))
    assert (
        fc["base_kind"] == "am"
        and fc["window"] == "after_morning_rate"
        and fc["target_kind"] == "pm"
    )
    assert fc["pred"]["beta_0.0"] == fc["base"]
    assert fc["pred"]["beta_1.0"] == pytest.approx(
        fc["base"] * math.exp(fc["x_since_fix"]), abs=0.01
    )
    assert (
        nfi.forecast_at(events, g, datetime(2026, 8, 5, 15, 0, tzinfo=UTC))["window"]
        == "after_afternoon_rate"
    )
    assert (
        nfi.forecast_at(events, g, datetime(2026, 8, 5, 23, 0, tzinfo=UTC))["window"]
        == "after_us_close"
    )


def test_shadow_logs_once_per_run_and_resolves_the_next_fix(tmp_path: Path):
    _, events = _setup(tmp_path)
    g = nfi.load_bars(tmp_path)
    t = datetime(2026, 8, 5, 9, 15, tzinfo=UTC)
    entries = nfi.update_shadow([], events, g, t)
    entries = nfi.update_shadow(entries, events, g, t)  # same run time: not logged twice
    assert len(entries) == 1
    e = entries[0]
    assert e["target"] is not None and e["target_date"] == "2026-08-05"  # same-day PM fix exists


def test_backtest_finds_the_pass_through_when_fixes_follow_the_world_price(tmp_path: Path):
    _, events = _setup(tmp_path)
    bt = nfi.backtest(events, nfi.load_bars(tmp_path))
    w = bt["scored_on_last_decision_per_fix"]["after_afternoon_rate"]
    assert w["ready"] and w["mae_beta_1.0"] < w["mae_beta_0.0"]
    assert w["change_beta_1.0_ci95"][1] < 0
    assert w["direction_hit_rate"] > 0.7


def test_run_writes_the_shadow_and_backtest_files(tmp_path: Path):
    _setup(tmp_path)
    out = nfi.run(now=datetime(2026, 8, 20, 15, 0, tzinfo=UTC), data_dir=tmp_path)
    shadow = json.loads((tmp_path / "nextfix_intraday_shadow.json").read_text())
    assert shadow["entries"] and shadow["summary"]["n_entries"] == 1
    bt = json.loads((tmp_path / "nextfix_intraday_backtest.json").read_text())
    assert bt["ready"] and bt["n_decisions"] > 100
    assert out["backtest"]["n_decisions"] == bt["n_decisions"]


def test_shadow_never_touches_the_forecast(tmp_path: Path):
    _setup(tmp_path)
    nfi.run(now=datetime(2026, 8, 20, 15, 0, tzinfo=UTC), data_dir=tmp_path)
    assert not (tmp_path / "forecast.json").exists()
    src = (Path(nfi.__file__)).read_text()
    assert "forecast.json" not in src.split('"""', 2)[2]
