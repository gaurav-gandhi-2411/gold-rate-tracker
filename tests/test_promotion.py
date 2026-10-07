"""Champion/challenger promotion rule (ADR 072): frozen constants, forward-only scoring, the gates."""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest
from ml import promotion as pr


def _folds(
    n: int, skill: float, start: str = "2026-10-07", seed: int = 42, retro: bool | None = False
) -> list[dict]:
    """``skill`` in [0, 1]: share of the true move the forecast captures (0 = forecast flat)."""
    rng = np.random.default_rng(seed)
    d0 = date.fromisoformat(start)
    out = []
    for i in range(n):
        y = float(rng.normal(0, 0.01))
        noise = float(rng.normal(0, 0.002))
        ret = skill * y + noise
        pm0 = 13500.0
        f = {
            "d0": (d0 + timedelta(days=i)).isoformat(),
            "d1": (d0 + timedelta(days=i + 1)).isoformat(),
            "pm0": pm0,
            "pm1": pm0 * math.exp(y),
            "y": y,
            "ret": ret,
            "p_up": 0.5 + (0.3 if ret > 0 else -0.3),
            "vol": 0.01,
        }
        if retro is not None:
            f["retro"] = retro
        out.append(f)
    return out


def _paired(n: int, champ_skill: float, chall_skill: float) -> dict[str, list[dict]]:
    """Both models see the SAME outcomes; only their forecasts differ."""
    base = _folds(n, 1.0)
    champ, chall = [], []
    rng = np.random.default_rng(7)
    for f in base:
        for skill, bucket in ((champ_skill, champ), (chall_skill, chall)):
            ret = skill * f["y"] + float(rng.normal(0, 0.002))
            bucket.append({**f, "ret": ret, "p_up": 0.5 + (0.3 if ret > 0 else -0.3)})
    return {"p3": champ, "p3_roll60": chall}


def test_rule_is_frozen_by_hash() -> None:
    assert pr.rule_sha256() == pr.RULE_SHA256
    changed = {**pr.RULE, "min_gain": 0.01}
    assert pr.rule_sha256(changed) != pr.RULE_SHA256


def test_adr_records_the_same_hash() -> None:
    text = Path("docs/adr/072-champion-challenger-promotion-rule.md").read_text(encoding="utf-8")
    assert pr.RULE_SHA256 in text


def test_retro_and_pre_start_days_are_never_compared() -> None:
    champ = _folds(30, 0.2, start="2026-10-01", retro=None)  # no flag: a live record
    chall = _folds(30, 0.9, retro=True)  # every day a re-run
    assert pr.compare(champ, chall)["n"] == 0
    chall_live = _folds(30, 0.9, retro=False)
    r = pr.compare(champ, chall_live)
    # champion days from 2026-10-01; only days >= common_start count, and only both-live days
    assert r["first_day"] >= pr.RULE["common_start"]


def test_strong_challenger_with_enough_days_is_promoted() -> None:
    recs = _paired(1600, champ_skill=0.1, chall_skill=0.8)
    # required n is sized for a 5% difference on a noisy daily loss: long by design
    out = pr.decide("p3", recs)
    row = out["challengers"]["p3_roll60"]
    assert row["n"] >= row["n_required"] > pr.RULE["min_days"]
    assert row["gain"] > 0.05 and row["bh_significant"] and row["promotable"]
    assert out["promote"] == "p3_roll60"


def test_same_challenger_waits_until_n_required() -> None:
    recs = _paired(1600, champ_skill=0.1, chall_skill=0.8)
    short = {k: v[:250] for k, v in recs.items()}
    out = pr.decide("p3", short)
    assert out["promote"] is None
    assert not out["challengers"]["p3_roll60"]["promotable"]


def test_equal_models_never_promote() -> None:
    recs = _paired(250, champ_skill=0.5, chall_skill=0.5)
    assert pr.decide("p3", recs)["promote"] is None


def test_worse_challenger_never_promotes() -> None:
    recs = _paired(250, champ_skill=0.8, chall_skill=0.1)
    assert pr.decide("p3", recs)["promote"] is None


def test_hourly_without_live_predictor_is_scored_but_not_switchable() -> None:
    recs = _paired(1600, champ_skill=0.1, chall_skill=0.8)
    recs["hourly"] = recs.pop("p3_roll60")
    out = pr.decide("p3", recs)
    row = out["challengers"]["hourly"]
    assert row["bh_significant"] and not row["live_capable"] and not row["promotable"]
    assert out["promote"] is None


def test_coverage_below_target_is_reported_and_blocks() -> None:
    recs = _paired(300, champ_skill=0.1, chall_skill=0.8)
    # errors that keep growing: each day's range, built from earlier days, is too narrow
    rng = np.random.default_rng(3)
    grown = []
    for i, f in enumerate(recs["p3_roll60"]):
        noise = float(rng.normal(0, 0.002 * (1 + i / 20)))
        grown.append({**f, "ret": f["y"] + noise})
    row = pr.compare(recs["p3"], grown)
    assert row["coverage"] < 0.8 and row["coverage_ok"] is False


def test_direction_worse_blocks_promotion() -> None:
    recs = _paired(1600, champ_skill=0.1, chall_skill=0.8)
    # lower error but the stated direction is always wrong
    recs["p3_roll60"] = [{**f, "p_up": 1.0 - f["p_up"]} for f in recs["p3_roll60"]]
    row = pr.decide("p3", recs)["challengers"]["p3_roll60"]
    assert not row["direction_ok"] and not row["promotable"]


def test_bh_controls_across_challengers() -> None:
    assert pr.bh_reject([0.001, 0.2, 0.9], 0.05) == [True, False, False]
    assert pr.bh_reject([0.04, 0.045, 0.049], 0.05) == [True, True, True]
    assert pr.bh_reject([0.2, 0.5], 0.05) == [False, False]


def test_required_n_scales_with_variance_and_has_a_floor() -> None:
    rng = np.random.default_rng(42)
    quiet = rng.normal(-1.0, 5.0, 200)
    noisy = rng.normal(-1.0, 50.0, 200)
    nq, nn = pr.required_n(quiet, 40.0), pr.required_n(noisy, 40.0)
    assert nq is not None and nn is not None
    assert nq >= pr.RULE["min_days"] and nn > nq
    assert pr.required_n(quiet[:5], 40.0) is None


def test_first_reachable_date() -> None:
    assert pr.first_reachable(10, 40, "2026-10-07", per_day=1.0) == "2026-11-06"
    assert pr.first_reachable(50, 40, "2026-10-07", per_day=1.0) == "2026-10-07"
    assert pr.first_reachable(10, None, "2026-10-07") is None


def test_champion_state_roundtrip_and_rollback(tmp_path) -> None:
    path = tmp_path / pr.CHAMPION_FILE
    state = pr.load_champion(path)
    assert state["champion"] == "p3" and state["history"] == []
    new = pr.promote(state, "p3_roll60", {"gain": 0.07})
    pr.save_champion(path, new)
    loaded = pr.load_champion(path)
    assert loaded["champion"] == "p3_roll60" and loaded["history"][0]["to"] == "p3_roll60"
    back = pr.rollback(loaded)
    assert back["champion"] == "p3" and back["history"][-1]["event"] == "rolled_back"
    with pytest.raises(ValueError):
        pr.rollback(state)


def test_cannot_promote_a_model_without_a_live_predictor() -> None:
    with pytest.raises(ValueError):
        pr.promote(pr.empty_champion(), "hourly", {})


@pytest.mark.parametrize(
    "content", ["{not json", '{"champion": "mystery"}', "[]", '{"champion": 3}']
)
def test_unreadable_champion_state_fails_closed_to_p3(tmp_path, content) -> None:
    path = tmp_path / pr.CHAMPION_FILE
    path.write_text(content, encoding="utf-8")
    state = pr.load_champion(path)
    assert state["champion"] == "p3" and state["unreadable"] is True


def test_decide_output_is_json_serialisable() -> None:
    out = pr.decide("p3", _paired(120, 0.1, 0.8))
    json.dumps(out, default=float)
