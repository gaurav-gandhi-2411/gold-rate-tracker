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
        assert r["sentence"].startswith(("No forward data", "No data")), r["id"]
        assert r["forward"]["n"] == 0 or r["id"].startswith("hold_"), r["id"]


@pytest.mark.parametrize("content", ["", "   ", "{not json", "[]", "null"])
def test_malformed_files_never_produce_a_verdict(tmp_path: Path, content: str) -> None:
    for name in (
        "nextfix_oos.json",
        "nextfix_p3_oos.json",
        "nextfix_p3_variants_oos.json",
        "model_demotion_state.json",
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
        assert "demotion" in r  # the documented hook is present (None unless the row is watched)


# --- ADR 069 / 071 / 068: P3 live, ensemble shadow, variants, demotion monitor -------------------

P3_START = bms.FORWARD_STARTS["p3"]["date"]  # 2026-10-07 (ml.nextfix.P3_FORWARD_FROM)


def _pfold(d0: str, model_err: float, flat_err: float, retro: bool | None) -> dict:
    f = _fold(d0, model_err, flat_err)
    if retro is not None:
        f["retro"] = retro
    return f


def _p3_files(
    tmp: Path,
    n_retro: int = 30,
    n_fwd: int = 25,
    p3_err: float = 20.0,
    ens_err: float = 80.0,
) -> None:
    """P3 record: n_retro re-run days then n_fwd live days. Ensemble: same days, no retro flag."""
    retro_days = _days("2026-09-01", n_retro)
    fwd_days = _days(P3_START, n_fwd)
    p3 = [_pfold(d, p3_err + (i % 5), 100.0 + (i % 7), True) for i, d in enumerate(retro_days)]
    p3 += [_pfold(d, p3_err + (i % 5), 100.0 + (i % 7), False) for i, d in enumerate(fwd_days)]
    ens = [_pfold(d, ens_err + (i % 5), 100.0 + (i % 7), None) for i, d in enumerate(retro_days)]
    ens += [_pfold(d, ens_err + (i % 5), 100.0 + (i % 7), None) for i, d in enumerate(fwd_days)]
    _write(tmp, "nextfix_p3_oos.json", {"folds": p3})
    _write(tmp, "nextfix_oos.json", {"folds": ens})


def test_p3_absent_says_no_record_and_the_old_row_still_works(tmp_path: Path) -> None:
    new = [_fold(d, 20.0, 100.0) for d in _days("2026-10-01", 25)]
    _write(tmp_path, "nextfix_oos.json", {"folds": new})
    rows = bms.build_rows(tmp_path)
    p3 = _row(rows, "nextfix_p3")
    assert p3["verdict"] == "grey" and p3["verdict_kind"] == "no_data"
    assert "record not present yet" in p3["sentence"]
    assert p3["forward"]["n"] == 0
    old = _row(rows, "nextfix_model")  # exactly as before: live, from nextfix_oos.json, start 10-01
    assert old["status"] == "LIVE"
    assert old["forward_start"] == START
    assert old["forward"]["n"] == 25
    assert not any(r["id"] == "nextfix_ensemble_shadow" for r in rows)
    # variants cannot be judged without P3
    for vid in ("p3_roll60", "p3_monday"):
        assert "record not present yet" in _row(rows, vid)["sentence"]


def test_p3_forward_is_retro_false_only_and_retro_days_are_labelled_past_tests(
    tmp_path: Path,
) -> None:
    _p3_files(tmp_path, n_retro=30, n_fwd=5)
    rows = bms.build_rows(tmp_path)
    p3 = _row(rows, "nextfix_p3")
    assert p3["status"] == "LIVE"
    assert p3["forward"]["n"] == 5
    assert p3["forward"]["first_date"] == P3_START
    assert p3["verdict_kind"] == "too_early"
    # the 30 re-run days (far better than hold) must not leak into the forward numbers
    assert p3["forward"]["model"] == pytest.approx(22.0, abs=3.0)
    txt = p3["retrospective"]["text"]
    assert "n=30" in txt and "NOT live calls" in txt
    assert p3["retrospective"]["marking"] == "VERIFIED"
    assert "nextfix_model" not in {r["id"] for r in rows}


def test_p3_fold_without_retro_flag_or_flagged_retro_is_never_forward(tmp_path: Path) -> None:
    folds = [
        _pfold("2026-10-05", 10.0, 100.0, None),  # no flag: fail closed
        _pfold("2026-10-06", 10.0, 100.0, True),  # flagged re-run even though the day is late
        _pfold("2026-10-07", 10.0, 100.0, False),
        _pfold("2026-10-04", 10.0, 100.0, False),  # before the live start: not forward
    ]
    _write(tmp_path, "nextfix_p3_oos.json", {"folds": folds})
    p3 = _row(bms.build_rows(tmp_path), "nextfix_p3")
    assert p3["forward"]["n"] == 1
    assert p3["forward"]["first_date"] == "2026-10-07"


def test_p3_file_present_but_unreadable_is_no_data_not_a_record_absent_message(
    tmp_path: Path,
) -> None:
    (tmp_path / "nextfix_p3_oos.json").write_text("{not json", encoding="utf-8")
    p3 = _row(bms.build_rows(tmp_path), "nextfix_p3")
    assert p3["verdict_kind"] == "no_data"
    assert "unreadable" in p3["sentence"]


def test_ensemble_becomes_a_shadow_scored_on_the_p3_days_with_a_paired_comparison(
    tmp_path: Path,
) -> None:
    _p3_files(tmp_path, n_fwd=40, p3_err=20.0, ens_err=80.0)
    rows = bms.build_rows(tmp_path)
    ens = _row(rows, "nextfix_ensemble_shadow")
    assert ens["name"] == "shadow: ridge+neural-net ensemble"
    assert ens["status"] == "SHADOW"
    assert ens["forward_start"] == P3_START
    assert ens["forward"]["n"] == 40
    assert ens["forward"]["first_date"] == P3_START
    assert "NOT live calls" in ens["retrospective"]["text"]
    pr = ens["forward"]["paired"]
    assert pr["n"] == 40
    assert pr["effective_n"] is not None
    assert pr["model"] < pr["baseline"]  # P3 error below the ensemble's
    assert pr["diff"] < 0 and pr["ci95"][1] < 0
    assert pr["p_one_sided"] < 0.05
    assert pr["marking"] == "VERIFIED"
    assert any("P3 vs the ensemble" in x and "one-sided p=" in x for x in ens["forward"]["extras"])


def test_paired_comparison_is_too_early_below_the_minimum(tmp_path: Path) -> None:
    _p3_files(tmp_path, n_fwd=bms.MIN_FORWARD_N - 1)
    ens = _row(bms.build_rows(tmp_path), "nextfix_ensemble_shadow")
    assert any("too early (n=19" in x for x in ens["forward"]["extras"])
    assert ens["verdict_kind"] == "too_early"


def test_paired_comparison_uses_only_days_both_records_scored(tmp_path: Path) -> None:
    _p3_files(tmp_path, n_fwd=25)
    ens_doc = json.loads((tmp_path / "nextfix_oos.json").read_text(encoding="utf-8"))
    # drop the later days
    ens_doc["folds"] = [f for f in ens_doc["folds"] if f["d0"] < "2026-10-15"]
    _write(tmp_path, "nextfix_oos.json", ens_doc)
    ens = _row(bms.build_rows(tmp_path), "nextfix_ensemble_shadow")
    # forward days left: 2026-10-07 .. 2026-10-14 = 8 (it said 10 when the start was wrongly 10-05)
    assert ens["forward"]["n"] == 8
    assert ens["forward"]["paired"]["n"] == 8


def _variant_files(tmp: Path, n_fwd: int, v_err: float, p3_err: float = 100.0) -> None:
    fwd_days = _days(P3_START, n_fwd)
    retro_days = _days("2026-09-01", 10)
    p3 = [_pfold(d, p3_err + (i % 7), 100.0, True) for i, d in enumerate(retro_days)]
    p3 += [_pfold(d, p3_err + (i % 7), 100.0, False) for i, d in enumerate(fwd_days)]
    v = [_pfold(d, v_err + (i % 5), 100.0, True) for i, d in enumerate(retro_days)]
    v += [_pfold(d, v_err + (i % 5), 100.0, False) for i, d in enumerate(fwd_days)]
    _write(tmp, "nextfix_p3_oos.json", {"folds": p3})
    _write(tmp, "nextfix_p3_variants_oos.json", {"variants": {"p3_roll60": v, "p3_monday": v}})
    _write(tmp, "nextfix_oos.json", {"folds": p3})


def test_variants_get_no_verdict_before_40_forward_days_even_if_they_look_great(
    tmp_path: Path,
) -> None:
    _variant_files(tmp_path, n_fwd=39, v_err=1.0)
    for vid in ("p3_roll60", "p3_monday"):
        r = _row(bms.build_rows(tmp_path), vid)
        assert r["verdict"] == "grey" and r["verdict_kind"] == "too_early"
        assert "Too early (n=39" in r["sentence"]
        assert r["forward"]["n"] == 39  # 39 >= MIN_FORWARD_N (20): still no verdict
        assert "NOT live calls" in r["retrospective"]["text"]


def test_variant_clearly_better_at_40_days_is_green_and_recommends_not_ships(
    tmp_path: Path,
) -> None:
    _variant_files(tmp_path, n_fwd=bms.VARIANT_MIN_FORWARD_N, v_err=40.0)
    r = _row(bms.build_rows(tmp_path), "p3_roll60")
    assert r["verdict_kind"] == "scored" and r["verdict"] == "green"
    assert "nothing ships automatically" in r["sentence"]
    assert r["forward"]["n"] == bms.VARIANT_MIN_FORWARD_N


def test_variant_that_is_only_marginally_better_is_not_green(tmp_path: Path) -> None:
    _variant_files(tmp_path, n_fwd=bms.VARIANT_MIN_FORWARD_N, v_err=99.0)  # about 1% better
    r = _row(bms.build_rows(tmp_path), "p3_monday")
    assert r["verdict"] in ("amber", "red") and r["verdict_kind"] == "scored"
    assert "P3 stays" in r["sentence"]


def test_variant_reliably_worse_is_red(tmp_path: Path) -> None:
    _variant_files(tmp_path, n_fwd=bms.VARIANT_MIN_FORWARD_N, v_err=200.0)
    assert _row(bms.build_rows(tmp_path), "p3_roll60")["verdict"] == "red"


def test_variants_file_absent_means_record_not_present(tmp_path: Path) -> None:
    _p3_files(tmp_path)
    r = _row(bms.build_rows(tmp_path), "p3_roll60")
    assert r["verdict_kind"] == "no_data"
    assert "record not present yet" in r["sentence"]


def test_variants_are_outside_the_benjamini_hochberg_family(tmp_path: Path) -> None:
    _variant_files(tmp_path, n_fwd=bms.VARIANT_MIN_FORWARD_N, v_err=40.0)
    assert _row(bms.build_rows(tmp_path), "p3_roll60")["forward"]["bh_significant"] is None


def _state(demoted: bool = False, **kw: object) -> dict:
    return {
        "schema_version": 1,
        "model_version": "nextfix_p3_v1",
        "demoted": demoted,
        "since": "2026-10-20" if demoted else None,
        "reasons": ["error"] if demoted else [],
        "last_checked": "2026-10-21T00:00:00+00:00",
        "history": [],
        **kw,
    }


def test_demotion_monitor_not_demoted_with_live_readout_is_green(tmp_path: Path) -> None:
    _write(tmp_path, "model_demotion_state.json", _state())
    _write(
        tmp_path,
        "forecast.json",
        {"next_fix": {"demotion": {"demoted": False, "rules_breaching_now": []}}},
    )
    rows = bms.build_rows(tmp_path)
    m = _row(rows, "demotion_monitor")
    assert m["verdict"] == "green" and m["verdict_kind"] == "monitor"
    assert "demoted: no" in m["forward"]["extras"]
    assert "rules breaching now (forecast.json): none" in m["forward"]["extras"]
    assert _row(rows, "nextfix_p3")["demotion"]["state"] == "not demoted"
    md = bms.render(bms.build(tmp_path))
    assert "demotion: not demoted" in md


def test_demotion_monitor_breach_now_is_amber_and_demoted_is_red(tmp_path: Path) -> None:
    _write(tmp_path, "model_demotion_state.json", _state())
    _write(
        tmp_path,
        "forecast.json",
        {"next_fix": {"demotion": {"rules_breaching_now": ["direction"]}}},
    )
    m = _row(bms.build_rows(tmp_path), "demotion_monitor")
    assert m["verdict"] == "amber" and "direction" in " ".join(m["forward"]["extras"])
    _write(tmp_path, "model_demotion_state.json", _state(demoted=True))
    rows = bms.build_rows(tmp_path)
    assert _row(rows, "demotion_monitor")["verdict"] == "red"
    assert _row(rows, "nextfix_p3")["demotion"]["state"] == "demoted"
    assert "since: 2026-10-20" in _row(rows, "demotion_monitor")["forward"]["extras"]


def test_demotion_state_without_live_readout_is_amber_not_green(tmp_path: Path) -> None:
    _write(tmp_path, "model_demotion_state.json", _state())
    m = _row(bms.build_rows(tmp_path), "demotion_monitor")
    assert m["verdict"] == "amber"
    assert "not available yet" in " ".join(m["forward"]["extras"])


def test_demotion_state_absent_is_no_data_not_not_demoted(tmp_path: Path) -> None:
    rows = bms.build_rows(tmp_path)
    m = _row(rows, "demotion_monitor")
    assert m["verdict_kind"] == "no_data" and m["verdict"] == "grey"
    assert "not present yet" in m["forward"]["extras"][0]
    assert _row(rows, "nextfix_p3")["demotion"] is None


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[]",
        '{"demoted": "no"}',
        '{"demoted": null}',
        '{"demoted": false, "reasons": 5}',
    ],
)
def test_demotion_state_corrupt_is_no_data_never_green(tmp_path: Path, content: str) -> None:
    (tmp_path / "model_demotion_state.json").write_text(content, encoding="utf-8")
    rows = bms.build_rows(tmp_path)
    m = _row(rows, "demotion_monitor")
    assert m["verdict"] == "grey" and m["verdict_kind"] == "no_data"
    assert "unreadable or malformed" in m["sentence"]
    assert _row(rows, "nextfix_p3")["demotion"] is None


def test_intraday_rows_report_the_excluded_late_entries_from_the_shadow_summary(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "nextfix_intraday_shadow.json",
        {"entries": [], "summary": {"after_us_close": {"n_excluded_logged_after_target": 3}}},
    )
    rows = bms.build_rows(tmp_path)
    # empty entries list means no data (fail closed); with entries the count is read from summary
    assert _row(rows, "intraday_after_us_close")["verdict_kind"] == "no_data"
    entry = {
        "logged_at": "2026-10-05T01:12:00Z",
        "window": "after_us_close",
        "base_at": "2026-10-01T11:30:00Z",
        "base_date": "2026-10-01",
        "target": 100.0,
        "target_kind": "am",
        "target_date": "2026-10-05",
        "pred": {"beta_0.0": 90.0, "beta_0.5": 95.0, "beta_1.0": 99.0},
    }
    _write(
        tmp_path,
        "nextfix_intraday_shadow.json",
        {"entries": [entry], "summary": {"after_us_close": {"n_excluded_logged_after_target": 3}}},
    )
    rows = bms.build_rows(tmp_path)
    assert "3 excluded so far" in " ".join(
        _row(rows, "intraday_after_us_close")["forward"]["extras"]
    )
    # an unreported count is stated as such, never invented
    assert "not in its summary" in " ".join(
        _row(rows, "intraday_after_afternoon_rate")["forward"]["extras"]
    )


def test_render_never_calls_a_retro_figure_live(tmp_path: Path) -> None:
    _p3_files(tmp_path, n_retro=30, n_fwd=3)
    md = bms.render(bms.build(tmp_path))
    checked = 0
    for line in md.splitlines():
        if line.startswith("| **ml.nextfix P3") or line.startswith("| **shadow: ridge"):
            assert "NOT live calls" in line
            checked += 1
    assert checked == 2


def test_p3_forward_starts_equal_the_live_code_constant() -> None:
    # the scorecard once said 2026-10-05 while ml.nextfix flags retro folds before P3_FORWARD_FROM
    from ml import nextfix

    assert bms.FORWARD_STARTS["p3"]["date"] == nextfix.P3_FORWARD_FROM
    assert bms.FORWARD_STARTS["p3_variants"]["date"] == nextfix.P3_FORWARD_FROM
    assert nextfix.P3_FORWARD_FROM in bms.FORWARD_STARTS["p3"]["basis"]
    assert nextfix.P3_FORWARD_FROM in bms.FORWARD_STARTS["p3_variants"]["basis"]


def test_shadow_ensemble_counts_the_same_forward_days_as_p3(tmp_path: Path) -> None:
    # P3 treats the 10-05 and 10-06 folds as retro; the ensemble must not count them as forward
    _p3_files(tmp_path, n_retro=0, n_fwd=0)
    days = _days("2026-10-05", 6)  # 10-05 .. 10-10
    p3 = [_pfold(d, 20.0, 100.0, d < P3_START) for d in days]
    ens = [_pfold(d, 80.0, 100.0, None) for d in days]
    _write(tmp_path, "nextfix_p3_oos.json", {"folds": p3})
    _write(tmp_path, "nextfix_oos.json", {"folds": ens})
    rows = bms.build_rows(tmp_path)
    p3_row, ens_row = _row(rows, "nextfix_p3"), _row(rows, "nextfix_ensemble_shadow")
    assert p3_row["forward"]["n"] == ens_row["forward"]["n"] == 4
    assert ens_row["forward"]["first_date"] == p3_row["forward"]["first_date"] == P3_START
