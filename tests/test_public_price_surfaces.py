"""Public price surfaces after GG decision 4c (2026-09-25).

* data/ibja_derived_prices.json -- the trend chart's series: timestamp + 22K only, built by
  scripts/build_ibja_derived_prices.py --public-out; never written to prices.json.
* data/wait_or_buy_today.json -- public (page_v2 reads horizons.<N>.sentence), so it carries
  no raw IBJA level: no price_t, and no range.lo/range.hi (price_t = lo - lo_rs exactly).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parent.parent


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


build = _load_script("build_ibja_derived_prices")


def _walk_numbers(obj):
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_numbers(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_numbers(v)


def test_public_chart_rows_are_timestamp_and_22k_only():
    ibja = pd.DataFrame(
        {
            "date": ["2026-09-23", "2026-09-24"],
            "pm_916": [141000.0, 140000.0],
            "pm_999": [154000.0, 153000.0],
            "pm_750": [115000.0, 114000.0],
        }
    )
    rows = build.build_derived_prices(ibja, {"slope": 1.0, "intercept": 0.0})
    public = build.public_chart_rows(rows)
    assert public == [
        {"timestamp": "2026-09-23T11:30:00.000Z", "22k": 14100},
        {"timestamp": "2026-09-24T11:30:00.000Z", "22k": 14000},
    ]


def test_public_out_writes_the_chart_file_and_refuses_prices_json(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    pd.DataFrame(
        {
            "date": ["2026-09-23", "2026-09-24"],
            "pm_916": [141000.0, 140000.0],
            "pm_999": [154000.0, 153000.0],
            "pm_750": [115000.0, 114000.0],
        }
    ).to_parquet(data / "ibja_rates.parquet")
    (data / "calibration.json").write_text(json.dumps({"slope": 1.0, "intercept": 0.0}))
    out = data / "ibja_derived_prices.json"
    assert build.main(["--data-dir", str(data), "--public-out", str(out)]) == 0
    assert [r["22k"] for r in json.loads(out.read_text())] == [14100, 14000]
    assert not (data / "prices.json").exists()
    assert build.main(["--data-dir", str(data), "--public-out", str(data / "prices.json")]) == 1
    assert not (data / "prices.json").exists()


def test_committed_chart_series_shape():
    rows = json.loads((REPO / "data" / "ibja_derived_prices.json").read_text(encoding="utf-8"))
    assert len(rows) >= 2
    assert all(set(r) == {"timestamp", "22k"} for r in rows)
    assert [r["timestamp"] for r in rows] == sorted(r["timestamp"] for r in rows)
    assert all(2_000 <= r["22k"] <= 25_000 for r in rows)  # per gram, not IBJA's per 10 g


@pytest.fixture(scope="module")
def wob():
    return _load_script("run_wait_or_buy_shadow")


def test_public_today_entry_drops_every_raw_ibja_level(wob):
    entry = {
        "as_of": "2026-09-24",
        "n": 1,
        "price_t": 138119.0,
        "prob": {"n": 193, "p_hat": 0.5},
        "range": {
            "scale": 1.0,
            "lo": 135436.8,
            "hi": 141133.4,
            "lo_rs": -2682.2,
            "hi_rs": 3014.4,
            "x_rs": 3014.4,
            "n_cal": 192,
        },
        "volatility": {"category": "usual"},
        "sentence": "Waiting 1 day: prices are about equally likely to go up or down.",
    }
    out = wob.public_today_entry(entry)
    assert "price_t" not in out
    assert "as_of" not in out
    assert "n" not in out
    assert out["range"] == {
        "scale": 1.0,
        "lo_rs": -2682.2,
        "hi_rs": 3014.4,
        "x_rs": 3014.4,
        "n_cal": 192,
    }
    assert out["sentence"] == entry["sentence"]
    assert entry["price_t"] == 138119.0  # the shadow LOG's entry is not mutated
    assert entry["range"]["lo"] == 135436.8


def test_committed_wait_or_buy_today_has_no_ibja_level():
    today = json.loads((REPO / "data" / "wait_or_buy_today.json").read_text(encoding="utf-8"))
    for h in today["horizons"].values():
        assert "price_t" not in h
        assert "lo" not in h.get("range", {})
        assert "hi" not in h.get("range", {})
        assert isinstance(h["sentence"], str)
        assert h["sentence"]
    # No number anywhere in the IBJA per-10 g band (the E1 sweep's definition of raw IBJA).
    assert not [v for v in _walk_numbers(today) if 60_000 <= abs(v) <= 200_000]
