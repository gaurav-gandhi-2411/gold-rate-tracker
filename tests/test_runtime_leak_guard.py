"""ADR 073: the leak guard runs INSIDE ml.nextfix at forecast time, not only in the offline audit.

Each test builds synthetic data where one input is known too late and shows the published forecast
falls back to the hold figure (reason ``leak_guard``, an ERROR log naming the input), then shows the
same data without the leak gives a normal forecast. Hermetic: every root is ``tmp_path``.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from ml import inference, known_at, nextfix
from ml import runtime_leak_guard as rlg


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(nextfix, "MLP_MODELS", 1)
    monkeypatch.setattr(nextfix, "MLP_EPOCHS", 10)
    monkeypatch.setattr(nextfix, "MIN_TRAIN", 40)


def _setup(tmp_path: Path) -> tuple[pd.DataFrame, datetime, pd.Timestamp]:
    """(macro, now, d0): weekday IBJA fixes that follow a random-walk world price; ``now`` is 23:00
    UTC on the last IBJA day, i.e. after the US close, so the model window is open."""
    rng = np.random.default_rng(3)
    n = 150
    days = pd.bdate_range("2026-01-05", periods=n)
    close = 125000 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    pm = np.empty(n)
    pm[0] = 13000.0
    pm[1:] = 13000 * np.exp(
        0.5 * np.log(close[:-1] / close[0]) + 0.5 * np.log(close[1:] / close[0])
    )
    am = pm * np.exp(np.random.default_rng(1).normal(0, 0.004, n))
    pd.DataFrame({"date": days, "pm_916": pm * 10.0, "am_916": am * 10.0}).to_parquet(
        tmp_path / "ibja_rates.parquet"
    )
    cal = pd.date_range(days.min(), days.max(), freq="D")
    glob = pd.Series(close, index=days).reindex(cal).ffill()
    macro = pd.DataFrame({"gold_usd": glob.to_numpy() / 100.0, "usd_inr": 100.0}, index=cal)
    d0 = days[-1]
    return macro, datetime(d0.year, d0.month, d0.day, 23, tzinfo=UTC), d0


def _guard_errors(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]


def test_clean_run_publishes_a_checked_guard_and_a_normal_forecast(tmp_path: Path):
    macro, now, _ = _setup(tmp_path)
    fc = nextfix.run(now=now, macro=macro, data_dir=tmp_path)["forecast"]
    assert fc["mode"] == "after_us_close" and fc["model_version"] == nextfix.MODEL_VERSION
    lg = fc["leak_guard"]
    assert lg["checked"] is True and lg["violations"] == [] and lg["n_inputs"] > 100
    json.dumps(lg)  # JSON-safe


def test_a_macro_row_dated_d_plus_1_claimed_available_at_now_holds(
    tmp_path: Path, monkeypatch, caplog
):
    # a loader bug that picks the LATEST macro row (dated D+1) for the decision-day close
    macro, now, d0 = _setup(tmp_path)
    orig = nextfix.global_series

    def picks_latest(m=None, label_path=nextfix.LABEL_PATH):
        g = orig(m, label_path)
        src = g.attrs["source_date"].copy()
        src.loc[d0] = d0 + pd.Timedelta(days=1)
        g.attrs["source_date"] = src
        return g

    monkeypatch.setattr(nextfix, "global_series", picks_latest)
    with caplog.at_level(logging.ERROR):
        fc = nextfix.run(now=now, macro=macro, data_dir=tmp_path)["forecast"]
    assert fc["mode"] == "after_afternoon_rate" and fc["reason"] == "leak_guard"
    assert fc["model_version"].startswith("hold_latest_fix") and fc["p_up"] is None
    names = {v["input"] for v in fc["leak_guard"]["violations"]}
    assert "gold_usd(D)" in names and fc["leak_guard"]["checked"] is True
    assert any("gold_usd(D)" in m and "known_at" in m for m in _guard_errors(caplog))

    monkeypatch.setattr(nextfix, "global_series", orig)  # leak removed: same data, normal forecast
    ok = nextfix.run(now=now, macro=macro, data_dir=tmp_path)["forecast"]
    assert ok["mode"] == "after_us_close" and ok["leak_guard"]["violations"] == []


def test_usdinr_known_after_now_holds_and_the_clock_is_really_evaluated(
    tmp_path: Path, monkeypatch, caplog
):
    # negative control: a LEGITIMATE input is made late by moving the measured clock past `now`
    macro, now, _ = _setup(tmp_path)
    late = known_at.Clock("late", "UTC", time(23, 30))
    with monkeypatch.context() as m, caplog.at_level(logging.ERROR):
        m.setitem(known_at.MACRO_DAILY_CLOCKS, "usd_inr", late)
        fc = nextfix.run(now=now, macro=macro, data_dir=tmp_path)["forecast"]
    assert fc["reason"] == "leak_guard" and fc["mode"] == "after_afternoon_rate"
    assert "usd_inr(D)" in {v["input"] for v in fc["leak_guard"]["violations"]}
    assert any("usd_inr(D)" in m for m in _guard_errors(caplog))
    # nothing wrong was persisted: no out-of-sample fold built from late inputs
    assert nextfix.load_oos(tmp_path / "nextfix_p3_oos.json") == []
    assert nextfix.load_oos(tmp_path / "nextfix_oos.json") == []


def test_a_training_pair_whose_target_fix_is_after_now_holds(tmp_path: Path, caplog):
    macro, _, d0 = _setup(tmp_path)
    full = nextfix.load_ibja_full(tmp_path / "ibja_rates.parquet")
    glob = nextfix.global_series(macro, tmp_path / "none.parquet")
    pairs = nextfix.build_pairs(full.dropna(subset=["pm"]).reset_index(drop=True), glob)
    folds = [{"y": 0.0, "ret": 0.001, "pm0": 1.0, "pm1": 1.0, "vol": 0.01}] * 30
    # `now` is 10:00 UTC on D, before D's own PM fix (11:30 UTC): the pair (D-1 -> D) has a target
    # fix that is not yet known, and so does the base fix
    early = datetime(d0.year, d0.month, d0.day, 10, tzinfo=UTC)
    lg = rlg.LeakCheck()
    with caplog.at_level(logging.ERROR):
        assert nextfix._model_forecast(full, glob, folds, d0, now=early, lg=lg) is None
    names = {v["input"] for v in lg.info()["violations"]}
    assert "label ibja_pm(D1)" in names and "ibja_pm(D)" in names
    assert _guard_errors(caplog)
    # the same call at the real issue time is clean and gives a forecast
    lg2 = rlg.LeakCheck()
    now = datetime(d0.year, d0.month, d0.day, 23, tzinfo=UTC)
    assert nextfix._model_forecast(full, glob, folds, d0, now=now, lg=lg2) is not None
    assert lg2.info()["violations"] == [] and lg2.info()["n_inputs"] > 0
    assert len(pairs) > 0


def test_a_tampered_training_set_is_caught_at_the_decision_moment():
    # a pair whose target fix lies AFTER the decision day (the missing-embargo leak, ADR 038 A1)
    d0 = pd.Timestamp("2026-03-10")
    pair = {"d0": d0 - pd.Timedelta(days=1), "d1": d0 + pd.Timedelta(days=1)}
    train = pd.DataFrame([pair])
    row = pd.Series({"d0": d0})
    lg = rlg.LeakCheck()
    ok = lg.check_forecast(datetime(2026, 3, 10, 23, tzinfo=UTC), train, row, d0)
    assert ok is False
    assert "label ibja_pm(D1)" in {v["input"] for v in lg.info()["violations"]}


def test_a_failure_inside_the_guard_fails_closed_never_raises(tmp_path: Path, monkeypatch):
    macro, now, _ = _setup(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("guard bug")

    monkeypatch.setattr(rlg.LeakCheck, "check_pairs", boom)
    fc = nextfix.run(now=now, macro=macro, data_dir=tmp_path)["forecast"]
    assert fc["reason"] == "leak_guard" and fc["mode"] == "after_afternoon_rate"
    assert fc["leak_guard"]["violations"][0]["input"] == "guard_error"


def test_the_published_block_carries_json_safe_guard_info_and_no_raw_rates(
    tmp_path: Path, monkeypatch
):
    macro, now, _ = _setup(tmp_path)
    monkeypatch.setattr(inference, "DATA_DIR", tmp_path)
    import ml.macro as macro_mod

    monkeypatch.setattr(macro_mod, "load_macro_features", lambda *a, **k: macro)
    cal = {"slope": 1.0, "valid": True}
    block = inference._next_fix_block(now, 12000, cal, None)
    assert block["leak_guard"] == {
        "checked": True,
        "n_inputs": block["leak_guard"]["n_inputs"],
        "violations": [],
    }
    assert block["leak_guard"]["n_inputs"] > 100
    json.dumps(block)
    late = known_at.Clock("late", "UTC", time(23, 30))
    monkeypatch.setitem(known_at.MACRO_DAILY_CLOCKS, "usd_inr", late)
    held = inference._next_fix_block(now, 12000, cal, None)
    assert held["reason"] == "leak_guard" and held["leak_guard"]["violations"]
    v = held["leak_guard"]["violations"][0]
    assert set(v) <= {"input", "source", "known_at", "issue_time", "late_by_s", "context", "count"}
    json.dumps(held)


def test_update_oos_skips_a_fold_whose_training_set_leaks_and_logs(monkeypatch, caplog):
    rng = np.random.default_rng(0)
    days = pd.bdate_range("2026-01-05", periods=80)
    close = (
        pd.Series(125000 * np.exp(np.cumsum(rng.normal(0, 0.01, 80))), index=days)
        .reindex(pd.date_range(days.min(), days.max(), freq="D"))
        .ffill()
    )
    ibja = pd.DataFrame(
        {"date": days, "pm": 13000 * np.exp(rng.normal(0, 0.005, 80)), "am": np.nan}
    )
    pairs = nextfix.build_pairs(ibja, close)
    late = known_at.Clock("late", "UTC", time(23, 30))
    with monkeypatch.context() as m, caplog.at_level(logging.ERROR):
        m.setitem(known_at.MACRO_DAILY_CLOCKS, "usd_inr", late)
        out = nextfix.update_oos(pairs, [], nextfix.predict_p3)
    assert out == [] and _guard_errors(caplog)
    assert len(nextfix.update_oos(pairs, [], nextfix.predict_p3)) > 0
