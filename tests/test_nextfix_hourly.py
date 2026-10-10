"""The hourly world-price model as a live-capable SHADOW (ADR 066 predictor, built 2026-10-10).

Synthetic data only. What must hold: the record is built from hourly bars with a runtime leak
guard; the live path is bit-for-bit unchanged; the model is NOT in the challenger pool or the
frozen rule; and, if the pool is later opened, it runs live under the same demotion protections as
P3 (its own sticky state file).
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from ml import nextfix, promotion
from ml import nextfix_intraday as nfi
from ml import runtime_leak_guard as rlg

from tests.test_demotion_wired import _setup


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(nextfix, "MLP_MODELS", 1)
    monkeypatch.setattr(nextfix, "MLP_EPOCHS", 10)
    monkeypatch.setattr(nextfix, "MIN_TRAIN", 40)
    monkeypatch.setattr(nextfix, "HOURLY_FORWARD_FROM", "2026-06-01")


def _write_bars(tmp_path: Path, start: str = "2025-12-29", hours: int = 24 * 215) -> pd.Series:
    rng = np.random.default_rng(5)
    idx = pd.date_range(start, periods=hours, freq="h", tz=UTC)  # bar START times
    gold = 4200 * np.exp(np.cumsum(rng.normal(0, 0.002, hours)))
    pd.DataFrame({"gold_usd": gold, "usd_inr": 96.0}, index=idx).to_parquet(
        tmp_path / "macro_intraday.parquet"
    )
    return nfi.load_bars(tmp_path)


def _pairs(tmp_path: Path, bars: pd.Series | None):
    _ibja, macro, _now = _setup(tmp_path)
    full = nextfix.load_ibja_full(tmp_path / "ibja_rates.parquet")
    glob = nextfix.global_series(macro, tmp_path / "label.parquet")
    return nextfix.build_pairs(full.dropna(subset=["pm"]).reset_index(drop=True), glob, bars)


def test_x_hourly_is_the_world_move_from_the_pm_fix_to_the_us_close(tmp_path: Path) -> None:
    g = _write_bars(tmp_path)
    pairs = _pairs(tmp_path, g)
    row = pairs.iloc[60]
    a = nfi.value_at(g, nextfix._at(row["d0"], nextfix.IBJA_PM_PUBLISH_UTC))
    b = nfi.value_at(g, nextfix._at(row["d0"], nextfix.US_CLOSE_UTC))
    assert row["x_hourly"] == pytest.approx(math.log(b[0] / a[0]), rel=1e-12)
    assert row["h_fix_end"].to_pydatetime() == a[1] and row["h_now_end"].to_pydatetime() == b[1]
    assert row["h_fix_end"] <= pd.Timestamp(nextfix._at(row["d0"], nextfix.IBJA_PM_PUBLISH_UTC))
    assert row["h_now_end"] <= pd.Timestamp(nextfix._at(row["d0"], nextfix.US_CLOSE_UTC))


def test_days_the_bars_do_not_cover_have_no_hourly_move(tmp_path: Path) -> None:
    pairs = _pairs(tmp_path, _write_bars(tmp_path, start="2026-04-01", hours=24 * 60))
    assert pairs["x_hourly"].isna().sum() > 0 and pairs["x_hourly"].notna().sum() > 0
    assert _pairs(tmp_path, None)["x_hourly"].isna().all()
    assert _pairs(tmp_path, pd.Series(dtype=float))["x_hourly"].isna().all()


def test_predict_hourly_passes_the_move_through_and_refuses_a_missing_one(tmp_path: Path) -> None:
    pairs = _pairs(tmp_path, _write_bars(tmp_path))
    resolved = pairs[pairs["d1"].notna()]
    row = resolved.iloc[100]
    train = resolved[resolved["d1"] <= row["d0"]]
    p = nextfix.predict_hourly(train, row)
    assert p.ret == row["x_hourly"] and p.vol > 0 and 0.0 < p.p_up < 1.0
    assert (p.p_up > 0.5) == (row["x_hourly"] > 0)
    with pytest.raises(ValueError, match="no hourly world move"):
        nextfix.predict_hourly(train, row.copy().set_axis(row.index).where(row.index != "x_hourly"))


def test_record_has_folds_only_where_bars_exist_flags_retro_and_never_sees_its_target(
    tmp_path: Path,
) -> None:
    pairs = _pairs(tmp_path, _write_bars(tmp_path, start="2026-03-01", hours=24 * 120))
    seen = []

    def spy(train, row, resid_sd=None):
        seen.append((train["d1"].max(), row["d0"]))
        return nextfix.predict_hourly(train, row, resid_sd)

    folds = nextfix.update_oos(pairs, [], spy, forward_from="2026-06-01", hourly=True)
    assert folds and all(f["d0"] >= "2026-03-01" for f in folds)
    assert all(f["retro"] == (f["d0"] < "2026-06-01") for f in folds)
    assert any(f["retro"] for f in folds) and any(not f["retro"] for f in folds)
    assert seen and all(d1 <= d0 for d1, d0 in seen)
    assert all(math.isfinite(f["ret"]) and f["vol"] > 0 for f in folds)
    # nothing is appended twice
    assert nextfix.update_oos(pairs, folds, spy, forward_from="2026-06-01", hourly=True) == folds


def test_a_bar_that_ended_after_the_decision_moment_is_a_violation_and_no_fold_is_kept(
    tmp_path: Path,
) -> None:
    """Negative control for the runtime leak guard on the hourly inputs."""
    pairs = _pairs(tmp_path, _write_bars(tmp_path, start="2026-03-01", hours=24 * 120))
    bad = pairs.index[pairs["x_hourly"].notna()][10]
    leak_day = pairs.loc[bad, "d0"]
    pairs.loc[bad, "h_now_end"] = pd.Timestamp(nextfix._at(leak_day, nextfix.US_CLOSE_UTC)) + (
        pd.Timedelta(hours=2)
    )
    guard = rlg.LeakCheck()
    folds = nextfix.update_oos(
        pairs, [], nextfix.predict_hourly, forward_from="2026-06-01", guard=guard, hourly=True
    )
    assert guard.violations and guard.violations[0]["input"] == "hourly_bar_1"
    assert nextfix._day(leak_day) not in {f["d0"] for f in folds}
    assert all(f["d0"] < nextfix._day(leak_day) for f in folds)  # the walk stops at the leak


def test_check_bar_ends_denies_a_missing_or_late_bar_and_passes_a_known_one() -> None:
    moment = datetime(2026, 7, 1, 22, 15, tzinfo=UTC)
    ok = rlg.LeakCheck()
    assert ok.check_bar_ends(moment, [pd.Timestamp("2026-07-01 11:30", tz=UTC)] * 2, "t")
    assert ok.n_inputs == 2 and not ok.violations
    late = rlg.LeakCheck()
    assert not late.check_bar_ends(moment, [pd.Timestamp("2026-07-01 22:15", tz=UTC)], "t")
    assert late.violations[0]["source"] == "hourly_bar"
    nat = rlg.LeakCheck()
    assert not nat.check_bar_ends(moment, [pd.NaT], "t")  # unverifiable is a deny
    assert nat.violations and nat.violations[0]["input"] == "guard_error"


def _run(tmp_path: Path, with_bars: bool):
    _, macro, now = _setup(tmp_path)
    if with_bars:
        _write_bars(tmp_path)
    return nextfix.run(now=now, macro=macro, data_dir=tmp_path)


def test_the_live_path_is_identical_with_and_without_the_hourly_bars(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    out_a, out_b = _run(a, with_bars=False), _run(b, with_bars=True)
    assert out_a["forecast"] == out_b["forecast"]
    for name in ("nextfix_p3_oos.json", "nextfix_oos.json"):
        assert (a / name).read_text() == (b / name).read_text()
    va, vb = (json.loads((d / "nextfix_p3_variants_oos.json").read_text()) for d in (a, b))
    assert va["variants"] == vb["variants"]


def test_the_hourly_record_lives_apart_from_the_registered_variants(tmp_path: Path) -> None:
    _run(tmp_path, with_bars=True)
    doc = json.loads((tmp_path / "nextfix_p3_variants_oos.json").read_text())
    assert set(doc["variants"]) == {"p3_roll60", "p3_monday"}  # unchanged set
    h = doc[nextfix.HOURLY_KEY]
    assert h["model_version"] == nextfix.HOURLY_VERSION and h["folds"]
    assert set(nextfix.load_variant_folds(tmp_path)) == {"p3_roll60", "p3_monday"}
    assert nextfix.load_hourly_folds(tmp_path) == h["folds"]
    assert nextfix.load_hourly_folds(tmp_path / "missing") == []


def test_no_bars_means_an_empty_hourly_record_and_an_untouched_live_forecast(
    tmp_path: Path,
) -> None:
    out = _run(tmp_path, with_bars=False)
    assert out["forecast"]["active"] is True
    assert nextfix.load_hourly_folds(tmp_path) == []


def test_the_pool_and_the_frozen_rule_are_untouched(tmp_path: Path) -> None:
    assert "hourly" not in promotion.LIVE_CAPABLE
    assert (
        promotion.RULE_SHA256 == "e4efabec2193950ee4bf0193b00b3f17eed8c0cf5f81da4394d13acb51fdbdfa"
    )
    assert promotion.rule_sha256() == promotion.RULE_SHA256
    assert "hourly" not in promotion.RULE["registered"]
    assert promotion.RULE["registered_on_first_record"] == ["hourly", "p3_hourly"]
    # a champion file naming it still fails closed to P3
    path = tmp_path / promotion.CHAMPION_FILE
    path.write_text(json.dumps({**promotion.empty_champion(), "champion": "hourly"}))
    assert promotion.load_champion(path)["champion"] == "p3"
    assert promotion.load_champion(path).get("unreadable") is True


def test_if_the_pool_were_opened_it_runs_live_with_its_own_demotion_state(
    tmp_path: Path, monkeypatch
) -> None:
    """Readiness, not enablement: widening LIVE_CAPABLE (a frozen-rule amendment) is all that is
    missing. The hourly champion issues the forecast, is labelled with its own version, and is
    demoted/monitored under a state file of its own, like any other non-P3 champion."""
    monkeypatch.setattr(promotion, "LIVE_CAPABLE", (*promotion.LIVE_CAPABLE, "hourly"))
    monkeypatch.setattr(nextfix, "HOURLY_FORWARD_FROM", "2026-01-01")
    monkeypatch.setattr(nextfix, "P3_FORWARD_FROM", "2026-01-01")
    monkeypatch.setitem(promotion.RULE, "common_start", "2026-01-01")
    monkeypatch.setattr(promotion, "decide", lambda *a, **k: {"promote": None, "challengers": {}})
    _, macro, now = _setup(tmp_path)
    _write_bars(tmp_path)
    nextfix.run(now=now, macro=macro, data_dir=tmp_path)  # builds every record
    state = {
        **promotion.empty_champion(),
        "champion": "hourly",
        "since": "2026-07-01T00:00:00+00:00",
    }
    (tmp_path / promotion.CHAMPION_FILE).write_text(json.dumps(state))
    out = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    assert out["forecast"]["active"] and out["forecast"]["mode"] == "after_us_close"
    assert out["forecast"]["model_version"] == nextfix.HOURLY_VERSION
    assert (tmp_path / nextfix.demotion_state_file(nextfix.HOURLY_VERSION)).exists()
    assert out["demotion"]["demoted"] is False
    # and its forecast is the hourly pass-through of the last decision day
    last = _pairs(tmp_path, nfi.load_bars(tmp_path)).iloc[-1]
    assert out["forecast"]["pred"] == pytest.approx(
        round(float(last["pm0"]) * math.exp(float(last["x_hourly"])), 2)
    )


def test_a_live_hourly_forecast_without_bars_falls_back_to_the_hold_figure(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(promotion, "LIVE_CAPABLE", (*promotion.LIVE_CAPABLE, "hourly"))
    monkeypatch.setattr(nextfix, "HOURLY_FORWARD_FROM", "2026-01-01")
    monkeypatch.setitem(promotion.RULE, "common_start", "2026-01-01")
    monkeypatch.setattr(promotion, "decide", lambda *a, **k: {"promote": None, "challengers": {}})
    _, macro, now = _setup(tmp_path)
    bars_dir = tmp_path
    _write_bars(bars_dir)
    nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    (tmp_path / "macro_intraday.parquet").unlink()  # the bars vanish before the live forecast
    state = {
        **promotion.empty_champion(),
        "champion": "hourly",
        "since": "2026-07-01T00:00:00+00:00",
    }
    (tmp_path / promotion.CHAMPION_FILE).write_text(json.dumps(state))
    out = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    assert out["forecast"].get("model_version") != nextfix.HOURLY_VERSION  # never a guess


def test_a_bar_ending_exactly_at_the_decision_moment_skips_the_day_not_the_record(
    tmp_path: Path,
) -> None:
    """Verifier defect 1: value_at accepts end == t while the guard denies end >= moment. Off-grid
    bars (ending 22:15:00) must leave those days without an hourly move, not stop the walk."""
    rng = np.random.default_rng(9)
    idx = pd.date_range("2026-03-01 00:15", periods=24 * 120, freq="h", tz=UTC)  # ends at :15 + 1h
    gold = 4200 * np.exp(np.cumsum(rng.normal(0, 0.002, len(idx))))
    pd.DataFrame({"gold_usd": gold, "usd_inr": 96.0}, index=idx - pd.Timedelta(hours=1)).to_parquet(
        tmp_path / "macro_intraday.parquet"
    )
    g = nfi.load_bars(tmp_path)
    assert (g.index.minute == 15).all()  # every bar ends on :15, including 22:15
    pairs = _pairs(tmp_path, g)
    sample = pairs[pairs["d0"] >= "2026-03-10"].iloc[3]
    close = pd.Timestamp(nextfix._at(sample["d0"], nextfix.US_CLOSE_UTC))
    assert close in g.index  # a bar ends exactly at the decision moment
    assert sample["h_now_end"] < close  # ...and is not the one used
    guard = rlg.LeakCheck()
    folds = nextfix.update_oos(
        pairs, [], nextfix.predict_hourly, forward_from="2026-06-01", guard=guard, hourly=True
    )
    assert folds and not guard.violations


def test_hourly_columns_failure_never_reaches_the_live_pairs(tmp_path: Path, monkeypatch) -> None:
    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(nextfix, "hourly_columns", boom)
    pairs = _pairs(tmp_path, _write_bars(tmp_path))
    assert not pairs.empty and pairs["x_hourly"].isna().all()


def test_a_fold_created_long_after_its_day_is_never_labelled_live(tmp_path: Path) -> None:
    pairs = _pairs(tmp_path, _write_bars(tmp_path))
    late = datetime(2026, 12, 1, tzinfo=UTC)  # far after every synthetic decision day
    folds = nextfix.update_oos(
        pairs, [], nextfix.predict_hourly, forward_from="2026-01-01", hourly=True, as_of=late
    )
    assert folds and all(f["retro"] for f in folds)
    fresh = nextfix.update_oos(
        pairs, [], nextfix.predict_hourly, forward_from="2026-01-01", hourly=True
    )
    assert any(not f["retro"] for f in fresh)  # no clock given: the forward_from rule alone


def test_malformed_stored_hourly_folds_do_not_stop_the_other_records(tmp_path: Path) -> None:
    _run(tmp_path, with_bars=True)
    path = tmp_path / "nextfix_p3_variants_oos.json"
    doc = json.loads(path.read_text())
    doc[nextfix.HOURLY_KEY]["folds"] = [5, "x", {"no": "day"}, *doc[nextfix.HOURLY_KEY]["folds"]]
    path.write_text(json.dumps(doc))
    pairs = _pairs(tmp_path, nfi.load_bars(tmp_path))
    counts = nextfix.update_variants(pairs, tmp_path)
    assert counts["p3_roll60"] > 0 and counts["p3_monday"] > 0
    assert all(isinstance(f, dict) and "d0" in f for f in nextfix.load_hourly_folds(tmp_path))


def test_the_run_log_carries_the_hourly_record_counts_and_nothing_else(
    tmp_path: Path, caplog
) -> None:
    import logging

    with caplog.at_level(logging.INFO, logger="ml.nextfix"):
        _run(tmp_path, with_bars=True)
    line = next(r.getMessage() for r in caplog.records if "hourly shadow record:" in r.getMessage())
    assert line.startswith("hourly shadow record: ") and "folds (" in line
    assert not any(
        ch.isalpha()
        for ch in line.split(": ", 1)[1]
        .replace("folds", "")
        .replace("forward", "")
        .replace("re-run", "")
    )  # digits and punctuation only: no price level
