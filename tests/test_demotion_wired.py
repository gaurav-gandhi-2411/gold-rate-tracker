"""ADR 068 wired (GG decision D3): a degraded live model falls back to hold and alerts; a healthy one
stays quiet. End to end through ml.nextfix.run, with synthetic data."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from ml import demotion, nextfix, notifications
from ml.notification_routing import PUBLIC_ALLOWLIST, resolve_topic


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(nextfix, "MLP_MODELS", 1)
    monkeypatch.setattr(nextfix, "MLP_EPOCHS", 10)
    monkeypatch.setattr(nextfix, "MIN_TRAIN", 40)


def _synthetic(n_days: int = 150, seed: int = 3) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2026-01-05", periods=n_days)
    glob_close = 125000 * np.exp(np.cumsum(rng.normal(0, 0.01, n_days)))
    pm = 13000 * np.exp(0.5 * np.log(glob_close / glob_close[0]))
    pm[1:] = 13000 * np.exp(
        0.5 * np.log(glob_close[:-1] / glob_close[0]) + 0.5 * np.log(glob_close[1:] / glob_close[0])
    )
    ibja = pd.DataFrame({"date": days, "pm": pm, "am": np.nan})
    glob = pd.Series(glob_close, index=days)
    return ibja, glob.reindex(pd.date_range(days.min(), days.max(), freq="D")).ffill()


def _setup(tmp_path: Path) -> tuple[pd.DataFrame, pd.DataFrame, datetime]:
    ibja, glob = _synthetic()
    am = ibja["pm"] * np.exp(np.random.default_rng(1).normal(0, 0.004, len(ibja)))
    pd.DataFrame(
        {"date": ibja["date"], "pm_916": ibja["pm"] * 10.0, "am_916": am * 10.0}
    ).to_parquet(tmp_path / "ibja_rates.parquet")
    macro = pd.DataFrame({"gold_usd": glob.to_numpy() / 100.0, "usd_inr": 100.0}, index=glob.index)
    d0 = ibja["date"].iloc[-1]
    return ibja, macro, datetime(d0.year, d0.month, d0.day, 23, tzinfo=UTC)


def _state(tmp_path: Path) -> dict:
    return json.loads((tmp_path / nextfix.STATE_FILE).read_text())


def test_approved_settings():
    p = demotion.DemotionParams()
    assert (p.dir_floor, p.persist, p.err_window, p.dir_window, p.cov_window) == (
        0.55,
        3,
        40,
        40,
        60,
    )


def test_a_healthy_live_model_stays_live_and_quiet(tmp_path: Path):
    _, macro, now = _setup(tmp_path)
    out = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    assert out["demotion"]["demoted"] is False and out["demotion"]["newly_demoted"] is False
    assert out["forecast"]["mode"] == "after_us_close" and "demoted" not in out["forecast"]
    assert _state(tmp_path)["demoted"] is False
    fc = {"next_fix": {"active": True, "demotion": {"demoted": False, "since": None}}}
    alert = notifications._check_t16_model_demoted(
        fc, notifications.NotificationState(), datetime.now(notifications.IST)
    )
    assert alert is None


def test_a_degraded_live_model_falls_back_to_hold_and_stays_there(tmp_path: Path, monkeypatch):
    _, macro, now = _setup(tmp_path)
    real = nextfix.predict_p3

    def inverted(train, row, resid_sd=None):  # a model that has lost its edge and then some
        p = real(train, row, resid_sd)
        return nextfix.Prediction(ret=-p.ret, p_up=1.0 - p.p_up, vol=p.vol)

    monkeypatch.setattr(nextfix, "predict_p3", inverted)
    out = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    dm = out["demotion"]
    assert dm["demoted"] is True and dm["newly_demoted"] is True and dm["reasons"]
    assert {r["rule"] for r in dm["reasons"]} & {"error", "direction"}
    fc = out["forecast"]
    assert fc["mode"] == "after_afternoon_rate" and fc["model_version"].startswith(
        "hold_latest_fix"
    )
    assert fc["demoted"] is True and fc["p_up"] is None  # no direction while held
    assert _state(tmp_path)["demoted"] is True

    # sticky: even if the record looks healthy again, a human has to reset the state file
    monkeypatch.setattr(nextfix, "predict_p3", real)
    out2 = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    assert out2["demotion"]["demoted"] is True and out2["demotion"]["newly_demoted"] is False
    assert out2["forecast"]["mode"] == "after_afternoon_rate"

    # resetting the file alone does not help while the record is still degraded: the rules re-fire
    st = _state(tmp_path)
    st.update(demoted=False, since=None, reasons=[])
    (tmp_path / nextfix.STATE_FILE).write_text(json.dumps(st))
    out3 = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    assert out3["demotion"]["demoted"] is True and out3["demotion"]["newly_demoted"] is True
    # once the record is healthy again (rebuilt here) and a person resets the file: back to the model
    (tmp_path / "nextfix_p3_oos.json").unlink()
    st = _state(tmp_path)
    st.update(demoted=False, since=None, reasons=[])
    (tmp_path / nextfix.STATE_FILE).write_text(json.dumps(st))
    out4 = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    assert out4["forecast"]["mode"] == "after_us_close" and out4["demotion"]["demoted"] is False


def test_the_alert_fires_once_per_demotion_to_the_private_topic(tmp_path: Path):
    since = "2026-10-06T07:00:00+00:00"
    fc = {
        "next_fix": {
            "active": True,
            "demotion": {"demoted": True, "since": since, "reasons": [{"rule": "error"}]},
        }
    }
    now_ist = datetime.now(notifications.IST)
    state = notifications.NotificationState()
    alert = notifications._check_t16_model_demoted(fc, state, now_ist)
    assert alert is not None and alert.trigger_id == "T16" and alert.priority == 5
    assert alert.bypass_quiet and "error" in alert.body and alert.title.isascii()
    # sent: not repeated for the same demotion ...
    state.last_sent["T16"] = "2026-10-06T07:05:00+00:00"
    assert notifications._check_t16_model_demoted(fc, state, now_ist) is None
    # ... but a LATER demotion alerts again
    fc["next_fix"]["demotion"]["since"] = "2026-11-01T07:00:00+00:00"
    assert notifications._check_t16_model_demoted(fc, state, now_ist) is not None
    # private topic only
    assert "T16" not in PUBLIC_ALLOWLIST
    audience, topic = resolve_topic("T16", {"NTFY_TOPIC": "priv", "NTFY_TOPIC_PUBLIC": "pub"})
    assert audience != "public" and topic == "priv"


def test_t16_is_part_of_check_triggers():
    fc = {
        "next_fix": {
            "active": True,
            "demotion": {"demoted": True, "since": "2026-10-06T07:00:00+00:00", "reasons": []},
        }
    }
    alerts = notifications.check_triggers(
        fc,
        {"status": "missing"},
        [],
        {},
        notifications.NotificationState(),
        datetime(2026, 10, 6, 14, 0, tzinfo=notifications.IST),
    )
    assert "T16" in {a.trigger_id for a in alerts}


def test_a_broken_monitor_never_switches_the_live_model_off(tmp_path: Path, monkeypatch):
    _, macro, now = _setup(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("monitor bug")

    monkeypatch.setattr(demotion, "demotion_status", boom)
    out = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    assert out["demotion"]["checked"] is False and out["demotion"]["demoted"] is False
    assert out["forecast"]["mode"] == "after_us_close"


def test_state_for_another_model_version_is_not_applied(tmp_path: Path):
    path = tmp_path / "s.json"
    demotion.save_state(path, {**demotion.empty_state("old_model"), "demoted": True, "since": "x"})
    assert demotion.load_state(path, "nextfix_p3_v1")["demoted"] is False
    assert demotion.load_state(path, "old_model")["demoted"] is True
    assert demotion.load_state(tmp_path / "missing.json", "m")["demoted"] is False


def test_hac_helper_unchanged_sanity():
    assert math.isclose(demotion.binom_p_lower(0, 3, 0.5), 0.125)


def test_a_failing_shadow_ensemble_cannot_take_the_live_run_down(tmp_path: Path, monkeypatch):
    _, macro, now = _setup(tmp_path)

    def boom(*a, **k):
        raise ImportError("No module named torch")

    monkeypatch.setattr(nextfix, "predict", boom)  # the ensemble's point model
    out = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    assert out["forecast"]["model_version"] == nextfix.MODEL_VERSION
    assert out["shadow_eval"]["ready"] is False and "shadow" not in out["forecast"]
    assert (tmp_path / "nextfix_p3_oos.json").exists()


@pytest.mark.parametrize("bad", ["{not json", "[]", '"false"', '{"demoted": "false"}'])
def test_a_corrupt_state_file_fails_closed_not_open(tmp_path: Path, bad: str):
    path = tmp_path / "s.json"
    path.write_text(bad)
    st = demotion.load_state(path, "nextfix_p3_v1")
    assert st["demoted"] is True and st["reasons"][0]["rule"] == "state_unreadable"
    assert st["since"]  # so T16 alerts once


def test_a_naive_since_timestamp_still_alerts():
    fc = {
        "next_fix": {"demotion": {"demoted": True, "since": "2026-10-06T07:00:00", "reasons": []}}
    }
    state = notifications.NotificationState()
    state.last_sent["T16"] = "2026-10-06T07:05:00+00:00"
    alert = notifications._check_t16_model_demoted(fc, state, datetime.now(notifications.IST))
    assert alert is not None  # cannot compare naive with aware: alert rather than stay silent
