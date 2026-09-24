"""Tests for scripts/run_nowcast_shadow.py (G3 forward shadow). Synthetic data only."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run_nowcast_shadow.py"
_spec = importlib.util.spec_from_file_location("run_nowcast_shadow", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["run_nowcast_shadow"] = mod
_spec.loader.exec_module(mod)


def _fake_r2(dates: list[str], m0: list[float], m3: list[float], truth: list[float]):
    m = pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "ibja_date": pd.to_datetime(dates),
            "gap_days": [0] * len(dates),
            "t22": truth,
        }
    )
    return SimpleNamespace(
        load_truth=lambda: None,
        load_ibja=lambda: None,
        build=lambda truth, ibja, glob: m,
        predict_all=lambda frame: {"M0_current": np.array(m0), "M3_am_pm": np.array(m3)},
    )


def test_only_completed_days_after_the_shadow_start_are_logged() -> None:
    dates = ["2026-09-24", "2026-09-25", "2026-09-28", "2026-09-29"]
    r2 = _fake_r2(dates, [1.0] * 4, [1.0] * 4, [1.0] * 4)
    rows = mod.new_rows(r2, logged=set(), today_utc="2026-09-29")
    # 09-24 is the merge day (not after it); 09-29 is today (truth not final).
    assert [r["date"] for r in rows] == ["2026-09-25", "2026-09-28"]


def test_logged_days_are_never_rescored() -> None:
    r2 = _fake_r2(["2026-09-25", "2026-09-28"], [1.0, 1.0], [1.0, 1.0], [1.0, 1.0])
    rows = mod.new_rows(r2, logged={"2026-09-25"}, today_utc="2026-10-01")
    assert [r["date"] for r in rows] == ["2026-09-28"]


def test_missing_prediction_is_none_not_nan() -> None:
    r2 = _fake_r2(["2026-09-25"], [np.nan], [14000.0], [14010.0])
    row = mod.new_rows(r2, logged=set(), today_utc="2026-10-01")[0]
    assert row["M0_current"] is None
    json.dumps(row, allow_nan=False)


def test_summary_prefers_the_better_arm() -> None:
    rng = np.random.default_rng(42)
    truth = 14000 + rng.normal(0, 10, 30)
    days = [
        {
            "date": f"2026-10-{i + 1:02d}",
            "gap_days": 0,
            "truth_rs_per_g": float(t),
            "M0_current": float(t + 50 + rng.normal(0, 5)),
            "M3_am_pm": float(t + 10 + rng.normal(0, 5)),
        }
        for i, t in enumerate(truth)
    ]
    s = mod.summarise(days)
    assert s["n_same_day"] == 30
    assert s["mae_m3_rs_per_g"] < s["mae_m0_rs_per_g"]
    assert s["p_one_sided_m3_better"] < 0.05


def test_summary_with_too_few_days_reports_n_only() -> None:
    assert mod.summarise([]) == {"n_same_day": 0, "n_all_logged": 0}
