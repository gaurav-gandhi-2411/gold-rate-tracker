"""ADR 075 wired: a degraded live record demotes through the real ml.nextfix._demotion step, a healthy
one does not, a failing monitor fails closed, and the T18 coverage alert. Synthetic records only."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from ml import demotion, inference, nextfix, notifications
from ml import live_monitor as lm
from ml.notification_routing import PUBLIC_ALLOWLIST, audience_for

START = datetime(2026, 10, 7)


def _fold(day: int, hold_err: float, model_err: float, sign: int, retro: bool | None) -> dict:
    """A fold whose hold error is ``hold_err`` and whose model error is ``model_err`` (Rs./g)."""
    pm0 = 13000.0
    pm1 = pm0 + sign * hold_err
    pred = pm1 + sign * model_err  # |pred - pm1| = model_err
    f = {
        "d0": (START + timedelta(days=day)).strftime("%Y-%m-%d"),
        "pm0": pm0,
        "pm1": pm1,
        "ret": math.log(pred / pm0),
        "p_up": 0.6 if pred > pm0 else 0.4,
        "vol": 0.006,
    }
    if retro is not None:
        f["retro"] = retro
    return f


def _record(n: int, ratio: float, seed: int, retro: bool | None = False, sd: float = 0.35) -> list:
    """n days; the model's error is ``ratio`` x the hold error on average (lognormal spread)."""
    rng = np.random.default_rng(seed)
    hold = np.abs(rng.normal(100.0, 70.0, n)) + 5.0
    fac = np.exp(rng.normal(math.log(ratio) - sd**2 / 2, sd, n))
    sign = rng.choice([-1, 1], n)
    return [
        _fold(i, float(h), float(h * f), int(s), retro)
        for i, (h, f, s) in enumerate(zip(hold, fac, sign, strict=True))
    ]


def _demote(tmp_path: Path, folds: list[dict]) -> dict:
    now = datetime(2026, 11, 30, 12, tzinfo=UTC)
    return nextfix._demotion(tmp_path, folds, {"ready": False}, now, "test_model_v1")


def test_degraded_live_record_demotes_through_the_real_step_and_stays_demoted(tmp_path):
    out = _demote(tmp_path, _record(60, ratio=1.5, seed=0))
    assert out["demoted"] is True and out["newly_demoted"] is True and out["checked"] is True
    assert "live_vs_hold" in {r["rule"] for r in out["reasons"]}
    assert "live_vs_hold" in out["rules_breaching_now"]
    state = json.loads((tmp_path / nextfix.demotion_state_file("test_model_v1")).read_text())
    assert state["demoted"] is True
    # sticky: a healthy-looking record does not bring it back
    again = _demote(tmp_path, _record(60, ratio=0.9, seed=0))
    assert again["demoted"] is True and again["newly_demoted"] is False


def test_healthy_live_record_is_not_demoted_and_reports_its_status(tmp_path):
    out = _demote(tmp_path, _record(60, ratio=0.9, seed=0))
    assert out["demoted"] is False and out["checked"] is True
    assert out["live_monitor"]["error"]["n_live"] == 60
    assert out["live_monitor"]["rule_sha256"] == lm.LIVE_RULE_SHA256


def test_a_failing_live_monitor_fails_closed_to_hold_without_a_sticky_state(tmp_path, monkeypatch):
    def boom(folds):
        raise RuntimeError("monitor bug")

    monkeypatch.setattr(lm, "live_status", boom)
    out = _demote(tmp_path, _record(60, ratio=0.9, seed=0))
    assert out["demoted"] is True and out["checked"] is False
    assert out["reasons"][0]["rule"] == "monitor_failed"
    assert not (tmp_path / nextfix.demotion_state_file("test_model_v1")).exists()  # self-heals


def test_the_status_takes_the_live_rule_without_persistence():
    folds = _record(30, ratio=1.0, seed=0)
    live = {"breach": True, "n_live": 30}
    st = demotion.demotion_status(folds, None, live=live)
    assert st["demote"]["live_vs_hold"] is True and st["demote_any"] is True
    assert "live_vs_hold" not in demotion.demotion_status(folds, None)["demote"]  # opt-in


def test_summary_is_small_and_tolerates_a_missing_monitor():
    assert inference._live_monitor_summary(None) == {"checked": False}
    s = inference._live_monitor_summary(lm.live_status(_record(40, 0.9, 1)))
    assert s["checked"] is True and s["n_live"] == 40 and s["error_breach"] is False
    assert "pm0" not in json.dumps(s) and "mean_diff" not in s  # counts and flags, no prices


def _fc(alert: bool, checked: bool = True) -> dict:
    lmon = {
        "checked": checked,
        "coverage_alert": alert,
        "coverage_n_live": 40,
        "coverage_live": 0.5,
    }
    return {"next_fix": {"demotion": {"live_monitor": lmon}}}


def test_t18_fires_once_a_week_and_only_when_checked_and_alerting():
    now = datetime.now(notifications.IST)
    state = notifications.NotificationState()
    a = notifications._check_t18_live_coverage(_fc(True), state, now)
    assert a is not None and a.trigger_id == "T18" and "80%" in a.body
    assert notifications._check_t18_live_coverage(_fc(False), state, now) is None
    assert notifications._check_t18_live_coverage(_fc(True, checked=False), state, now) is None
    state.last_sent["T18"] = (now - timedelta(days=2)).isoformat()
    assert notifications._check_t18_live_coverage(_fc(True), state, now) is None
    state.last_sent["T18"] = (now - timedelta(days=8)).isoformat()
    assert notifications._check_t18_live_coverage(_fc(True), state, now) is not None


def test_t18_is_private_never_public():
    assert "T18" not in PUBLIC_ALLOWLIST and audience_for("T18") == "ops"


def test_a_non_finite_live_fold_fails_closed_not_silent(tmp_path):
    folds = _record(30, ratio=0.9, seed=0)
    folds[5] = {**folds[5], "ret": float("nan")}
    out = _demote(tmp_path, folds)
    assert out["demoted"] is True and out["checked"] is False
    assert out["reasons"][0]["rule"] == "monitor_failed"


def test_a_repeated_day_is_counted_once():
    folds = _record(12, ratio=0.9, seed=0)
    assert lm.error_status(folds + folds[:5])["n_live"] == 12
