"""ADR 072 step 2: the live next-fix model is chosen by data/champion_state.json, and the frozen
promotion rule is applied after every run. End to end through ml.nextfix.run on synthetic data."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from ml import nextfix, notifications, promotion

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


def _mstate(tmp_path: Path, version: str) -> dict:
    """The sticky demotion state of one model (ADR 072: per-model files)."""
    return json.loads((tmp_path / nextfix.demotion_state_file(version)).read_text())


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
    assert _mstate(tmp_path, ROLL)["demoted"] is False
    assert _state(tmp_path) == old  # P3's own file is untouched by the other model
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
    assert _mstate(tmp_path, MONDAY)["model_version"] == MONDAY


def test_champion_ensemble(tmp_path: Path):
    macro, now, _ = _prepared(tmp_path)
    _write_champion(tmp_path, "ensemble")
    out = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    ens = nextfix.load_oos(tmp_path / nextfix.OOS_PATH.name)
    assert out["forecast"]["model_version"] == nextfix.ENSEMBLE_VERSION
    assert out["eval"] == nextfix.evaluate(ens, nextfix.ENSEMBLE_VERSION)
    assert out["forecast"]["shadow"]["model_version"] == nextfix.MODEL_VERSION
    ens_state = _mstate(tmp_path, nextfix.ENSEMBLE_VERSION)
    assert ens_state["model_version"] == nextfix.ENSEMBLE_VERSION


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


# --- 8. review fixes (verifier findings on PR #2507) --------------------------------------------
def _promote_then_settle(tmp_path: Path, monkeypatch):
    """Promote p3 -> p3_roll60, run once as roll60, return (macro, now)."""
    _forward_everywhere(monkeypatch)
    macro, now, _ = _prepared(tmp_path)
    monkeypatch.setattr(promotion, "decide", _winner("p3_roll60"))
    nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    monkeypatch.setattr(promotion, "decide", lambda *a, **k: {"promote": None, "challengers": {}})
    nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    return macro, now


def _champ(tmp_path: Path) -> dict:
    return json.loads((tmp_path / CHAMP).read_text(encoding="utf-8"))


def test_h1_rollback_pins_and_the_rule_does_not_repromote_the_same_challenger(
    tmp_path: Path, monkeypatch
):
    macro, now = _promote_then_settle(tmp_path, monkeypatch)
    assert promotion.main(["rollback", "--data-dir", str(tmp_path)]) == 0
    st = _champ(tmp_path)
    assert st["champion"] == "p3" and st["pinned"] is True
    assert st["history"][-1]["event"] == "rolled_back" and st["history"][-1]["pinned"] is True
    before = (tmp_path / CHAMP).read_bytes()
    monkeypatch.setattr(promotion, "decide", _winner("p3_roll60"))  # same evidence again
    out = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    assert out["champion"]["id"] == "p3" and out["champion"]["promoted_now"] is False
    assert out["champion"]["pinned"] is True
    assert (tmp_path / CHAMP).read_bytes() == before


def test_h2_demotion_state_is_per_model_and_survives_champion_changes(tmp_path: Path):
    macro, now, _ = _prepared(tmp_path)
    assert nextfix.demotion_state_file(nextfix.MODEL_VERSION) == "model_demotion_state.json"
    assert nextfix.demotion_state_file(ROLL) == f"model_demotion_state__{ROLL}.json"
    p3 = _state(tmp_path)
    p3.update(demoted=True, since="2026-10-01T00:00:00+00:00", reasons=[{"rule": "error"}])
    (tmp_path / nextfix.STATE_FILE).write_text(json.dumps(p3))
    p3_bytes = (tmp_path / nextfix.STATE_FILE).read_bytes()
    # roll60 becomes champion: its own file, P3's file untouched
    _write_champion(tmp_path, "p3_roll60")
    nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    assert (tmp_path / nextfix.STATE_FILE).read_bytes() == p3_bytes
    assert _mstate(tmp_path, ROLL)["model_version"] == ROLL
    # P3 returns as champion: it finds its OWN sticky demotion, not a fresh start
    _write_champion(tmp_path, "p3")
    out = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    assert out["demotion"]["demoted"] is True
    assert out["forecast"]["model_version"].startswith("hold_latest_fix")


@pytest.mark.parametrize("how", ["demoted", "unreadable"])
def test_h2_a_challenger_whose_own_state_is_demoted_or_unreadable_is_not_promoted(
    tmp_path: Path, monkeypatch, caplog, how: str
):
    from ml import demotion

    _forward_everywhere(monkeypatch)
    macro, now, _ = _prepared(tmp_path)
    path = tmp_path / nextfix.demotion_state_file(ROLL)
    if how == "demoted":
        st = {**demotion.empty_state(ROLL), "demoted": True, "since": "2026-10-01T00:00:00+00:00"}
        path.write_text(json.dumps(st))
    else:
        path.write_text("{nope")
    monkeypatch.setattr(promotion, "decide", _winner("p3_roll60"))
    with caplog.at_level(logging.INFO):
        out = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    assert out["champion"]["promoted_now"] is False and not (tmp_path / CHAMP).exists()
    assert any("p3_roll60" in r.getMessage() for r in caplog.records)


def test_h2_workflow_stages_per_model_state_files_with_a_guarded_line():
    root = Path(__file__).resolve().parent.parent
    yml = (root / ".github/workflows/check-price.yml").read_text(encoding="utf-8")
    assert 'compgen -G "data/model_demotion_state__*.json"' in yml
    assert "git add data/model_demotion_state__*.json || true" in yml


@pytest.mark.parametrize("hist", ["null", "[1, 2]", '"x"', "{}", '[{"event": "promoted"}, 3]'])
def test_m1_malformed_history_in_a_valid_champion_file_is_unreadable_not_a_crash(
    tmp_path: Path, hist: str
):
    (tmp_path / CHAMP).write_text(
        '{"champion": "p3_roll60", "since": null, "history": ' + hist + "}", encoding="utf-8"
    )
    st = promotion.load_champion(tmp_path / CHAMP)
    assert st["unreadable"] is True and st["champion"] == "p3"
    macro, now, _ = _prepared(tmp_path)
    out = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    assert out["champion"]["unreadable"] is True and out["champion"]["id"] == "p3"
    assert out["forecast"]["model_version"] == nextfix.MODEL_VERSION
    block = nextfix._promotion_step(tmp_path, {**st, "history": None}, {}, now)  # never raises
    assert block["unreadable"] is True


def test_m2_a_missing_champion_record_falls_back_to_p3_loudly_without_writing(
    tmp_path: Path, monkeypatch, caplog
):
    macro, now, base = _prepared(tmp_path)
    _write_champion(tmp_path, "p3_roll60")
    before = (tmp_path / CHAMP).read_bytes()
    monkeypatch.setattr(nextfix, "load_variant_folds", lambda d: {"p3_roll60": []})
    with caplog.at_level(logging.ERROR):
        out = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    assert out["forecast"]["model_version"] == nextfix.MODEL_VERSION
    assert out["forecast"] == base["forecast"]
    ch = out["champion"]
    assert ch["id"] == "p3_roll60" and ch["fallback"] == "record_missing"
    assert ch["effective_id"] == "p3"
    assert (tmp_path / CHAMP).read_bytes() == before
    assert any(r.levelno == logging.ERROR for r in caplog.records)
    monkeypatch.setattr(nextfix, "load_variant_folds", lambda d: {})  # absent altogether
    again = nextfix.run(now=now, macro=macro, data_dir=tmp_path)
    assert again["champion"]["fallback"] == "record_missing"


def test_m2_fallback_and_pin_are_published_by_inference(tmp_path: Path, monkeypatch):
    from ml import inference

    macro, now, _ = _prepared(tmp_path)
    monkeypatch.setattr(inference, "DATA_DIR", tmp_path)
    monkeypatch.setattr("ml.macro.load_macro_features", lambda: macro)
    monkeypatch.setattr(nextfix, "load_variant_folds", lambda d: {})
    _write_champion(tmp_path, "p3_roll60")
    ch = inference._next_fix_block(now, 14000, {"slope": 1.0, "valid": True})["champion"]
    assert ch["fallback"] == "record_missing" and ch["effective_id"] == "p3"
    assert ch["pinned"] is False
