"""ADR 074 W2 ``p3_bandbucket`` (ml/nextfix_w2.py): the bucketed range is built as pre-registered,
counts only forward days P3 was live, and can never change the live forecast."""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from ml import nextfix, nextfix_w2

from tests.test_demotion_wired import _setup

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 29, 3, 0, tzinfo=UTC)


def _f(i: int, ret: float, err: float, vol: float = 0.01, retro: bool | None = False) -> dict:
    """A P3 fold on day i with forecast ``ret`` and a realised error of ``err`` x vol x pm0."""
    pm0 = 10000.0
    d0 = (date(2026, 6, 1) + timedelta(days=i)).isoformat()
    f = {
        "d0": d0,
        "d1": (date(2026, 6, 1) + timedelta(days=i + 1)).isoformat(),
        "pm0": pm0,
        "pm1": pm0 * math.exp(ret) + err * vol * pm0,
        "ret": ret,
        "vol": vol,
    }
    if retro is not None:
        f["retro"] = retro
    return f


def _two_regimes(n: int = 60) -> list[dict]:
    """Even days forecast big moves and miss by a lot; odd days forecast small moves, small misses."""
    return [
        _f(i, 0.02, 2.0 + 0.01 * i) if i % 2 == 0 else _f(i, 0.001, 0.2 + 0.001 * i)
        for i in range(n)
    ]


def test_buckets_split_at_the_median_and_use_their_own_quantile() -> None:
    prev = _two_regimes()
    scores = np.array([nextfix_w2._score(f) for f in prev])
    sizes = np.array([nextfix_w2._size(f) for f in prev])
    med = np.median(sizes)
    big = nextfix_w2.band_q(prev, _f(99, 0.03, 0.0))
    small = nextfix_w2.band_q(prev, _f(99, 0.0005, 0.0))
    assert big["bucket"] == "high" and small["bucket"] == "low"
    assert big["q_w2"] == pytest.approx(np.quantile(scores[sizes > med], 0.8))
    assert small["q_w2"] == pytest.approx(np.quantile(scores[sizes <= med], 0.8))
    assert big["q_p3"] == small["q_p3"] == pytest.approx(nextfix.conformal_q(prev))
    assert small["q_w2"] < big["q_p3"] < big["q_w2"]  # wider only where moves are forecast large


def test_a_fold_exactly_at_the_median_is_in_the_low_bucket() -> None:
    prev = _two_regimes()
    med = float(np.median([nextfix_w2._size(f) for f in prev]))
    at = _f(99, med * 0.01, 0.0)
    assert nextfix_w2.band_q(prev, at)["bucket"] == "low"


def test_only_the_most_recent_60_folds_are_used() -> None:
    old_noise = [_f(i, 0.02, 50.0) for i in range(40)]  # far outside the window
    recent = [_f(100 + i, f["ret"], 0.0) for i, f in enumerate(_two_regimes())]
    for i, f in enumerate(recent):
        f["pm1"] = _two_regimes()[i]["pm1"]
    with_old = nextfix_w2.band_q(old_noise + recent, _f(999, 0.03, 0.0))
    without = nextfix_w2.band_q(recent, _f(999, 0.03, 0.0))
    assert with_old == without


def test_a_bucket_under_15_folds_uses_the_pooled_quantile() -> None:
    prev = _two_regimes(28)  # 14 per bucket
    b = nextfix_w2.band_q(prev, _f(99, 0.03, 0.0))
    assert b["bucket"] == "high_pooled" and b["q_w2"] == b["q_p3"] == nextfix.conformal_q(prev)
    assert nextfix_w2.band_q(_two_regimes(30), _f(99, 0.03, 0.0))["bucket"] == "high"


def test_no_range_while_p3_has_none() -> None:
    assert nextfix_w2.band_q(_two_regimes(19), _f(99, 0.03, 0.0)) is None


def test_forward_days_only_from_the_day_after_the_first_run_and_only_live_p3_days() -> None:
    p3 = _two_regimes(150)  # d0 runs 2026-06-01 .. 2026-10-28
    first = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    rec = nextfix_w2.update(p3, None, first)
    assert rec["forward_from"] == "2026-10-21"
    assert [f["d0"] for f in rec["folds"]] == [f"2026-10-{d}" for d in range(21, 29)]
    # retro (a re-run, or a forecast never published) or unmarked P3 days are never counted
    p3[-1]["retro"] = True
    p3[-2].pop("retro")
    rec = nextfix_w2.update(p3, None, first)
    assert "2026-10-28" not in {f["d0"] for f in rec["folds"]}
    assert "2026-10-27" not in {f["d0"] for f in rec["folds"]}


def test_a_later_run_keeps_its_start_and_its_folds() -> None:
    p3 = _two_regimes(150)
    rec = nextfix_w2.update(p3[:-3], None, datetime(2026, 10, 20, tzinfo=UTC))
    kept = json.loads(json.dumps(rec))
    again = nextfix_w2.update(p3, kept, NOW)
    assert again["forward_from"] == "2026-10-21"
    assert again["folds"][: len(rec["folds"])] == rec["folds"]
    assert len(again["folds"]) == len(rec["folds"]) + 3


def test_each_fold_records_both_ranges_on_the_same_day() -> None:
    p3 = _two_regimes(150)
    rec = nextfix_w2.update(p3, None, datetime(2026, 10, 20, tzinfo=UTC))
    for w in rec["folds"]:
        src = next(f for f in p3 if f["d0"] == w["d0"])
        prev = [f for f in p3 if f["d0"] < w["d0"]]
        b = nextfix_w2.band_q(prev, src)
        s = nextfix_w2._score(src)
        assert (w["q_w2"], w["q_p3"], w["bucket"]) == (b["q_w2"], b["q_p3"], b["bucket"])
        assert w["covered_w2"] is (s <= b["q_w2"]) and w["covered_p3"] is (s <= b["q_p3"])
        assert w["width_p3"] == round(2 * b["q_p3"] * src["vol"] * src["pm0"], 2)


def test_an_unreadable_record_is_never_overwritten(tmp_path: Path) -> None:
    path = tmp_path / nextfix_w2.W2_PATH.name
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        nextfix_w2.run(_two_regimes(150), tmp_path, NOW)
    assert path.read_text(encoding="utf-8") == "{not json"


def test_the_run_log_shows_the_count_and_never_coverage(tmp_path: Path, caplog) -> None:
    caplog.set_level("INFO", logger="ml.nextfix_w2")
    nextfix_w2.run(_two_regimes(150), tmp_path, datetime(2026, 10, 20, tzinfo=UTC))
    line = next(r.getMessage() for r in caplog.records if "W2 band shadow" in r.getMessage())
    assert line == "W2 band shadow record: 8 forward folds since 2026-10-21"


@pytest.fixture
def _fast(monkeypatch):
    monkeypatch.setattr(nextfix, "MLP_MODELS", 1)
    monkeypatch.setattr(nextfix, "MLP_EPOCHS", 10)
    monkeypatch.setattr(nextfix, "MIN_TRAIN", 40)


def test_the_live_forecast_is_the_same_with_w2_failing(tmp_path: Path, monkeypatch, _fast) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _, macro, now = _setup(a)
    _setup(b)
    ok = nextfix.run(now=now, macro=macro, data_dir=a)
    assert (a / nextfix_w2.W2_PATH.name).exists()

    def boom(*_a, **_k):
        raise RuntimeError("w2 down")

    monkeypatch.setattr(nextfix_w2, "run", boom)
    down = nextfix.run(now=now, macro=macro, data_dir=b)
    assert down["forecast"] == ok["forecast"] and down["eval"] == ok["eval"]
    assert not (b / nextfix_w2.W2_PATH.name).exists()


def test_the_record_is_registered_encrypted_and_committed_by_check_price() -> None:
    import ast

    tree = ast.parse((ROOT / "scripts/data_crypt.py").read_text(encoding="utf-8"))
    registry = next(
        n.value
        for n in tree.body
        if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "REGISTRY"
    )
    logical = "data/" + nextfix_w2.W2_PATH.name
    assert logical in {k.value for k in registry.keys}
    assert f"/{logical}" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    wf = (ROOT / ".github/workflows/check-price.yml").read_text(encoding="utf-8")
    encrypt = next(ln for ln in wf.splitlines() if "data_crypt.py encrypt" in ln)
    assert logical in encrypt.split()
    assert re.search(rf"^\s*git add {re.escape(logical)} \|\| true$", wf, re.M)
