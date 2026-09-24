"""Tests for ml.next_day_range (ADR 047). Synthetic data only."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from ml.next_day_range import next_day_range_pct
from ml.weekly_range import HS_MIN_SAMPLE, MIN_CAL, base_range, complete_windows, score


def _data(n_proxy: int = 700, n_ibja: int = 150, seed: int = 42) -> tuple[pd.Series, pd.Series]:
    rng = np.random.default_rng(seed)
    pdates = pd.bdate_range("2023-01-02", periods=n_proxy)
    proxy = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, n_proxy))), index=pdates)
    idates = pdates[-n_ibja:]
    ibja = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.008, n_ibja))), index=idates)
    return proxy, ibja


def test_returns_none_below_min_cal() -> None:
    proxy, ibja = _data(n_proxy=700, n_ibja=MIN_CAL - 5)
    d = ibja.index[-1] + pd.Timedelta(days=1)
    assert next_day_range_pct(proxy, ibja, d) is None


def test_returns_none_below_hs_min_sample() -> None:
    # Proxy history too short for base_range's own sample requirement, even though IBJA has
    # plenty of rows to build MIN_CAL+ matured windows from (independent date ranges, on
    # purpose: this isolates the HS_MIN_SAMPLE gate from the MIN_CAL gate).
    rng = np.random.default_rng(1)
    n_proxy = HS_MIN_SAMPLE - 5
    pdates = pd.bdate_range("2023-01-02", periods=n_proxy)
    proxy = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, n_proxy))), index=pdates)
    n_ibja = MIN_CAL + 40
    idates = pd.bdate_range(pdates[0], periods=n_ibja)
    ibja = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.008, n_ibja))), index=idates)
    d = idates[-1] + pd.Timedelta(days=1)
    assert next_day_range_pct(proxy, ibja, d) is None


def test_no_look_ahead_proxy_and_ibja() -> None:
    """Altering data dated >= d must not change d's range (both series)."""
    proxy, ibja = _data()
    d = ibja.index[-20]
    want = next_day_range_pct(proxy, ibja, d)
    assert want is not None

    spiked_proxy = proxy.copy()
    spiked_proxy[spiked_proxy.index >= d] *= 5.0
    assert next_day_range_pct(spiked_proxy, ibja, d) == want

    spiked_ibja = ibja.copy()
    spiked_ibja[spiked_ibja.index >= d] *= 5.0
    assert next_day_range_pct(proxy, spiked_ibja, d) == want

    # Extra future rows appended after d must not change the range either.
    extra_dates = pd.bdate_range(ibja.index[-1] + pd.Timedelta(days=1), periods=10)
    extended_ibja = pd.concat([ibja, pd.Series([999.0] * len(extra_dates), index=extra_dates)])
    assert next_day_range_pct(proxy, extended_ibja, d) == want


def test_range_is_a_sane_interval_around_zero() -> None:
    proxy, ibja = _data()
    d = ibja.index[-5]
    pct = next_day_range_pct(proxy, ibja, d)
    assert pct is not None
    lo, hi = pct
    assert lo < 0 < hi


def test_matches_hand_rolled_walk_forward_scale() -> None:
    """next_day_range_pct at d reuses exactly the same base_range/score/conformal_scale
    primitives ml.weekly_range.walk_forward uses, restricted to windows ending before d."""
    proxy, ibja = _data()
    d = ibja.index[-10]
    proxy_before = proxy[proxy.index < d]
    ibja_before = ibja[ibja.index < d]

    windows = complete_windows(ibja_before, "1d")
    scores = []
    for w in windows:
        base = base_range(proxy_before, w.as_of, 1)
        if base is not None:
            scores.append(score(w, *base))
    from ml.weekly_range import conformal_scale as _cs

    s = _cs(scores)
    assert s is not None
    lo_base, hi_base = base_range(proxy_before, d, 1)  # type: ignore[misc]

    got = next_day_range_pct(proxy, ibja, d)
    assert got is not None
    import math

    assert got[0] == pytest.approx(math.exp(s * lo_base) - 1.0)
    assert got[1] == pytest.approx(math.exp(s * hi_base) - 1.0)


def test_data_before_d_unchanged_gives_same_range_across_calls() -> None:
    """Determinism: same inputs, same output, called twice."""
    proxy, ibja = _data()
    d = ibja.index[-30]
    a = next_day_range_pct(proxy, ibja, d)
    b = next_day_range_pct(proxy, ibja, d)
    assert a == b
