"""ADR 076: the forward-only second look of the hourly model (synthetic data only).

The look must (1) use only entries logged strictly after the cut-off, (2) compute NO effect size, CI
or p-value before the look is open (a Monday on or after 2026-11-16) and the counts are met, and
(3) leave the ADR 066 check unchanged."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from ml import nextfix_intraday as ni

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_adr066_promotion.py"
_spec = importlib.util.spec_from_file_location("check_adr066_promotion_076", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules["check_adr066_promotion_076"] = mod
_spec.loader.exec_module(mod)

WIN = "after_morning_rate"
CUTOFF = datetime(2026, 10, 16, 17, 38, tzinfo=UTC)
FIRST_DAY = datetime(2026, 10, 17, tzinfo=UTC)
LOOK_MONDAY = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
OPTS = {
    "first_entry_after": mod.ADR076_FIRST_ENTRY_AFTER,
    "min_fixes": mod.ADR076_MIN_FIXES,
    "min_days": mod.ADR076_MIN_DAYS,
    "alpha": mod.ADR076_ALPHA,
    "beta": mod.ADR076_BETA,
    "look_not_before": mod.ADR076_LOOK_NOT_BEFORE,
}


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_entries(
    n: int,
    slope: float = 1.0,
    noise_sd: float = 0.002,
    seed: int = 42,
    start: datetime = FIRST_DAY,
    hour: int = 8,
    window: str = WIN,
) -> list[dict]:
    """One decision per day (AM fix 06:30Z, decision at ``hour``, target = same day's PM fix).
    target = base * exp(slope * x + noise): beta 1.0 is right when slope == 1."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        d = start + timedelta(days=i)
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


def evaluate(entries: list[dict], now: datetime, opts: dict | None = None, allowed: bool = True):
    return mod.evaluate_window_076(
        WIN, mod.clean_entries(entries), now, opts or OPTS, look_allowed=allowed
    )


@pytest.fixture
def no_effect(monkeypatch):
    """Any effect computation fails the test: used wherever the look must stay closed."""

    def boom(*a, **k):
        raise AssertionError("an effect was computed before the look")

    monkeypatch.setattr(mod, "adr076_effect", boom)
    monkeypatch.setattr(mod, "dm_p_one_sided", boom)
    monkeypatch.setattr(mod.nextfix_intraday, "_cluster_ci", boom)
    monkeypatch.setattr(mod.nextfix_intraday, "score", boom)
    monkeypatch.setattr(mod.nextfix, "diebold_mariano_p", boom)
    monkeypatch.setattr(mod.nextfix, "flat_record", boom)


# -- the look stays closed until it is due ------------------------------------------------------


def test_the_look_is_closed_before_its_date_and_computes_nothing(no_effect):
    entries = make_entries(60)
    for now in (datetime(2026, 11, 9, 9, tzinfo=UTC), datetime(2026, 10, 19, 9, tzinfo=UTC)):
        assert now.weekday() == 0
        res = mod.run_adr076(Path("."), now, OPTS)  # no shadow file in cwd -> zero entries
        assert res["look_open"] is False
        r = evaluate(entries, now, allowed=res["look_open"])
        assert r["verdict"].startswith("counts only")
        assert "effect" not in r


def test_the_look_is_closed_on_a_non_monday_even_after_the_date(tmp_path, no_effect):
    tue = datetime(2026, 11, 17, 9, tzinfo=UTC)
    assert tue.weekday() == 1
    (tmp_path / "nextfix_intraday_shadow.json").write_text(
        json.dumps({"entries": make_entries(60)})
    )
    res = mod.run_adr076(tmp_path, tue, OPTS)
    assert res["look_open"] is False
    w = res["windows"][WIN]
    assert w["counts_ready"] is True and "effect" not in w
    assert w["verdict"].startswith("counts only")


def test_a_monday_on_or_after_the_date_opens_the_look():
    assert LOOK_MONDAY.weekday() == 0
    res = mod.run_adr076(Path("."), LOOK_MONDAY, OPTS)
    assert res["look_open"] is True


def test_short_of_the_counts_on_a_look_day_computes_nothing(no_effect):
    r = evaluate(make_entries(15), LOOK_MONDAY)
    assert r["verdict"].startswith("short of the counts on a look day")
    assert r["counts"]["n_resolved_base_fixes"] == 15 and r["counts_ready"] is False
    assert "effect" not in r


def test_days_gate_alone_keeps_the_look_closed(no_effect):
    # 25 fixes but a 25-day span: below the 28 calendar days
    r = evaluate(make_entries(25), LOOK_MONDAY)
    assert r["counts"]["calendar_span_days"] == 25 and r["counts_ready"] is False
    assert "effect" not in r


# -- the data: forward-only, resolved ---------------------------------------------------------


def test_only_entries_strictly_after_the_cutoff_count():
    at_cutoff = make_entries(1, start=datetime(2026, 10, 16, tzinfo=UTC), hour=17)[0]
    at_cutoff["logged_at"] = at_cutoff["t"] = _iso(CUTOFF)
    before = make_entries(3, start=datetime(2026, 10, 10, tzinfo=UTC))
    after_by_a_second = make_entries(1, start=datetime(2026, 10, 17, tzinfo=UTC))
    c = mod.adr076_counts(mod.clean_entries([*before, at_cutoff, *after_by_a_second]), WIN, CUTOFF)
    assert c["n_entries"] == 1 and c["n_resolved_base_fixes"] == 1


def test_entries_logged_after_their_target_was_published_do_not_count():
    late = make_entries(30, hour=12)  # logged 12:00Z, after the 11:30Z PM publication
    c = mod.adr076_counts(mod.clean_entries(late), WIN, CUTOFF)
    assert c["n_entries"] == 30 and c["n_resolved_base_fixes"] == 0


def test_other_windows_are_not_mixed_in():
    other = make_entries(30, window="after_afternoon_rate")
    assert mod.adr076_counts(mod.clean_entries(other), WIN, CUTOFF)["n_entries"] == 0


def test_the_latest_decision_per_fix_counts_once():
    a = make_entries(1)[0]
    b = {**a, "logged_at": "2026-10-17T09:00:00Z", "t": "2026-10-17T09:00:00Z"}
    assert mod.adr076_counts(mod.clean_entries([a, b]), WIN, CUTOFF)["n_resolved_base_fixes"] == 1


# -- the look itself -----------------------------------------------------------------------------


def test_a_good_hourly_model_passes_all_three_conditions_on_enough_data():
    # coverage (c) needs MIN_CONFORMAL warm-up pairs plus MIN_CONFORMAL scored: 40+ fixes
    r = evaluate(make_entries(60), LOOK_MONDAY)
    eff = r["effect"]
    assert eff["a_bootstrap_ci_below_0"]["pass"] and eff["a_bootstrap_ci_below_0"]["ci95"][1] < 0
    assert eff["b_dm_one_sided"]["pass"] and eff["b_dm_one_sided"]["dm_p_one_sided"] < 0.025
    assert eff["c_range_coverage"]["pass"], eff["c_range_coverage"]
    assert eff["all_pass"] and r["verdict"].startswith("pass on beta 1.0")


def test_a_useless_hourly_model_fails_a_and_b():
    r = evaluate(make_entries(60, slope=0.0, noise_sd=0.003, seed=7), LOOK_MONDAY)
    eff = r["effect"]
    assert not eff["a_bootstrap_ci_below_0"]["pass"]
    assert not eff["b_dm_one_sided"]["pass"]
    assert not eff["all_pass"] and r["verdict"].startswith("fail:")


def test_a_harmful_hourly_model_gets_a_large_one_sided_p():
    r = evaluate(make_entries(60, slope=-1.0, noise_sd=0.001, seed=9), LOOK_MONDAY)
    assert r["effect"]["b_dm_one_sided"]["dm_p_one_sided"] > 0.5
    assert not r["effect"]["all_pass"]


def test_condition_c_is_not_evaluable_at_exactly_the_minimum_fixes():
    # ADR 076 limit, recorded in the ADR: the walk-forward conformal band needs 2 x MIN_CONFORMAL
    # pairs, so 20-39 fixes meet the gate but cannot satisfy condition c (fail closed).
    r = evaluate(make_entries(30), LOOK_MONDAY, {**OPTS, "min_days": 28})
    c = r["effect"]["c_range_coverage"]
    assert c["pass"] is False and "not evaluable" in c["detail"]
    assert r["effect"]["all_pass"] is False


def test_dm_one_sided_is_half_the_two_sided_p_when_better_and_mirrors_when_worse():
    rng = np.random.default_rng(1)
    hold = np.abs(rng.normal(0, 1.0, 40)) + 0.5
    better = hold * 0.7
    p2 = mod.nextfix.diebold_mariano_p(better, hold)
    assert mod.dm_p_one_sided(better, hold) == pytest.approx(p2 / 2)
    assert mod.dm_p_one_sided(hold, better) == pytest.approx(1 - p2 / 2)
    assert mod.dm_p_one_sided(better, hold) < 0.025 < mod.dm_p_one_sided(hold, better)


def test_the_bootstrap_is_pre_registered_and_deterministic():
    assert (mod.ADR076_BOOTSTRAP_B, mod.ADR076_SEED) == (10_000, 42)
    e = make_entries(60)
    a = evaluate(e, LOOK_MONDAY)["effect"]["a_bootstrap_ci_below_0"]["ci95"]
    b = evaluate(e, LOOK_MONDAY)["effect"]["a_bootstrap_ci_below_0"]["ci95"]
    assert a == b


def test_options_override_the_counts():
    r = evaluate(make_entries(8), LOOK_MONDAY, {**OPTS, "min_fixes": 5, "min_days": 5})
    assert r["counts_ready"] is True and "effect" in r


# -- the command line -----------------------------------------------------------------------------


def test_the_defaults_are_the_adr_values():
    assert mod.ADR076_FIRST_ENTRY_AFTER == "2026-10-16T17:38:00Z"
    assert (mod.ADR076_MIN_FIXES, mod.ADR076_MIN_DAYS) == (20, 28)
    assert (mod.ADR076_ALPHA, mod.ADR076_BETA) == (0.025, 1.0)
    assert mod.ADR076_LOOK_NOT_BEFORE == "2026-11-16"


def test_the_adr066_check_keeps_its_defaults_and_refuses_076_options():
    assert (mod.MIN_SHADOW_DAYS, mod.MIN_RESOLVED_FIXES, mod.DM_ALPHA) == (14, 8, 0.05)
    assert mod.CANDIDATE_BETAS == (0.5, 1.0)
    with pytest.raises(SystemExit) as ei:
        mod.main(["--min-fixes", "5"])
    assert ei.value.code == 2


def test_cli_076_before_the_look_writes_counts_only(tmp_path, capsys):
    data = tmp_path / "data"
    data.mkdir()
    (data / "nextfix_intraday_shadow.json").write_text(json.dumps({"entries": make_entries(60)}))
    out = tmp_path / "out"
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
    for w in rep["windows"].values():
        assert "effect" not in w
    text = capsys.readouterr().out
    assert "counts only" in text and "ci95" not in text and "dm_p" not in text


def test_cli_076_on_the_look_day_reports_the_effect(tmp_path, capsys):
    data = tmp_path / "data"
    data.mkdir()
    (data / "nextfix_intraday_shadow.json").write_text(json.dumps({"entries": make_entries(60)}))
    out = tmp_path / "out"
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
