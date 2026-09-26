from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


def _mod():  # type: ignore[no-untyped-def]
    name = "analysis_fomc_aligned"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def test_events_exclude_unscheduled_and_add_missing_2019_meeting() -> None:
    m = _mod()
    fomc, every = m.load_events()
    iso = {d.isoformat() for d in fomc}
    assert "2019-09-18" in iso
    assert not iso & m.EXCLUDED_UNSCHEDULED
    assert all(m.WINDOW_START <= d <= m.WINDOW_END for d in iso)
    assert "2020-03-15" in every  # still excluded from the normal pool


def test_pools_flag_events_and_drop_excluded_normals() -> None:
    m = _mod()
    idx = pd.bdate_range("2013-01-01", periods=40)
    s = pd.Series(np.exp(np.cumsum(np.full(40, 0.01))), index=idx)
    ev = [idx[10]]
    excl = {idx[20].date()}
    df = m.pools(s, ev, excl)
    assert bool(df.loc[idx[10], "event"])
    assert idx[20] not in df.index
    assert np.allclose(df["abs_ret"], 0.01)


def test_pools_consecutive_rule_drops_sparse_pairs() -> None:
    m = _mod()
    idx = pd.to_datetime(["2025-05-05", "2025-05-06", "2025-05-20", "2025-05-21"])
    s = pd.Series([100.0, 101.0, 102.0, 102.0], index=idx)
    df = m.pools(s, [], set(), consecutive_busdays=2, drop_zero=True)
    assert list(df.index) == [pd.Timestamp("2025-05-06")]


def test_hac_test_detects_a_real_spike_and_not_a_null() -> None:
    m = _mod()
    rng = np.random.default_rng(42)
    n = 2000
    y = np.abs(rng.normal(0, 0.01, n))
    ev = np.zeros(n, dtype=bool)
    ev[::50] = True
    spike = pd.DataFrame({"abs_ret": np.where(ev, y * 2.5, y), "event": ev})
    null = pd.DataFrame({"abs_ret": y, "event": ev})
    assert m.hac_event_test(spike)["p_one_sided_hac"] < 0.001
    assert m.hac_event_test(null)["p_one_sided_hac"] > 0.01
    assert 0 < m.hac_event_test(spike)["effective_n_event"] <= 2 * ev.sum()


def test_cell_marks_small_samples_untestable() -> None:
    m = _mod()
    df = pd.DataFrame(
        {"abs_ret": [0.01] * 20 + [0.02] * 3, "event": [False] * 20 + [True] * 3},
        index=pd.bdate_range("2025-01-01", periods=23),
    )
    c = m.cell("x", df, "synthetic")
    assert c["testable"] is False
    assert abs(c["ratio_of_means"] - 2.0) < 1e-9
    assert c["event_sessions"][0] == date(2025, 1, 29).isoformat()
