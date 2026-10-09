"""The "test on past days" chart's data: P3's own daily error and range hit, scores only (item 3)."""

from __future__ import annotations

import json
import math
from datetime import date, timedelta

from ml import backtest, nextfix

PM0 = 123456.0  # a distinctive level, so a leak of it into the output is easy to spot


def _folds(n: int = 45, retro_until: int = 0) -> list[dict]:
    out = []
    for i in range(n):
        d0 = date(2026, 8, 1) + timedelta(days=i)
        miss = (i % 5 - 2) * 40.0  # signed miss of the official rate vs the estimate, -80..+80
        out.append(
            {
                "d0": d0.isoformat(),
                "d1": (d0 + timedelta(days=1)).isoformat(),
                "pm0": PM0,
                "pm1": PM0 + miss,
                "y": 0.0,
                "ret": 0.0,  # the estimate is the held rate
                "p_up": 0.5,
                "vol": 0.0004,
                "retro": i < retro_until,
            }
        )
    return out


def test_no_row_until_enough_earlier_days_for_a_range() -> None:
    folds = _folds()
    s = nextfix.past_error_series(folds, 1.0, last=100)
    assert s is not None
    assert s["rows"][0]["day"] == folds[nextfix.MIN_CONFORMAL]["d0"]
    assert nextfix.past_error_series(folds[: nextfix.MIN_CONFORMAL], 1.0) is None
    assert nextfix.past_error_series([], 1.0) is None


def test_sign_is_estimate_minus_official_and_scaled_by_the_slope() -> None:
    folds = _folds()
    folds[30]["ret"] = math.log((PM0 + 100.0) / PM0)  # estimate 100 above the held rate
    folds[30]["pm1"] = PM0  # official rate came in at the held rate
    row = {r["day"]: r for r in nextfix.past_error_series(folds, 1.5, last=100)["rows"]}
    assert row[folds[30]["d0"]]["err"] == 150.0  # 100 too high x slope 1.5


def test_range_hit_is_the_live_rule_using_only_earlier_days() -> None:
    folds = _folds()
    folds[40]["pm1"] += 5000.0  # one big miss, far outside any range built from the earlier days
    s = nextfix.past_error_series(folds, 1.0, last=100)
    for r in s["rows"]:
        i = next(k for k, f in enumerate(folds) if f["d0"] == r["day"])
        f = folds[i]
        half = nextfix.conformal_q(folds[:i]) * f["vol"] * f["pm0"]
        assert r["in_range"] == (abs(f["pm0"] * math.exp(f["ret"]) - f["pm1"]) <= half)
    assert any(r["in_range"] for r in s["rows"]) and not all(r["in_range"] for r in s["rows"])


def test_a_later_day_never_changes_an_earlier_row() -> None:
    folds = _folds()
    before = nextfix.past_error_series(folds, 1.0, last=100)["rows"]
    changed = _folds()
    changed[-1]["pm1"] += 9999.0
    after = nextfix.past_error_series(changed, 1.0, last=100)["rows"]
    assert before[:-1] == after[:-1]
    assert before[-1] != after[-1]


def test_only_errors_and_flags_are_published() -> None:
    s = nextfix.past_error_series(_folds(retro_until=30), 1.0)
    assert s["schema"] == "p3_past_errors_v1"
    assert all(set(r) == {"day", "err", "in_range", "retro"} for r in s["rows"])
    assert str(int(PM0)) not in json.dumps(s)
    assert s["n"] == len(s["rows"]) <= 30
    assert s["n_in_range"] == sum(r["in_range"] for r in s["rows"])
    assert s["n_retro"] == sum(r["retro"] for r in s["rows"]) > 0
    assert s["mean_abs_err"] == round(sum(abs(r["err"]) for r in s["rows"]) / s["n"], 1)


def test_missing_record_or_slope_gives_no_series(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(nextfix, "P3_OOS_PATH", tmp_path / "absent.json")
    assert backtest.p3_past_errors() is None


def test_a_non_finite_error_is_skipped_never_published() -> None:
    folds = _folds()
    folds[35]["ret"] = float("nan")
    s = nextfix.past_error_series(folds, 1.0, last=100)
    assert folds[35]["d0"] not in {r["day"] for r in s["rows"]}
    json.loads(json.dumps(s, allow_nan=False))  # strict JSON: no NaN/Infinity anywhere
