"""P3 is the live next-fix model, the ensemble runs in shadow (ADR 067/069)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from ml import nextfix


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(nextfix, "MLP_MODELS", 1)
    monkeypatch.setattr(nextfix, "MLP_EPOCHS", 20)
    monkeypatch.setattr(nextfix, "MIN_TRAIN", 40)


def _synthetic(n_days: int = 120, seed: int = 3) -> tuple[pd.DataFrame, pd.Series]:
    """Weekday fixes that follow the world price with a lag, plus the world series."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2026-01-05", periods=n_days)
    glob_close = 125000 * np.exp(np.cumsum(rng.normal(0, 0.01, n_days)))
    pm = 13000 * np.exp(0.5 * np.log(glob_close / glob_close[0]))
    pm[1:] = 13000 * np.exp(
        0.5 * np.log(glob_close[:-1] / glob_close[0]) + 0.5 * np.log(glob_close[1:] / glob_close[0])
    )
    ibja = pd.DataFrame({"date": days, "pm": pm, "am": np.nan})
    glob = pd.Series(glob_close, index=days)
    glob = glob.reindex(pd.date_range(days.min(), days.max(), freq="D")).ffill()
    return ibja, glob


def _pairs() -> pd.DataFrame:
    ibja, glob = _synthetic()
    return nextfix.build_pairs(ibja, glob)


def test_live_model_is_p3_and_the_ensemble_is_the_shadow():
    assert nextfix.MODEL_VERSION == "nextfix_p3_v1"
    assert nextfix.ENSEMBLE_VERSION == "nextfix_ridge_mlp_v1"
    assert nextfix.P3_OOS_PATH.name != nextfix.OOS_PATH.name


def test_p3_is_one_slope_through_the_origin_on_resolved_pairs():
    pairs = _pairs()
    resolved = pairs[pairs["d1"].notna()]
    train, row = resolved.iloc[:80], resolved.iloc[80]
    p = nextfix.predict_p3(train, row)
    x, y = train["x_glob"].to_numpy(float), train["y"].to_numpy(float)
    b = float(x @ y / (x @ x))
    assert p.ret == pytest.approx(b * float(row["x_glob"]), rel=1e-12)
    assert (p.p_up > 0.5) == (p.ret > 0)
    # nothing from after the training window can change the forecast
    later = resolved.iloc[:80].copy()
    assert nextfix.predict_p3(later, row).ret == p.ret


def test_p3_record_is_walk_forward_and_flags_retro_vs_forward():
    pairs = _pairs()
    seen = []

    def spy(train, row, resid_sd=None):
        seen.append((train["d1"].max(), row["d0"]))
        return nextfix.predict_p3(train, row, resid_sd)

    cut = pairs[pairs["d1"].notna()]["d0"].iloc[-10].strftime("%Y-%m-%d")
    folds = nextfix.update_oos(pairs, [], spy, forward_from=cut)
    assert all(d1 <= d0 for d1, d0 in seen)
    assert all(f["retro"] == (f["d0"] < cut) for f in folds)
    assert sum(f["retro"] is False for f in folds) == 10
    assert nextfix.evaluate(folds)["n_forward"] == 10
    # idempotent: a second run adds nothing
    assert nextfix.update_oos(pairs, folds, spy, forward_from=cut) == folds


def _full_glob():
    ibja, glob = _synthetic()
    return ibja[["date", "am", "pm"]], glob


def _folds_for(pairs, predictor):
    return nextfix.update_oos(pairs, [], predictor, forward_from="2099-01-01")


def test_forecast_uses_p3_and_returns_the_ensemble_only_as_shadow():
    full, glob = _full_glob()
    pairs = nextfix.build_pairs(full.dropna(subset=["pm"]).reset_index(drop=True), glob)
    p3 = _folds_for(pairs, nextfix.predict_p3)
    ens = _folds_for(pairs, nextfix.predict)
    d0 = full["date"].iloc[-1]
    now = datetime(d0.year, d0.month, d0.day, 23, tzinfo=UTC)
    fc = nextfix.forecast(full, glob, p3, now, shadow_folds=ens)
    assert fc["active"] and fc["model_version"] == nextfix.MODEL_VERSION
    assert fc["shadow"]["model_version"] == nextfix.ENSEMBLE_VERSION
    assert fc["shadow"]["pred"] != fc["pred"] or fc["shadow"]["p_up"] != fc["p_up"]
    assert "shadow" not in nextfix.forecast(full, glob, p3, now)


def test_a_failing_shadow_never_takes_the_live_forecast_down(monkeypatch):
    full, glob = _full_glob()
    pairs = nextfix.build_pairs(full.dropna(subset=["pm"]).reset_index(drop=True), glob)
    p3 = _folds_for(pairs, nextfix.predict_p3)
    ens = _folds_for(pairs, nextfix.predict)

    def boom(*a, **k):
        raise RuntimeError("torch missing")

    monkeypatch.setattr(nextfix, "predict", boom)
    d0 = full["date"].iloc[-1]
    fc = nextfix.forecast(
        full, glob, p3, datetime(d0.year, d0.month, d0.day, 23, tzinfo=UTC), shadow_folds=ens
    )
    assert fc["active"] and fc["model_version"] == nextfix.MODEL_VERSION and "shadow" not in fc


def test_run_keeps_two_separate_records_and_scores_both(tmp_path: Path):
    full, glob = _full_glob()
    ibja = pd.DataFrame({"date": full["date"], "pm_916": full["pm"] * 10.0, "am_916": np.nan})
    ibja.to_parquet(tmp_path / "ibja_rates.parquet")
    macro = pd.DataFrame({"gold_usd": glob.to_numpy() / 100.0, "usd_inr": 100.0}, index=glob.index)
    d0 = full["date"].iloc[-1]
    out = nextfix.run(
        now=datetime(d0.year, d0.month, d0.day, 23, tzinfo=UTC), macro=macro, data_dir=tmp_path
    )
    assert (tmp_path / "nextfix_p3_oos.json").exists() and (tmp_path / "nextfix_oos.json").exists()
    assert out["eval"]["ready"] and out["shadow_eval"]["ready"]
    p3 = nextfix.load_oos(tmp_path / "nextfix_p3_oos.json")
    ens = nextfix.load_oos(tmp_path / "nextfix_oos.json")
    assert [f["d0"] for f in p3] == [f["d0"] for f in ens]
    assert p3 != ens
    assert all("retro" in f for f in p3) and all("retro" not in f for f in ens)
    assert out["forecast"]["model_version"] == nextfix.MODEL_VERSION


# The first check-price run after this lands creates the record (``run()`` rebuilds it from the
# pairs when absent; test_run_keeps_two_separate_records_and_scores_both covers that). Until then
# there is nothing committed to score.
@pytest.mark.skipif(
    not (Path(__file__).resolve().parent.parent / "data" / "nextfix_p3_oos.json").exists(),
    reason="data/nextfix_p3_oos.json is created by the first check-price run",
)
def test_committed_p3_record_passes_the_gate_and_beats_hold():
    repo = Path(__file__).resolve().parent.parent
    folds = nextfix.load_oos(repo / "data" / "nextfix_p3_oos.json")
    ev = nextfix.evaluate(folds)
    assert ev["n"] >= 143 and ev["direction_gate"]["ship"] is True
    assert ev["timing_gate"]["ship"] is False
    assert ev["mae_model"] < ev["mae_flat"]
    assert 0.75 <= ev["range_coverage"] <= 0.9
    # every fold before the go-live date is flagged as a re-run, never as a live call
    assert all(f["retro"] for f in folds if f["d0"] < nextfix.P3_FORWARD_FROM)
