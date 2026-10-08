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


def test_strong_challenger_is_promoted_after_the_minimum_days() -> None:
    recs = _paired(60, champ_skill=0.1, chall_skill=0.8)
    out = pr.decide("p3", recs)
    row = out["challengers"]["p3_roll60"]
    assert row["n"] == 60 and row["looks_started"] and not row["retired"]
    assert row["lower_bound"] > pr.RULE["min_gain"] and row["gain_estimate"] > 0.5
    assert row["promotable"] and out["promote"] == "p3_roll60"
    at_first_look = pr.decide("p3", {k: v[:20] for k, v in recs.items()})
    assert at_first_look["challengers"]["p3_roll60"]["looks_started"]


def test_no_look_before_the_first_look_day() -> None:
    recs = _paired(19, champ_skill=0.0, chall_skill=0.95)  # a huge, obvious gain
    row = pr.decide("p3", recs)["challengers"]["p3_roll60"]
    assert row["n"] == 19 and row["first_look_day"] == 20
    assert not row["looks_started"] and row["lower_bound"] is None and not row["promotable"]
    assert row["gain_estimate"] is None  # no number is shown before the first look
    e = np.arange(1, 40, dtype=float) + np.sin(np.arange(1, 40))
    lb = pr.cs_lower_bounds(e)
    assert np.isnan(lb[:19]).all() and not np.isnan(lb[19:]).any()


def test_equal_models_never_promote() -> None:
    recs = _paired(120, champ_skill=0.5, chall_skill=0.5)
    assert pr.decide("p3", recs)["promote"] is None


def test_worse_challenger_never_promotes() -> None:
    recs = _paired(120, champ_skill=0.8, chall_skill=0.1)
    assert pr.decide("p3", recs)["promote"] is None


def test_identical_models_have_no_bound_and_fail_closed() -> None:
    base = _folds(60, 0.5)
    row = pr.decide("p3", {"p3": base, "ensemble": [dict(f) for f in base]})["challengers"][
        "ensemble"
    ]
    assert not row["promotable"]


def test_hourly_without_live_predictor_is_scored_but_not_switchable() -> None:
    recs = _paired(60, champ_skill=0.1, chall_skill=0.8)
    recs["hourly"] = recs.pop("p3_roll60")
    out = pr.decide("p3", recs)
    row = out["challengers"]["hourly"]
    assert row["lower_bound_clears"] and not row["live_capable"] and not row["promotable"]
    assert row["registered"] == row["first_day"]  # registered on the day it got a record
    assert out["promote"] is None


def test_never_promotes_at_or_after_the_horizon_and_retires() -> None:
    recs = _paired(200, champ_skill=0.1, chall_skill=0.8)  # 200 consecutive calendar days
    early = pr.decide("p3", {k: v[:150] for k, v in recs.items()})
    assert early["promote"] == "p3_roll60" and not early["challengers"]["p3_roll60"]["retired"]
    late = pr.decide("p3", recs)  # champion clock is now 199 days after registration
    row = late["challengers"]["p3_roll60"]
    assert row["lower_bound_clears"] is True  # the evidence is as strong as ever ...
    assert row["retired"] and row["horizon_left"] == 0 and not row["promotable"]
    assert late["promote"] is None  # ... but a retired challenger is never promoted


def test_horizon_boundary_is_calendar_days_since_registration() -> None:
    recs = _paired(60, champ_skill=0.1, chall_skill=0.8)
    reg = date.fromisoformat(pr.RULE["registered"]["p3_roll60"])
    h = pr.RULE["horizon_days"]
    last = (reg + timedelta(days=h - 1)).isoformat()
    first_retired = (reg + timedelta(days=h)).isoformat()
    ok = pr.decide("p3", recs, as_of=last)["challengers"]["p3_roll60"]
    gone = pr.decide("p3", recs, as_of=first_retired)["challengers"]["p3_roll60"]
    assert ok["days_since_registration"] == h - 1 and ok["horizon_left"] == 1 and ok["promotable"]
    assert gone["days_since_registration"] == h and gone["retired"] and not gone["promotable"]


def test_registry_is_part_of_the_frozen_rule() -> None:
    reg = pr.RULE["registered"]
    assert reg["ensemble"] == reg["p3_roll60"] == reg["p3_monday"] == "2026-10-07"
    assert set(pr.RULE["registered_on_first_record"]) == {"hourly", "p3_hourly"}
    assert not set(pr.RULE["registered_on_first_record"]) & set(pr.LIVE_CAPABLE)
    moved = {**pr.RULE, "registered": {**reg, "p3_monday": "2026-11-01"}}
    assert pr.rule_sha256(moved) != pr.RULE_SHA256
    assert pr.RULE["version"] == 2


def test_alpha_is_split_over_every_challenger_a_champion_can_face() -> None:
    assert pr.RULE["family_size"] == len(pr.LIVE_CAPABLE) - 1
    assert pr.cs_level() == pytest.approx(pr.RULE["alpha"] / pr.RULE["family_size"])


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
    recs = _paired(60, champ_skill=0.1, chall_skill=0.8)
    # lower error but the stated direction is always wrong
    recs["p3_roll60"] = [{**f, "p_up": 1.0 - f["p_up"]} for f in recs["p3_roll60"]]
    row = pr.decide("p3", recs)["challengers"]["p3_roll60"]
    assert row["lower_bound_clears"] and not row["direction_ok"] and not row["promotable"]


def test_coverage_below_target_blocks_promotion(monkeypatch) -> None:
    recs = _paired(60, champ_skill=0.1, chall_skill=0.8)
    # the challenger's own range misses every day: coverage 0 while its error is much lower
    monkeypatch.setattr(pr, "range_hits", lambda folds: {f["d0"]: False for f in folds})
    row = pr.decide("p3", recs)["challengers"]["p3_roll60"]
    assert row["lower_bound_clears"] and not row["coverage_ok"] and not row["promotable"]


def test_running_variance_matches_the_reference_estimator() -> None:
    rng = np.random.default_rng(42)
    e = rng.normal(0.3, 2.0, (3, 70)) + 0.5 * np.roll(rng.normal(0, 1, (3, 70)), 1, axis=1)
    run = pr.running_long_run_var(e, 4)
    for i in range(3):
        for n in (2, 3, 5, 20, 70):
            ref = pr._long_run_var(e[i, :n], 4)
            assert run[i, n - 1] == pytest.approx(ref, rel=1e-9, abs=1e-12)


def test_confidence_sequence_is_valid_on_an_autocorrelated_null() -> None:
    """Simulated size at every look: AR(1) heavy-tailed e with true mean 0 (the boundary of the
    claim 'gain >= 5%'). Deterministic (seed 42); the rule must promote on at most 5% of paths."""
    rng = np.random.default_rng(42)
    paths, length, phi = 1500, 128, 0.3
    noise = rng.standard_t(4, (paths, length)) * 20.0  # heavy tails, like absolute-error gaps
    e = np.empty_like(noise)
    e[:, 0] = noise[:, 0]
    for t in range(1, length):
        e[:, t] = phi * e[:, t - 1] + noise[:, t]
    lb = pr.cs_lower_bounds(e)
    rate = float(np.mean(np.nan_to_num(lb, nan=-1.0).max(axis=1) > 0))
    assert rate <= 0.05
    # and it is not vacuous: a real positive mean is detected on most paths
    lb_pos = pr.cs_lower_bounds(e + 25.0)
    assert float(np.mean(np.nan_to_num(lb_pos, nan=-1.0).max(axis=1) > 0)) > 0.5


def test_adr_records_the_amendment_and_the_superseded_hash() -> None:
    text = Path("docs/adr/072-champion-challenger-promotion-rule.md").read_text(encoding="utf-8")
    assert "Amendment 1" in text and "superseded (v1)" in text
    assert "0782301d8890788287be983c63d3d4e9bbd9eb0d8cd5d50d213960207dc44902" in text  # v1 hash
    assert (
        pr.RULE_SHA256 in text
        and pr.RULE_SHA256 != "0782301d8890788287be983c63d3d4e9bbd9eb0d8cd5d50d213960207dc44902"
    )


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
