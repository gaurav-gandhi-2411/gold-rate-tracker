"""Tests for scripts/build_model_scorecard.py: synthetic data only, no network, no committed data."""

from __future__ import annotations

import importlib.util
import json
import math
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "build_model_scorecard", ROOT / "scripts" / "build_model_scorecard.py"
)
assert _spec is not None and _spec.loader is not None
bms = importlib.util.module_from_spec(_spec)
sys.modules["build_model_scorecard"] = bms
_spec.loader.exec_module(bms)

START = bms.FORWARD_STARTS["nextfix"]["date"]  # 2026-10-01


def _days(first: str, n: int) -> list[str]:
    d0 = date.fromisoformat(first)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def _fold(d0: str, model_err: float, flat_err: float) -> dict:
    """pm1 sits `flat_err` above pm0; the model (ret=0 baseline shifted) misses by `model_err`."""
    pm0 = 13000.0
    pm1 = pm0 + flat_err
    # ret chosen so pm0*exp(ret) is `model_err` away from pm1, on the low side
    ret = math.log((pm1 - model_err) / pm0)
    return {
        "d0": d0,
        "d1": d0,
        "pm0": pm0,
        "pm1": pm1,
        "y": 0.001,
        "ret": ret,
        "p_up": 0.6,
        "vol": 0.01,
    }


def _write(tmp: Path, name: str, obj: object) -> None:
    (tmp / name).write_text(json.dumps(obj), encoding="utf-8")


def _row(rows: list[dict], row_id: str) -> dict:
    return next(r for r in rows if r["id"] == row_id)


# --- hostile: missing / empty / malformed input is "no data", never a pass -------------------------


def test_empty_data_dir_yields_no_data_everywhere(tmp_path: Path) -> None:
    rows = bms.build_rows(tmp_path)
    assert rows, "the scorecard must still list every model"
    for r in rows:
        assert r["verdict"] == "grey", r["id"]
        assert r["verdict_kind"] == "no_data", r["id"]
        assert r["sentence"].startswith("No forward data"), r["id"]
        assert r["forward"]["n"] == 0 or r["id"].startswith("hold_"), r["id"]


@pytest.mark.parametrize("content", ["", "   ", "{not json", "[]", "null"])
def test_malformed_files_never_produce_a_verdict(tmp_path: Path, content: str) -> None:
    for name in (
        "nextfix_oos.json",
        "nextfix_intraday_shadow.json",
        "weekly_range_shadow_log.json",
        "next_day_range_shadow.json",
        "wait_or_buy_shadow.json",
        "nowcast_shadow_log.json",
        "preregistered_h2_shadow_results.json",
        "backtest.json",
        "direction_baseline.json",
        "calibration_band_coverage.json",
    ):
        (tmp_path / name).write_text(content, encoding="utf-8")
    for r in bms.build_rows(tmp_path):
        assert r["verdict"] in ("grey",), (r["id"], r["verdict"])
        assert r["verdict_kind"] == "no_data", r["id"]


# --- forward vs retrospective separation ----------------------------------------------------------


def test_forward_excludes_retrospective_folds(tmp_path: Path) -> None:
    old = [_fold(d, 10.0, 100.0) for d in _days("2026-08-01", 30)]  # model far better, old
    new = [_fold(d, 100.0, 100.0) for d in _days("2026-10-01", 5)]  # model == hold, forward
    _write(tmp_path, "nextfix_oos.json", {"folds": old + new})
    r = _row(bms.build_rows(tmp_path), "nextfix_model")
    f = r["forward"]
    assert f["n"] == 5
    assert f["first_date"] == "2026-10-01"
    # forward MAE is the forward-only 100, not the blend with the (much better) retrospective 10
    assert f["model"] == pytest.approx(100.0, abs=0.5)
    assert f["baseline"] == pytest.approx(100.0, abs=0.5)
    assert "n=30" in r["retrospective"]["text"]
    assert r["retrospective"]["marking"] == "VERIFIED"
    assert "forward" not in r["retrospective"]["text"].lower().replace("not forward", "")


def test_too_early_shows_no_verdict_even_if_the_numbers_look_great(tmp_path: Path) -> None:
    new = [_fold(d, 1.0, 100.0) for d in _days("2026-10-01", bms.MIN_FORWARD_N - 1)]
    _write(tmp_path, "nextfix_oos.json", {"folds": new})
    r = _row(bms.build_rows(tmp_path), "nextfix_model")
    assert r["verdict"] == "grey"
    assert r["verdict_kind"] == "too_early"
    assert f"n={bms.MIN_FORWARD_N - 1}" in r["sentence"]
    md = bms.render({**_doc(tmp_path)})
    assert "Too early" in md


def test_green_needs_enough_forward_data_and_a_real_gap(tmp_path: Path) -> None:
    errs = [(8.0 + (i % 5), 100.0 + (i % 7)) for i in range(40)]
    new = [_fold(d, a, b) for d, (a, b) in zip(_days("2026-10-01", 40), errs, strict=True)]
    _write(tmp_path, "nextfix_oos.json", {"folds": new})
    r = _row(bms.build_rows(tmp_path), "nextfix_model")
    assert r["forward"]["n"] == 40
    assert r["forward"]["effective_n"] is not None
    assert r["forward"]["bh_significant"] is True
    assert r["verdict"] == "green"


def test_model_reliably_worse_is_red(tmp_path: Path) -> None:
    errs = [(150.0 + (i % 5), 50.0 + (i % 7)) for i in range(40)]
    new = [_fold(d, a, b) for d, (a, b) in zip(_days("2026-10-01", 40), errs, strict=True)]
    _write(tmp_path, "nextfix_oos.json", {"folds": new})
    assert _row(bms.build_rows(tmp_path), "nextfix_model")["verdict"] == "red"


def test_weekly_range_counts_only_issues_after_the_start(tmp_path: Path) -> None:
    start = bms.FORWARD_STARTS["weekly_range"]["date"]
    entries = [
        {"as_of": "2026-09-01", "horizon": "1d", "result": {"inside": False}},  # before: retro
        {"as_of": "2026-09-25", "horizon": "1d", "result": {"inside": True}},
        {"as_of": "2026-09-28", "horizon": "1d", "result": {"inside": False}},
        {"as_of": "2026-09-29", "horizon": "1d"},  # unscored
        {"as_of": "2026-09-25", "horizon": "week", "result": {"inside": True}},
    ]
    assert entries[1]["as_of"] > start
    _write(tmp_path, "weekly_range_shadow_log.json", {"entries": entries})
    rows = bms.build_rows(tmp_path)
    one = _row(rows, "weekly_range_1d")["forward"]
    assert one["n"] == 2
    assert one["model"] == 0.5
    assert _row(rows, "weekly_range_week")["forward"]["n"] == 1


def test_intraday_ignores_entries_logged_after_the_target_was_published() -> None:
    def entry(logged: str, beta1: float) -> dict:
        return {
            "logged_at": logged,
            "window": "after_us_close",
            "base_at": "2026-10-01T11:30:00Z",
            "base_date": "2026-10-01",
            "target": 100.0,
            "target_kind": "am",
            "target_date": "2026-10-05",
            "pred": {"beta_0.0": 90.0, "beta_0.5": 95.0, "beta_1.0": beta1},
        }

    early = entry("2026-10-05T01:12:00Z", 99.0)
    late = entry(
        "2026-10-05T07:13:00Z", 100.0
    )  # after the 06:30Z AM publish: would leak the answer
    out = bms._dedupe_intraday([early, late])
    assert [e["logged_at"] for e in out["after_us_close"]] == ["2026-10-05T01:12:00Z"]


# --- statistics -----------------------------------------------------------------------------------


def test_wilson_and_binomial_basics() -> None:
    lo, hi = bms.wilson(8, 10)
    assert lo < 0.8 < hi
    assert bms.wilson(0, 0) is None
    assert bms.p_below(10, 10) == pytest.approx(1.0)
    assert bms.p_below(0, 10) == pytest.approx(0.2**10)


def test_bh_flags_keep_none_and_apply_the_step_up_rule() -> None:
    flags = bms.bh_flags([0.001, None, 0.04, 0.5])
    assert flags[1] is None
    assert flags[0] is True
    assert flags[3] is False


def test_mcnemar_one_sided() -> None:
    assert bms.mcnemar_one_sided(0, 0) is None
    assert bms.mcnemar_one_sided(5, 0) == pytest.approx(1 / 32)


def test_effective_n_shrinks_with_autocorrelation() -> None:
    iid = [1.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0, 0.0, 1.0, 0.0]
    sticky = [1.0] * 5 + [0.0] * 5
    assert bms.effective_n_ar1(sticky) < bms.effective_n_ar1(iid)
    assert bms.effective_n_ar1([1.0, 0.0]) is None


# --- document regenerated identically from the JSON ----------------------------------------------


def _doc(tmp_path: Path) -> dict:
    return bms.build(tmp_path)


def test_markdown_is_a_pure_function_of_the_json(tmp_path: Path) -> None:
    new = [_fold(d, 20.0, 100.0) for d in _days("2026-10-01", 25)]
    _write(tmp_path, "nextfix_oos.json", {"folds": new})
    out_json, out_md = tmp_path / "s.json", tmp_path / "s.md"
    cli = [sys.executable, str(ROOT / "scripts" / "build_model_scorecard.py")]
    args = ["--out-json", str(out_json), "--out-md", str(out_md), "--data-dir", str(tmp_path)]
    subprocess.run([*cli, *args], check=True, capture_output=True)
    first_md = out_md.read_bytes()
    out_md.unlink()
    subprocess.run([*cli, "--render-only", *args], check=True, capture_output=True)
    assert out_md.read_bytes() == first_md
    assert b"\r" not in first_md  # LF only
    assert bms.render(json.loads(out_json.read_text(encoding="utf-8"))).encode() == first_md


def test_render_only_needs_no_heavy_imports() -> None:
    code = (
        "import importlib.util, json, sys;"
        f"s=importlib.util.spec_from_file_location('m', r'{ROOT / 'scripts' / 'build_model_scorecard.py'}');"
        "m=importlib.util.module_from_spec(s); sys.modules['m']=m; s.loader.exec_module(m);"
        "m.render({'generated_at_utc':'x','script':'s','git_sha':'','script_blob_sha':'',"
        "'min_forward_n':20,'models':[]});"
        "assert 'numpy' not in sys.modules and 'pandas' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_every_row_has_provenance_and_marking(tmp_path: Path) -> None:
    doc = _doc(tmp_path)
    assert doc["script"] == "scripts/build_model_scorecard.py"
    for r in doc["models"]:
        assert r["sources"] or r["id"] == "error", r["id"]
        assert r["forward"]["marking"] in ("VERIFIED", "INFERRED")
        assert r["retrospective"]["marking"] in ("VERIFIED", "INFERRED")
        assert r["forward_start"] and r["forward_start_basis"]
        assert "demotion" in r  # the documented hook is present, None until ml.demotion exists
