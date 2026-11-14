"""scripts/live_freshness.py: page on what visitors see, once per INCIDENT, plus one recovery."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import live_freshness as lf

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    # CI sets these and has broken tests twice; no test here may send anything to ntfy.
    for name in ("GITHUB_ACTIONS", "GITHUB_EVENT_NAME", "NTFY_TOPIC"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        lf, "send", lambda msg, topic: (_ for _ in ()).throw(AssertionError("sent to ntfy"))
    )


def _iso(hours_ago: float, now: datetime = NOW) -> str:
    return (now - timedelta(hours=hours_ago)).isoformat()


def _values(forecast_h: float | None, tanishq_h: float | None) -> dict[str, str | None]:
    return {
        "forecast": None if forecast_h is None else _iso(forecast_h),
        "tanishq": None if tanishq_h is None else _iso(tanishq_h),
    }


def _later(hours: float) -> datetime:
    return NOW + timedelta(hours=hours)


def _fresh_values(now: datetime) -> dict[str, str | None]:
    return {"forecast": _iso(1, now), "tanishq": _iso(2, now)}


def test_fresh_payload_stays_quiet():
    msgs, state, healthy = lf.decide({}, _values(1, 3), NOW)
    assert (msgs, state, healthy) == ([], {}, True)


def test_stale_forecast_pages_once_stays_quiet_then_recovers_once_after_hysteresis():
    vals = _values(19.3, 3)  # the 2026-10-29 shape: forecast frozen, Tanishq fine
    msgs, state, healthy = lf.decide({}, vals, NOW)
    assert not healthy and len(msgs) == 1
    assert "forecast is 19.3 h old" in msgs[0]["title"] and msgs[0]["priority"] == "high"
    # an hour later, same frozen value: no second page
    msgs2, state2, healthy2 = lf.decide(state, vals, _later(1))
    assert msgs2 == [] and not healthy2 and state2 == state
    # first fresh check: no recovery yet (one check is not enough), the incident stays open
    t1 = _later(2)
    msgs3, state3, healthy3 = lf.decide(state2, _fresh_values(t1), t1)
    assert msgs3 == [] and not healthy3 and state3["incident"]["fresh_checks"] == 1
    # second fresh check, over an hour later: exactly one recovery message, state cleared
    t2 = _later(3.5)
    msgs4, state4, healthy4 = lf.decide(state3, _fresh_values(t2), t2)
    assert healthy4 and state4 == {} and len(msgs4) == 1
    assert msgs4[0]["title"] == "Gold Tracker: live site data is fresh again"
    assert "forecast" in msgs4[0]["body"]


def test_two_fresh_checks_less_than_an_hour_apart_do_not_recover():
    # a schedule run and a dispatch run can land minutes apart (11-12 03:23 and 03:51Z)
    _, state, _ = lf.decide({}, _values(12, 1), NOW)
    t1, t2 = _later(1), _later(1.4)
    _, state, _ = lf.decide(state, _fresh_values(t1), t1)
    msgs, state, healthy = lf.decide(state, _fresh_values(t2), t2)
    assert msgs == [] and not healthy and state["incident"]["fresh_checks"] == 2
    t3 = _later(2.1)  # 1.1 h after the first fresh check
    msgs, state, healthy = lf.decide(state, _fresh_values(t3), t3)
    assert healthy and state == {} and len(msgs) == 1


def test_a_signal_going_stale_again_inside_the_window_continues_the_same_incident():
    _, state, _ = lf.decide({}, _values(12, 1), NOW)
    t1 = _later(1)
    _, state, _ = lf.decide(state, _fresh_values(t1), t1)  # one fresh check
    t2 = _later(4)
    msgs, state, healthy = lf.decide(state, {"forecast": _iso(10, t2), "tanishq": _iso(1, t2)}, t2)
    assert msgs == [] and not healthy  # no new open page, and the fresh streak starts over
    assert "fresh_checks" not in state["incident"]
    t3 = _later(5)
    msgs, state, _ = lf.decide(state, _fresh_values(t3), t3)
    assert msgs == [] and state["incident"]["fresh_checks"] == 1


def test_a_second_stale_signal_joins_the_open_incident_without_a_page():
    _, state, _ = lf.decide({}, _values(12, 1), NOW)  # forecast stale opens it
    t1 = _later(2)
    msgs, state, healthy = lf.decide(state, {"forecast": _iso(14, t1), "tanishq": _iso(25, t1)}, t1)
    assert msgs == [] and not healthy
    assert set(state["incident"]["signals"]) == {"forecast", "tanishq"}
    t2, t3 = _later(3), _later(5)
    _, state, _ = lf.decide(state, _fresh_values(t2), t2)
    msgs, state, healthy = lf.decide(state, _fresh_values(t3), t3)
    assert healthy and len(msgs) == 1
    assert "forecast, tanishq" in msgs[0]["body"]  # the recovery lists every signal that was bad


def test_two_signals_stale_at_once_make_one_page_listing_both():
    msgs, _, _ = lf.decide({}, _values(12, 30), NOW)
    assert len(msgs) == 1
    assert (
        "forecast is 12.0 h old" in msgs[0]["title"] and "tanishq is 30.0 h old" in msgs[0]["title"]
    )


def test_limits_are_the_derived_ones_and_boundaries_hold():
    assert lf.LIMITS_H == {"forecast": 9.0, "tanishq": 20.0}
    assert (lf.MODEL_PAUSED_H, lf.RECOVERY_CHECKS, lf.RECOVERY_H) == (9.0, 2, 1.0)
    assert lf.decide({}, _values(8.99, 19.99), NOW)[2] is True
    msgs, _, healthy = lf.decide({}, _values(9.01, 20.01), NOW)
    assert not healthy and len(msgs) == 1  # both over their limits: still one page


def test_an_unreadable_site_fails_closed_once_and_recovers():
    msgs, state, healthy = lf.decide({}, _values(None, None), NOW)
    assert not healthy and len(msgs) == 1 and "cannot read" in msgs[0]["title"]
    assert lf.decide(state, _values(None, None), NOW)[0] == []
    t1, t2 = _later(1), _later(3)
    _, state, _ = lf.decide(state, _fresh_values(t1), t1)
    msgs2, state2, healthy2 = lf.decide(state, _fresh_values(t2), t2)
    assert healthy2 and state2 == {} and "fresh again" in msgs2[0]["title"]


def test_latest_values_reads_what_the_page_reads():
    prices = [
        {"timestamp": "2026-10-29T10:00:00Z"},
        {"timestamp": "2026-10-30T10:01:08.961Z"},
        {"timestamp": "garbage"},
        "not a row",
    ]
    vals = lf.latest_values({"predicted_at": "2026-10-30T11:10:50.086551+00:00"}, prices)
    assert vals == {
        "forecast": "2026-10-30T11:10:50.086551+00:00",
        "tanishq": "2026-10-30T10:01:08.961Z",
    }
    assert lf.latest_values({"predicted_at": None}, []) == {"forecast": None, "tanishq": None}
    assert lf.latest_values(None, None) == {"forecast": None, "tanishq": None}


def test_a_future_timestamp_fails_closed_instead_of_reading_fresh():
    # one bad row a month ahead would otherwise read "fresh" until then and hide a real outage
    msgs, state, healthy = lf.decide({}, _values(1, -24 * 30), NOW)
    assert not healthy and len(msgs) == 1
    assert "cannot read" in msgs[0]["title"] and "in the future" in msgs[0]["body"]
    assert lf.decide(state, _values(1, -24 * 30), NOW)[0] == []  # one page per incident
    # small clock skew inside the tolerance is not an incident
    assert lf.decide({}, _values(-0.05, 1), NOW) == ([], {}, True)


def test_a_state_of_the_old_per_signal_version_is_adopted_without_a_page():
    old = {"forecast": _iso(12)}  # what the previous version kept in the Actions cache
    msgs, state, healthy = lf.decide(old, _values(12, 1), NOW)
    assert msgs == [] and not healthy and set(state["incident"]["signals"]) == {"forecast"}
    assert "forecast" not in state  # the legacy keys are gone


def _good_incident() -> dict:
    return {"opened_at": _iso(5), "last_paged_at": _iso(5), "signals": {"forecast": _iso(5)}}


DAMAGED = [
    {"opened_at": "x", "signals": {}},  # unparseable opened_at
    {"signals": {}},  # missing opened_at
    {"opened_at": _iso(5)},  # missing signals
    {"opened_at": _iso(5), "signals": []},  # signals of the wrong type
    {"opened_at": _iso(5), "signals": {}, "fresh_since": "junk"},
    {"opened_at": _iso(5), "signals": {}, "fresh_checks": "two"},
    {"opened_at": _iso(5), "signals": {}, "fresh_checks": True},
    {"opened_at": _iso(5), "signals": {}, "last_paged_at": "junk"},
]


@pytest.mark.parametrize("damaged", DAMAGED)
def test_a_damaged_incident_with_a_stale_site_pages_like_a_first_run(damaged):
    msgs, state, healthy = lf.decide({"incident": damaged}, _values(12, 1), NOW)
    assert len(msgs) == 1 and "forecast is 12.0 h old" in msgs[0]["title"] and not healthy
    assert lf.decide(state, _values(12, 1), _later(1))[0] == []  # and then it is a normal incident


@pytest.mark.parametrize("damaged", DAMAGED)
def test_a_damaged_incident_with_a_fresh_site_is_dropped_quietly(damaged):
    assert lf.decide({"incident": damaged}, _values(1, 1), NOW) == ([], {}, True)


def test_a_good_incident_is_not_mistaken_for_a_damaged_one():
    assert lf._valid_incident(_good_incident())
    msgs, state, _ = lf.decide({"incident": _good_incident()}, _values(12, 1), NOW)
    assert msgs == [] and state["incident"]["opened_at"] == _iso(5)  # kept, not re-opened


# --- the 24 h re-page ----------------------------------------------------------------------------


def test_a_long_incident_pages_again_once_a_day_and_the_recovery_stays_one():
    sent: list[str] = []
    state: dict = {}
    for hours in range(0, 60, 3):  # forecast frozen for the whole span, a check every 3 h
        now = _later(hours)
        msgs, state, healthy = lf.decide(
            state, {"forecast": _iso(12, NOW), "tanishq": _iso(1, now)}, now
        )
        assert not healthy
        sent += [m["title"] for m in msgs]
        if hours == 21:
            assert len(sent) == 1  # 21 h in: only the opening page
    # opens at 0 h, "still stale" at 24 h and 48 h: at most one push a day
    assert len(sent) == 3 and sent[1].startswith("Gold Tracker: still stale after 24 h --")
    assert sent[2].startswith("Gold Tracker: still stale after 48 h --")
    assert "forecast is" in sent[1]
    for hours in (60, 63):  # fresh: still ONE recovery
        now = _later(hours)
        msgs, state, healthy = lf.decide(state, _fresh_values(now), now)
        sent += [m["title"] for m in msgs]
    assert sent[-1] == "Gold Tracker: live site data is fresh again" and sent.count(sent[-1]) == 1
    assert state == {}


def test_a_signal_joining_a_long_incident_is_listed_by_the_next_daily_page_only():
    _, state, _ = lf.decide({}, _values(12, 1), NOW)
    t1 = _later(10)
    msgs, state, _ = lf.decide(state, {"forecast": _iso(22, t1), "tanishq": _iso(25, t1)}, t1)
    assert msgs == []  # joins silently
    t2 = _later(24)
    msgs, state, _ = lf.decide(state, {"forecast": _iso(36, t2), "tanishq": _iso(39, t2)}, t2)
    assert len(msgs) == 1 and "forecast" in msgs[0]["title"] and "tanishq" in msgs[0]["title"]


def test_the_repage_clock_is_not_reset_by_a_fresh_check_and_a_fresh_wait_does_not_repage():
    _, state, _ = lf.decide({}, _values(12, 1), NOW)
    t1 = _later(25)  # a day later but fresh: waiting out the recovery window, no re-page
    msgs, state, healthy = lf.decide(state, _fresh_values(t1), t1)
    assert msgs == [] and not healthy
    t2 = _later(26)  # stale again: the 24 h are long past, so this one re-pages
    msgs, state, _ = lf.decide(state, {"forecast": _iso(10, t2), "tanishq": _iso(1, t2)}, t2)
    assert len(msgs) == 1 and msgs[0]["title"].startswith("Gold Tracker: still stale")
    assert state["incident"]["last_paged_at"] == t2.isoformat()


# --- the model forecast ------------------------------------------------------------------------


def _fc(active, down_since=None, predicted_at: str | None = None) -> dict:
    nf: dict = {"active": active}
    if down_since is not None:
        nf["down_since"] = down_since
    return {"predicted_at": predicted_at or _iso(0.5), "next_fix": nf}


def test_model_pause_reader_treats_only_the_boolean_true_as_active():
    assert lf.model_pause(_fc(True)) == (False, None)
    assert lf.model_pause(_fc(False, "2026-10-30T01:00:00+00:00")) == (
        True,
        "2026-10-30T01:00:00+00:00",
    )
    assert lf.model_pause(_fc("true")) == (True, None)
    assert lf.model_pause({"predicted_at": _iso(1)}) == (True, None)  # no next_fix block at all
    assert lf.model_pause(_fc(False, "garbage")) == (True, None)
    assert lf.model_pause(None) is None and lf.model_pause([]) is None


def test_a_pause_past_the_limit_pages_once_and_a_shorter_one_does_not():
    short = lf.decide({}, _values(1, 1), NOW, (True, _iso(8.9)))
    assert short[0] == [] and short[2] is True  # 8.9 h: under the 9 h limit
    msgs, state, healthy = lf.decide({}, _values(1, 1), NOW, (True, _iso(9.1)))
    assert not healthy and len(msgs) == 1 and "no model forecast for 9.1 h" in msgs[0]["title"]
    assert lf.decide(state, _values(1, 1), _later(1), (True, _iso(9.1)))[0] == []


def test_a_pause_without_down_since_is_timed_from_when_it_was_first_seen():
    values = _values(0.5, 1)  # the forecast written 0.5 h ago is the first paused one seen
    _, state, healthy = lf.decide({}, values, NOW, (True, None))
    assert healthy and state["model_since"] == values["forecast"]  # remembered, not yet 9 h
    t = _later(9.6)
    msgs, state, healthy = lf.decide(state, _fresh_values(t), t, (True, None))
    assert not healthy and "no model forecast for 10.1 h" in msgs[0]["title"]
    t2, t3 = _later(10), _later(12)
    _, state, _ = lf.decide(state, _fresh_values(t2), t2, (False, None))
    assert "model_since" not in state  # active again: the streak is forgotten
    msgs, state, healthy = lf.decide(state, _fresh_values(t3), t3, (False, None))
    assert healthy and len(msgs) == 1


def test_an_unreadable_forecast_keeps_the_remembered_pause_start():
    _, state, _ = lf.decide({}, _values(0.5, 1), NOW, (True, None))
    _, state2, _ = lf.decide(state, _values(None, 1), _later(1), None)
    assert state2["model_since"] == state["model_since"]


# --- main(), the way ci-health runs it ---------------------------------------------------------


class _Site:
    """What the live site serves: swapped between runs by the tests."""

    def __init__(self, monkeypatch, sent: list[dict[str, str]]):
        self.forecast: object = None
        self.prices: object = None
        monkeypatch.setattr(
            lf,
            "fetch_json",
            lambda url, attempts=3: self.forecast if url.endswith("forecast.json") else self.prices,
        )
        monkeypatch.setattr(lf, "send", lambda msg, topic: sent.append(msg) or True)
        monkeypatch.setenv("NTFY_TOPIC", "t")


def _run(st: Path, now: datetime) -> int:
    return lf.main(["--state-in", str(st), "--state-out", str(st), "--now", now.isoformat()])


def test_main_simulated_stale_pages_once_and_fresh_is_quiet(capsys, monkeypatch):
    sent: list[dict[str, str]] = []
    monkeypatch.setattr(lf, "send", lambda msg, topic: sent.append(msg) or True)
    monkeypatch.setenv("NTFY_TOPIC", "t")
    assert lf.main(["--simulate", "stale", "--now", NOW.isoformat()]) == 1
    assert len(sent) == 1 and sent[0]["title"].startswith("[TEST] ")
    sent.clear()
    assert lf.main(["--simulate", "fresh", "--now", NOW.isoformat()]) == 0
    assert sent == []
    assert "no page: all fresh" in capsys.readouterr().out


def test_main_keeps_state_between_runs_and_never_fetches_when_simulating(tmp_path, monkeypatch):
    sent: list[dict[str, str]] = []
    site = _Site(monkeypatch, sent)
    site.forecast = _fc(True, predicted_at=_iso(12))
    site.prices = [{"timestamp": _iso(2)}]
    st = tmp_path / "s" / "state.json"
    assert _run(st, NOW) == 1 and len(sent) == 1  # opens the incident
    assert list(json.loads(st.read_text())["incident"]["signals"]) == ["forecast"]
    assert _run(st, NOW) == 1 and len(sent) == 1  # same incident: quiet
    site.forecast = _fc(True, predicted_at=_iso(0.5, _later(3)))
    assert _run(st, _later(3)) == 1 and len(sent) == 1  # fresh once: still open, still quiet
    site.forecast = _fc(True, predicted_at=_iso(0.5, _later(5)))
    assert _run(st, _later(5)) == 0 and len(sent) == 2 and "fresh again" in sent[-1]["title"]
    assert json.loads(st.read_text()) == {}
    calls = {"fetch": 0}
    monkeypatch.setattr(lf, "fetch_json", lambda url, attempts=3: calls.update(fetch=1))
    lf.main(["--simulate", "stale", "--dry-run", "--now", NOW.isoformat()])
    assert calls["fetch"] == 0


def test_a_failed_send_keeps_the_old_state_so_the_page_is_retried(tmp_path, monkeypatch):
    site = _Site(monkeypatch, [])
    site.forecast = _fc(True, predicted_at=_iso(12))
    site.prices = [{"timestamp": _iso(1)}]
    monkeypatch.setattr(lf, "send", lambda msg, topic: False)
    st = tmp_path / "state.json"
    assert _run(st, NOW) == 1
    assert json.loads(st.read_text()) == {}


def test_a_due_page_with_no_topic_fails_the_step(monkeypatch):
    monkeypatch.setattr(lf, "send", lambda msg, topic: True)
    monkeypatch.setenv("NTFY_TOPIC", "")
    assert lf.main(["--simulate", "stale", "--now", NOW.isoformat()]) == 1


def test_a_state_file_that_is_not_an_object_starts_empty_and_still_pages(tmp_path, monkeypatch):
    sent: list[dict[str, str]] = []
    site = _Site(monkeypatch, sent)
    site.forecast = _fc(True, predicted_at=_iso(12))
    site.prices = [{"timestamp": _iso(1)}]
    st = tmp_path / "state.json"
    st.write_text("[]", encoding="utf-8")
    assert _run(st, NOW) == 1
    assert len(sent) == 1 and "forecast" in json.loads(st.read_text())["incident"]["signals"]


def test_proof_i_a_poisoned_record_pages_once_then_silence_then_one_recovery(tmp_path, monkeypatch):
    """2026-11-12/13 shape: forecast.json keeps updating but next_fix is inactive (down_since set
    by ml/inference.py), the price and Tanishq are fresh, the 9 h forecast alarm sees nothing."""
    sent: list[dict[str, str]] = []
    site = _Site(monkeypatch, sent)
    st = tmp_path / "state.json"
    down = NOW - timedelta(hours=1)
    codes = []
    for hours in range(0, 14, 2):  # a check every 2 h while the model stays down
        now = NOW + timedelta(hours=hours)
        site.forecast = _fc(False, down.isoformat(), predicted_at=_iso(0.7, now))
        site.prices = [{"timestamp": _iso(1, now)}]
        codes.append(_run(st, now))
        if hours == 6:  # 7 h down: under the limit, nothing yet
            assert sent == []
    titles = [m["title"] for m in sent]
    assert len(titles) == 1 and "no model forecast" in titles[0], titles  # paged once, at > 9 h
    assert codes[-1] == 1 and codes[0] == 0
    sent.clear()  # recovery: the model is back; two fresh checks over an hour
    for hours in (14, 16, 18):
        now = NOW + timedelta(hours=hours)
        site.forecast = _fc(True, predicted_at=_iso(0.7, now))
        site.prices = [{"timestamp": _iso(1, now)}]
        codes.append(_run(st, now))
    assert [m["title"] for m in sent] == ["Gold Tracker: live site data is fresh again"]
    assert codes[-1] == 0 and json.loads(st.read_text()) == {}


def test_proof_ii_a_normal_hold_window_never_pages(tmp_path, monkeypatch):
    sent: list[dict[str, str]] = []
    site = _Site(monkeypatch, sent)
    st = tmp_path / "state.json"
    for hours in range(0, 48, 3):
        now = NOW + timedelta(hours=hours)
        fc = _fc(True, predicted_at=_iso(0.7, now))
        fc["headline"] = {
            "method": "hold_latest_fix_am_to_pm"
        }  # the model working, holding the fix
        site.forecast, site.prices = fc, [{"timestamp": _iso(1, now)}]
        assert _run(st, now) == 0
    assert sent == []


def test_proof_iii_a_forecast_that_stopped_updating_still_pages_once(tmp_path, monkeypatch):
    """predicted_at ageing with next_fix.active still true from the last run: the 9 h freshness
    alarm covers it, once."""
    sent: list[dict[str, str]] = []
    site = _Site(monkeypatch, sent)
    st = tmp_path / "state.json"
    stopped = _fc(True, predicted_at=_iso(0.5))
    for hours in range(0, 14, 2):
        now = NOW + timedelta(hours=hours)
        site.forecast, site.prices = stopped, [{"timestamp": _iso(1, now)}]
        _run(st, now)
    assert len(sent) == 1 and "forecast is" in sent[0]["title"]
    assert "no model forecast" not in sent[0]["title"]
