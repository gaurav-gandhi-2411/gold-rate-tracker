"""ml.nextfix (ADR 064): the next official-rate forecast, its track record, and its use in inference."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from ml import nextfix

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(nextfix, "MLP_MODELS", 1)
    monkeypatch.setattr(nextfix, "MLP_EPOCHS", 20)


def _synthetic(n_days: int = 140, seed: int = 0) -> tuple[pd.DataFrame, pd.Series]:
    """Weekday IBJA fixes that follow the global price with a lag, plus the global series."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2026-01-05", periods=n_days)
    glob_close = 125000 * np.exp(np.cumsum(rng.normal(0, 0.01, n_days)))
    # the fix on day t sits between the close of t-1 and the close of t
    pm = 13000 * np.exp(0.5 * np.log(glob_close / glob_close[0]))
    pm[1:] = 13000 * np.exp(
        0.5 * np.log(glob_close[:-1] / glob_close[0]) + 0.5 * np.log(glob_close[1:] / glob_close[0])
    )
    ibja = pd.DataFrame({"date": days, "pm": pm, "am": np.nan})
    glob = pd.Series(glob_close, index=days)
    glob = glob.reindex(pd.date_range(days.min(), days.max(), freq="D")).ffill()
    return ibja, glob


# ── global series dating ─────────────────────────────────────────────────────────────────────────


def test_history_seed_is_shifted_back_one_day(tmp_path: Path):
    # Seed row dated Mon 2026-09-21 holds Friday 09-18's close (as the real file does).
    idx = pd.to_datetime(["2026-09-18", "2026-09-19", "2026-09-20", "2026-09-21"]).tz_localize(UTC)
    pd.DataFrame({"raw_pre_duty": [100.0, 110.0, 110.0, 110.0]}, index=idx).to_parquet(
        tmp_path / "seed.parquet"
    )
    g = nextfix.global_series(None, tmp_path / "seed.parquet")
    assert g.loc["2026-09-18"] == 110.0  # Friday's close, now dated Friday
    assert g.loc["2026-09-17"] == 100.0


def test_macro_cache_wins_and_the_seed_is_rescaled_to_it(tmp_path: Path):
    idx = pd.date_range("2026-01-01", periods=60, freq="D", tz=UTC)
    seed = pd.DataFrame({"raw_pre_duty": np.linspace(100, 160, 60)}, index=idx)
    seed.to_parquet(tmp_path / "seed.parquet")
    natural = seed["raw_pre_duty"].copy()
    natural.index = natural.index.tz_localize(None) - pd.Timedelta(days=1)
    macro_idx = natural.index[30:]
    macro = pd.DataFrame(
        {"gold_usd": natural.loc[macro_idx].to_numpy() * 2.0 / 90.0, "usd_inr": 90.0},
        index=macro_idx.tz_localize(UTC),
    )
    g = nextfix.global_series(macro, tmp_path / "seed.parquet")
    # before the cache starts, the seed is rescaled (x2) so the series has no jump
    assert g.loc[natural.index[5]] == pytest.approx(2.0 * natural.iloc[5])
    assert g.loc[macro_idx[3]] == pytest.approx(2.0 * natural.loc[macro_idx[3]])


# ── pairs and features ───────────────────────────────────────────────────────────────────────────


def test_pairs_features_and_the_open_last_row():
    ibja, glob = _synthetic(40)
    pairs = nextfix.build_pairs(ibja, glob)
    last = pairs.iloc[-1]
    assert bool(last["last"]) and pd.isna(last["d1"]) and math.isnan(last["y"])
    row = pairs.iloc[10]
    d0 = row["d0"]
    assert row["x_glob"] == pytest.approx(
        math.log(glob.loc[d0] / glob.loc[d0 - pd.Timedelta(days=1)])
    )
    assert row["y"] == pytest.approx(math.log(row["pm1"] / row["pm0"]))
    # every resolved pair's target is the next IBJA day
    resolved = pairs[pairs["d1"].notna()]
    assert ((resolved["d1"] - resolved["d0"]).dt.days <= nextfix.MAX_GAP_DAYS).all()


def test_a_gap_in_the_ibja_series_has_no_target():
    ibja, glob = _synthetic(40)
    ibja = ibja.drop(index=range(20, 27)).reset_index(drop=True)  # 7 weekdays missing
    pairs = nextfix.build_pairs(ibja, glob)
    assert not (pairs["d1"] - pairs["d0"]).dt.days.gt(nextfix.MAX_GAP_DAYS).any()


# ── track record: no lookahead ───────────────────────────────────────────────────────────────────


def test_each_out_of_sample_fold_trains_only_on_fixes_known_before_it(monkeypatch):
    ibja, glob = _synthetic(90)
    pairs = nextfix.build_pairs(ibja, glob)
    monkeypatch.setattr(nextfix, "MIN_TRAIN", 40)
    seen = []

    def spy(train, row, resid_sd=None):
        seen.append((train["d1"].max(), row["d0"]))
        return nextfix.Prediction(ret=0.0, p_up=0.5, vol=0.01)

    monkeypatch.setattr(nextfix, "predict", spy)
    folds = nextfix.update_oos(pairs, [])
    assert len(folds) == len(pairs[pairs["d1"].notna()]) - 40
    assert all(max_d1 <= d0 for max_d1, d0 in seen)
    # a second run adds nothing new
    seen.clear()
    assert nextfix.update_oos(pairs, folds) == folds and not seen


def test_the_model_beats_flat_hold_when_the_fix_lags_the_global_price(monkeypatch):
    ibja, glob = _synthetic(160, seed=3)
    monkeypatch.setattr(nextfix, "MIN_TRAIN", 60)
    folds = nextfix.update_oos(nextfix.build_pairs(ibja, glob), [])
    ev = nextfix.evaluate(folds)
    assert ev["ready"] and ev["mae_model"] < ev["mae_flat"]
    assert ev["direction"]["accuracy"] > 0.6


# ── forecast window and retail mapping ───────────────────────────────────────────────────────────


def _folds(n: int = 40) -> list[dict]:
    return [
        {
            "d0": f"2026-01-{i + 1:02d}",
            "d1": "x",
            "pm0": 13000.0,
            "pm1": 13000.0 + (i % 5 - 2) * 30,
            "y": 0.0,
            "ret": 0.0,
            "p_up": 0.5,
            "vol": 0.01,
        }
        for i in range(n)
    ]


def test_forecast_waits_for_the_us_close_and_stops_at_the_next_am_fix():
    ibja, glob = _synthetic(120)
    d0 = ibja["date"].iloc[-1]
    before = datetime(d0.year, d0.month, d0.day, 21, 0, tzinfo=UTC)
    after = datetime(d0.year, d0.month, d0.day, 23, 0, tzinfo=UTC)
    assert nextfix.forecast(ibja, glob, _folds(), before)["reason"] == "waiting_for_us_close"
    assert (
        nextfix.forecast(ibja, glob, _folds(), after, newer_am=True)["reason"]
        == "newer_am_fix_published"
    )
    assert nextfix.forecast(ibja, glob, _folds(5), after)["reason"] == "track_record_too_short"
    fc = nextfix.forecast(ibja, glob, _folds(), after)
    assert fc["active"] and fc["d0"] == d0.strftime("%Y-%m-%d")
    assert 0.0 <= fc["p_up"] <= 1.0 and fc["half_width_pm"] > 0


def test_forecast_needs_the_global_close_of_the_latest_fix_day():
    ibja, glob = _synthetic(120)
    d0 = ibja["date"].iloc[-1]
    now = datetime(d0.year, d0.month, d0.day, 23, 0, tzinfo=UTC)
    assert (
        nextfix.forecast(ibja, glob[glob.index < d0], _folds(), now)["reason"]
        == "global_close_missing"
    )


def test_retail_mapping_scales_the_move_and_range_by_the_calibration_slope():
    fc = {"pm0": 13000.0, "pred_pm1": 13100.0, "half_width_pm": 150.0}
    r = nextfix.to_retail(fc, current_22k=13050, slope=1.02)
    assert r == {"predicted_22k": 13152, "lower": 12999, "upper": 13305}


# ── inference integration ────────────────────────────────────────────────────────────────────────


def test_inference_block_never_raises_and_keeps_flat_hold_on_failure(monkeypatch):
    from ml import inference

    def boom(**_):
        raise RuntimeError("no data")

    monkeypatch.setattr(nextfix, "run", boom)
    block = inference._next_fix_block(datetime.now(UTC), 13700, {"slope": 1.0})
    assert block == {"active": False, "reason": "error: RuntimeError"}


def test_inference_block_shows_direction_only_when_the_gate_ships(monkeypatch):
    from ml import inference

    fc = {
        "active": True,
        "model_version": nextfix.MODEL_VERSION,
        "d0": "2026-10-01",
        "pm0": 13569.4,
        "pred_pm1": 13620.0,
        "half_width_pm": 170.0,
        "p_up": 0.66,
    }
    ev = {
        "ready": True,
        "n": 137,
        "first_d0": "a",
        "last_d0": "b",
        "mae_model": 105.2,
        "mae_flat": 115.8,
        "mae_change_pct": -9.2,
        "wilcoxon_p": 0.004,
        "direction": {
            "accuracy": 0.657,
            "always_up_accuracy": 0.489,
            "p_value": 0.004,
            "ece": 0.064,
        },
        "direction_gate": {"ship": True, "reason": "ok"},
        "timing_gate": {"ship": False},
        "range_coverage": 0.803,
        "range_n": 117,
        "range_mean_width": 356.1,
    }
    monkeypatch.setattr(nextfix, "run", lambda **_: {"forecast": fc, "eval": ev})
    block = inference._next_fix_block(datetime.now(UTC), 13725, {"slope": 1.0})
    assert block["active"] and block["direction"] == {
        "show": True,
        "side": "up",
        "probability": 0.66,
    }
    assert block["predicted_22k"] == 13776 and block["lower"] == 13606 and block["upper"] == 13946
    ev["direction_gate"]["ship"] = False
    assert (
        inference._next_fix_block(datetime.now(UTC), 13725, {"slope": 1.0})["direction"]["show"]
        is False
    )
    fc["p_up"] = 0.52  # within 5 points of a coin flip
    assert (
        inference._next_fix_block(datetime.now(UTC), 13725, {"slope": 1.0})["direction"]["side"]
        == "unclear"
    )


# ── the committed track record backs the promotion record ────────────────────────────────────────


def test_committed_track_record_passes_the_direction_gate_as_recorded():
    folds = nextfix.load_oos(REPO / "data" / "nextfix_oos.json")
    ev = nextfix.evaluate(folds)
    record = json.loads((REPO / "data" / "direction_promotion_record.json").read_text())
    assert ev["n"] >= record["evidence_at_promotion"]["n_test_folds"]
    assert ev["direction_gate"]["ship"] is True
    assert ev["timing_gate"]["ship"] is False
    assert ev["mae_model"] < ev["mae_flat"]
    assert 0.75 <= ev["range_coverage"] <= 0.9
