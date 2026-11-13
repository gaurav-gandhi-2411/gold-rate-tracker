"""A stored fold with a 0 price must not take the live forecast down (P0, 2026-11-12).

PR #3780 stopped a 0 rate from entering new folds, but the live log then showed the remaining
cause: ``ml/nextfix.py conformal_q`` divides by ``vol * pm0`` and a fold already stored with
``pm0 == 0`` made ``evaluate`` raise ``float division by zero`` on every run. The fix sets such a
fold aside (kept in the file under ``quarantined_invalid``, never deleted) and guards the division.
No test reads the clock.
"""

from __future__ import annotations

import json
import logging
import math

import pytest
from ml import nextfix


def _fold(day: int, pm0: float = 13000.0, pm1: float = 13010.0) -> dict:
    return {
        "d0": f"2026-10-{day:02d}",
        "d1": f"2026-10-{day + 1:02d}",
        "pm0": pm0,
        "pm1": pm1,
        "y": math.log(pm1 / pm0) if pm0 > 0 and pm1 > 0 else 0.0,
        "ret": 0.0005,
        "p_up": 0.55,
        "vol": 0.004,
        "retro": False,
    }


def _good(n: int) -> list[dict]:
    return [_fold(1 + i, pm1=13000.0 + 5 * (i % 7)) for i in range(n)]


@pytest.mark.parametrize(
    ("overrides", "valid"),
    [
        ({}, True),
        ({"pm0": 0.0}, False),
        ({"pm1": 0.0}, False),
        ({"pm0": -5.0}, False),
        ({"pm0": float("nan")}, False),
        ({"pm1": float("inf")}, False),
        ({"ret": float("nan")}, False),
        ({"pm0": "13000"}, False),
    ],
)
def test_fold_is_valid_needs_real_prices(overrides, valid):
    f = {**_fold(1), **overrides}
    assert nextfix.fold_is_valid(f) is valid


def test_a_legacy_fold_without_the_price_keys_keeps_its_old_behaviour():
    assert nextfix.fold_is_valid({"d0": "2026-10-01", "y": 0.0})


def test_conformal_q_survives_a_stored_zero_price_fold():
    # the production failure: ZeroDivisionError at conformal_q with a pm0 == 0 fold in the window
    folds = _good(30)
    folds[10] = _fold(11, pm0=0.0, pm1=13010.0)
    q = nextfix.conformal_q(folds)
    assert q is not None and math.isfinite(q)


def test_load_oos_sets_the_bad_fold_aside_and_keeps_it_in_the_file(tmp_path, caplog):
    path = tmp_path / "oos.json"
    bad = _fold(11, pm0=0.0)
    folds = _good(25)
    folds.insert(10, bad)
    path.write_text(json.dumps({"schema_version": 1, "folds": folds}), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="ml.nextfix"):
        valid = nextfix.load_oos(path)
    assert len(valid) == 25 and all(nextfix.fold_is_valid(f) for f in valid)
    assert "1 fold(s)" in caplog.text and "2026-10-11" in caplog.text
    assert "13000" not in caplog.text  # days only, no prices, in the public log
    nextfix.save_oos(valid, path, nextfix.MODEL_VERSION)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert len(saved["folds"]) == 25
    assert saved["quarantined_invalid"] == [bad]  # set aside, not deleted
    # a later run loads the clean file, keeps the audit entry, and does not duplicate it
    again = nextfix.load_oos(path)
    nextfix.save_oos(again, path, nextfix.MODEL_VERSION)
    assert json.loads(path.read_text(encoding="utf-8"))["quarantined_invalid"] == [bad]


def test_evaluate_runs_on_the_loaded_record_that_had_a_zero_price_fold(tmp_path):
    path = tmp_path / "oos.json"
    folds = _good(40)
    folds[12] = _fold(13, pm0=0.0)
    path.write_text(json.dumps({"schema_version": 1, "folds": folds}), encoding="utf-8")
    ev = nextfix.evaluate(nextfix.load_oos(path))
    assert ev["ready"] is True and ev["n"] == 39
    assert math.isfinite(ev["mae_model"])


def test_a_clean_record_round_trips_unchanged_and_gains_no_quarantine_key(tmp_path):
    path = tmp_path / "oos.json"
    nextfix.save_oos(_good(22), path, nextfix.MODEL_VERSION)
    nextfix.save_oos(nextfix.load_oos(path), path, nextfix.MODEL_VERSION)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "quarantined_invalid" not in saved and len(saved["folds"]) == 22
