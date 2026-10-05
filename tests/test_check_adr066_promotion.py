"""Tests for scripts/check_adr066_promotion.py: the ADR 066 promotion check (synthetic data only)."""

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

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_adr066_promotion.py"
_spec = importlib.util.spec_from_file_location("check_adr066_promotion", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules["check_adr066_promotion"] = mod
_spec.loader.exec_module(mod)

WIN = "after_morning_rate"
DAY0 = datetime(2026, 8, 1, tzinfo=UTC)


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_rows(
    n: int,
    slope: float = 0.6,
    noise=lambda i, rng: rng.normal(0, 0.0005),
    seed: int = 42,
    late: bool = False,
    x_sd: float = 0.01,
    window: str = WIN,
    start: datetime = DAY0,
) -> list[dict]:
    """One decision per day: AM fix at 06:30Z, decision 08:00Z, target = same day's PM fix.

    target = base * exp(slope * x + noise), so the pass-through with beta ~ slope is the right
    forecast; ``late=True`` logs the decision after the PM fix (11:30Z) was published."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        d = start + timedelta(days=i)
        x = float(rng.normal(0, x_sd))
        base = 13000.0
        t = d.replace(hour=12 if late else 8)
        rows.append(
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
                "target": round(base * float(np.exp(slope * x + noise(i, rng))), 2),
                "target_date": d.strftime("%Y-%m-%d"),
            }
        )
    return rows


def evaluate(shadow_rows: list[dict], bt_rows: list[dict] | None, window: str = WIN) -> dict:
    entries = mod.clean_entries(shadow_rows)
    bt = {
        "source": "test",
        "scores": ni.score(bt_rows) if bt_rows else {},
        "rows": bt_rows,
    }
    return mod.evaluate_window(window, entries, bt)


@pytest.fixture(scope="module")
def good_bt() -> list[dict]:
    return make_rows(46, seed=1)


def test_all_conditions_pass(good_bt):
    res = evaluate(make_rows(20, seed=2), good_bt)
    conds = res["conditions"]
    assert {k: v["pass"] for k, v in conds.items()} == {
        "1_shadow_length": True,
        "2_backtest_ci_dm": True,
        "3_shadow_agrees": True,
        "4_range_coverage": True,
    }, conds
    assert res["verdict"].startswith("promotable")
    assert res["backtest"]["chosen_beta"] == 0.5  # lower MAE than beta 1 when slope is 0.6
    assert res["backtest"]["other_beta_reported_only"]["beta"] == 1.0
    assert "direction_line" in res and res["direction_line"]["required_for_any_direction_line"]


def test_too_early_reports_n(good_bt):
    res = evaluate(make_rows(6, seed=3), good_bt)
    assert res["verdict"].startswith("too early (n=6")
    assert res["conditions"]["1_shadow_length"]["pass"] is False


def test_fails_on_days_alone(good_bt):
    # 8 resolved fixes but all on 8 consecutive days: span < 14 -> too early, not promotable
    res = evaluate(make_rows(8, seed=3), good_bt)
    assert res["conditions"]["1_shadow_length"]["calendar_span_days"] == 8
    assert res["verdict"].startswith("too early")


def test_fails_on_fix_count_alone(good_bt):
    # 14+ calendar days but only 5 resolved fixes (one every 3 days)
    rows = [r for i, r in enumerate(make_rows(16, seed=3)) if i % 3 == 0]
    res = evaluate(rows, good_bt)
    c1 = res["conditions"]["1_shadow_length"]
    assert c1["calendar_span_days"] >= 14 and c1["n_resolved_base_fixes"] < 8
    assert res["verdict"].startswith("too early")


def test_fails_condition_2_only(good_bt):
    useless = make_rows(46, slope=0.0, noise=lambda i, r: r.normal(0, 0.003), seed=4)
    res = evaluate(make_rows(20, slope=0.0, noise=lambda i, r: r.normal(0, 0.003), seed=5), useless)
    c = res["conditions"]
    assert c["2_backtest_ci_dm"]["pass"] is False
    assert c["1_shadow_length"]["pass"] is True
    assert res["verdict"].startswith("not yet")
    assert "2_backtest_ci_dm" in res["verdict"]


def test_fails_condition_3_only(good_bt):
    wrong_way = make_rows(20, slope=-0.6, seed=6)  # shadow says the pass-through hurts
    res = evaluate(wrong_way, good_bt)
    c = res["conditions"]
    assert c["3_shadow_agrees"]["pass"] is False
    assert c["3_shadow_agrees"]["same_sign"] is False
    assert [k for k, v in c.items() if not v["pass"]] == ["3_shadow_agrees"]
    assert res["verdict"].startswith("not yet")


def test_fails_condition_4_only():
    # errors whose scale keeps accelerating faster than the EWMA volatility can follow: the
    # walk-forward 80% band under-covers, so the Wilson 95% interval excludes 0.80 although the
    # pass-through still clearly beats holding.
    def accel(i, rng):
        return 1e-4 * float(np.exp(0.0012 * i * i)) * rng.choice([-1.0, 1.0])

    bt = make_rows(60, slope=0.5, noise=accel, x_sd=0.05, seed=7)
    res = evaluate(bt, bt)  # a shadow that agrees exactly with the backtest
    c = res["conditions"]
    assert c["4_range_coverage"]["pass"] is False
    assert c["4_range_coverage"]["wilson95"][1] < 0.8
    assert [k for k, v in c.items() if not v["pass"]] == ["4_range_coverage"], c
    assert res["verdict"].startswith("not yet")


def test_condition_4_fails_closed_without_rows(good_bt):
    res = evaluate(make_rows(20, seed=2), None)  # no bt at all -> no data
    assert res["verdict"].startswith("no data")
    bt = {"source": "json", "scores": ni.score(good_bt), "rows": None}
    res2 = mod.evaluate_window(WIN, mod.clean_entries(make_rows(20, seed=2)), bt)
    assert res2["conditions"]["4_range_coverage"]["pass"] is False
    assert "not evaluable" in res2["conditions"]["4_range_coverage"]["detail"]
    assert res2["verdict"].startswith("not yet")
    assert res2["direction_line"]["ship"] is False


def test_entries_logged_after_target_are_excluded(good_bt):
    ok = make_rows(10, seed=2)
    late = make_rows(10, seed=9, late=True, start=DAY0 + timedelta(days=30))
    res = evaluate(ok + late, good_bt)
    c1 = res["conditions"]["1_shadow_length"]
    assert c1["n_excluded_logged_after_target"] == 10
    assert c1["n_resolved_base_fixes"] == 10
    only_late = evaluate(make_rows(20, seed=9, late=True), good_bt)
    assert only_late["conditions"]["1_shadow_length"]["n_resolved_base_fixes"] == 0
    assert only_late["verdict"].startswith("too early (n=0")


def test_chosen_beta_is_lower_mae_and_other_is_reported_not_substituted():
    # slope 1.0: beta 1 is the right forecast, so it has the lower MAE and is chosen
    bt = make_rows(46, slope=1.0, seed=10)
    res = evaluate(make_rows(20, slope=1.0, seed=11), bt)
    assert res["backtest"]["chosen_beta"] == 1.0
    assert res["backtest"]["other_beta_reported_only"]["beta"] == 0.5
    assert "other_beta_cond2" in res["backtest"]


def test_choose_beta_tie_goes_to_half():
    assert mod.choose_beta({"mae_beta_0.5": 10.0, "mae_beta_1.0": 10.0}) == 0.5
    assert mod.choose_beta({"mae_beta_0.5": 11.0, "mae_beta_1.0": 10.0}) == 1.0
    assert mod.choose_beta({"mae_beta_0.5": 11.0}) is None


# -- hostile input -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    ["", "{bad json", "[]", "null", '{"entries": "nope"}', '{"entries": [1, null, {}]}', "NaN"],
)
def test_malformed_shadow_is_no_data_never_a_pass(tmp_path, content):
    (tmp_path / "nextfix_intraday_shadow.json").write_text(content)
    (tmp_path / "nextfix_intraday_backtest.json").write_text(content)
    res = mod.run_check(tmp_path)
    for w in mod.WINDOWS:
        assert res["windows"][w]["verdict"].startswith("no data")
        assert "promotable" not in res["windows"][w]["verdict"]


def test_missing_data_dir_is_no_data(tmp_path):
    res = mod.run_check(tmp_path / "does_not_exist")
    assert all(r["verdict"].startswith("no data") for r in res["windows"].values())


def test_nan_and_garbage_values_do_not_pass(good_bt):
    rows = make_rows(20, seed=2)
    rows[0]["pred"] = {"beta_0.0": "x"}  # incomplete pred -> dropped by clean_entries
    assert len(mod.clean_entries(rows)) == 19
    assert mod._num(float("nan")) is None and mod._num("1") is None and mod._num(True) == 1.0


# -- end to end via the JSON fallback, and the report file --------------------------------------


def test_main_writes_report_with_provenance(tmp_path, good_bt):
    data = tmp_path / "data"
    data.mkdir()
    shadow = make_rows(20, seed=2)
    (data / "nextfix_intraday_shadow.json").write_text(json.dumps({"entries": shadow}))
    bt = {"scored_on_last_decision_per_fix": ni.score(good_bt), "bars_from": "a", "bars_to": "b"}
    (data / "nextfix_intraday_backtest.json").write_text(json.dumps(bt))
    out = tmp_path / "reports"
    assert (
        mod.main(["--data-dir", str(data), "--out-dir", str(out), "--now", "2026-10-16T12:00:00Z"])
        == 0
    )
    rep = json.loads((out / "adr066_check_2026-10-16.json").read_text())
    assert rep["backtest_source"] == "data/nextfix_intraday_backtest.json"
    assert "macro_intraday.parquet absent" in rep["backtest_fallback_reason"]
    assert rep["data_file_sha256"]["nextfix_intraday_shadow.json"]
    assert rep["data_file_sha256"]["macro_intraday.parquet"] is None
    assert rep["repo_sha"] and rep["run_at"] == "2026-10-16T12:00:00Z"
    assert rep["out_of_scope"]["windows"] == ["after_us_close"]
    assert "after_us_close" not in rep["windows"]
    # conditions 1-3 pass from the JSON but 4 cannot be evaluated -> not promotable
    v = rep["windows"][WIN]["verdict"]
    assert v.startswith("not yet") and "4_range_coverage" in v
    assert not (data.parent / "ml").exists()


# -- the recomputed backtest must equal ml.nextfix_intraday.backtest ------------------------------


def test_backtest_rows_match_published_backtest_scores():
    rng = np.random.default_rng(42)
    days = [pd.Timestamp("2026-08-03") + pd.Timedelta(days=i) for i in range(40)]
    events = []
    for k, d in enumerate(days):
        events.append(
            (
                d.to_pydatetime().replace(hour=6, minute=30, tzinfo=UTC),
                "am",
                d,
                13000.0 + 20 * k + float(rng.normal(0, 30)),
            )
        )
        events.append(
            (
                d.to_pydatetime().replace(hour=11, minute=30, tzinfo=UTC),
                "pm",
                d,
                13010.0 + 20 * k + float(rng.normal(0, 30)),
            )
        )
    idx = pd.date_range("2026-08-03 01:00", periods=40 * 24, freq="h", tz="UTC")
    g = pd.Series(400000 * np.exp(np.cumsum(rng.normal(0, 0.002, len(idx)))), index=idx)
    rows = mod.backtest_rows(events, g)
    assert rows
    assert ni.score(rows) == ni.backtest(events, g)["scored_on_last_decision_per_fix"]
