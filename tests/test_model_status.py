"""scripts/build_model_status.py: the weekly one-screen note renders only computed numbers."""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_module():
    spec = importlib.util.spec_from_file_location("bms", ROOT / "scripts" / "build_model_status.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _folds(n: int, ret_scale: float, start: str = "2026-10-07") -> list[dict]:
    d0 = date.fromisoformat(start)
    out = []
    for i in range(n):
        y = 0.004 * (1 if i % 2 else -1)
        out.append(
            {
                "d0": (d0 + timedelta(days=i)).isoformat(),
                "d1": (d0 + timedelta(days=i + 1)).isoformat(),
                "pm0": 13500.0,
                "pm1": 13500.0 * math.exp(y),
                "y": y,
                "ret": y * ret_scale,
                "p_up": 0.7 if y > 0 else 0.3,
                "vol": 0.01,
                "retro": False,
            }
        )
    return out


def _write(tmp: Path, p3: list[dict], ens: list[dict]) -> None:
    (tmp / "nextfix_p3_oos.json").write_text(json.dumps({"folds": p3}))
    (tmp / "nextfix_oos.json").write_text(json.dumps({"folds": ens}))
    var = {"variants": {"p3_roll60": p3, "p3_monday": p3}}
    (tmp / "nextfix_p3_variants_oos.json").write_text(json.dumps(var))


def test_no_forward_days_claims_nothing(tmp_path, monkeypatch) -> None:
    mod = _load_module()
    monkeypatch.setattr(mod, "DATA", tmp_path)
    _write(tmp_path, [], [])
    s = mod.compute()
    md = mod.render(s)
    assert s["live"]["n"] == 0
    assert "no scored real days yet" in md
    assert "Rs." not in md.split("## What is being tested")[0]


def test_live_numbers_are_computed_from_the_records(tmp_path, monkeypatch) -> None:
    mod = _load_module()
    monkeypatch.setattr(mod, "DATA", tmp_path)
    _write(tmp_path, _folds(60, 0.5), _folds(60, 0.2))
    s = mod.compute()
    live = s["live"]
    assert live["n"] == 60
    assert live["mae_model"] < live["mae_hold"]
    assert live["direction_hit"] == 1.0
    md = mod.render(s)
    assert "**60**" in md and "Rs." in md
    assert s["rule_sha256"] in json.dumps(s)


def test_retro_days_never_counted(tmp_path, monkeypatch) -> None:
    mod = _load_module()
    monkeypatch.setattr(mod, "DATA", tmp_path)
    retro = [{**f, "retro": True} for f in _folds(50, 0.5)]
    _write(tmp_path, retro, retro)
    assert mod.compute()["live"]["n"] == 0


def test_demotion_and_unreadable_champion_state_are_shown(tmp_path, monkeypatch) -> None:
    mod = _load_module()
    monkeypatch.setattr(mod, "DATA", tmp_path)
    _write(tmp_path, _folds(30, 0.5), _folds(30, 0.2))
    (tmp_path / "champion_state.json").write_text("{broken")
    (tmp_path / "model_demotion_state.json").write_text(
        json.dumps({"demoted": True, "since": "2026-10-20T00:00:00+00:00", "reasons": []})
    )
    md = mod.render(mod.compute())
    assert "switched off" in md and "could not be read" in md


def test_timeliness_section_is_computed_and_never_guessed(tmp_path, monkeypatch) -> None:
    mod = _load_module()
    monkeypatch.setattr(mod, "DATA", tmp_path)
    _write(tmp_path, [], [])
    md = mod.render(mod.compute())
    assert "No timeliness report has been produced yet." in md
    (tmp_path / "input_timeliness_weekly.json").write_text(
        json.dumps(
            {
                "tanishq_slots": {"slots": 18, "served": 7, "missed": 11, "window_minutes": 45},
                "overnight_window": {
                    "available": True,
                    "nights": 4,
                    "nights_with_model_forecast": 3,
                    "median_minutes_after_us_close": 20,
                },
            }
        )
    )
    md = mod.render(mod.compute())
    assert "**7 of 18**" in md and "**3 of 4**" in md and "20 minutes" in md


def test_timeliness_attribution_is_stated_in_plain_words_and_old_json_still_renders(
    tmp_path, monkeypatch
) -> None:
    mod = _load_module()
    monkeypatch.setattr(mod, "DATA", tmp_path)
    _write(tmp_path, [], [])
    slots = {"slots": 18, "served": 7, "missed": 11, "window_minutes": 45}
    path = tmp_path / "input_timeliness_weekly.json"
    path.write_text(json.dumps({"tanishq_slots": slots}))  # old JSON: no classification
    md = mod.render(mod.compute())
    assert "cannot tell" in md and "never dispatched" not in md
    cls = {
        "no_dispatch": 7,
        "dispatched_cancelled": 2,
        "dispatched_failed": 1,
        "late": 1,
        "dispatched_other": 0,
        "unknown": 0,
    }
    path.write_text(json.dumps({"tanishq_slots": {**slots, "classification": cls}}))
    md = mod.render(mod.compute())
    assert "Of the 11 not run on time: 7 never dispatched" in md
    assert "2 dispatched then cancelled in a catch-up burst" in md
    assert "1 dispatched and failed" in md and "1 ran late" in md
    assert "does not tell a laptop that was off from a scheduler" in md
    assert "cannot tell a laptop that was off from a failed visit" not in md


def test_demotion_shown_is_the_live_champions_own_not_p3s(tmp_path, monkeypatch) -> None:
    mod = _load_module()
    monkeypatch.setattr(mod, "DATA", tmp_path)
    _write(tmp_path, _folds(30, 0.5), _folds(30, 0.2))
    st = {"champion": "p3_roll60", "since": None, "history": []}
    (tmp_path / "champion_state.json").write_text(json.dumps(st))
    (tmp_path / "model_demotion_state.json").write_text(  # P3's file: demoted, not the champion's
        json.dumps({"demoted": True, "since": "2026-10-20T00:00:00+00:00", "reasons": []})
    )
    assert mod.compute()["demotion"] is None
    (tmp_path / "model_demotion_state__nextfix_p3_roll60_v1.json").write_text(
        json.dumps({"demoted": False, "since": None, "reasons": []})
    )
    assert mod.compute()["demotion"]["demoted"] is False


def test_before_the_first_look_the_table_says_so_and_shows_no_gain(tmp_path, monkeypatch) -> None:
    mod = _load_module()
    monkeypatch.setattr(mod, "DATA", tmp_path)
    _write(tmp_path, _folds(10, 0.2), _folds(10, 0.9))
    s = mod.compute()
    md = mod.render(s)
    assert "first look after 20 days (10 so far)" in md
    row = s["challengers"]["ensemble"]
    assert row["looks_started"] is False and row["lower_bound"] is None
    assert "safe estimate of the gain" not in md


def test_once_looking_the_lower_bound_and_days_left_are_shown(tmp_path, monkeypatch) -> None:
    mod = _load_module()
    monkeypatch.setattr(mod, "DATA", tmp_path)
    _write(tmp_path, _folds(60, 0.1), _folds(60, 0.9))
    s = mod.compute()
    row = s["challengers"]["ensemble"]
    assert row["looks_started"] and row["lower_bound"] is not None
    md = mod.render(s)
    assert "safe estimate of the gain" in md and f"{row['horizon_left']} decision days left" in md


def test_a_challenger_past_the_horizon_is_shown_as_retired(tmp_path, monkeypatch) -> None:
    mod = _load_module()
    monkeypatch.setattr(mod, "DATA", tmp_path)
    _write(tmp_path, _folds(200, 0.1), _folds(200, 0.9))
    s = mod.compute()
    assert s["challengers"]["ensemble"]["retired"] is True and s["promote"] is None
    md = mod.render(s)
    assert "retired: 180 decision days passed without qualifying" in md


def test_autocorrelation_of_the_daily_difference_is_reported_from_20_days(tmp_path, monkeypatch):
    """ADR 072 Amendment 1: the safety margin assumes modest autocorrelation, so it is watched."""
    mod = _load_module()
    monkeypatch.setattr(mod, "DATA", tmp_path)
    _write(tmp_path, _folds(10, 0.2), _folds(10, 0.9))
    s = mod.compute()
    assert s["autocorr"] == {} and "Pattern check" not in mod.render(s)
    # alternating wins and losses: lag-1 is strongly negative but lag-2 strongly positive, so it is flagged
    _write(tmp_path, _folds(30, 0.2), _folds(30, 0.9))
    s = mod.compute()
    ac = s["autocorr"]["ensemble"]
    assert ac["n"] == 30 and len(ac["lags"]) == 4 and ac["lags"][0] < -0.5 and ac["high"] is True
    assert "Pattern check" in mod.render(s)


def test_laptop_attribution_lines_explain_missed_visits_in_plain_words() -> None:
    mod = _load_module()
    assert mod._laptop_lines(None) == [] and mod._laptop_lines({"counts": {}}) == []
    att = {
        "generated_at": "2026-10-08T05:41:34+00:00",
        "counts": {"laptop_off": 7, "before_schedule_installed": 4},
    }
    (line,) = mod._laptop_lines(att)
    assert "Why those 11 visits were missed" in line and "checked 2026-10-08" in line
    assert "7 happened while the laptop was shut down" in line
    assert "4 were before the timed visits were set up on 2026-10-05" in line


def test_blocked_challenger_is_labelled_held_back() -> None:
    mod = _load_module()
    row = {
        "retired": False,
        "n": 30,
        "first_look_day": 20,
        "gain": 0.12,
        "lower_bound": 0.07,
        "horizon_left": 100,
        "promotable": False,
        "blocked_reason": "size above its allowance at the 5% boundary",
    }
    _gain, stand, verdict = mod._challenger_cells(row)
    assert verdict == "held back" and "held back until a known flaw" in stand


def test_blocked_challenger_is_marked_even_before_its_first_look() -> None:
    mod = _load_module()
    row = {
        "retired": False,
        "n": 3,
        "first_look_day": 20,
        "first_reachable": "2026-11-04",
        "blocked_reason": "x",
    }
    _gain, stand, verdict = mod._challenger_cells(row)
    assert verdict == "too early" and "held back until a known flaw" in stand
