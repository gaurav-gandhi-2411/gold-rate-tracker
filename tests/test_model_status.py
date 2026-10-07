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
