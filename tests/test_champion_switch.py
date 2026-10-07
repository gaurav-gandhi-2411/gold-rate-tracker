"""ADR 072 step 2: the live next-fix model is chosen by data/champion_state.json, and the frozen
promotion rule is applied after every run. End to end through ml.nextfix.run on synthetic data."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from ml import nextfix, notifications, promotion
from ml.notification_routing import PUBLIC_ALLOWLIST, resolve_topic

from tests.test_demotion_wired import _setup, _state

CHAMP = promotion.CHAMPION_FILE
ROLL = "nextfix_p3_roll60_v1"
MONDAY = "nextfix_p3_monday_v1"


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(nextfix, "MLP_MODELS", 1)
    monkeypatch.setattr(nextfix, "MLP_EPOCHS", 10)
    monkeypatch.setattr(nextfix, "MIN_TRAIN", 40)


def _write_champion(tmp_path: Path, cid: str, history: list | None = None) -> None:
    state = {**promotion.empty_champion(), "champion": cid, "since": "2026-10-08T00:00:00+00:00"}
    state["history"] = history or []
    (tmp_path / CHAMP).write_text(json.dumps(state), encoding="utf-8")


def _prepared(tmp_path: Path):
    """Synthetic data plus one default run, so every record file (p3, ensemble, variants) exists."""
    _, macro, now = _setup(tmp_path)
    base = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    return macro, now, base


def _forward_everywhere(monkeypatch) -> None:
    """The synthetic days are in January: count them as forward days for the promotion step. Call
    BEFORE ``_prepared`` (the ``retro`` flag is written when a fold is first recorded). The real
    rule is also stubbed to "nobody wins" so only the test's own verdict can promote."""
    monkeypatch.setattr(nextfix, "P3_FORWARD_FROM", "2026-01-01")
    monkeypatch.setitem(promotion.RULE, "common_start", "2026-01-01")
    monkeypatch.setattr(promotion, "decide", lambda *a, **k: {"promote": None, "challengers": {}})


def _winner(to: str = "p3_roll60"):
    def decide(champion_id, records, rule=promotion.RULE):
        return {
            "champion": champion_id,
            "promote": to,
            "challengers": {to: {"n": 41, "gain": 0.07, "p_better": 0.01, "status": "scored"}},
        }

    return decide


def _fc_block(out: dict) -> dict:
    return {"next_fix": {"active": True, "champion": out["champion"]}}


def _variant_folds(tmp_path: Path, name: str) -> list[dict]:
    data = json.loads((tmp_path / nextfix.P3_VARIANTS_PATH.name).read_text())
    return data["variants"][name]


# --- 1. the default champion is exactly the old live path ---------------------------------------
def test_champion_p3_is_identical_to_the_old_live_path(tmp_path: Path):
    macro, now, out = _prepared(tmp_path)
    assert out["champion"]["id"] == "p3" and out["champion"]["promoted_now"] is False
    assert out["champion"]["unreadable"] is False
    full = nextfix.load_ibja_full(tmp_path / "ibja_rates.parquet")
    glob = nextfix.global_series(macro, tmp_path / nextfix.LABEL_PATH.name)
    p3 = nextfix.load_oos(tmp_path / nextfix.P3_OOS_PATH.name)
    ens = nextfix.load_oos(tmp_path / nextfix.OOS_PATH.name)
    ev = nextfix.evaluate(p3, nextfix.MODEL_VERSION)
    ref = nextfix.forecast(full, glob, p3, now, out["windows"], ens, demoted=False)
    assert out["forecast"] == ref and out["eval"] == ev
    assert out["forecast"]["model_version"] == nextfix.MODEL_VERSION
    assert out["forecast"]["shadow"]["model_version"] == nextfix.ENSEMBLE_VERSION
    assert _state(tmp_path)["model_version"] == nextfix.MODEL_VERSION
    # an explicit p3 champion file changes nothing
    _write_champion(tmp_path, "p3")
    again = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    assert again["forecast"] == out["forecast"] and again["eval"] == out["eval"]


# --- 2. a variant champion uses its own folds, label, predictor and demotion state --------------
def test_champion_p3_roll60_uses_variant_folds_label_and_fresh_demotion_state(tmp_path: Path):
    macro, now, base = _prepared(tmp_path)
    old = _state(tmp_path)
    old.update(demoted=True, since="2026-10-01T00:00:00+00:00", reasons=[{"rule": "error"}])
    (tmp_path / nextfix.STATE_FILE).write_text(json.dumps(old))  # P3 is demoted ...
    _write_champion(tmp_path, "p3_roll60")
    out = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    fc = out["forecast"]
    # ... the new champion starts clean: same rules, its own sticky state keyed by its version
    assert out["demotion"]["demoted"] is False and out["demotion"]["checked"] is True
    assert _state(tmp_path)["model_version"] == ROLL and _state(tmp_path)["demoted"] is False
    assert fc["mode"] == "after_us_close" and fc["model_version"] == ROLL
    folds = _variant_folds(tmp_path, "p3_roll60")
    assert out["eval"] == nextfix.evaluate(folds, ROLL)
    full = nextfix.load_ibja_full(tmp_path / "ibja_rates.parquet")
    glob = nextfix.global_series(macro, tmp_path / nextfix.LABEL_PATH.name)
    d_pm = full.dropna(subset=["pm"])["date"].iloc[-1]
    ref = nextfix._model_forecast(full, glob, folds, d_pm, nextfix.predict_p3_roll60, ROLL)
    assert {k: fc[k] for k in ref} == ref
    assert fc["pred"] != base["forecast"]["pred"]  # a different slope really was used
    # shadow is the previous incumbent's analogue: P3 on P3's own record, labelled
    sh = fc["shadow"]
    assert sh["model_version"] == nextfix.MODEL_VERSION
    p3 = nextfix.load_oos(tmp_path / nextfix.P3_OOS_PATH.name)
    assert out["shadow_eval"] == nextfix.evaluate(p3, nextfix.MODEL_VERSION)


def test_champion_p3_monday_label_and_folds(tmp_path: Path):
    macro, now, _ = _prepared(tmp_path)
    _write_champion(tmp_path, "p3_monday")
    out = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    assert out["forecast"]["model_version"] == MONDAY
    assert out["eval"] == nextfix.evaluate(_variant_folds(tmp_path, "p3_monday"), MONDAY)
    assert _state(tmp_path)["model_version"] == MONDAY


def test_champion_ensemble(tmp_path: Path):
    macro, now, _ = _prepared(tmp_path)
    _write_champion(tmp_path, "ensemble")
    out = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    ens = nextfix.load_oos(tmp_path / nextfix.OOS_PATH.name)
    assert out["forecast"]["model_version"] == nextfix.ENSEMBLE_VERSION
    assert out["eval"] == nextfix.evaluate(ens, nextfix.ENSEMBLE_VERSION)
    assert out["forecast"]["shadow"]["model_version"] == nextfix.MODEL_VERSION
    assert _state(tmp_path)["model_version"] == nextfix.ENSEMBLE_VERSION


# --- 3. unreadable or unknown champion file: P3, flagged, never a challenger --------------------
@pytest.mark.parametrize("bad", ["{nope", "[]", '{"champion": "hourly"}', '{"champion": "zzz"}'])
def test_unreadable_champion_file_is_p3_flagged_and_never_promotes(
    tmp_path: Path, monkeypatch, caplog, bad: str
):
    _forward_everywhere(monkeypatch)
    macro, now, _ = _prepared(tmp_path)
    called: list[str] = []
    monkeypatch.setattr(promotion, "decide", lambda *a, **k: called.append("x") or _winner()(*a))
    (tmp_path / CHAMP).write_text(bad, encoding="utf-8")
    with caplog.at_level(logging.ERROR):
        out = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    assert out["champion"]["id"] == "p3" and out["champion"]["unreadable"] is True
    assert out["champion"]["promoted_now"] is False and not called
    assert out["forecast"]["model_version"] == nextfix.MODEL_VERSION
    assert (tmp_path / CHAMP).read_text(encoding="utf-8") == bad  # the evidence is kept
    assert any(r.levelno == logging.ERROR for r in caplog.records)


# --- 4. promotion: file written, takes effect next run, T17 once --------------------------------
def test_promotion_happy_path_writes_file_takes_effect_next_run_and_alerts_once(
    tmp_path: Path, monkeypatch, caplog
):
    _forward_everywhere(monkeypatch)
    macro, now, base = _prepared(tmp_path)
    monkeypatch.setattr(promotion, "decide", _winner("p3_roll60"))
    with caplog.at_level(logging.WARNING):
        out = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    ch = out["champion"]
    assert ch["promoted_now"] is True and ch["id"] == "p3" and ch["history_len"] == 1
    # one run, one model: THIS run's forecast is still P3's
    assert out["forecast"]["model_version"] == nextfix.MODEL_VERSION
    assert out["forecast"] == base["forecast"]
    saved = json.loads((tmp_path / CHAMP).read_text(encoding="utf-8"))
    assert saved["champion"] == "p3_roll60" and saved["since"] == ch["since"]
    ev = saved["history"][0]
    assert (ev["event"], ev["from"], ev["to"]) == ("promoted", "p3", "p3_roll60")
    assert ev["evidence"]["gain"] == 0.07
    assert [p.name for p in tmp_path.glob("*.tmp")] == []
    assert any("promot" in r.getMessage().lower() for r in caplog.records)
    # T17 fires once
    state = notifications.NotificationState()
    now_ist = datetime.now(notifications.IST)
    alert = notifications._check_t17_model_promoted(_fc_block(out), state, now_ist)
    assert alert is not None and alert.trigger_id == "T17"
    state.last_sent["T17"] = datetime.now(UTC).isoformat()
    assert notifications._check_t17_model_promoted(_fc_block(out), state, now_ist) is None
    # next run: the new champion is live, no new promotion event, no second alert
    monkeypatch.setattr(promotion, "decide", lambda *a, **k: {"promote": None, "challengers": {}})
    out2 = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    assert out2["forecast"]["model_version"] == ROLL
    assert out2["champion"]["id"] == "p3_roll60" and out2["champion"]["promoted_now"] is False
    assert out2["champion"]["history_len"] == 1
    assert notifications._check_t17_model_promoted(_fc_block(out2), state, now_ist) is None


def test_no_promotion_without_the_champions_own_forward_days(tmp_path: Path, monkeypatch):
    macro, now, _ = _prepared(tmp_path)  # default RULE start 2026-10-07: synthetic days are earlier
    called: list[str] = []
    monkeypatch.setattr(promotion, "decide", lambda *a, **k: called.append("x") or _winner()(*a))
    out = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    assert not called and out["champion"]["promoted_now"] is False
    assert not (tmp_path / CHAMP).exists()


def test_a_failure_inside_the_promotion_step_changes_nothing(tmp_path: Path, monkeypatch, caplog):
    _forward_everywhere(monkeypatch)
    macro, now, base = _prepared(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("evaluator bug")

    monkeypatch.setattr(promotion, "decide", boom)
    with caplog.at_level(logging.ERROR):
        out = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    assert out["forecast"] == base["forecast"] and out["champion"]["promoted_now"] is False
    assert not (tmp_path / CHAMP).exists()
    assert any(r.levelno >= logging.ERROR for r in caplog.records)
    # a failing write leaves an existing champion file untouched too
    _write_champion(tmp_path, "p3")
    before = (tmp_path / CHAMP).read_bytes()
    monkeypatch.setattr(promotion, "decide", _winner())
    monkeypatch.setattr(promotion, "save_champion", boom)
    out2 = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    assert out2["champion"]["promoted_now"] is False and out2["forecast"] == base["forecast"]
    assert (tmp_path / CHAMP).read_bytes() == before


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


# --- 5. rollback --------------------------------------------------------------------------------
def test_rollback_restores_the_old_champion_for_the_next_run(tmp_path: Path, monkeypatch):
    _forward_everywhere(monkeypatch)
    macro, now, base = _prepared(tmp_path)
    monkeypatch.setattr(promotion, "decide", _winner("p3_roll60"))
    nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    monkeypatch.setattr(promotion, "decide", lambda *a, **k: {"promote": None, "challengers": {}})
    assert nextfix.run(now=now, macro=macro, data_dir=tmp_path)["forecast"]["model_version"] == ROLL
    assert promotion.main(["rollback", "--data-dir", str(tmp_path)]) == 0
    out = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    assert out["forecast"]["model_version"] == nextfix.MODEL_VERSION
    assert out["forecast"] == base["forecast"]
    assert out["champion"]["id"] == "p3" and out["champion"]["history_len"] == 2
    assert out["champion"]["last_change"]["event"] == "rolled_back"


# --- 6. T17 -------------------------------------------------------------------------------------
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


# --- 7. inference exposes the champion block ----------------------------------------------------
def test_inference_next_fix_block_carries_the_champion(tmp_path: Path, monkeypatch):
    from ml import inference

    macro, now, _ = _prepared(tmp_path)
    monkeypatch.setattr(inference, "DATA_DIR", tmp_path)
    monkeypatch.setattr("ml.macro.load_macro_features", lambda: macro)  # not the real macro cache
    block = inference._next_fix_block(now, 14000, {"slope": 1.0, "valid": True})
    ch = block["champion"]
    assert set(ch) >= {"id", "since", "promoted_now", "unreadable", "history_len"}
    assert ch["id"] == "p3" and ch["promoted_now"] is False and block["mode"] == "after_us_close"
    json.dumps(block)  # JSON-safe
    assert "shadow_model_version" not in block["shadow_ensemble"]  # p3: unchanged shape
    _write_champion(tmp_path, "p3_roll60")
    block2 = inference._next_fix_block(now, 14000, {"slope": 1.0, "valid": True})
    assert block2["model_version"] == ROLL and block2["champion"]["id"] == "p3_roll60"
    assert block2["shadow_ensemble"]["shadow_model_version"] == nextfix.MODEL_VERSION
