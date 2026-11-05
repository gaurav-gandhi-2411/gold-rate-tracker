"""scripts/check_price_history_integrity.py -- GG P0 2026-11-05 item 1f (i)-(iii)."""

from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "check_price_history_integrity", REPO / "scripts" / "check_price_history_integrity.py"
)
chk = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(chk)


def _rows(*pairs: tuple[str, int]) -> list[dict]:
    return [{"timestamp": f"{d}T11:30:00.000Z", "22k": v} for d, v in pairs]


def test_unexplained_adjacent_jump_is_flagged_and_an_explained_one_is_not():
    rows = _rows(("2026-05-12", 14097), ("2026-05-13", 14966), ("2026-05-14", 14950))
    assert [m["date"] for m in chk.unexplained_moves(rows, [])] == ["2026-05-13"]
    explained = [{"date": "2026-05-13", "move_pct": 6.16}]
    assert chk.unexplained_moves(rows, explained) == []
    # an explanation of the wrong sign or size does not excuse the move
    assert chk.unexplained_moves(rows, [{"date": "2026-05-13", "move_pct": -6.16}])
    assert chk.unexplained_moves(rows, [{"date": "2026-05-13", "move_pct": 3.0}])


def test_gap_steps_are_not_day_moves_but_published_gaps_are_flagged():
    rows = _rows(("2025-08-08", 9385), ("2025-11-17", 11428), ("2025-11-18", 11450))
    assert chk.unexplained_moves(rows, []) == []  # a 101-day step is not a one-day move...
    assert chk.published_gaps(rows) == [{"from": "2025-08-08", "to": "2025-11-17", "days": 101}]


def test_committed_explanations_cover_every_move_in_the_committed_history():
    expl = json.loads((REPO / "data" / "price_move_explanations.json").read_text(encoding="utf-8"))
    assert all({"date", "move_pct", "cause", "evidence"} <= set(e) for e in expl["moves"])
    published = json.loads((REPO / "data" / "ibja_derived_prices.json").read_text(encoding="utf-8"))
    assert chk.unexplained_moves(published, expl["moves"]) == []


def test_reference_flags_a_basis_error_and_passes_the_right_basis():
    gold = {date(2026, 6, 8): 4363.4}
    fx = {date(2026, 6, 9): 95.688}
    duty = [{"effective_date": "2026-05-13", "total_duty_pct": 15.0}]
    landed = 4363.4 / 31.1035 * 95.688 * 1.15 * 0.916
    ok = _rows(("2026-06-09", round(landed * 1.01)))
    assert chk.reference_flags(ok, gold, fx, duty) == ([], 1)
    as_24k = _rows(("2026-06-09", round(landed * 24 / 22)))  # 24K published as 22K
    flags, n = chk.reference_flags(as_24k, gold, fx, duty)
    assert n == 1 and flags and flags[0]["ratio"] > 1.06


def test_sample_spans_the_whole_history_and_ends_at_the_latest_rows():
    rows = _rows(*[(f"2026-01-{d:02d}", 10_000 + d) for d in range(1, 32)]) * 1
    rows = rows + _rows(*[(f"2026-03-{d:02d}", 10_000 + d) for d in range(1, 32)])
    s = chk.sample_rows(rows, n=20)
    assert s[0] == rows[0] and s[-1] == rows[-1] and len(s) <= 20 + 1


def test_main_is_report_only_and_says_could_not_verify_without_the_ibja_history(tmp_path, capsys):
    d = tmp_path / "data"
    d.mkdir()
    (d / "price_move_explanations.json").write_text(json.dumps({"moves": []}))
    (d / "ibja_derived_prices.json").write_text(
        json.dumps(_rows(("2025-08-08", 9385), ("2025-11-17", 11428)))
    )
    assert chk.main(["--data-dir", str(d)]) == 0  # report-only: findings never fail the run
    out = capsys.readouterr().out
    assert "COULD NOT VERIFY" in out and "GAP(S)" in out and "FINDINGS" in out
    assert chk.main(["--data-dir", str(d), "--strict"]) == 1


def test_main_flags_an_unexplained_move_in_the_full_history(tmp_path, capsys):
    d = tmp_path / "data"
    d.mkdir()
    pd.DataFrame(
        {
            "date": ["2026-09-23", "2026-09-24"],
            "pm_916": [141000.0, 131000.0],
            "pm_999": [154000.0, 143000.0],
            "pm_750": [115000.0, 107000.0],
        }
    ).to_parquet(d / "ibja_rates.parquet")
    (d / "calibration.json").write_text(json.dumps({"slope": 1.0, "intercept": 0.0}))
    (d / "price_move_explanations.json").write_text(json.dumps({"moves": []}))
    (d / "ibja_derived_prices.json").write_text(json.dumps([]))
    assert chk.main(["--data-dir", str(d)]) == 0
    assert "1 UNEXPLAINED" in capsys.readouterr().out


def test_check_price_runs_it_non_blocking():
    """Standing rule (2026-10-30): a new check starts non-blocking. Flipping it is a separate PR
    citing 5 clean real runs over >= 24 h."""
    wf = yaml.safe_load((REPO / ".github" / "workflows" / "check-price.yml").read_text("utf-8"))
    steps = [s for j in wf["jobs"].values() for s in j.get("steps", [])]
    mine = [s for s in steps if "check_price_history_integrity.py" in str(s.get("run", ""))]
    assert len(mine) == 1
    assert mine[0].get("continue-on-error") is True
    assert "--strict" not in mine[0]["run"]
