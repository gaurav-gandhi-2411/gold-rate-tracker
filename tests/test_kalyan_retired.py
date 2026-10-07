"""ADR 070: Kalyan is retired. Prove the live fallback order works without it.

Tier order (ml.inference._select_price_source): 1 fresh Tanishq, 2 IBJA-calibrated,
3 live GRT + Malabar fusion consensus, 4 last-known Tanishq price. Self-contained fixtures.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import ml.inference as inf
import ml.markup as markup
import ml.retailers as retailers
import ml.sources.grt as grt_mod
import ml.sources.malabar as malabar_mod
import pandas as pd
import pytest
from ml.sources.base import SourceNetworkError, SourceReading

NOW = datetime(2026, 3, 16, 8, 0, tzinfo=UTC)
CAL = {"valid": True, "slope": 1.0, "intercept": 100.0, "residual_std": 50.0}
ROOT = Path(__file__).resolve().parent.parent


def _ts(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _raise_network(*_a, **_k):
    raise SourceNetworkError("test: network disabled")


def _board(source: str, rate: float) -> SourceReading:
    return SourceReading(
        source=source, city=None, rate_22k=rate, observed_at=NOW, attribution=f"{source} test"
    )


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path


def _write_ibja(data_dir: Path, *, fresh: bool) -> None:
    date = NOW.date() if fresh else (NOW - timedelta(days=30)).date()
    pd.DataFrame([{"date": date.isoformat(), "pm_916": 144000.0}]).to_parquet(
        data_dir / "ibja_rates.parquet", index=False
    )


def _select(data_dir: Path, scraped_hours_ago: float):
    return inf._select_price_source(14400, _ts(scraped_hours_ago), CAL, data_dir, NOW)


def test_tier1_fresh_tanishq_wins_and_fusion_is_never_fetched(data_dir, monkeypatch):
    _write_ibja(data_dir, fresh=True)
    monkeypatch.setattr(grt_mod, "fetch_grt", _raise_network)
    monkeypatch.setattr(malabar_mod, "fetch_malabar", _raise_network)
    result = _select(data_dir, scraped_hours_ago=1.0)
    assert result[0] == 14400 and result[1] == "tanishq_scrape"


def test_tier2_stale_tanishq_fresh_ibja_serves_ibja_calibrated(data_dir, monkeypatch):
    _write_ibja(data_dir, fresh=True)
    monkeypatch.setattr(grt_mod, "fetch_grt", _raise_network)
    monkeypatch.setattr(malabar_mod, "fetch_malabar", _raise_network)
    result = _select(data_dir, scraped_hours_ago=30.0)
    assert result[1] == "ibja_calibrated"
    assert result[5] is None  # fusion_sources only set on tier 3


def test_tier3_both_stale_fuses_grt_and_malabar_only(data_dir, monkeypatch):
    _write_ibja(data_dir, fresh=False)
    monkeypatch.setattr(grt_mod, "fetch_grt", lambda: _board("grt", 14000.0))
    monkeypatch.setattr(malabar_mod, "fetch_malabar", lambda: _board("malabar", 14100.0))
    result = _select(data_dir, scraped_hours_ago=30.0)
    assert result[1] == "fusion_consensus"
    assert result[5] == ["grt", "malabar"]
    assert "kalyan" not in result[5]
    # Equal 0.7 weights -> mean 14050; band = degraded (national band x 1.5).
    assert result[0] == 14050
    assert result[2] < result[0] < result[3]


def test_tier3_single_remaining_retailer_still_serves(data_dir, monkeypatch):
    _write_ibja(data_dir, fresh=False)
    monkeypatch.setattr(grt_mod, "fetch_grt", _raise_network)
    monkeypatch.setattr(malabar_mod, "fetch_malabar", lambda: _board("malabar", 14100.0))
    result = _select(data_dir, scraped_hours_ago=30.0)
    assert result[1] == "fusion_consensus" and result[5] == ["malabar"]


def test_tier4_everything_stale_returns_last_known_tanishq(data_dir, monkeypatch):
    _write_ibja(data_dir, fresh=False)
    monkeypatch.setattr(grt_mod, "fetch_grt", _raise_network)
    monkeypatch.setattr(malabar_mod, "fetch_malabar", _raise_network)
    result = _select(data_dir, scraped_hours_ago=30.0)
    assert result[0] == 14400 and result[1] == "tanishq_scrape"
    assert result[5] is None


# -- the registry, switch file, and every reader agree Kalyan is gone ------------------------


def test_kalyan_is_not_in_the_active_source_registry():
    assert importlib.util.find_spec("ml.sources.kalyan") is None
    assert frozenset({"tanishq", "grt", "malabar"}) == retailers.KNOWN_RETAILERS
    assert set(retailers.load_retailer_flags()) == {"tanishq", "grt", "malabar"}
    committed = json.loads((ROOT / "config" / "retailers.json").read_text(encoding="utf-8"))
    assert "kalyan" not in committed["retailers"]
    assert "kalyan" not in {source for _key, source, _city in markup.FUSION_RETAILERS}


def test_archived_adapter_is_kept_for_reproducibility():
    assert (ROOT / "archive" / "kalyan" / "kalyan.py").is_file()
