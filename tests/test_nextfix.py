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


def test_label_seed_is_used_as_dated(tmp_path: Path):
    # The label seed's row dated D is the close of D (unlike the proxy seed, which ADR 030 lags).
    idx = pd.to_datetime(["2026-09-17", "2026-09-18", "2026-09-19", "2026-09-21"]).tz_localize(UTC)
    pd.DataFrame({"raw_pre_duty": [100.0, 110.0, 110.0, 120.0]}, index=idx).to_parquet(
        tmp_path / "label.parquet"
    )
    g = nextfix.global_series(None, tmp_path / "label.parquet")
    assert g.loc["2026-09-17"] == 100.0 and g.loc["2026-09-18"] == 110.0
    assert g.loc["2026-09-20"] == 110.0  # weekend carries Friday's close
    assert g.loc["2026-09-21"] == 120.0


def test_production_does_not_read_the_lagged_proxy_seed():
    src = (REPO / "ml" / "nextfix.py").read_text()
    assert "history_seed_inr22k_label.parquet" in src
    assert 'history_seed_inr22k_proxy.parquet"' not in src and "PROXY_PATH" not in src


def test_macro_cache_wins_and_the_seed_is_rescaled_to_it(tmp_path: Path):
    idx = pd.date_range("2026-01-01", periods=60, freq="D", tz=UTC)
    seed = pd.DataFrame({"raw_pre_duty": np.linspace(100, 160, 60)}, index=idx)
    seed.to_parquet(tmp_path / "label.parquet")
    natural = seed["raw_pre_duty"].copy()
    natural.index = natural.index.tz_localize(None)
    macro_idx = natural.index[30:]
    macro = pd.DataFrame(
        {"gold_usd": natural.loc[macro_idx].to_numpy() * 2.0 / 90.0, "usd_inr": 90.0},
        index=macro_idx.tz_localize(UTC),
    )
    g = nextfix.global_series(macro, tmp_path / "label.parquet")
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


# ── forecast: one window per part of the day ─────────────────────────────────────────────────────


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


def _full(n_days: int = 120) -> tuple[pd.DataFrame, pd.Series]:
    ibja, glob = _synthetic(n_days)
    rng = np.random.default_rng(1)
    ibja["am"] = ibja["pm"] * np.exp(rng.normal(0, 0.004, len(ibja)))
    return ibja[["date", "am", "pm"]], glob


def _at(d: pd.Timestamp, h: int, m: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, tzinfo=UTC)


def test_after_the_us_close_the_model_forecasts_the_next_pm_fix():
    full, glob = _full()
    d0 = full["date"].iloc[-1]
    fc = nextfix.forecast(full, glob, _folds(), _at(d0, 23))
    assert fc["active"] and fc["mode"] == "after_us_close" and fc["base_kind"] == "pm"
    assert fc["model_version"] == nextfix.MODEL_VERSION and 0.0 <= fc["p_up"] <= 1.0
    assert fc["half_width"] > 0 and fc["base_date"] == d0.strftime("%Y-%m-%d")


def test_before_the_us_close_the_latest_pm_fix_is_held():
    full, glob = _full()
    d0 = full["date"].iloc[-1]
    fc = nextfix.forecast(full, glob, _folds(), _at(d0, 15))
    assert fc["mode"] == "after_afternoon_rate" and fc["target_kind"] == "am"
    assert fc["pred"] == fc["base"] == round(float(full["pm"].iloc[-1]), 2) and fc["p_up"] is None


def test_without_the_global_close_the_model_falls_back_to_holding_the_fix():
    full, glob = _full()
    d0 = full["date"].iloc[-1]
    fc = nextfix.forecast(full, glob[glob.index < d0], _folds(), _at(d0, 23))
    assert fc["mode"] == "after_afternoon_rate"
    fc = nextfix.forecast(full, glob, _folds(5), _at(d0, 23))  # model track record too short
    assert fc["mode"] == "after_afternoon_rate"


def test_after_an_am_fix_with_no_pm_yet_the_am_fix_is_held():
    full, glob = _full()
    d1 = full["date"].iloc[-1] + pd.Timedelta(days=3)
    full = pd.concat(
        [full, pd.DataFrame({"date": [d1], "am": [13500.0], "pm": [np.nan]})], ignore_index=True
    )
    fc = nextfix.forecast(full, glob, _folds(), _at(d1, 8))
    assert (
        fc["mode"] == "after_morning_rate" and fc["base_kind"] == "am" and fc["target_kind"] == "pm"
    )
    assert fc["pred"] == 13500.0 and fc["half_width"] > 0


def test_flat_record_is_walk_forward_and_near_its_target_on_iid_moves():
    rng = np.random.default_rng(7)
    n = 300
    base = 13000 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    pairs = pd.DataFrame(
        {
            "d0": pd.bdate_range("2025-01-01", periods=n),
            "base": base,
            "target": base * np.exp(rng.normal(0, 0.004, n)),
        }
    )
    pairs["y"] = np.log(pairs["target"] / pairs["base"])
    rec = nextfix.flat_record(pairs)
    assert rec["ready"] and 0.74 <= rec["range_coverage"] <= 0.86
    lo, hi = rec["range_coverage_ci95"]
    assert lo < rec["range_coverage"] < hi


def test_ref_fix_is_the_latest_fix_published_before_the_price_was_read():
    full = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-10-01", "2026-10-05"]),
            "am": [13600.0, 13700.0],
            "pm": [13650.0, np.nan],
        }
    )
    assert nextfix.ref_fix(full, datetime(2026, 10, 1, 7, 0, tzinfo=UTC)) == 13600.0
    assert nextfix.ref_fix(full, datetime(2026, 10, 3, 9, 0, tzinfo=UTC)) == 13650.0
    assert nextfix.ref_fix(full, datetime(2026, 10, 5, 6, 45, tzinfo=UTC)) == 13700.0


def test_retail_mapping_anchors_on_the_fix_the_shop_price_reflects():
    fc = {"base": 13000.0, "pred": 13100.0, "half_width": 150.0}
    assert nextfix.to_retail(fc, current_22k=13050, slope=1.02) == {
        "predicted_22k": 13152,
        "lower": 12999,
        "upper": 13305,
    }
    # the shop price already reflects a fix 50 above the base: that move is not added twice
    assert (
        nextfix.to_retail(fc, current_22k=13050, slope=1.0, ref=13050.0)["predicted_22k"] == 13100
    )


def test_confidence_helpers():
    rng = np.random.default_rng(3)
    a, b = rng.normal(0, 1, 200), rng.normal(0, 1, 200)
    assert nextfix.diebold_mariano_p(a, b) > 0.05
    assert nextfix.diebold_mariano_p(a * 0.5, b) < 0.001
    lo, hi = nextfix.block_bootstrap_ci(200, lambda ix: float(np.mean(np.abs(a[ix]))))
    assert lo < float(np.mean(np.abs(a))) < hi


# ── inference integration ────────────────────────────────────────────────────────────────────────


def test_inference_block_never_raises_and_keeps_flat_hold_on_failure(monkeypatch):
    from ml import inference

    def boom(**_):
        raise RuntimeError("no data")

    monkeypatch.setattr(nextfix, "run", boom)
    block = inference._next_fix_block(datetime.now(UTC), 13700, {"slope": 1.0})
    assert block == {"active": False, "reason": "error: RuntimeError"}


_EV = {
    "ready": True,
    "n": 138,
    "first_d0": "a",
    "last_d0": "b",
    "mae_model": 105.4,
    "mae_flat": 115.8,
    "mae_change_pct": -9.0,
    "mae_change_ci95": [-15.1, -3.3],
    "dm_p": 0.006,
    "wilcoxon_p": 0.005,
    "direction_accuracy_ci95": [0.58, 0.725],
    "direction": {"accuracy": 0.652, "always_up_accuracy": 0.486, "p_value": 0.004, "ece": 0.068},
    "direction_gate": {"ship": True, "reason": "ok"},
    "timing_gate": {"ship": False},
    "range_coverage": 0.805,
    "range_coverage_ci95": [0.724, 0.866],
    "range_n": 118,
    "range_mean_width": 355.3,
}
_WINDOWS = {
    "am_to_pm": {
        "n": 148,
        "ready": True,
        "range_coverage": 0.791,
        "conformal_q": 1.0,
        "vol_now": 0.004,
    },
    "pm_to_am": {
        "n": 144,
        "ready": True,
        "range_coverage": 0.792,
        "conformal_q": 1.0,
        "vol_now": 0.01,
    },
}


def _run_with(fc: dict):
    return lambda **_: {"forecast": fc, "eval": _EV, "windows": _WINDOWS, "ibja": None}


def test_inference_block_shows_direction_only_in_the_model_window_and_when_the_gate_ships(
    monkeypatch,
):
    from ml import inference

    fc = {
        "active": True,
        "mode": "after_us_close",
        "model_version": nextfix.MODEL_VERSION,
        "base_kind": "pm",
        "base_date": "2026-10-01",
        "base": 13569.4,
        "target_kind": "pm",
        "pred": 13620.0,
        "half_width": 170.0,
        "p_up": 0.66,
    }
    monkeypatch.setattr(nextfix, "run", _run_with(fc))
    block = inference._next_fix_block(datetime.now(UTC), 13725, {"slope": 1.0})
    assert block["direction"] == {"show": True, "side": "up", "probability": 0.66}
    assert (block["predicted_22k"], block["lower"], block["upper"]) == (13776, 13606, 13946)
    assert block["range_record"] == {"coverage": 0.805, "n": 118}
    assert block["track_record"]["mae_change_ci95"] == [-15.1, -3.3]
    fc["p_up"] = 0.52  # within 5 points of a coin flip
    assert (
        inference._next_fix_block(datetime.now(UTC), 13725, {"slope": 1.0})["direction"]["side"]
        == "unclear"
    )
    _EV["direction_gate"]["ship"] = False
    try:
        assert (
            inference._next_fix_block(datetime.now(UTC), 13725, {"slope": 1.0})["direction"]["show"]
            is False
        )
    finally:
        _EV["direction_gate"]["ship"] = True


def test_inference_block_holding_a_fix_shows_no_direction_and_its_own_coverage(monkeypatch):
    from ml import inference

    fc = {
        "active": True,
        "mode": "after_morning_rate",
        "model_version": "hold_latest_fix_am_to_pm",
        "base_kind": "am",
        "base_date": "2026-10-05",
        "base": 13619.7,
        "target_kind": "pm",
        "pred": 13619.7,
        "half_width": 55.0,
        "p_up": None,
    }
    monkeypatch.setattr(nextfix, "run", _run_with(fc))
    block = inference._next_fix_block(datetime.now(UTC), 13725, {"slope": 1.0})
    assert block["mode"] == "after_morning_rate" and block["direction"]["show"] is False
    assert block["range_record"] == {"coverage": 0.791, "n": 148}
    assert (block["lower"], block["upper"]) == (13670, 13780)
    assert "conformal_q" not in block["windows"]["am_to_pm"]


# ── the committed track record backs the promotion record ────────────────────────────────────────


@pytest.mark.skipif(
    not (REPO / "data" / "nextfix_oos.json").exists(),
    reason="ADR 060: the track record is committed as ciphertext; run `data_crypt.py decrypt` first",
)
def test_committed_track_record_passes_the_direction_gate_as_recorded():
    folds = nextfix.load_oos(REPO / "data" / "nextfix_oos.json")
    ev = nextfix.evaluate(folds)
    record = json.loads((REPO / "data" / "direction_promotion_record.json").read_text())
    assert ev["n"] >= record["evidence_at_promotion"]["n_test_folds"]
    assert ev["direction_gate"]["ship"] is True
    assert ev["timing_gate"]["ship"] is False
    assert ev["mae_model"] < ev["mae_flat"]
    assert 0.75 <= ev["range_coverage"] <= 0.9
