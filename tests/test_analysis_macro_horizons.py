"""Tests for scripts/analysis_macro_horizons.py (item 8, ADR 053). No network, no
COT/real-yield/price data -- every dataset here is synthetic."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "analysis_macro_horizons.py"
_spec = importlib.util.spec_from_file_location("analysis_macro_horizons", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["analysis_macro_horizons"] = mod
_spec.loader.exec_module(mod)


def _synthetic_dataset(n: int = 600, horizon: int = 5, seed: int = 42, return_target: bool = False):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, len(mod.FEATURES)))
    days = np.array([f"2020-{(i // 20) % 12 + 1:02d}-{i % 28 + 1:02d}" for i in range(n)])
    # label_date is `horizon` rows ahead -- matches ml.direction.comex_daily's
    # convention where each row is one trading day and label_date_hN is N rows out.
    label_date = np.array(
        [days[i + horizon] if i + horizon < n else "9999-01-01" for i in range(n)]
    )
    if return_target:
        y = 0.02 * X[:, 0] + rng.normal(scale=0.01, size=n)
    else:
        y = (rng.random(n) < 1 / (1 + np.exp(-1.5 * X[:, 0]))).astype(int)
    return {
        "X": X,
        "y": y,
        "as_of": days,
        "label_date": label_date,
        "horizon": horizon,
        "features": list(mod.FEATURES),
        "min_train": 200,
        "block": 21,
    }


def test_shards_cover_the_full_family_plus_injection() -> None:
    assert len(mod.SHARDS) == 15
    assert len(mod.FAMILY_KEYS) == 12
    for h in mod.HORIZONS:
        assert f"h{h}_direction_logit" in mod.SHARDS
        assert f"h{h}_direction_gbm_stumps" in mod.SHARDS
        assert f"h{h}_return_ridge" in mod.SHARDS
        assert f"h{h}_return_gbm_stumps_reg" in mod.SHARDS
        assert f"h{h}_injection" in mod.SHARDS


def test_run_shard_key_parsing_matches_shard_list() -> None:
    for key in mod.SHARDS:
        if key.endswith("_injection"):
            horizon = int(key.removeprefix("h").removesuffix("_injection"))
            assert horizon in mod.HORIZONS
        else:
            h_part, target, model_name = key.split("_", 2)
            assert int(h_part.removeprefix("h")) in mod.HORIZONS
            assert target in ("direction", "return")
            if target == "direction":
                assert model_name in mod.DIRECTION_MODELS
            else:
                assert model_name in mod.RETURN_MODELS


@pytest.mark.parametrize("name", ["ridge", "gbm_stumps_reg"])
def test_make_return_model_fits_and_predicts(name: str) -> None:
    rng = np.random.default_rng(0)
    X = rng.normal(size=(50, 3))
    y = 0.5 * X[:, 0] + rng.normal(scale=0.01, size=50)
    m = mod.make_return_model(name)
    m.fit(X, y)
    pred = m.predict(X[:5])
    assert len(pred) == 5


def test_walk_forward_return_is_forward_only_and_populates_climatology() -> None:
    d = _synthetic_dataset(n=400, horizon=5, return_target=True)
    wf = mod.walk_forward_return(d, "ridge")
    assert len(wf["idx"]) > 0
    assert len(wf["idx"]) == len(wf["p"]) == len(wf["p_clim"])
    assert wf["idx"].min() >= d["min_train"]
    assert np.all(np.diff(wf["idx"]) >= 1)  # scored rows strictly increasing


def test_score_direction_always_up_baseline_is_literal() -> None:
    d = _synthetic_dataset(n=400, horizon=5)
    diag = mod._diag()
    wf = diag.walk_forward(d, "logit")
    sc = mod.score_direction(d, wf)
    y = d["y"][wf["idx"]]
    expected_always_up_acc = float(np.mean(y == 1))
    assert sc["acc_always_up"] == pytest.approx(expected_always_up_acc)
    assert "loss_model" in sc and "loss_baseline" in sc
    assert len(sc["loss_model"]) == len(y)


def test_score_return_zero_baseline_matches_raw_y_squared() -> None:
    d = _synthetic_dataset(n=400, horizon=5, return_target=True)
    wf = mod.walk_forward_return(d, "ridge")
    sc = mod.score_return(d, wf)
    y = d["y"][wf["idx"]]
    assert sc["mse_zero_return"] == pytest.approx(float(np.mean(y**2)))
    assert np.allclose(sc["loss_baseline"], y**2)


def test_half_signs_splits_on_split_date_and_flags_short_halves() -> None:
    d = {
        "as_of": np.array(["2010-01-01", "2010-01-02", "2020-01-01", "2020-01-02"]),
    }
    wf = {"idx": np.array([0, 1, 2, 3])}
    loss_model = np.array([1.0, 1.0, 0.0, 0.0])
    loss_baseline = np.array([2.0, 2.0, 1.0, 1.0])
    out = mod.half_signs(d, wf, loss_model, loss_baseline)
    # Fewer than 30 scored rows per half -> "better" reported as None, not a fabricated sign.
    assert out["half_2006_2015"]["better"] is None
    assert out["half_2016_2026"]["better"] is None
    assert out["half_2006_2015"]["n"] == 2
    assert out["half_2016_2026"]["n"] == 2


def test_half_signs_detects_a_consistent_improvement() -> None:
    as_of = np.array(
        [f"2010-{i // 28 + 1:02d}-{i % 28 + 1:02d}" for i in range(60)]
        + [f"2020-{i // 28 + 1:02d}-{i % 28 + 1:02d}" for i in range(60)]
    )
    d = {"as_of": as_of}
    wf = {"idx": np.arange(120)}
    loss_model = np.full(120, 0.2)
    loss_baseline = np.full(120, 0.3)  # model loss always lower -> "better" both halves
    out = mod.half_signs(d, wf, loss_model, loss_baseline)
    assert out["half_2006_2015"]["better"] is True
    assert out["half_2016_2026"]["better"] is True


def test_injection_signal_is_deterministic_and_binary() -> None:
    d = _synthetic_dataset(n=300, horizon=5)
    sig = mod._injection_signal(d)
    assert set(np.unique(sig)).issubset({0, 1})
    sig2 = mod._injection_signal(d)
    assert np.array_equal(sig, sig2)


def test_build_verdict_bonferroni_and_replication_gate() -> None:
    cells = []
    for key in mod.FAMILY_KEYS:
        h_part, target, model = key.split("_", 2)
        horizon = int(h_part.removeprefix("h"))
        # One cell is a clean, strong, replicating win; the rest are null.
        is_winner = key == mod.FAMILY_KEYS[0]
        cells.append(
            {
                "horizon": horizon,
                "target": target,
                "model": model,
                "p_primary": 0.0001 if is_winner else 0.5,
                "effective_n_primary": 1000,
                "n": 1000,
                "half_2006_2015": {
                    "n": 200,
                    "mean_diff": -0.01 if is_winner else 0.0,
                    "better": is_winner or None,
                },
                "half_2016_2026": {
                    "n": 200,
                    "mean_diff": -0.01 if is_winner else 0.0,
                    "better": is_winner or None,
                },
            }
        )
    verdict = mod.build_verdict(cells)
    assert verdict["family_size"] == 12
    winner_key = mod.FAMILY_KEYS[0]
    assert verdict["table"][winner_key]["bonferroni_significant"] is True
    assert verdict["table"][winner_key]["replicates_both_halves"] is True
    assert verdict["table"][winner_key]["success"] is True
    assert verdict["any_success"] is True
    other_key = mod.FAMILY_KEYS[1]
    assert verdict["table"][other_key]["success"] is False


def test_build_verdict_fails_when_halves_disagree_even_if_significant() -> None:
    cells = []
    for i, key in enumerate(mod.FAMILY_KEYS):
        h_part, target, model = key.split("_", 2)
        horizon = int(h_part.removeprefix("h"))
        significant_but_reversed = i == 0
        cells.append(
            {
                "horizon": horizon,
                "target": target,
                "model": model,
                "p_primary": 0.0001 if significant_but_reversed else 0.9,
                "effective_n_primary": 1000,
                "n": 1000,
                "half_2006_2015": {"n": 200, "mean_diff": -0.01, "better": True},
                "half_2016_2026": {"n": 200, "mean_diff": 0.01, "better": False},
            }
        )
    verdict = mod.build_verdict(cells)
    assert verdict["table"][mod.FAMILY_KEYS[0]]["bonferroni_significant"] is True
    assert verdict["table"][mod.FAMILY_KEYS[0]]["replicates_both_halves"] is False
    assert verdict["table"][mod.FAMILY_KEYS[0]]["success"] is False
    assert verdict["any_success"] is False
