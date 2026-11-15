"""The live next-fix model going down is recorded in the ops log (2026-11-12: down ~32 h unnoticed).

Also pins the volatility guard: a 0 price in prices.json must not raise (2026-11-13). No clock reads.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest
import yaml
from ml.volatility import _log_returns

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_next_fix_active as chk


def _forecast(tmp_path: Path, doc: object) -> Path:
    p = tmp_path / "forecast.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


@pytest.mark.parametrize(
    ("doc", "ok", "needle"),
    [
        ({"next_fix": {"active": True}}, True, "active"),
        (
            {"next_fix": {"active": False, "reason": "error: ZeroDivisionError"}},
            False,
            "ZeroDivision",
        ),
        ({"next_fix": {"active": False}}, False, "no reason recorded"),
        ({"next_fix": {"active": "true"}}, False, "'true'"),  # not the boolean: not active
        ({"next_fix": {}}, False, "unreadable"),
        ({}, False, "unreadable"),
        ({"next_fix": None}, False, "unreadable"),
    ],
)
def test_verdict(tmp_path, doc, ok, needle):
    got_ok, reason = chk.verdict(_forecast(tmp_path, doc))
    assert got_ok is ok and needle in reason


def test_a_missing_or_malformed_file_is_down_not_ok(tmp_path):
    assert chk.verdict(tmp_path / "nope.json")[0] is False
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    assert chk.verdict(bad)[0] is False


def test_main_always_exits_0_and_says_down(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("OPS_LOG_NO_FILE", "1")  # no file append in a test
    p = _forecast(tmp_path, {"next_fix": {"active": False, "reason": "error: ZeroDivisionError"}})
    assert chk.main(["--forecast", str(p)]) == 0
    out = capsys.readouterr().out
    assert "next-fix health: DOWN" in out and "ops-alert" in out
    ok = _forecast(tmp_path, {"next_fix": {"active": True}})
    assert chk.main(["--forecast", str(ok)]) == 0
    assert "next-fix health: OK" in capsys.readouterr().out


def test_the_workflow_runs_it_after_inference_always_and_non_blocking():
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "check-price.yml").read_text("utf-8"))
    steps = [s for j in wf["jobs"].values() for s in j.get("steps", [])]
    names = [s.get("name", "") for s in steps]
    mine = [s for s in steps if "check_next_fix_active.py" in str(s.get("run", ""))]
    assert len(mine) == 1
    s = mine[0]
    assert s["if"] == "always()" and s["continue-on-error"] is True
    assert names.index("Run inference") + 1 == names.index(s["name"])


def test_log_returns_skip_a_pair_with_a_non_positive_price_instead_of_raising():
    assert _log_returns([100.0, 110.0])[0] == pytest.approx(math.log(1.1))
    assert _log_returns([]) == [] and _log_returns([100.0]) == []
    assert _log_returns([100.0, 0.0, 110.0]) == []  # both pairs touch the 0
    got = _log_returns([100.0, 110.0, 0.0, 120.0, 121.0])
    assert got == [pytest.approx(math.log(1.1)), pytest.approx(math.log(121 / 120))]
