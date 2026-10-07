"""ADR 072 part 1: champion state (pin / unpin / rollback / atomic save / validation) and the T17
alert. Direct tests of ml.promotion and ml.notifications; no model run is needed."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from ml import notifications, promotion
from ml.notification_routing import PUBLIC_ALLOWLIST, resolve_topic

CHAMP = promotion.CHAMPION_FILE
ROLL = "p3_roll60"
T0 = datetime(2026, 10, 20, 7, 0, tzinfo=UTC)


def _promoted_file(tmp_path: Path) -> Path:
    """A champion file with one promotion p3 -> p3_roll60 in force."""
    path = tmp_path / CHAMP
    st = promotion.promote(promotion.empty_champion(), ROLL, {"n": 41}, now=T0)
    promotion.save_champion(path, st)
    return path


def test_save_champion_is_atomic(tmp_path: Path, monkeypatch):
    import os

    path = tmp_path / CHAMP
    promotion.save_champion(path, promotion.empty_champion())
    good = path.read_bytes()

    def boom(*a, **k):
        raise OSError("disk full")

    with monkeypatch.context() as m:
        m.setattr(os, "replace", boom)
        with pytest.raises(OSError):
            promotion.save_champion(path, {**promotion.empty_champion(), "champion": "ensemble"})
    assert path.read_bytes() == good and [p.name for p in tmp_path.iterdir()] == [CHAMP]


def _champion_block(since: str | None, promoted_now: bool = False) -> dict:
    return {
        "next_fix": {
            "champion": {
                "id": "p3",
                "since": since,
                "promoted_now": promoted_now,
                "unreadable": False,
                "history_len": 1,
                "last_change": {
                    "event": "promoted",
                    "from": "p3",
                    "to": "p3_roll60",
                    "at": since,
                },
            }
        }
    }


def test_t17_wording_routing_and_once_per_event():
    now_ist = datetime.now(notifications.IST)
    state = notifications.NotificationState()
    since = "2026-10-20T07:00:00+00:00"
    alert = notifications._check_t17_model_promoted(_champion_block(since, True), state, now_ist)
    assert alert is not None and alert.trigger_id == "T17" and alert.bypass_quiet
    text = alert.title + " " + alert.body
    assert "forecast" not in text.lower() and alert.title.isascii()
    assert "p3" in text and "p3_roll60" in text
    assert "python -m ml.promotion rollback" in alert.body
    # sent: silent; a LATER change (e.g. a rollback) alerts again; before it was sent, still alerts
    state.last_sent["T17"] = "2026-10-20T07:05:00+00:00"
    assert notifications._check_t17_model_promoted(_champion_block(since), state, now_ist) is None
    later = _champion_block("2026-11-01T07:00:00+00:00")
    later["next_fix"]["champion"]["last_change"] = {
        "event": "rolled_back",
        "from": "p3_roll60",
        "to": "p3",
    }
    again = notifications._check_t17_model_promoted(later, state, now_ist)
    assert again is not None and "p3_roll60" in again.body
    # never promoted: silent
    assert notifications._check_t17_model_promoted(_champion_block(None), state, now_ist) is None
    assert notifications._check_t17_model_promoted({}, state, now_ist) is None
    # private topic only
    assert "T17" not in PUBLIC_ALLOWLIST
    audience, topic = resolve_topic("T17", {"NTFY_TOPIC": "priv", "NTFY_TOPIC_PUBLIC": "pub"})
    assert audience != "public" and topic == "priv"


def test_t17_is_in_check_triggers_and_cannot_drop_other_triggers(monkeypatch):
    now_ist = datetime(2026, 10, 20, 14, 0, tzinfo=notifications.IST)
    fc = _champion_block("2026-10-20T07:00:00+00:00", True)
    fc["next_fix"]["demotion"] = {"demoted": True, "since": "2026-10-06T07:00:00+00:00"}

    def run() -> set[str]:
        alerts = notifications.check_triggers(
            fc, {"status": "missing"}, [], {}, notifications.NotificationState(), now_ist
        )
        return {a.trigger_id for a in alerts}

    assert {"T16", "T17"} <= run()

    def boom(*a, **k):
        raise TypeError("bad")

    monkeypatch.setattr(notifications, "_check_t17_model_promoted", boom)
    ids = run()
    assert "T16" in ids and "T17" not in ids


def test_h1_t17_wording_rollback_is_by_a_person_promotion_is_on_its_own():
    now_ist = datetime.now(notifications.IST)
    promoted = notifications._check_t17_model_promoted(
        _champion_block("2026-10-20T07:00:00+00:00", True),
        notifications.NotificationState(),
        now_ist,
    )
    assert promoted is not None and "on its own" in promoted.body
    rolled = _champion_block("2026-10-21T07:00:00+00:00")
    rolled["next_fix"]["champion"]["last_change"] = {
        "event": "rolled_back",
        "from": "p3_roll60",
        "to": "p3",
    }
    alert = notifications._check_t17_model_promoted(
        rolled, notifications.NotificationState(), now_ist
    )
    assert alert is not None
    assert "rolled back by a person" in alert.body and "on its own" not in alert.body


def test_h1_unpin_is_an_explicit_event_and_lets_the_rule_run_again(tmp_path: Path):
    path = _promoted_file(tmp_path)
    assert promotion.main(["rollback", "--data-dir", str(tmp_path)]) == 0
    pinned = promotion.load_champion(path)
    assert pinned["champion"] == "p3" and pinned["pinned"] is True
    assert pinned["history"][-1]["event"] == "rolled_back" and pinned["history"][-1]["pinned"]
    assert promotion.main(["unpin", "--data-dir", str(tmp_path)]) == 0
    st = promotion.load_champion(path)
    assert st["pinned"] is False and st["history"][-1]["event"] == "unpinned"
    # unpinned: the same challenger can be promoted again (the rule is free to run)
    again = promotion.promote(st, ROLL, {"n": 41})
    assert again["champion"] == ROLL
    # unpinning something that is not pinned changes nothing
    before = path.read_bytes()
    assert promotion.main(["unpin", "--data-dir", str(tmp_path)]) == 1
    assert path.read_bytes() == before
    with pytest.raises(ValueError):
        promotion.unpin(st)


def test_h1_rollback_without_a_promotion_after_the_last_rollback_raises_and_writes_nothing(
    tmp_path: Path,
):
    path = _promoted_file(tmp_path)
    promotion.main(["rollback", "--data-dir", str(tmp_path)])
    before = path.read_bytes()
    with pytest.raises(ValueError):
        promotion.rollback(promotion.load_champion(path))
    assert promotion.main(["rollback", "--data-dir", str(tmp_path)]) == 1
    assert path.read_bytes() == before
    # also: nothing was ever promoted
    path.unlink()
    assert promotion.main(["rollback", "--data-dir", str(tmp_path)]) == 1
    assert not path.exists()


def test_h1_show_prints_pinned_and_pinned_survives_load_save(tmp_path: Path, capsys):
    st = {**promotion.empty_champion(), "pinned": True}
    promotion.save_champion(tmp_path / CHAMP, st)
    assert promotion.load_champion(tmp_path / CHAMP)["pinned"] is True
    assert promotion.empty_champion()["pinned"] is False
    assert promotion.main(["show", "--data-dir", str(tmp_path)]) == 0
    assert '"pinned": true' in capsys.readouterr().out


@pytest.mark.parametrize("bad", ['"yes"', "1", "null", "[]"])
def test_h1_malformed_pinned_value_is_treated_as_pinned(tmp_path: Path, bad: str):
    (tmp_path / CHAMP).write_text(
        '{"champion": "p3", "since": null, "history": [], "pinned": ' + bad + "}", encoding="utf-8"
    )
    st = promotion.load_champion(tmp_path / CHAMP)
    assert st["pinned"] is True and not st.get("unreadable")


@pytest.mark.parametrize("hist", ["null", "[1, 2]", '["x"]', '"x"', '[{"event": "promoted"}, 3]'])
def test_load_champion_rejects_a_history_that_is_not_a_list_of_objects(tmp_path: Path, hist: str):
    (tmp_path / CHAMP).write_text(
        '{"champion": "p3_roll60", "since": null, "history": ' + hist + "}", encoding="utf-8"
    )
    st = promotion.load_champion(tmp_path / CHAMP)
    assert st["unreadable"] is True and st["champion"] == promotion.DEFAULT_CHAMPION


def test_load_champion_pinned_defaults_false_and_true_is_kept(tmp_path: Path):
    (tmp_path / CHAMP).write_text('{"champion": "p3", "history": []}', encoding="utf-8")
    st = promotion.load_champion(tmp_path / CHAMP)
    assert st["pinned"] is False and not st.get("unreadable")
    (tmp_path / CHAMP).write_text(
        json.dumps({"champion": "p3", "history": [], "pinned": True}), encoding="utf-8"
    )
    assert promotion.load_champion(tmp_path / CHAMP)["pinned"] is True


def test_cli_refuses_rollback_and_unpin_on_an_unreadable_file(tmp_path: Path):
    bad = tmp_path / CHAMP
    bad.write_text("{not json", encoding="utf-8")
    before = bad.read_bytes()
    assert promotion.main(["rollback", "--data-dir", str(tmp_path)]) == 1
    assert promotion.main(["unpin", "--data-dir", str(tmp_path)]) == 1
    assert bad.read_bytes() == before
