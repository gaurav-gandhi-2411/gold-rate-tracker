"""ADR 076 (rule v2, amendment 2026-10-17): the forward-only second look of the hourly model.

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


def evaluate(entries: list[dict], now: datetime, opts: dict | None = None) -> dict:
    return mod.evaluate_window_076(WIN, entries, now, opts or OPTS)


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


def test_cli_076_on_the_look_day_reports_the_effect(tmp_path, capsys):
    raw = make_entries(list(range(12)), start=PRE_START, seed=3) + make_entries(OFFSETS_20, seed=4)
    data, out = _shadow(tmp_path, raw)
    mod.main(
        [
            "--adr",
            "076",
            "--now",
            "2026-11-16T09:00:00Z",
            "--data-dir",
            str(data),
            "--out-dir",
            str(out),
        ]
    )
    rep = json.loads((out / "adr076_look_2026-11-16.json").read_text())
    assert rep["look_open"] is True and "effect" in rep["windows"][WIN]
    assert "a_bootstrap_ci_below_0" in capsys.readouterr().out
