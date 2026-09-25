"""Unit tests for the availability-time filter in scripts/audit_kalman_leak.py (the independent
leak audit of ADR 055): strict-before semantics, IBJA publication times, COMEX lag, and the
provenance builder's strict placement."""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "audit_kalman_leak.py"
_spec = importlib.util.spec_from_file_location("audit_kalman_leak", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["audit_kalman_leak"] = mod
_spec.loader.exec_module(mod)

T = pd.Timestamp("2026-09-24 17:05:31")


def test_observation_at_exactly_target_time_is_excluded() -> None:
    assert mod.is_available(T, T) is False


def test_observation_one_second_earlier_is_included() -> None:
    assert mod.is_available(T - pd.Timedelta(seconds=1), T) is True


def test_observation_after_target_is_excluded() -> None:
    assert mod.is_available(T + pd.Timedelta(seconds=36), T) is False


def test_guard_excludes_same_run_capture() -> None:
    g = pd.Timedelta(minutes=15)
    assert mod.is_available(T - pd.Timedelta(minutes=14), T, g) is False
    assert mod.is_available(T - pd.Timedelta(minutes=16), T, g) is True


def test_ibja_am_value_date_used_before_noon_ist_is_excluded() -> None:
    d = pd.Timestamp("2026-09-24")
    known = mod.ibja_known_at(d, "am")
    assert known == pd.Timestamp("2026-09-24 06:30")  # 12:00 IST
    assert mod.is_available(known, pd.Timestamp("2026-09-24 06:29:59")) is False  # 11:59:59 IST
    assert mod.is_available(known, pd.Timestamp("2026-09-24 06:30:00")) is False
    assert mod.is_available(known, pd.Timestamp("2026-09-24 06:30:01")) is True


def test_ibja_pm_known_at_17_ist() -> None:
    known = mod.ibja_known_at("2026-09-24", "pm")
    assert known == pd.Timestamp("2026-09-24 11:30")
    assert mod.is_available(known, pd.Timestamp("2026-09-24 11:00")) is False


def test_comex_close_known_next_utc_day() -> None:
    k = mod.comex_known_at("2026-09-25")  # Friday close
    assert k == pd.Timestamp("2026-09-26 00:00")
    assert mod.is_available(k, pd.Timestamp("2026-09-25 23:59:59")) is False
    assert mod.is_available(k, pd.Timestamp("2026-09-26 04:00")) is True


def test_first_available_day_defers_past_early_target() -> None:
    targets = {
        pd.Timestamp("2026-09-24"): pd.Timestamp("2026-09-24 06:30"),  # before PM publish
        pd.Timestamp("2026-09-25"): pd.Timestamp("2026-09-25 21:00"),
    }
    known = mod.ibja_known_at("2026-09-24", "pm")
    assert mod.first_available_day(known, pd.Timestamp("2026-09-24"), targets) == pd.Timestamp(
        "2026-09-25"
    )
    late = {pd.Timestamp("2026-09-24"): pd.Timestamp("2026-09-24 21:00")}
    assert mod.first_available_day(known, pd.Timestamp("2026-09-24"), late) == pd.Timestamp(
        "2026-09-24"
    )


def _fixture() -> tuple[pd.DatetimeIndex, pd.Series, dict, pd.DataFrame, pd.DataFrame, pd.Series]:
    days = pd.date_range("2026-09-21", "2026-09-23", freq="D")
    targets = {
        days[0]: pd.Timestamp("2026-09-21 21:00"),
        days[1]: pd.Timestamp("2026-09-22 09:00"),  # before IBJA PM publish
        days[2]: pd.Timestamp("2026-09-23 21:00"),
    }
    tanishq = pd.Series([14000.0, 14010.0, 14020.0], index=days)
    ibja = pd.DataFrame(
        {"am": [13800.0, 13810.0, 13820.0], "pm": [13805.0, 13815.0, 13825.0]}, index=days
    )
    snaps = pd.DataFrame(
        {
            "source": ["grt", "grt", "grt"],
            "as_of_date": ["2026-09-22", "2026-09-22", "2026-09-23"],
            "cap": pd.to_datetime(["2026-09-22 08:00", "2026-09-22 09:00", "2026-09-23 20:00"]),
            "obs_day": pd.to_datetime(["2026-09-22", "2026-09-22", "2026-09-23"]),
            "rate_22k": [14100.0, 14110.0, 14120.0],
        }
    )
    comex = pd.Series([13000.0], index=[pd.Timestamp("2026-09-21")])
    return days, tanishq, targets, ibja, snaps, comex


def test_strict_builder_moves_early_ibja_and_drops_target_time_capture() -> None:
    days, tanishq, targets, ibja, snaps, comex = _fixture()
    obs, prov = mod.build_with_provenance(
        days, tanishq, targets, ibja, snaps, comex, strict_ibja=True, strict_retail=True
    )
    for i, d in enumerate(days):
        for m in prov[i]:
            if m["source"] != "tanishq":
                assert mod.is_available(m["known_at"], targets[d]), (d, m)
    day1 = {m["source"]: m for m in prov[1]}
    # AM (06:30) is before the 09:00 target, PM (11:30) is not: PM deferred to the next day
    assert "ibja_am" in day1 and "ibja_pm" not in day1
    assert day1["grt"]["known_at"] == pd.Timestamp("2026-09-22 08:00")  # 09:00 capture == target
    pm2 = [o for o in obs[2] if o.source == "ibja_pm"]
    assert len(pm2) == 2 and sorted(o.age_days for o in pm2) == [0.0, 1.0]
    assert math.isclose(obs[2][-1].log_value, math.log(14020.0))  # anchor last


def test_registered_builder_keeps_leaky_placement() -> None:
    days, tanishq, targets, ibja, snaps, comex = _fixture()
    _, prov = mod.build_with_provenance(
        days, tanishq, targets, ibja, snaps, comex, strict_ibja=False, strict_retail=False
    )
    leaks = [
        m
        for m in prov[1]
        if m["source"] != "tanishq" and not mod.is_available(m["known_at"], targets[days[1]])
    ]
    assert sorted(m["source"] for m in leaks) == ["grt", "ibja_pm"]
    assert np.all([m["source"] != "tanishq" or m["known_at"] == targets[days[1]] for m in prov[1]])
