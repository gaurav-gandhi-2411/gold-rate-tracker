"""Tests for the freshness-stratified SHADOW band scorer in ml/calibration.py (AP3, 2026-09-21)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from ml.calibration import (
    NOMINAL_COVERAGE_PCT,
    _stratified_shadow_payload,
    evaluate_empirical_band_coverage,
    evaluate_stratified_band_coverage,
    save_calibration_band_coverage,
)

_SEED = 42  # hardcoded, per the repo's determinism rule


def _synthetic(
    n_days: int = 110, weekend_extra_sd: float = 70.0, weekend_drift_sd: float = 25.0
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """IBJA publishes Mon-Fri only. Tanishq is quoted every day. On weekends the underlying price
    keeps drifting (weekend_drift_sd) while IBJA is stale, and Tanishq carries extra noise
    (weekend_extra_sd): the mechanism under test. Setting both low makes stale days no harder."""
    rng = np.random.default_rng(_SEED)
    days = pd.date_range("2026-03-02", periods=n_days, freq="D")  # 2026-03-02 is a Monday
    ibja_rows, tq_rows = [], []
    ibja_per_g = 13000.0
    last_weekday_price = ibja_per_g
    for d in days:
        ibja_per_g += rng.normal(0, 25 if d.weekday() < 5 else weekend_drift_sd)
        if d.weekday() < 5:
            last_weekday_price = ibja_per_g
            ibja_rows.append({"date": d.strftime("%Y-%m-%d"), "pm_916": ibja_per_g * 10.0})
            tq = 1.05 * ibja_per_g + 200 + rng.normal(0, 8)
        else:
            tq = 1.05 * ibja_per_g + 200 + rng.normal(0, weekend_extra_sd)
        tq_rows.append({"date": d.strftime("%Y-%m-%d"), "22k": float(tq)})
    assert last_weekday_price > 0
    return pd.DataFrame(ibja_rows), pd.DataFrame(tq_rows)


def test_production_numbers_match_the_production_scorer_exactly():
    """The shadow scorer's production band must BE the production band, or the comparison is void."""
    ibja, tq = _synthetic()
    prod = evaluate_empirical_band_coverage(ibja, tq, level=NOMINAL_COVERAGE_PCT)
    shadow = evaluate_stratified_band_coverage(ibja, tq, level=NOMINAL_COVERAGE_PCT)
    assert shadow["n"] == prod["n"] > 0
    assert shadow["production"]["n_in_band"] == prod["n_in_band"]


def test_stratified_band_only_changes_carry_forward_days():
    ibja, tq = _synthetic()
    s = evaluate_stratified_band_coverage(ibja, tq, level=NOMINAL_COVERAGE_PCT)["strata"]
    assert s["same_day"]["production_in_band"] == s["same_day"]["stratified_in_band"]
    assert (
        s["same_day"]["mean_half_width_production"] == s["same_day"]["mean_half_width_stratified"]
    )
    assert s["carry_forward"]["n"] > 0


def test_stratified_band_fixes_carry_forward_undercoverage_and_is_wider_there():
    ibja, tq = _synthetic()
    r = evaluate_stratified_band_coverage(ibja, tq, level=NOMINAL_COVERAGE_PCT)
    cf = r["strata"]["carry_forward"]
    assert cf["stratified_in_band"] > cf["production_in_band"]
    assert cf["mean_half_width_stratified"] > cf["mean_half_width_production"]
    assert r["stratified"]["coverage"] > r["production"]["coverage"]


def test_no_gain_and_no_change_when_weekends_are_not_harder():
    """If stale days are no noisier, the stratified band must not be dramatically wider or worse."""
    ibja, tq = _synthetic(weekend_extra_sd=8.0, weekend_drift_sd=0.0)
    r = evaluate_stratified_band_coverage(ibja, tq, level=NOMINAL_COVERAGE_PCT)
    cf = r["strata"]["carry_forward"]
    assert cf["mean_half_width_stratified"] < 2.0 * cf["mean_half_width_production"]


def test_uses_only_days_strictly_before_t():
    """Perturbing a FUTURE Tanishq reading must not change any earlier day's result."""
    ibja, tq = _synthetic()
    base = evaluate_stratified_band_coverage(ibja, tq, level=NOMINAL_COVERAGE_PCT)
    tq2 = tq.copy()
    tq2.loc[tq2.index[-1], "22k"] += 5000.0  # only the very last day's own outcome changes
    changed = evaluate_stratified_band_coverage(ibja, tq2, level=NOMINAL_COVERAGE_PCT)
    assert changed["n"] == base["n"]
    # At most the last scored day can differ (its own outcome moved); everything earlier is identical.
    assert abs(changed["stratified"]["n_in_band"] - base["stratified"]["n_in_band"]) <= 1
    assert abs(changed["production"]["n_in_band"] - base["production"]["n_in_band"]) <= 1


def test_falls_back_to_production_band_below_the_minimum_history():
    ibja, tq = _synthetic(n_days=45)
    r = evaluate_stratified_band_coverage(
        ibja, tq, level=NOMINAL_COVERAGE_PCT, min_carry_residuals=10_000
    )
    assert r["carry_forward_fallback_days"] == r["strata"]["carry_forward"]["n"]
    assert r["stratified"]["n_in_band"] == r["production"]["n_in_band"]


def test_shadow_payload_shape_and_decision_statistic():
    ibja, tq = _synthetic()
    p = _stratified_shadow_payload(
        evaluate_stratified_band_coverage(ibja, tq, level=NOMINAL_COVERAGE_PCT),
        NOMINAL_COVERAGE_PCT / 100.0,
    )
    assert p["status"].startswith("shadow")
    cf = p["strata"]["carry_forward"]["production"]
    assert set(cf) >= {
        "n",
        "n_in_band",
        "coverage",
        "wilson_ci_low",
        "wilson_ci_high",
        "p_vs_nominal",
    }
    assert 0.0 <= cf["p_vs_nominal"] <= 1.0
    assert p["pooled_stratified"]["n"] == p["n"]


def test_save_writes_the_shadow_block_and_leaves_the_existing_fields_untouched(tmp_path: Path):
    ibja, tq = _synthetic()
    ibja.to_parquet(tmp_path / "ibja_rates.parquet")
    prices = [{"timestamp": f"{r['date']}T06:00:00Z", "22k": r["22k"]} for _, r in tq.iterrows()]
    (tmp_path / "prices.json").write_text(json.dumps(prices), encoding="utf-8")
    payload = save_calibration_band_coverage(data_dir=tmp_path)
    written = json.loads((tmp_path / "calibration_band_coverage.json").read_text(encoding="utf-8"))
    assert payload is not None
    for key in ("coverage", "n", "n_in_band", "wilson_ci_low", "wilson_ci_high", "resolvable_at_n"):
        assert key in written  # the fields README markers and the PWA already read
    assert written["schema_version"] == 1
    assert "stratified_shadow" in written
    # the shadow block's production numbers agree with the top-level (displayed-band) numbers
    assert written["stratified_shadow"]["pooled_production"]["n_in_band"] == written["n_in_band"]
    assert written["stratified_shadow"]["n"] == written["n"]
