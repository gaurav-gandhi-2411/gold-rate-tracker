"""ADR 076 (rule v3, amendments 1 and 2, 2026-10-17): the forward-only second look of the hourly model.

Synthetic data only. The look must (1) score only entries logged strictly after the cut-off, (2) use
entries logged before it only to fix the range band, never to score, (3) compute NO effect size, CI or
p-value before the look day, (4) look exactly once: the first Monday on or after 2026-11-16 on which a
window meets both count gates, and (5) leave the ADR 066 check unchanged."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from ml import nextfix_intraday as ni

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "check_adr066_promotion_076", _ROOT / "scripts" / "check_adr066_promotion.py"
)
mod = importlib.util.module_from_spec(_spec)
sys.modules["check_adr066_promotion_076"] = mod
_spec.loader.exec_module(mod)

WIN = "after_morning_rate"
CUTOFF = datetime(2026, 10, 16, 17, 38, tzinfo=UTC)
FIRST_DAY = datetime(2026, 10, 17, tzinfo=UTC)
PRE_START = datetime(2026, 9, 20, tzinfo=UTC)
LOOK_MONDAY = datetime(2026, 11, 16, tzinfo=UTC)
OPTS = {
    "first_entry_after": mod.ADR076_FIRST_ENTRY_AFTER,
    "min_fixes": mod.ADR076_MIN_FIXES,
    "min_days": mod.ADR076_MIN_DAYS,
    "alpha": mod.ADR076_ALPHA,
    "beta": mod.ADR076_BETA,
    "look_not_before": mod.ADR076_LOOK_NOT_BEFORE,
}
# 20 fixes spread over 30 calendar days (10-17 .. 11-15): meets 20 fixes and 28 days before 11-16
OFFSETS_20 = [round(i * 29 / 19) for i in range(20)]


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_entries(
    offsets: list[int],
    start: datetime = FIRST_DAY,
    slope: float = 1.0,
    noise_sd: float = 0.002,
    seed: int = 42,
    hour: int = 8,
    window: str = WIN,
) -> list[dict]:
    """One decision per listed day (AM fix 06:30Z, decision at ``hour``, target = same day's PM fix).
    target = base * exp(slope * x + noise): beta 1.0 is right when slope == 1."""
    rng = np.random.default_rng(seed)
    out = []
    for off in offsets:
        d = start + timedelta(days=off)
        x = float(rng.normal(0, 0.01))
        base = 13000.0
        t = d.replace(hour=hour)
        out.append(
            {
                "logged_at": _iso(t),
                "t": _iso(t),
                "base_kind": "am",
                "base_date": d.strftime("%Y-%m-%d"),
                "base_at": _iso(d.replace(hour=6, minute=30)),
                "base": base,
                "window": window,
                "target_kind": "pm",
                "x_since_fix": x,
                "pred": {f"beta_{b}": round(base * float(np.exp(b * x)), 2) for b in ni.BETAS},
                "target": round(base * float(np.exp(slope * x + rng.normal(0, noise_sd))), 2),
                "target_date": d.strftime("%Y-%m-%d"),
            }
        )
    return out


def book(n_post_offsets: list[int], n_cal: int = 12, **kw) -> list[dict]:
    """Pre-cut-off calibration entries plus post-cut-off entries, cleaned like the real log."""
    cal = make_entries(list(range(n_cal)), start=PRE_START, seed=3, **kw)
    post = make_entries(n_post_offsets, seed=4, **kw)
    return mod.clean_entries(cal + post)


def evaluate(
    entries: list[dict], now: datetime, opts: dict | None = None, folds: object = ()
) -> dict:
    return mod.evaluate_window_076(WIN, entries, now, opts or OPTS, folds)


@pytest.fixture
def no_effect(monkeypatch):
    """Any effect computation fails the test: used wherever the look must stay closed."""

    def boom(*a, **k):
        raise AssertionError("an effect was computed before the look")

    monkeypatch.setattr(mod, "adr076_effect", boom)
    monkeypatch.setattr(mod, "cond_c_076", boom)
    monkeypatch.setattr(mod, "dm_p_one_sided", boom)
    monkeypatch.setattr(mod.nextfix_intraday, "_cluster_ci", boom)
    monkeypatch.setattr(mod.nextfix_intraday, "score", boom)
    monkeypatch.setattr(mod.nextfix, "diebold_mariano_p", boom)
    monkeypatch.setattr(mod.nextfix, "flat_record", boom)


# -- timing: counts only until the first Monday on which both gates hold -----------------------


def test_nothing_is_computed_before_the_look_date(no_effect):
    entries = book(list(range(60)))
    for now in (datetime(2026, 11, 9, 9, tzinfo=UTC), datetime(2026, 10, 19, 9, tzinfo=UTC)):
        r = evaluate(entries, now)
        assert r["look_day"] is None and "effect" not in r
        assert r["verdict"].startswith("counts only")


def test_short_of_the_counts_on_the_first_monday_computes_nothing_and_spends_no_look(no_effect):
    entries = book(list(range(15)))
    r = evaluate(entries, datetime(2026, 11, 16, 9, tzinfo=UTC))
    assert r["look_day"] is None and "effect" not in r
    assert r["counts"]["n_resolved_base_fixes"] == 15 and r["counts_ready"] is False


def test_days_gate_alone_keeps_the_look_closed(no_effect):
    r = evaluate(book(list(range(25))), datetime(2026, 11, 16, 9, tzinfo=UTC))
    assert r["counts"]["calendar_span_days"] == 25 and r["look_day"] is None


def test_the_look_is_the_first_monday_on_which_both_gates_hold():
    # 14 fixes by 11-15, 6 more on 11-17..11-22: ready only as of Monday 11-23
    offs = [round(i * 29 / 13) for i in range(14)] + [31, 32, 33, 34, 35, 36]
    r = evaluate(book(offs), datetime(2026, 11, 30, 9, tzinfo=UTC))
    assert r["look_day"] == "2026-11-23"
    assert r["counts"]["n_resolved_base_fixes"] == 20


def test_a_rerun_after_the_look_day_is_the_same_look_and_ignores_later_entries():
    on_day = evaluate(book(OFFSETS_20), LOOK_MONDAY + timedelta(hours=9))
    later = book([*OFFSETS_20, 31, 32, 33], slope=-1.0)  # entries after the look day, other regime
    again = evaluate(book([*OFFSETS_20, 31, 32, 33]), datetime(2026, 12, 7, 9, tzinfo=UTC))
    assert on_day["look_day"] == again["look_day"] == "2026-11-16"
    assert on_day["effect"] == again["effect"]
    assert later  # (built only to show later entries exist in a different regime)


# -- the data: forward-only for scoring, pre-cut-off only for calibration ------------------------


def test_only_entries_strictly_after_the_cutoff_are_scored():
    at_cutoff = make_entries([0], start=datetime(2026, 10, 16, tzinfo=UTC), hour=17)[0]
    at_cutoff["logged_at"] = at_cutoff["t"] = _iso(CUTOFF)
    after = make_entries([0], start=datetime(2026, 10, 17, tzinfo=UTC))
    c = mod.adr076_counts(mod.clean_entries([at_cutoff, *after]), WIN, CUTOFF)
    assert c["n_entries"] == 1 and c["n_resolved_base_fixes"] == 1


def test_an_entry_logged_exactly_at_the_cutoff_is_neither_scored_nor_calibration():
    at_cutoff = make_entries([0], start=datetime(2026, 10, 16, tzinfo=UTC), hour=17)[0]
    at_cutoff["logged_at"] = at_cutoff["t"] = _iso(CUTOFF)
    es = mod.clean_entries([at_cutoff])
    assert mod.forward_rows(es, WIN, CUTOFF) == []
    assert mod.calibration_rows(es, WIN, CUTOFF) == []


def test_calibration_rows_are_pre_cutoff_and_never_in_the_scored_set():
    es = book(OFFSETS_20, n_cal=12)
    cal = mod.calibration_rows(es, WIN, CUTOFF)
    fwd = mod.forward_rows(es, WIN, CUTOFF)
    assert len(cal) == 12 and len(fwd) == 20
    assert {r["base_at"] for r in cal}.isdisjoint({r["base_at"] for r in fwd})
    assert all(mod._parse_t(r["logged_at"]) < CUTOFF for r in cal)


def test_a_fix_with_decisions_on_both_sides_of_the_cutoff_is_scored_not_calibrated():
    pre = make_entries([0], start=datetime(2026, 10, 16, tzinfo=UTC), hour=8)[0]
    pre["target_date"] = "2026-10-17"  # target published after both decisions
    post = {**pre, "logged_at": "2026-10-16T18:00:00Z", "t": "2026-10-16T18:00:00Z"}
    es = mod.clean_entries([pre, post])
    assert mod.calibration_rows(es, WIN, CUTOFF) == []
    assert len(mod.forward_rows(es, WIN, CUTOFF)) == 1


def test_entries_logged_after_their_target_was_published_do_not_count():
    late = mod.clean_entries(make_entries(list(range(30)), hour=12))
    c = mod.adr076_counts(late, WIN, CUTOFF)
    assert c["n_entries"] == 30 and c["n_resolved_base_fixes"] == 0


def test_other_windows_are_not_mixed_in():
    other = mod.clean_entries(make_entries(list(range(30)), window="after_afternoon_rate"))
    assert mod.adr076_counts(other, WIN, CUTOFF)["n_entries"] == 0


# -- the look ----------------------------------------------------------------------------------


def test_condition_c_is_evaluable_at_exactly_20_post_cutoff_fixes():
    r = evaluate(book(OFFSETS_20), LOOK_MONDAY + timedelta(hours=9))
    assert r["counts"]["n_resolved_base_fixes"] == 20
    c = r["effect"]["c_range_coverage"]
    assert "not evaluable" not in c["detail"]
    assert c["n_scored"] == 20 and c["n_calibration"] == 12
    assert c["pass"] is True, c


def test_a_good_hourly_model_passes_all_three_conditions_at_20_fixes():
    r = evaluate(book(OFFSETS_20), LOOK_MONDAY + timedelta(hours=9))
    eff = r["effect"]
    assert eff["a_bootstrap_ci_below_0"]["pass"] and eff["a_bootstrap_ci_below_0"]["n"] == 20
    assert eff["b_dm_one_sided"]["pass"] and eff["b_dm_one_sided"]["dm_p_one_sided"] < 0.025
    assert eff["all_pass"] and "pass on beta 1.0" in r["verdict"]


def test_calibration_entries_are_not_scored_in_conditions_a_and_b():
    eff = evaluate(book(OFFSETS_20), LOOK_MONDAY + timedelta(hours=9))["effect"]
    assert eff["a_bootstrap_ci_below_0"]["n"] == 20  # not 32


def test_too_few_calibration_fixes_is_not_evaluable_and_fails_closed():
    r = evaluate(book(OFFSETS_20, n_cal=4), LOOK_MONDAY + timedelta(hours=9))
    c = r["effect"]["c_range_coverage"]
    assert c["pass"] is False and "not evaluable" in c["detail"]
    assert r["effect"]["all_pass"] is False


def test_the_band_is_fixed_on_calibration_so_forward_outliers_cannot_widen_it():
    cal = mod.calibration_rows(book(OFFSETS_20), WIN, CUTOFF)
    fwd = mod.forward_rows(book(OFFSETS_20), WIN, CUTOFF)
    wild = [{**r, "target": r["target"] * 1.05} for r in fwd]  # all far outside a 0.2% band
    c = mod.cond_c_076(cal, wild, 1.0)
    assert c["coverage"] == 0.0 and c["pass"] is False


def test_a_useless_hourly_model_fails_a_and_b():
    r = evaluate(book(OFFSETS_20, slope=0.0, noise_sd=0.003), LOOK_MONDAY + timedelta(hours=9))
    eff = r["effect"]
    assert not eff["a_bootstrap_ci_below_0"]["pass"] and not eff["b_dm_one_sided"]["pass"]
    assert not eff["all_pass"] and "fail:" in r["verdict"]


def test_a_harmful_hourly_model_gets_a_large_one_sided_p():
    r = evaluate(book(OFFSETS_20, slope=-1.0, noise_sd=0.001), LOOK_MONDAY + timedelta(hours=9))
    assert r["effect"]["b_dm_one_sided"]["dm_p_one_sided"] > 0.5


def test_dm_one_sided_is_half_the_two_sided_p_when_better_and_mirrors_when_worse():
    rng = np.random.default_rng(1)
    hold = np.abs(rng.normal(0, 1.0, 40)) + 0.5
    better = hold * 0.7
    p2 = mod.nextfix.diebold_mariano_p(better, hold)
    assert mod.dm_p_one_sided(better, hold) == pytest.approx(p2 / 2)
    assert mod.dm_p_one_sided(hold, better) == pytest.approx(1 - p2 / 2)


def test_the_bootstrap_is_pre_registered_and_deterministic():
    assert (mod.ADR076_BOOTSTRAP_B, mod.ADR076_SEED) == (10_000, 42)
    e = book(OFFSETS_20)
    now = LOOK_MONDAY + timedelta(hours=9)
    assert evaluate(e, now)["effect"] == evaluate(e, now)["effect"]


def test_options_override_the_counts():
    opts = {**OPTS, "min_fixes": 5, "min_days": 5}
    r = evaluate(book(list(range(8))), datetime(2026, 11, 16, 9, tzinfo=UTC), opts)
    assert r["look_day"] == "2026-11-16" and "effect" in r


# -- the frozen rule -----------------------------------------------------------------------------


def test_the_rule_hash_is_in_the_adr_and_changes_when_a_registered_value_changes(monkeypatch):
    adr = (_ROOT / "docs" / "adr" / "076-hourly-model-forward-check-preregistration.md").read_text(
        encoding="utf-8"
    )
    assert mod.adr076_rule_hash() in adr
    monkeypatch.setitem(mod.ADR076_RULE, "min_fixes", 21)
    assert mod.adr076_rule_hash() not in adr


def test_the_defaults_are_the_adr_values():
    assert mod.ADR076_FIRST_ENTRY_AFTER == "2026-10-16T17:38:00Z"
    assert (mod.ADR076_MIN_FIXES, mod.ADR076_MIN_DAYS) == (20, 28)
    assert (mod.ADR076_ALPHA, mod.ADR076_BETA) == (0.025, 1.0)
    assert mod.ADR076_LOOK_NOT_BEFORE == "2026-11-16"
    assert mod.ADR076_MIN_CALIBRATION == 5


# -- the command line ------------------------------------------------------------------------------


def test_the_adr066_check_keeps_its_defaults_and_refuses_076_options():
    assert (mod.MIN_SHADOW_DAYS, mod.MIN_RESOLVED_FIXES, mod.DM_ALPHA) == (14, 8, 0.05)
    assert mod.CANDIDATE_BETAS == (0.5, 1.0)
    with pytest.raises(SystemExit) as ei:
        mod.main(["--min-fixes", "5"])
    assert ei.value.code == 2


def _shadow(tmp_path: Path, entries: list[dict]) -> tuple[Path, Path]:
    data = tmp_path / "data"
    data.mkdir()
    (data / "nextfix_intraday_shadow.json").write_text(json.dumps({"entries": entries}))
    return data, tmp_path / "out"


def test_cli_076_before_the_look_writes_counts_only(tmp_path, capsys):
    raw = make_entries(list(range(12)), start=PRE_START, seed=3) + make_entries(list(range(60)))
    data, out = _shadow(tmp_path, raw)
    rc = mod.main(
        [
            "--adr",
            "076",
            "--now",
            "2026-11-09T09:00:00Z",
            "--data-dir",
            str(data),
            "--out-dir",
            str(out),
        ]
    )
    assert rc == 0
    rep = json.loads((out / "adr076_look_2026-11-09.json").read_text())
    assert rep["adr"] == "076" and rep["look_open"] is False
    assert rep["rule_hash"] == mod.adr076_rule_hash()
    assert all("effect" not in w for w in rep["windows"].values())
    text = capsys.readouterr().out
    assert "counts only" in text and "ci95" not in text and "dm_p" not in text


def _look_args(data: Path, out: Path) -> list[str]:
    return ["--adr", "076", "--now", "2026-11-16T09:00:00Z", "--data-dir", str(data)] + [
        "--out-dir",
        str(out),
    ]


def test_cli_076_on_the_look_day_reports_the_effect(tmp_path, capsys, monkeypatch):
    raw = make_entries(list(range(12)), start=PRE_START, seed=3) + make_entries(OFFSETS_20, seed=4)
    data, out = _shadow(tmp_path, raw)
    monkeypatch.setattr(mod, "load_folds", lambda d: make_folds(list(range(30))))
    mod.main(_look_args(data, out))
    rep = json.loads((out / "adr076_look_2026-11-16.json").read_text())
    assert rep["look_open"] is True and "effect" in rep["windows"][WIN]
    assert rep["adr066_folds"] == {"available": True, "n_decisions": 30}
    assert "a_bootstrap_ci_below_0" in capsys.readouterr().out


def test_cli_076_on_the_look_day_without_the_folds_takes_no_look(tmp_path, capsys, no_effect):
    raw = make_entries(list(range(12)), start=PRE_START, seed=3) + make_entries(OFFSETS_20, seed=4)
    data, out = _shadow(tmp_path, raw)  # no cached bars in the data dir
    assert mod.main(_look_args(data, out)) == 0
    rep = json.loads((out / "adr076_look_2026-11-16.json").read_text())
    assert rep["look_open"] is False and rep["adr066_folds"]["available"] is False
    assert rep["windows"][WIN]["look_due"] == "2026-11-16" and "effect" not in rep["windows"][WIN]
    assert "NOT taken" in capsys.readouterr().out


# -- amendment 2: the ADR 066 re-run folds join the calibration ---------------------------------------


def make_folds(offsets: list[int], start: datetime = FIRST_DAY - timedelta(days=60), **kw) -> list:
    """ADR 066 re-run decisions as backtest_rows() returns them (they carry target_at, not logged_at)."""
    rows = []
    for e in make_entries(offsets, start=start, seed=11, **kw):
        t = datetime.fromisoformat(e["t"].replace("Z", "+00:00"))
        rows.append(
            {
                **{k: v for k, v in e.items() if k not in ("logged_at", "target_date")},
                "target_at": _iso(t.replace(hour=11, minute=30)),
            }
        )
    return rows


def test_the_folds_add_to_the_calibration_and_the_shadow_row_wins_on_an_overlap():
    entries = book(OFFSETS_20, n_cal=6)  # 6 shadow fixes on 2026-09-20 .. 09-25
    folds = make_folds(list(range(25)), start=PRE_START)  # 25 fixes, the first 6 are the same days
    cal = mod.calibration_rows(entries, WIN, CUTOFF, folds)
    shadow = mod.shadow_calibration_rows(entries, WIN, CUTOFF)
    assert (len(shadow), len(cal)) == (6, 25)
    kept = {r["base_at"]: r for r in cal}
    assert all(kept[r["base_at"]] is r for r in shadow)  # the shadow row, not the fold row
    assert [r["base_at"] for r in cal] == sorted(r["base_at"] for r in cal)
    cc = mod.calibration_counts(entries, WIN, CUTOFF, folds)
    assert cc == {"n_shadow": 6, "n_folds_added": 19, "n_total": 25}


def test_no_decision_or_outcome_from_after_the_cutoff_enters_the_calibration():
    entries = book(OFFSETS_20, n_cal=6)
    ok = make_folds([0, 1, 2], start=datetime(2026, 8, 1, tzinfo=UTC))
    late_decision = make_folds([0], start=datetime(2026, 10, 17, tzinfo=UTC))  # t after the cut-off
    at_cutoff = [{**ok[0], "base_at": "2026-08-20T06:30:00Z", "t": "2026-10-16T17:38:00Z"}]
    late_target = [  # decided before the cut-off, target published after it
        {**ok[0], "base_at": "2026-08-21T06:30:00Z", "target_at": "2026-10-17T06:30:00Z"}
    ]
    no_target_time = [{k: v for k, v in ok[0].items() if k != "target_at"}]
    no_target_time[0]["base_at"] = "2026-08-22T06:30:00Z"
    cal = mod.calibration_rows(
        entries, WIN, CUTOFF, ok + late_decision + at_cutoff + late_target + no_target_time
    )
    folds_in = [r for r in cal if r["base_at"] < "2026-09-01"]
    assert len(folds_in) == 3 and all(
        r["target_at"] < mod.ADR076_FIRST_ENTRY_AFTER for r in folds_in
    )


def test_a_scored_fix_is_never_calibration_even_if_a_fold_row_exists_for_it():
    entries = book(OFFSETS_20, n_cal=6)
    fwd = mod.forward_rows(entries, WIN, CUTOFF)
    twin = {**make_folds([0])[0], "base_at": fwd[0]["base_at"]}
    twin["t"] = "2026-10-01T08:00:00Z"
    twin["target_at"] = "2026-10-01T11:30:00Z"
    cal = mod.calibration_rows(entries, WIN, CUTOFF, [twin])
    assert fwd[0]["base_at"] not in {r["base_at"] for r in cal}


def test_condition_c_is_evaluable_at_20_fixes_with_folds_and_no_shadow_calibration():
    entries = book(OFFSETS_20, n_cal=0)
    folds = make_folds(list(range(40)))
    c = evaluate(entries, LOOK_MONDAY + timedelta(hours=9), folds=folds)["effect"][
        "c_range_coverage"
    ]
    assert "not evaluable" not in c["detail"]
    assert (c["n_calibration"], c["n_scored"]) == (40, 20)


def test_the_folds_are_not_scored_in_conditions_a_and_b():
    entries = book(OFFSETS_20, n_cal=6)
    r = evaluate(entries, LOOK_MONDAY + timedelta(hours=9), folds=make_folds(list(range(40))))
    assert r["effect"]["a_bootstrap_ci_below_0"]["n"] == 20


def test_few_calibration_fixes_without_folds_still_fail_closed():
    entries = book(OFFSETS_20, n_cal=3)
    c = evaluate(entries, LOOK_MONDAY + timedelta(hours=9), folds=make_folds([0]))["effect"][
        "c_range_coverage"
    ]
    assert c["pass"] is False and "not evaluable" in c["detail"]  # 3 shadow + 1 fold = 4 < 5


def test_counts_before_the_look_include_the_calibration_and_compute_no_effect(no_effect):
    entries = book(list(range(60)), n_cal=6)
    r = evaluate(entries, datetime(2026, 11, 9, 9, tzinfo=UTC), folds=make_folds(list(range(40))))
    assert r["look_day"] is None and "effect" not in r
    assert r["calibration_counts"]["n_total"] >= 40
    assert "calibration" in r["verdict"] and "ADR 066 folds" in r["verdict"]


def test_the_closed_look_stays_closed_with_a_large_fold_set(no_effect):
    folds = make_folds(list(range(300)), start=datetime(2026, 1, 1, tzinfo=UTC))
    for now in (datetime(2026, 10, 19, 9, tzinfo=UTC), datetime(2026, 11, 9, 9, tzinfo=UTC)):
        r = evaluate(book(OFFSETS_20), now, folds=folds)
        assert r["look_day"] is None and "effect" not in r


def test_unavailable_folds_on_the_look_day_take_no_look_and_spend_none(no_effect):
    r = evaluate(book(OFFSETS_20), LOOK_MONDAY + timedelta(hours=9), folds=None)
    assert r["look_day"] is None and r["look_due"] == "2026-11-16" and "effect" not in r
    assert "NOT taken" in r["verdict"] and "nothing computed" in r["verdict"]


def test_the_look_missed_for_missing_folds_is_the_same_monday_on_the_next_run():
    entries = book(OFFSETS_20)
    folds = make_folds(list(range(40)))
    later = datetime(2026, 11, 20, 9, tzinfo=UTC)
    first = evaluate(entries, LOOK_MONDAY + timedelta(hours=9), folds=folds)
    again = evaluate(entries, later, folds=folds)
    assert first["look_day"] == again["look_day"] == "2026-11-16"
    assert first["effect"] == again["effect"]


def test_load_folds_replays_decisions_and_scores_nothing(tmp_path, monkeypatch, no_effect):
    data = tmp_path
    (data / ni.INTRADAY_FILE).write_bytes(b"x")
    (data / mod.nextfix.IBJA_PATH.name).write_text("{}")
    sentinel = [{"window": WIN, "t": "2026-10-01T08:00:00Z", "target_at": "2026-10-01T11:30:00Z"}]
    monkeypatch.setattr(mod.nextfix, "load_ibja_full", lambda p: "ibja")
    monkeypatch.setattr(mod.nextfix, "fix_events", lambda x: ["events"])
    monkeypatch.setattr(mod.nextfix_intraday, "load_bars", lambda d: "bars")
    monkeypatch.setattr(mod, "backtest_rows", lambda events, g: sentinel)
    assert mod.load_folds(data) == sentinel  # no_effect: score/flat_record/DM would raise


def test_load_folds_is_none_when_the_bars_are_absent_or_unreadable(tmp_path, monkeypatch):
    assert mod.load_folds(tmp_path) is None
    (tmp_path / ni.INTRADAY_FILE).write_bytes(b"x")
    (tmp_path / mod.nextfix.IBJA_PATH.name).write_text("{}")

    def boom(*a, **k):
        raise ValueError("unreadable")

    monkeypatch.setattr(mod.nextfix, "load_ibja_full", boom)
    assert mod.load_folds(tmp_path) is None


def test_backtest_rows_carry_the_instant_their_target_fix_was_published():
    rng = np.random.default_rng(42)
    events = []
    for k in range(8):
        d = datetime(2026, 8, 3 + k, tzinfo=UTC)
        for kind, hh, mm in (("am", 6, 30), ("pm", 11, 30)):
            events.append(
                (d.replace(hour=hh, minute=mm), kind, pd.Timestamp(d.date()), 13000.0 + 10 * k)
            )
    idx = pd.date_range("2026-08-03 01:00", periods=8 * 24, freq="h", tz="UTC")
    g = pd.Series(400000 * np.exp(np.cumsum(rng.normal(0, 0.002, len(idx)))), index=idx)
    rows = mod.backtest_rows(events, g)
    assert rows
    assert all(_t(r["target_at"]) > _t(r["t"]) for r in rows)
    assert all(_t(r["target_at"]) > _t(r["base_at"]) for r in rows)


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def test_the_rule_is_final_and_the_adr_says_so():
    assert mod.ADR076_RULE["rule_version"] == 3 and mod.ADR076_RULE["final"]
    adr = (_ROOT / "docs" / "adr" / "076-hourly-model-forward-check-preregistration.md").read_text(
        encoding="utf-8"
    )
    assert "Amendment 2" in adr and "no further amendment" in adr.lower()
