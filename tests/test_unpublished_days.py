"""A decision day whose live forecast never reached users must not count as a live day (outage of
2026-10-14/15: the data-sync token had expired). The fold is still recorded and scored."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from ml import live_monitor, nextfix, promotion

ROOT = Path(__file__).resolve().parent.parent
CUT = "2026-04-01"


def _synthetic(n_days: int = 120, seed: int = 3):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2026-01-05", periods=n_days)
    glob_close = 125000 * np.exp(np.cumsum(rng.normal(0, 0.01, n_days)))
    pm = 13000 * np.exp(0.5 * np.log(glob_close / glob_close[0]))
    pm[1:] = 13000 * np.exp(
        0.5 * np.log(glob_close[:-1] / glob_close[0]) + 0.5 * np.log(glob_close[1:] / glob_close[0])
    )
    ibja = pd.DataFrame({"date": days, "pm": pm, "am": np.nan})
    glob = pd.Series(glob_close, index=days)
    glob = glob.reindex(pd.date_range(days.min(), days.max(), freq="D")).ffill()
    return ibja, glob


def _pairs() -> pd.DataFrame:
    ibja, glob = _synthetic()
    return nextfix.build_pairs(ibja, glob)


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(nextfix, "MIN_TRAIN", 40)


def _write(tmp_path: Path, days: dict) -> Path:
    p = tmp_path / "unpublished.json"
    p.write_text(json.dumps({"days": days}), encoding="utf-8")
    return p


def test_the_committed_file_names_2026_10_14_with_its_evidence():
    data = json.loads((ROOT / "data/unpublished_decision_days.json").read_text(encoding="utf-8"))
    assert set(data["days"]) == {"2026-10-14"}
    assert "never" in data["days"]["2026-10-14"] and "401" in data["days"]["2026-10-14"]
    assert nextfix.unpublished_days() == {"2026-10-14"}


def test_missing_file_means_no_unpublished_days(tmp_path):
    assert nextfix.unpublished_days(tmp_path / "nope.json") == set()


@pytest.mark.parametrize("text", ["{not json", '{"days": [1, 2]}', "[]"])
def test_an_unreadable_file_fails_closed(tmp_path, text):
    p = tmp_path / "u.json"
    p.write_text(text, encoding="utf-8")
    assert nextfix.unpublished_days(p) is None


def _folds(monkeypatch, days):
    monkeypatch.setattr(nextfix, "unpublished_days", lambda path=None: days)
    pairs = _pairs()
    return nextfix.update_oos(pairs, [], nextfix.predict_p3, forward_from=CUT)


def test_an_unpublished_day_is_recorded_but_flagged_retro(monkeypatch):
    plain = _folds(monkeypatch, set())
    target = next(f["d0"] for f in plain if f["retro"] is False)
    folds = _folds(monkeypatch, {target})
    assert len(folds) == len(plain)  # still recorded and scored
    flagged = {f["d0"]: f["retro"] for f in folds}
    assert flagged[target] is True
    assert all(flagged[d] == f["retro"] for d, f in ((x["d0"], x) for x in plain) if d != target)


def test_an_unreadable_file_makes_no_new_fold_live(monkeypatch):
    folds = _folds(monkeypatch, None)
    assert folds and all(f["retro"] is True for f in folds)


def test_the_flagged_day_is_not_counted_by_the_rule_the_monitor_or_the_scorer(monkeypatch):
    plain = _folds(monkeypatch, set())
    target = next(f["d0"] for f in plain if f["retro"] is False)
    folds = _folds(monkeypatch, {target})
    n_plain = len(live_monitor.live_days(plain))
    assert len(live_monitor.live_days(folds)) == n_plain - 1
    assert target not in promotion.forward_days(folds, CUT)
    assert nextfix.evaluate(folds)["n_forward"] == n_plain - 1
