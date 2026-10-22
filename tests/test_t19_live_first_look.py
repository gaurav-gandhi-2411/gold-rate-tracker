"""T19: GG hears once, with the verdict, when the live-only monitor (ADR 075) reaches its first look."""

from __future__ import annotations

from datetime import datetime

from ml import notifications
from ml.live_monitor import LIVE_RULE
from ml.notification_routing import PUBLIC_ALLOWLIST, audience_for

FIRST = LIVE_RULE["first_look_days"]


def _fc(n: object, checked: bool = True, breach: bool = False, cov_alert: bool = False) -> dict:
    lmon = {
        "checked": checked,
        "n_live": n,
        "error_breach": breach,
        "coverage_live": 0.6,
        "coverage_alert": cov_alert,
    }
    return {"next_fix": {"demotion": {"live_monitor": lmon}}}


def _now() -> datetime:
    return datetime.now(notifications.IST)


def test_silent_before_the_first_look_and_when_not_checked() -> None:
    state = notifications.NotificationState()
    assert notifications._check_t19_live_first_look(_fc(FIRST - 1), state, _now()) is None
    assert (
        notifications._check_t19_live_first_look(_fc(FIRST, checked=False), state, _now()) is None
    )
    assert notifications._check_t19_live_first_look(_fc(None), state, _now()) is None
    assert notifications._check_t19_live_first_look({}, state, _now()) is None


def test_fires_at_the_first_look_with_a_clean_verdict() -> None:
    a = notifications._check_t19_live_first_look(
        _fc(FIRST), notifications.NotificationState(), _now()
    )
    assert a is not None and a.trigger_id == "T19"
    assert f"reached {FIRST} live days" in a.body
    assert "not clearly worse than repeating the last rate" in a.body
    assert "not clearly below target" in a.body and "sent once" in a.body


def test_verdict_names_a_breach_and_a_coverage_alert() -> None:
    a = notifications._check_t19_live_first_look(
        _fc(FIRST + 3, breach=True, cov_alert=True), notifications.NotificationState(), _now()
    )
    assert a is not None
    assert "switched to hold" in a.body and "clearly below target (alert only)" in a.body


def test_never_twice() -> None:
    state = notifications.NotificationState()
    state.last_sent["T19"] = _now().isoformat()
    assert notifications._check_t19_live_first_look(_fc(FIRST + 5), state, _now()) is None


def test_private_only() -> None:
    assert "T19" not in PUBLIC_ALLOWLIST and audience_for("T19") == "ops"
