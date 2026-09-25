"""Tests for scripts/run_kalman_shadow.py (ADR 062: Kalman shadow scored only when Tanishq is not
fresh). Synthetic data only."""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "run_kalman_shadow", _ROOT / "scripts" / "run_kalman_shadow.py"
)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["run_kalman_shadow"] = mod
_spec.loader.exec_module(mod)

LEVEL = 14000.0


def _world(days: int = 40, seed: int = 42) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2026-08-01", periods=days, freq="D")
    lvl = LEVEL * np.exp(np.cumsum(rng.normal(0, 0.006, days)))
    ts, v = [], []
    for d, x in zip(dates, lvl, strict=True):
        for h in (4, 10):
            ts.append(d + pd.Timedelta(hours=h))
            v.append(float(round(x * (1 + rng.normal(0, 0.001)))))
    tanishq = pd.DataFrame({"ts": pd.DatetimeIndex(ts), "v": v})
    wd = dates[dates.dayofweek < 5]
    ibja = pd.DataFrame(
        {"am": lvl[dates.dayofweek < 5] / 1.012, "pm": lvl[dates.dayofweek < 5] / 1.0138},
        index=wd,
    )
    rows = []
    for d, x in zip(dates, lvl, strict=True):
        for src, mk in (("grt", 0.99), ("malabar", 1.01)):
            for h in (5, 14):
                rows.append(
                    {
                        "source": src,
                        "cap": d + pd.Timedelta(hours=h),
                        "as_of_date": str(d.date()),
                        "rate_22k": x * mk,
                        "obs_day": d,
                    }
                )
    snaps = pd.DataFrame(rows)
    comex = pd.Series(lvl[dates.dayofweek < 5] * 0.97, index=wd)
    return {"tanishq": tanishq, "ibja": ibja, "snaps": snaps, "comex": comex}


def _entry(run: pd.Timestamp, w: dict[str, Any]) -> dict[str, Any]:
    return mod.shadow_entry(run, w["tanishq"], w["ibja"], w["snaps"], w["comex"])


def test_strict_inputs_use_only_what_was_known_strictly_before_the_run() -> None:
    w = _world()
    run = pd.Timestamp("2026-09-08 10:00")  # a Tanishq reading sits exactly at R: excluded
    inp = mod.strict_inputs(run, w["tanishq"], w["ibja"], w["snaps"], w["comex"])
    prov = inp["provenance"]
    assert pd.Timestamp(prov["max_input_known_at"]).tz_localize(None) < run
    assert inp["last_ts"] == pd.Timestamp("2026-09-08 04:00")
    run_day = {(x["source"], x["known_at"]) for x in prov["run_day_inputs"]}
    assert ("ibja_am", "2026-09-08T06:30:00Z") in run_day  # published 06:30 < 10:00
    assert not any(s == "ibja_pm" for s, _ in run_day)  # 11:30 is after R
    assert ("grt", "2026-09-08T05:00:00Z") in run_day and (
        "grt",
        "2026-09-08T14:00:00Z",
    ) not in run_day
    assert ("comex", "2026-09-08T00:00:00Z") in run_day  # close of 09-07 known 09-08 00:00


def test_nowcast_does_not_move_when_anything_after_the_run_changes() -> None:
    w = _world()
    run = pd.Timestamp("2026-09-07 20:00")
    a = _entry(run, w)
    w2 = {k: v.copy() for k, v in w.items()}
    late = w2["tanishq"]["ts"] >= run
    w2["tanishq"].loc[late, "v"] *= 1.2
    w2["ibja"].loc[w2["ibja"].index > run.normalize()] *= 1.2  # published after R
    w2["snaps"].loc[w2["snaps"]["cap"] >= run, "rate_22k"] *= 1.2
    b = _entry(run, w2)
    for k in ("kalman_minus_last_rs_g", "ibja_markup_minus_last_rs_g", "sd_log", "inputs"):
        assert a[k] == b[k]


@pytest.mark.parametrize(("hours", "fresh"), [(7.9, True), (8.0, True), (8.1, False)])
def test_fresh_uses_the_site_8h_rule_at_the_run_moment(hours: float, fresh: bool) -> None:
    w = _world()
    run = pd.Timestamp("2026-09-05 10:00") + pd.Timedelta(hours=hours)  # last reading 10:00
    w["tanishq"] = w["tanishq"][w["tanishq"]["ts"] <= pd.Timestamp("2026-09-05 10:00")]
    e = _entry(run, w)
    assert e["tanishq_fresh"] is fresh
    assert e["last_tanishq_age_h"] == pytest.approx(hours, abs=1e-3)
    assert mod.STALE_THRESHOLD_H == 8.0


def test_entry_carries_no_absolute_price() -> None:
    w = _world()
    e = _entry(pd.Timestamp("2026-09-06 22:00"), w)

    def numbers(x: Any) -> list[float]:
        if isinstance(x, dict):
            return [n for v in x.values() for n in numbers(v)]
        if isinstance(x, list):
            return [n for v in x for n in numbers(v)]
        return [float(x)] if isinstance(x, int | float) and not isinstance(x, bool) else []

    assert all(abs(n) < LEVEL * 0.5 for n in numbers(e))  # no Rs/g level anywhere in the entry
    assert abs(e["kalman_minus_last_rs_g"]) < 500 and e["ibja_markup_minus_last_rs_g"] is not None


def _tan(rows: list[tuple[str, float]]) -> pd.DataFrame:
    ts = pd.to_datetime([r[0] for r in rows], utc=True).tz_localize(None)
    return pd.DataFrame({"ts": ts, "v": [r[1] for r in rows]})


def _row(run: str, last_ts: str, fresh: bool, dk: float, dib: float | None = 0.0) -> dict[str, Any]:
    return {
        "schema": 2,
        "run_utc": run,
        "tanishq_fresh": fresh,
        "last_tanishq_ts": last_ts,
        "kalman_minus_last_rs_g": dk,
        "ibja_markup_minus_last_rs_g": dib,
        "sd_log": 0.005,
    }


def test_score_groups_by_target_reading_and_applies_the_exclusions() -> None:
    tan = _tan(
        [
            ("2026-10-01T04:00:00Z", 14000),
            ("2026-10-01T20:00:00Z", 14100),
            ("2026-10-06T04:00:00Z", 14200),
        ]
    )
    rows = [
        _row("2026-10-01T06:00:00Z", "2026-10-01T04:00:00Z", True, 0),  # fresh: excluded
        _row("2026-10-01T13:00:00Z", "2026-10-01T04:00:00Z", False, 80),  # episode 1
        _row("2026-10-01T16:00:00Z", "2026-10-01T04:00:00Z", False, 60),  # episode 1
        _row("2026-10-02T06:00:00Z", "2026-10-01T20:00:00Z", False, 50),  # next reading > 72h
        _row("2026-10-06T14:00:00Z", "2026-10-06T04:00:00Z", False, 0),  # pending
    ]
    out = mod.score(rows, tan, pd.Timestamp("2026-10-06T15:00:00"))
    c = out["counts"]
    assert (c["fresh_excluded"], c["no_target_72h"], c["pending"]) == (1, 1, 1)
    assert out["n_episodes"] == 1 and c["entries_scored"] == 2
    assert out["mae_rs_g"]["k"]["mean"] == pytest.approx((20 + 40) / 2)  # |14080-14100|, |14060-..|
    assert out["mae_rs_g"]["last"]["mean"] == pytest.approx(100)
    assert out["verdict"] == "NOT YET" and out["read_allowed"] is False


def test_score_verdict_follows_the_read_rule() -> None:
    rng = np.random.default_rng(42)
    t0 = pd.Timestamp("2026-10-01 04:00")
    tan_rows, rows = [], []
    for i in range(35):
        a = t0 + pd.Timedelta(days=2 * i)
        b = a + pd.Timedelta(hours=20)
        y = 14000 + 100 * i
        tan_rows += [(a.isoformat() + "Z", y - 150.0), (b.isoformat() + "Z", float(y))]
        run = a + pd.Timedelta(hours=12)
        rows.append(
            _row(
                run.isoformat() + "Z",
                a.isoformat() + "Z",
                False,
                150 + float(rng.normal(0, 10)),
                0.0,
            )
        )
    tan = _tan(tan_rows)
    out = mod.score(rows, tan, pd.Timestamp("2027-01-15"))
    assert out["n_episodes"] == 35 and out["read_allowed"] is True
    assert out["H1_kalman_vs_last_tanishq"]["bonferroni"] is True
    assert out["verdict"] == "PASS"
    few = mod.score(rows[:10], tan, pd.Timestamp("2027-01-15"))
    assert few["verdict"] == "NOT YET"
    late = mod.score(rows[:10], tan, mod.HARD_READ_DATE)
    assert late["verdict"] == "INCONCLUSIVE (underpowered)"
    assert math.isfinite(out["mae_rs_g"]["k"]["mean"])
