"""Unit tests for ml/kalman_nowcast.py (ADR 055): filter math on synthetic data, including missing
observations, staleness, the same-day anchor exclusion, and the observation builder."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from ml.kalman_nowcast import (
    ANCHOR,
    PARAM_NAMES,
    STATE_DIM,
    Z80,
    InitialState,
    Observation,
    Params,
    build_observations,
    comex_rs_per_g_22k,
    default_initial_state,
    fit_params,
    nontrading_flags,
    predictive,
    run_filter,
)


def _params(**over: float) -> Params:
    base = {
        "q_level_trading": 1e-4,
        "q_level_nontrading": 2.5e-5,
        "q_markup": 1e-7,
        "q_age": 1e-5,
        "r_tanishq": 1e-6,
        "r_ibja_am": 4e-5,
        "r_ibja_pm": 1e-5,
        "r_grt": 2e-5,
        "r_malabar": 2e-5,
        "r_comex": 1e-4,
    }
    base.update(over)
    return Params(base)


def _init(level: float = 0.0, var: float = 1e-2) -> InitialState:
    return InitialState(
        mean=np.array([level] + [0.0] * (STATE_DIM - 1)), cov=np.eye(STATE_DIM) * var
    )


def test_params_reject_nonpositive_and_missing() -> None:
    with pytest.raises(ValueError):
        Params({k: 1e-5 for k in PARAM_NAMES[:-1]})
    bad = {k: 1e-5 for k in PARAM_NAMES}
    bad["r_comex"] = 0.0
    with pytest.raises(ValueError):
        Params(bad)
    p = _params()
    assert Params.from_log_vector(p.to_log_vector()).values == pytest.approx(p.values)


def test_no_observations_variance_grows_by_day_type_and_mean_is_constant() -> None:
    days = 6
    nontrading = [False, False, True, True, False, False]
    res = run_filter([[] for _ in range(days)], nontrading, _params(), _init(1.0, 1e-3))
    assert np.allclose(res.pre_anchor_mean, 1.0)
    inc = np.diff(res.pre_anchor_var)
    assert inc == pytest.approx([1e-4, 2.5e-5, 2.5e-5, 1e-4, 1e-4])
    assert res.n_loglik_terms == 0


def test_single_anchor_update_matches_scalar_kalman_closed_form() -> None:
    p0, r, y = 4e-4, 1e-4, 0.05
    params = _params(r_tanishq=r)
    res = run_filter([[Observation(ANCHOR, y)], []], [False, False], params, _init(0.0, p0), (0, 2))
    k = p0 / (p0 + r)
    # day 0: pre-anchor = prior; day 1 = posterior after day 0's anchor, plus one day of q
    assert res.pre_anchor_mean[0] == pytest.approx(0.0)
    assert res.pre_anchor_mean[1] == pytest.approx(k * y)
    assert res.pre_anchor_var[1] == pytest.approx((1 - k) * p0 + 1e-4)
    f = p0 + r
    assert res.loglik == pytest.approx(-0.5 * (math.log(2 * math.pi * f) + y * y / f))


def test_same_day_anchor_never_enters_that_days_nowcast() -> None:
    obs = [[Observation(ANCHOR, 0.0)], [Observation("ibja_pm", 0.01), Observation(ANCHOR, 0.5)]]
    with_anchor = run_filter(obs, [False, False], _params(), _init())
    without = run_filter([obs[0], [obs[1][0]]], [False, False], _params(), _init())
    assert with_anchor.pre_anchor_mean[1] == pytest.approx(without.pre_anchor_mean[1])
    assert with_anchor.pre_anchor_var[1] == pytest.approx(without.pre_anchor_var[1])
    # ...but it does move the state for the following day
    assert with_anchor.final_mean[0] > without.final_mean[0] + 0.1


def test_stale_reading_moves_the_state_less() -> None:
    prior = [[Observation(ANCHOR, 0.0)]] * 20  # pins the level and the markup is 0 +/- 10%

    def level_after(age: float) -> float:
        # learn grt's markup first (fresh readings at markup 0), then one shifted reading
        obs = [[*d, Observation("grt", 0.0)] for d in prior]
        obs.append([Observation("grt", 0.02, age)])
        res = run_filter(obs, [False] * len(obs), _params(), _init())
        return float(res.pre_anchor_mean[-1])

    fresh, stale = level_after(0.0), level_after(5.0)
    assert 0 < stale < fresh


def test_missing_days_widen_the_band_then_an_observation_narrows_it() -> None:
    obs: list[list[Observation]] = [[Observation(ANCHOR, 0.0), Observation("ibja_pm", 0.015)]] * 30
    obs = [list(d) for d in obs] + [[], [], [], [Observation("ibja_pm", 0.015)]]
    res = run_filter(obs, [False] * len(obs), _params(), _init())
    v = res.pre_anchor_var
    assert v[30] < v[31] < v[32]  # nothing observed: variance grows each day
    assert v[33] < v[32] + 1e-4  # the IBJA reading pulls it back below the no-data path


def test_markup_state_learns_a_constant_source_offset() -> None:
    rng = np.random.default_rng(42)
    n = 200
    level = np.cumsum(rng.normal(0, 0.01, n))
    obs = [
        [
            Observation("ibja_pm", level[t] - 0.015 + rng.normal(0, 0.002)),
            Observation(ANCHOR, level[t] + rng.normal(0, 0.001)),
        ]
        for t in range(n)
    ]
    res = run_filter(obs, [False] * n, _params(), _init(0.0))
    assert res.final_mean[1] == pytest.approx(-0.015, abs=0.002)


def _simulate(n: int, params: Params, seed: int = 42) -> tuple[list[list[Observation]], np.ndarray]:
    """Data generated from the model itself: anchor every weekday, IBJA AM/PM on weekdays with a
    fixed markup, COMEX next day; random 10% of anchor readings missing."""
    rng = np.random.default_rng(seed)
    days = pd.date_range("2026-01-05", periods=n, freq="D")
    nt = nontrading_flags(days)
    s = np.zeros(n)
    m = np.array([-0.015, 0.0, 0.0, 0.005])
    for t in range(1, n):
        q = params["q_level_nontrading"] if nt[t] else params["q_level_trading"]
        s[t] = s[t - 1] + rng.normal(0, math.sqrt(q))
    obs: list[list[Observation]] = [[] for _ in range(n)]
    for t in range(n):
        if not nt[t]:
            for src in ("ibja_am", "ibja_pm"):
                obs[t].append(
                    Observation(src, s[t] + m[0] + rng.normal(0, math.sqrt(params[f"r_{src}"])))
                )
            if t + 1 < n:
                r = params["r_comex"] + params["q_age"]
                obs[t + 1].append(
                    Observation("comex", s[t] + m[3] + rng.normal(0, math.sqrt(r)), 1.0)
                )
        if rng.random() > 0.1:
            obs[t].append(Observation(ANCHOR, s[t] + rng.normal(0, math.sqrt(params["r_tanishq"]))))
    return obs, s


def test_band_covers_about_80pct_on_data_from_the_model() -> None:
    params = _params()
    obs, s = _simulate(1500, params)
    nt = nontrading_flags(pd.date_range("2026-01-05", periods=len(obs), freq="D"))
    res = run_filter(obs, nt, params, _init(0.0))
    sd = np.sqrt(res.pre_anchor_var[50:] + params["r_tanishq"])
    y = s[50:]  # anchor noise is tiny next to the state variance
    cov = float(np.mean(np.abs(y - res.pre_anchor_mean[50:]) <= Z80 * sd))
    assert 0.76 <= cov <= 0.84


def test_mle_improves_likelihood_and_recovers_level_noise() -> None:
    true = _params()
    obs, _ = _simulate(250, true)
    nt = nontrading_flags(pd.date_range("2026-01-05", periods=len(obs), freq="D"))
    init = default_initial_state(obs[0][-1].log_value if obs[0] else 0.0)
    start = _params(q_level_trading=1e-6, r_ibja_pm=1e-3)
    fitted, info = fit_params(obs, nt, init, start=start, maxiter=60)
    ll_start = run_filter(obs, nt, start, init).loglik
    ll_fit = run_filter(obs, nt, fitted, init).loglik
    assert ll_fit > ll_start
    assert info["nll"] == pytest.approx(-ll_fit)
    assert 1e-4 / 3 < fitted["q_level_trading"] < 1e-4 * 3


def test_predictive_band_contains_point_and_widens_with_variance() -> None:
    p = _params()
    pt, lo, hi = predictive(math.log(14000.0), 1e-5, p)
    assert lo < pt < hi and pt == pytest.approx(14000.0)
    _, lo2, hi2 = predictive(math.log(14000.0), 1e-4, p)
    assert lo2 < lo and hi2 > hi


def test_comex_conversion_matches_hand_calculation() -> None:
    v = comex_rs_per_g_22k(3110.34768, 90.0, 0.06)
    assert v == pytest.approx(100.0 * 90.0 * 1.06 * 22 / 24)


def test_build_observations_timing_staleness_and_dedup() -> None:
    days = pd.date_range("2026-09-01", "2026-09-06", freq="D")
    tanishq = pd.Series([14000.0, 14010.0], index=pd.to_datetime(["2026-09-01", "2026-09-02"]))
    ibja = pd.DataFrame(
        {"am": [13800.0, 13800.0, 13850.0], "pm": [13810.0, 13820.0, 13820.0]},
        index=pd.to_datetime(["2026-09-01", "2026-09-02", "2026-09-03"]),
    )
    retail = pd.DataFrame(
        {
            "day": pd.to_datetime(["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"]),
            "source": ["malabar"] * 4,
            "rate_22k": [14100.0, 14100.0, 14100.0, 14150.0],
            "observed_at_day": pd.to_datetime(
                ["2026-08-31", "2026-08-31", "2026-08-31", "2026-09-04"]
            ),
        }
    )
    comex = pd.Series([13500.0], index=pd.to_datetime(["2026-09-04"]))  # a Friday close
    obs = build_observations(days, tanishq, ibja, retail, comex)
    by = [{o.source: o for o in d} for d in obs]
    assert by[0]["malabar"].age_days == 1.0  # captured 09-01, board dated 08-31
    assert "malabar" not in by[1] and "malabar" not in by[2]  # same board: used once only
    assert by[3]["malabar"].age_days == 0.0
    assert "ibja_am" not in by[1]  # identical AM re-publication skipped
    assert "ibja_pm" in by[1] and "ibja_pm" not in by[2]
    assert "ibja_am" in by[2]
    assert by[4]["comex"].age_days == 1.0 and "comex" not in by[3]  # Fri close -> Sat
    assert ANCHOR in by[0] and ANCHOR not in by[2]
    assert list(nontrading_flags(days)) == [False, False, False, False, True, True]


def test_unknown_source_is_rejected() -> None:
    with pytest.raises(ValueError):
        run_filter([[Observation("kalyan", 0.0)]], [False], _params(), _init())


def test_shadow_append_adds_one_entry_and_refuses_a_non_list(tmp_path: object) -> None:
    import importlib.util
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "run_kalman_shadow", root / "scripts" / "run_kalman_shadow.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = Path(str(tmp_path)) / "shadow.json"
    assert mod.append({"target_date": "2026-09-25"}, out) == 1
    assert mod.append({"target_date": "2026-09-26"}, out) == 2
    assert [r["target_date"] for r in json.loads(out.read_text())] == ["2026-09-25", "2026-09-26"]
    out.write_text("{}")
    with pytest.raises(SystemExit):
        mod.append({"target_date": "x"}, out)
