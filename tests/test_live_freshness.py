"""scripts/live_freshness.py: page on what visitors see, once per incident, plus recovery."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import live_freshness as lf

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def _iso(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat()


def _values(forecast_h: float | None, tanishq_h: float | None) -> dict[str, str | None]:
    return {
        "forecast": None if forecast_h is None else _iso(forecast_h),
        "tanishq": None if tanishq_h is None else _iso(tanishq_h),
    }


def test_fresh_payload_stays_quiet():
    msgs, state, healthy = lf.decide({}, _values(1, 3), NOW)
    assert (msgs, state, healthy) == ([], {}, True)


def test_stale_forecast_pages_once_then_stays_quiet_then_recovers_once():
    vals = _values(19.3, 3)  # the 2026-10-29 shape: forecast frozen, Tanishq fine
    msgs, state, healthy = lf.decide({}, vals, NOW)
    assert not healthy and len(msgs) == 1
    assert "forecast is 19.3 h old" in msgs[0]["title"] and msgs[0]["priority"] == "high"
    # an hour later, same frozen value: no second page
    msgs2, state2, healthy2 = lf.decide(state, vals, NOW + timedelta(hours=1))
    assert msgs2 == [] and not healthy2 and state2 == state
    # a fresh value arrives: exactly one recovery message, state cleared
    msgs3, state3, healthy3 = lf.decide(state2, _values(0.2, 3), NOW)
    assert healthy3 and state3 == {} and len(msgs3) == 1
    assert msgs3[0]["title"] == "Gold Tracker: forecast fresh again"


def test_limits_are_the_derived_ones_and_boundaries_hold():
    assert lf.LIMITS_H == {"forecast": 9.0, "tanishq": 20.0}
    assert lf.decide({}, _values(8.99, 19.99), NOW)[2] is True
    msgs, _, healthy = lf.decide({}, _values(9.01, 20.01), NOW)
    assert not healthy and len(msgs) == 2


def test_an_unreadable_site_fails_closed_once_and_recovers():
    msgs, state, healthy = lf.decide({}, _values(None, None), NOW)
    assert not healthy and len(msgs) == 1 and "cannot read" in msgs[0]["title"]
    assert lf.decide(state, _values(None, None), NOW)[0] == []
    msgs2, state2, healthy2 = lf.decide(state, _values(1, 1), NOW)
    assert healthy2 and state2 == {} and "readable again" in msgs2[0]["title"]


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


def test_main_simulated_stale_pages_and_fresh_is_quiet(capsys, monkeypatch):
    sent: list[dict[str, str]] = []
    monkeypatch.setattr(lf, "send", lambda msg, topic: sent.append(msg) or True)
    monkeypatch.setenv("NTFY_TOPIC", "t")
    assert lf.main(["--simulate", "stale", "--now", NOW.isoformat()]) == 1
    assert len(sent) == 2 and all(m["title"].startswith("[TEST] ") for m in sent)
    sent.clear()
    assert lf.main(["--simulate", "fresh", "--now", NOW.isoformat()]) == 0
    assert sent == []
    assert "no page: all fresh" in capsys.readouterr().out


def test_main_keeps_state_between_runs_and_never_fetches_when_simulating(tmp_path, monkeypatch):
    calls = {"fetch": 0}
    payload = {"forecast": {"predicted_at": _iso(12)}, "prices": [{"timestamp": _iso(2)}]}

    def fake_fetch(url: str, attempts: int = 3):
        calls["fetch"] += 1
        return payload["forecast"] if url.endswith("forecast.json") else payload["prices"]

    sent: list[dict[str, str]] = []
    monkeypatch.setattr(lf, "fetch_json", fake_fetch)
    monkeypatch.setattr(lf, "send", lambda msg, topic: sent.append(msg) or True)
    monkeypatch.setenv("NTFY_TOPIC", "t")
    st = tmp_path / "s" / "state.json"
    args = ["--state-in", str(st), "--state-out", str(st), "--now", NOW.isoformat()]
    assert lf.main(args) == 1 and len(sent) == 1  # opens the incident
    assert json.loads(st.read_text())["forecast"] == payload["forecast"]["predicted_at"]
    assert lf.main(args) == 1 and len(sent) == 1  # same incident: quiet
    payload["forecast"] = {"predicted_at": _iso(0.5)}
    assert lf.main(args) == 0 and len(sent) == 2 and "fresh again" in sent[-1]["title"]
    assert json.loads(st.read_text()) == {}
    fetched = calls["fetch"]
    lf.main(["--simulate", "stale", "--dry-run", "--now", NOW.isoformat()])
    assert calls["fetch"] == fetched


def test_a_failed_send_keeps_the_old_state_so_the_page_is_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(
        lf,
        "fetch_json",
        lambda url, attempts=3: (
            {"predicted_at": _iso(12)}
            if url.endswith("forecast.json")
            else [{"timestamp": _iso(1)}]
        ),
    )
    monkeypatch.setattr(lf, "send", lambda msg, topic: False)
    monkeypatch.setenv("NTFY_TOPIC", "t")
    st = tmp_path / "state.json"
    assert lf.main(["--state-in", str(st), "--state-out", str(st), "--now", NOW.isoformat()]) == 1
    assert json.loads(st.read_text()) == {}


def test_a_due_page_with_no_topic_fails_the_step(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "")
    assert lf.main(["--simulate", "stale", "--now", NOW.isoformat()]) == 1


def test_a_future_timestamp_fails_closed_instead_of_reading_fresh():
    # one bad row a month ahead would otherwise read "fresh" until then and hide a real outage
    msgs, state, healthy = lf.decide({}, _values(1, -24 * 30), NOW)
    assert not healthy and len(msgs) == 1
    assert "cannot read" in msgs[0]["title"] and "in the future" in msgs[0]["body"]
    assert lf.decide(state, _values(1, -24 * 30), NOW)[0] == []  # one page per incident
    # small clock skew inside the tolerance is not an incident
    assert lf.decide({}, _values(-0.05, 1), NOW) == ([], {}, True)


def test_a_new_stale_value_during_an_open_incident_pages_again():
    _, state, _ = lf.decide({}, _values(12, 1), NOW)
    msgs, state2, _ = lf.decide(state, _values(10, 1), NOW)  # a newer but still stale forecast
    assert len(msgs) == 1 and state2["forecast"] == _iso(10)


def test_a_state_file_that_is_not_an_object_starts_empty_and_still_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(
        lf,
        "fetch_json",
        lambda url, attempts=3: (
            {"predicted_at": _iso(12)}
            if url.endswith("forecast.json")
            else [{"timestamp": _iso(1)}]
        ),
    )
    sent: list[dict[str, str]] = []
    monkeypatch.setattr(lf, "send", lambda msg, topic: sent.append(msg) or True)
    monkeypatch.setenv("NTFY_TOPIC", "t")
    st = tmp_path / "state.json"
    st.write_text("[]", encoding="utf-8")
    assert lf.main(["--state-in", str(st), "--state-out", str(st), "--now", NOW.isoformat()]) == 1
    assert len(sent) == 1 and json.loads(st.read_text())["forecast"] == _iso(12)


def test_a_partial_send_failure_keeps_the_old_state_so_nothing_is_lost(tmp_path, monkeypatch):
    monkeypatch.setattr(
        lf,
        "fetch_json",
        lambda url, attempts=3: (
            {"predicted_at": _iso(12)}
            if url.endswith("forecast.json")
            else [{"timestamp": _iso(25)}]
        ),
    )
    results = iter([True, False])
    monkeypatch.setattr(lf, "send", lambda msg, topic: next(results))
    monkeypatch.setenv("NTFY_TOPIC", "t")
    st = tmp_path / "state.json"
    assert lf.main(["--state-in", str(st), "--state-out", str(st), "--now", NOW.isoformat()]) == 1
    # both incidents stay unrecorded, so the failed one is retried (the sent one repeats: a
    # duplicate, never a silence)
    assert json.loads(st.read_text()) == {}
