"""Retailer takedown switch (ADR 059, G1d) -- config/retailers.json and every reader.

Also produces the committed fixture the headless render test drives the real app.js
with (tests/fixtures/retailer_takedown/): the output of the REAL pipeline
(scripts/build_ibja_derived_prices.py -> ml.inference.main) with Tanishq and Kalyan
switched off. Regenerate after an intentional pipeline change with
``UPDATE_TAKEDOWN_FIXTURE=1 pytest tests/test_retailer_takedown.py``.
"""

from __future__ import annotations

import json
import os
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

import ml.calibration as calibration_mod
import ml.inference as inf
import ml.shadow_fusion as shadow
import ml.sources.grt as grt_mod
import ml.sources.kalyan as kalyan_mod
import ml.sources.malabar as malabar_mod
import pandas as pd
import pytest
from ml.retailers import (
    KNOWN_RETAILERS,
    RetailerConfigError,
    disabled_retailers,
    is_enabled,
    load_retailer_flags,
)
from ml.sources.base import SourceNetworkError, SourceReading
from scripts.build_ibja_derived_prices import DERIVED_SOURCE, build_derived_prices

REPO = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO / "tests" / "fixtures" / "retailer_takedown"
NOW = datetime(2026, 3, 16, 13, 30, tzinfo=UTC)  # a Monday, 2h after IBJA's PM fix


def _write_config(tmp_path: Path, **overrides: bool) -> Path:
    flags = dict.fromkeys(sorted(KNOWN_RETAILERS), True) | overrides
    path = tmp_path / "retailers.json"
    path.write_text(
        json.dumps({"retailers": {k: {"enabled": v} for k, v in flags.items()}}), encoding="utf-8"
    )
    return path


@pytest.fixture
def switch(tmp_path, monkeypatch):
    """Point every reader at a temp config; returns a setter taking retailer=bool."""

    def set_flags(**overrides: bool) -> Path:
        path = _write_config(tmp_path, **overrides)
        monkeypatch.setenv("RETAILERS_CONFIG_PATH", str(path))
        return path

    set_flags()
    return set_flags


def _raise_network(*_a, **_k):
    raise SourceNetworkError("test: network disabled")


def _reading(source: str, rate: float, observed_at: datetime = NOW) -> SourceReading:
    return SourceReading(
        source=source, city=None, rate_22k=rate, observed_at=observed_at, attribution=source
    )


def _ibja_rows(n_days: int = 45) -> list[dict]:
    rows, day = [], NOW.date()
    while len(rows) < n_days:
        if day.weekday() < 5:
            i = len(rows)
            pm916 = 144000.0 - i * 150 + (i % 5) * 90
            rows.append(
                {
                    "date": day.isoformat(),
                    "pm_916": pm916,
                    "pm_999": round(pm916 * 999 / 916),
                    "pm_750": round(pm916 * 750 / 916),
                }
            )
        day -= timedelta(days=1)
    return sorted(rows, key=lambda r: r["date"])


_CAL = {
    "valid": True,
    "slope": 1.01,
    "intercept": 20.0,
    "residual_std": 60.0,
    # frozen quantiles, as the real data/calibration.json carries (fit on Tanishq pairs)
    "residual_abs_quantiles": {"68": 70.0, "80": 77.0, "90": 103.0},
}


def _backtest(n_folds: int = 35) -> dict:
    folds = [
        {
            "fold_id": i,
            "context_end_date": f"2026-02-{(i % 28) + 1:02d}",
            "context_size": 30 + i,
            "actuals": [14000.0 + i * 10 + (j + 1) * 50 for j in range(5)],
            "chronos_p50": [14000.0 + i * 10 + (j + 1) * 60 for j in range(5)],
            "naive": [14000.0 + i * 10] * 5,
        }
        for i in range(n_folds)
    ]
    return {"n_folds": n_folds, "mae_5d_avg_naive": 249.5, "folds": folds}


def _probe(last: float = 14500.0) -> dict:
    return {
        "status": "success",
        "ibja_last_value": last,
        "ibja_forecast": [
            {"day": d, "p10": last * 0.98, "p50": last, "p90": last * 1.02} for d in range(1, 6)
        ],
        "majority_direction": "flat",
        "direction_consensus": 1.0,
        "num_samples": 5,
        "model_version": "amazon/chronos-bolt-tiny@test",
    }


def _write_pipeline_inputs(data_dir: Path, prices: list[dict]) -> None:
    (data_dir / "prices.json").write_text(json.dumps(prices))
    (data_dir / "backtest.json").write_text(json.dumps(_backtest()))
    (data_dir / "chronos_probe.json").write_text(json.dumps(_probe()))
    (data_dir / "calibration.json").write_text(json.dumps(_CAL))
    pd.DataFrame(_ibja_rows()).to_parquet(data_dir / "ibja_rates.parquet", index=False)


def _tanishq_prices(last_ts: datetime, last_22k: int = 14450) -> list[dict]:
    start = last_ts - timedelta(days=39)
    return [
        {
            "timestamp": (start + timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "22k": last_22k,
            "24k": round(last_22k * 24 / 22),
            "18k": round(last_22k * 18 / 22),
            "source": "https://www.tanishq.co.in/gold-rate.html?lang=en_IN",
        }
        for i in range(40)
    ]


# ── config/retailers.json itself ────────────────────────────────────────────


def test_committed_config_keeps_every_retailer_enabled():
    """This PR must not change live behaviour: the committed switch is all-on."""
    flags = load_retailer_flags(REPO / "config" / "retailers.json")
    assert flags == dict.fromkeys(KNOWN_RETAILERS, True)


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        json.dumps({"retailers": {"tanishq": {"enabled": True}}}),  # others missing
        json.dumps({"retailers": {r: {"enabled": "yes"} for r in KNOWN_RETAILERS}}),
        json.dumps(
            {"retailers": {**{r: {"enabled": True} for r in KNOWN_RETAILERS}, "x": {"enabled": 1}}}
        ),
        json.dumps({"nope": {}}),
    ],
)
def test_malformed_config_fails_loudly(tmp_path, payload):
    path = tmp_path / "retailers.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(RetailerConfigError):
        load_retailer_flags(path)


def test_missing_config_fails_loudly(tmp_path):
    with pytest.raises(RetailerConfigError):
        load_retailer_flags(tmp_path / "absent.json")


def test_disabled_listing_and_unknown_name(switch):
    switch(tanishq=False, kalyan=False)
    assert disabled_retailers() == ["kalyan", "tanishq"]
    assert is_enabled("grt") is True
    with pytest.raises(RetailerConfigError):
        is_enabled("joyalukkas")


# ── inference tiers ─────────────────────────────────────────────────────────


def test_fresh_tanishq_still_wins_when_enabled(tmp_path, monkeypatch, switch):
    """Default config: behaviour unchanged -- a fresh plausible Tanishq reading is tier 1."""
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)
    _write_pipeline_inputs(tmp_path, _tanishq_prices(NOW - timedelta(hours=1)))
    inf.main(now=NOW)
    fc = json.loads((tmp_path / "forecast.json").read_text())
    assert fc["price_source"] == "tanishq_scrape"


def test_tanishq_disabled_skips_fresh_tanishq_and_serves_ibja(tmp_path, monkeypatch, switch):
    switch(tanishq=False)
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)
    _write_pipeline_inputs(tmp_path, _tanishq_prices(NOW - timedelta(hours=1)))
    inf.main(now=NOW)
    fc = json.loads((tmp_path / "forecast.json").read_text())
    assert fc["price_source"] == "ibja_calibrated"


def test_suspect_fresh_tanishq_falls_to_ibja(tmp_path, monkeypatch, switch):
    """ADR 059 plausibility gate: >12% off IBJA-calibrated is not shown as confirmed."""
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)
    _write_pipeline_inputs(tmp_path, _tanishq_prices(NOW - timedelta(hours=1), last_22k=18000))
    inf.main(now=NOW)
    fc = json.loads((tmp_path / "forecast.json").read_text())
    assert fc["price_source"] == "ibja_calibrated"


def test_tanishq_disabled_and_everything_else_down_fails_loudly(tmp_path, monkeypatch, switch):
    switch(tanishq=False)
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)
    _write_pipeline_inputs(tmp_path, _tanishq_prices(NOW - timedelta(hours=30)))
    (tmp_path / "ibja_rates.parquet").unlink()
    for mod, name in ((grt_mod, "fetch_grt"), (malabar_mod, "fetch_malabar")):
        monkeypatch.setattr(mod, name, _raise_network)
    monkeypatch.setattr(kalyan_mod, "fetch_kalyan_city", _raise_network)
    with pytest.raises(inf.RetailerDisabledNoSourceError):
        inf.main(now=NOW)
    assert not (tmp_path / "forecast.json").exists()  # previous file is left untouched


def test_fusion_tier_never_fetches_a_disabled_retailer(tmp_path, monkeypatch, switch):
    switch(kalyan=False)
    calls: list[str] = []

    def kalyan_fetch(_city):
        calls.append("kalyan")
        raise AssertionError("disabled retailer must not be fetched")

    monkeypatch.setattr(grt_mod, "fetch_grt", lambda: _reading("grt", 14000.0))
    monkeypatch.setattr(malabar_mod, "fetch_malabar", lambda: _reading("malabar", 14100.0))
    monkeypatch.setattr(kalyan_mod, "fetch_kalyan_city", kalyan_fetch)
    result = inf._try_fusion_fallback(tmp_path, NOW)
    assert result is not None and result[1] == "fusion_consensus"
    assert result[5] == ["grt", "malabar"]
    assert calls == []


def test_fusion_tier_drops_a_stale_retailer_reading(tmp_path, monkeypatch, switch):
    old = NOW - timedelta(hours=inf._FUSION_MAX_AGE_H + 1)
    monkeypatch.setattr(grt_mod, "fetch_grt", lambda: _reading("grt", 14000.0))
    monkeypatch.setattr(malabar_mod, "fetch_malabar", lambda: _reading("malabar", 14100.0, old))
    monkeypatch.setattr(
        kalyan_mod,
        "fetch_kalyan_city",
        lambda _c: types.SimpleNamespace(reading=_reading("kalyan", 14080.0, old)),
    )
    result = inf._try_fusion_fallback(tmp_path, NOW)
    assert result is not None
    assert result[5] == ["grt"]  # the two stale boards are not shown by name


# ── shadow fusion + calibration ─────────────────────────────────────────────


def test_shadow_fusion_skips_disabled_retailers(monkeypatch, switch):
    switch(grt=False, kalyan=False)
    fetched: list[str] = []
    monkeypatch.setitem(
        shadow._NATIONAL_FETCHERS, "ibja", lambda: fetched.append("ibja") or _reading("ibja", 1e4)
    )
    monkeypatch.setitem(
        shadow._NATIONAL_FETCHERS, "grt", lambda: fetched.append("grt") or _reading("grt", 1e4)
    )
    monkeypatch.setitem(
        shadow._NATIONAL_FETCHERS,
        "malabar",
        lambda: fetched.append("malabar") or _reading("malabar", 1e4),
    )
    monkeypatch.setattr(shadow, "fetch_kalyan_city", lambda _c: fetched.append("kalyan"))
    _readings, failures = shadow._fetch_national_readings()
    k_readings, k_failures = shadow._fetch_kalyan_readings()
    assert fetched == ["ibja", "malabar"]
    assert failures == {} and k_readings == {} and k_failures == {}


def test_calibration_does_not_refit_or_score_when_tanishq_disabled(tmp_path, switch):
    switch(tanishq=False)
    assert calibration_mod.run_refit_if_needed(tmp_path) is False
    assert calibration_mod.save_calibration_band_coverage(tmp_path) is None


# ── derived prices + the end-to-end fallback proof ──────────────────────────


def test_build_derived_prices_is_ibja_times_calibration_only():
    rows = build_derived_prices(pd.DataFrame(_ibja_rows(3)), _CAL)
    assert len(rows) == 3
    last = rows[-1]
    ibja = _ibja_rows(3)[-1]
    assert last["22k"] == round(_CAL["slope"] * ibja["pm_916"] / 10 + _CAL["intercept"])
    assert last["24k"] > last["22k"] > last["18k"]
    assert last["source"] == DERIVED_SOURCE
    assert last["timestamp"] == f"{ibja['date']}T11:30:00.000Z"


def _run_takedown_pipeline(data_dir: Path) -> tuple[dict, list[dict]]:
    _write_pipeline_inputs(data_dir, prices=[])
    prices = build_derived_prices(pd.read_parquet(data_dir / "ibja_rates.parquet"), _CAL)
    (data_dir / "prices.json").write_text(json.dumps(prices))
    inf.main(now=NOW)
    return json.loads((data_dir / "forecast.json").read_text()), prices


def test_takedown_end_to_end_produces_valid_ibja_forecast(tmp_path, monkeypatch, switch):
    """Tanishq AND a fusion retailer (Kalyan) off: the real pipeline still writes a
    valid forecast.json via IBJA x markup, and never touches a disabled source."""
    switch(tanishq=False, kalyan=False)
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)
    for mod, name in ((grt_mod, "fetch_grt"), (malabar_mod, "fetch_malabar")):
        monkeypatch.setattr(mod, name, _raise_network)
    monkeypatch.setattr(
        kalyan_mod, "fetch_kalyan_city", lambda _c: pytest.fail("kalyan is disabled")
    )

    fc, prices = _run_takedown_pipeline(tmp_path)

    assert fc["price_source"] == "ibja_calibrated"
    assert fc["current_22k"] == prices[-1]["22k"]
    assert fc["ibja_asof"] is not None and fc["fusion_sources"] is None
    assert fc["headline"]["predicted_22k"] == fc["current_22k"]
    assert all(r["source"] == DERIVED_SOURCE for r in prices)

    if os.environ.get("UPDATE_TAKEDOWN_FIXTURE") == "1":
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        (FIXTURE_DIR / "forecast.json").write_text(json.dumps(fc, indent=2) + "\n")
        (FIXTURE_DIR / "prices.json").write_text(json.dumps(prices, indent=2) + "\n")
        (FIXTURE_DIR / "generated_at.json").write_text(
            json.dumps({"pipeline_now_utc": NOW.isoformat()}, indent=2) + "\n"
        )

    committed_fc = json.loads((FIXTURE_DIR / "forecast.json").read_text())
    committed_prices = json.loads((FIXTURE_DIR / "prices.json").read_text())
    # The headless test renders the committed files -- they must be what the
    # pipeline produces today, not a hand-edited stand-in.
    assert committed_prices == prices
    for key in ("price_source", "current_22k", "est_low", "est_high", "ibja_asof", "scraped_at"):
        assert committed_fc[key] == fc[key], key
    assert committed_fc["headline"] == fc["headline"]


def test_derived_rows_never_size_the_band(tmp_path, monkeypatch, switch):
    """Without frozen quantiles, the on-the-fly fit must not use IBJA-derived rows
    (IBJA vs itself -> a zero-width band shown as a range). Band is suppressed instead."""
    switch(tanishq=False)
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)
    _write_pipeline_inputs(tmp_path, prices=[])
    cal = {k: v for k, v in _CAL.items() if k != "residual_abs_quantiles"}
    (tmp_path / "calibration.json").write_text(json.dumps(cal))
    prices = build_derived_prices(pd.read_parquet(tmp_path / "ibja_rates.parquet"), cal)
    (tmp_path / "prices.json").write_text(json.dumps(prices))
    inf.main(now=NOW)
    fc = json.loads((tmp_path / "forecast.json").read_text())
    assert fc["price_source"] == "ibja_calibrated"
    assert fc["est_low"] is None and fc["est_high"] is None
    assert fc["band_unavailable_reason"]
