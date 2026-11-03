"""FOR_GG item 10: every live-capable record's days carry an explicit ``retro`` flag.

Rule v7's ``forward_days`` (ml/promotion.py, frozen) counts a day as live unless it is flagged
``retro`` True; every other reader counts only an explicit False. The shadow ensemble's record was
written without flags, so the two readers disagreed on all its days. The fix makes the ambiguity
impossible without touching the rule: the ensemble's days are flagged by P3's own rule (same
``forward_from``, same unpublished days), old days are back-filled the same way, and a report-only
count logs any live-capable record still missing a flag.

Rule v7 compares a challenger with the champion only on days BOTH issued live, so with P3 as
champion the flagged ensemble gives exactly the same comparison as the unflagged one (tested below).
"""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd
import pytest
from ml import nextfix, promotion

CUT = "2026-04-01"


def _pairs() -> pd.DataFrame:
    rng = np.random.default_rng(3)
    n = 120
    days = pd.bdate_range("2026-01-05", periods=n)
    g = 125000 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    pm = 13000 * np.exp(0.5 * np.log(g / g[0]))
    pm[1:] = 13000 * np.exp(0.5 * np.log(g[:-1] / g[0]) + 0.5 * np.log(g[1:] / g[0]))
    ibja = pd.DataFrame({"date": days, "pm": pm, "am": np.nan})
    glob = pd.Series(g, index=days).reindex(pd.date_range(days.min(), days.max(), freq="D"))
    return nextfix.build_pairs(ibja, glob.ffill())


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(nextfix, "MIN_TRAIN", 40)


def _records(monkeypatch, unpublished: set[str]):
    monkeypatch.setattr(nextfix, "unpublished_days", lambda path=None: unpublished)
    pairs = _pairs()
    champ = nextfix.update_oos(pairs, [], nextfix.predict_p3, forward_from=CUT)
    # the ensemble as it was written before: no forward_from, so no flags at all
    raw = nextfix.update_oos(pairs, [], nextfix.predict_p3_roll60)
    made_live = nextfix.update_oos(pairs, [], nextfix.predict_p3_roll60, forward_from=CUT)
    return champ, raw, made_live


def test_the_old_ensemble_record_had_no_flags_so_the_readers_disagreed(monkeypatch):
    _, raw, _ = _records(monkeypatch, set())
    assert raw and all("retro" not in f for f in raw)
    assert len(promotion.forward_days(raw, CUT)) > 0  # rule v7: every day after CUT is live
    assert nextfix.evaluate(raw)["n_forward"] == 0  # everyone else: no day is live


def test_back_filled_flags_equal_the_flags_a_new_fold_gets(monkeypatch):
    champ, raw, made_live = _records(monkeypatch, set())
    unpublished = {next(f["d0"] for f in champ if f["retro"] is False)}
    _, _, made_live = _records(monkeypatch, unpublished)
    filled = nextfix.flag_unflagged(raw, CUT, unpublished)
    assert [(f["d0"], f["retro"]) for f in filled] == [(f["d0"], f["retro"]) for f in made_live]
    assert [{k: v for k, v in f.items() if k != "retro"} for f in filled] == raw  # nothing else


def test_a_fold_that_already_has_a_flag_is_left_alone():
    folds = [{"d0": "2026-01-02", "retro": False}, {"d0": "2026-09-09", "retro": True}]
    assert nextfix.flag_unflagged(folds, CUT, set()) == folds
    # a non-boolean flag is not a flag: re-derived like a missing one
    assert nextfix.flag_unflagged([{"d0": "2026-09-09", "retro": "no"}], CUT, set()) == [
        {"d0": "2026-09-09", "retro": False}
    ]
    # an unreadable unpublished-days file: nothing back-filled counts as live (fail closed)
    assert nextfix.flag_unflagged([{"d0": "2026-09-09"}], CUT, None)[0]["retro"] is True


def test_rule_v7_gives_the_same_answer_with_the_flags_as_without(monkeypatch):
    champ, raw, _ = _records(monkeypatch, set())
    unpublished = {
        f["d0"] for f in champ if f["retro"] is False and f["d0"] in {g["d0"] for g in raw}
    }
    unpublished = {sorted(unpublished)[3]}  # one live day of the champion becomes unpublished
    champ, raw, _ = _records(monkeypatch, unpublished)
    filled = nextfix.flag_unflagged(raw, CUT, unpublished)
    assert nextfix.evaluate(filled)["n_forward"] > 0  # the other readers now see live days
    before = promotion.compare(champ, raw, since=CUT, cid="ensemble")
    after = promotion.compare(champ, filled, since=CUT, cid="ensemble")
    assert before == after
    assert before["n"] > 0


def test_the_report_counts_only_live_capable_records_and_never_raises(monkeypatch, caplog):
    champ, raw, _ = _records(monkeypatch, set())
    got = nextfix.report_unflagged({"p3": champ, "ensemble": raw, "p3_hourly": raw})
    assert got == {"ensemble": len(raw)}
    assert "RETRO FLAGS MISSING (report-only)" in caplog.text
    assert nextfix.report_unflagged({"p3": champ}) == {}
    assert nextfix.report_unflagged({"p3": "not a list"}) == {}  # report-only: no raise


def test_run_flags_the_ensemble_by_p3s_rule_and_reports():
    src = inspect.getsource(nextfix.run)
    assert "flag_unflagged(load_oos(oos_path), P3_FORWARD_FROM, unpublished_days())" in src
    assert "update_oos(pairs, shadow_old, forward_from=P3_FORWARD_FROM)" in src
    assert "report_unflagged(records)" in src
