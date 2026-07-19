"""Tests for ml.shadow_fusion — the orchestrator, all sources mocked."""

from __future__ import annotations

from datetime import UTC, datetime

import ml.shadow_fusion as shadow_fusion
import pytest
from ml.sources.base import SourceNetworkError, SourceReading, SourceStructureError

NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


def _reading(source: str, rate: float, city: str | None = None) -> SourceReading:
    return SourceReading(
        source=source, city=city, rate_22k=rate, observed_at=NOW, attribution=f"{source} test"
    )


@pytest.fixture(autouse=True)
def _isolate_snapshot_store(tmp_path, monkeypatch):
    # Every test gets its own empty PIT store — never touch the real data/ dir.
    monkeypatch.setattr(
        shadow_fusion, "append_snapshot_rows", lambda rows, store_path=None: len(rows)
    )
    monkeypatch.setattr(shadow_fusion, "SHADOW_OUTPUT_PATH", tmp_path / "shadow_fusion_output.json")


def _patch_national(monkeypatch, *, ibja=None, grt=None, malabar=None):
    def make(name, result):
        def fn():
            if isinstance(result, Exception):
                raise result
            return result

        return fn

    monkeypatch.setitem(
        shadow_fusion._NATIONAL_FETCHERS,
        "ibja",
        make("ibja", ibja if ibja is not None else _reading("ibja", 13000)),
    )
    monkeypatch.setitem(
        shadow_fusion._NATIONAL_FETCHERS,
        "grt",
        make("grt", grt if grt is not None else _reading("grt", 13050)),
    )
    monkeypatch.setitem(
        shadow_fusion._NATIONAL_FETCHERS,
        "malabar",
        make("malabar", malabar if malabar is not None else _reading("malabar", 13100)),
    )


def _patch_kalyan(monkeypatch, city_results: dict):
    def fake_fetch(city):
        result = city_results.get(city)
        if isinstance(result, Exception):
            raise result
        if result is None:
            raise SourceNetworkError(f"no fixture for {city}")

        class _Raw:
            reading = result

        return _Raw()

    monkeypatch.setattr(shadow_fusion, "fetch_kalyan_city", fake_fetch)


def test_all_sources_healthy_produces_full_output(monkeypatch):
    _patch_national(monkeypatch)
    _patch_kalyan(
        monkeypatch,
        {city: _reading("kalyan", 13100, city=city) for city in shadow_fusion.KALYAN_CITIES},
    )

    result = shadow_fusion.run_shadow_cycle()

    assert result["national_benchmark"] is not None
    assert result["national_failures"] == {}
    assert result["kalyan_failures"] == {}
    for city in shadow_fusion.KALYAN_CITIES:
        assert result["cities"][city]["coverage"] == "city_specific"


def test_one_national_source_down_still_produces_output(monkeypatch):
    _patch_national(monkeypatch, grt=SourceNetworkError("grt timed out"))
    _patch_kalyan(
        monkeypatch,
        {city: _reading("kalyan", 13100, city=city) for city in shadow_fusion.KALYAN_CITIES},
    )

    result = shadow_fusion.run_shadow_cycle()

    assert result["national_benchmark"] is not None
    assert "grt" in result["national_failures"]
    assert "network" in result["national_failures"]["grt"]
    assert set(result["national_benchmark"]["sources_used"]) == {"ibja", "malabar"}


def test_all_national_sources_down_raises(monkeypatch):
    _patch_national(
        monkeypatch,
        ibja=SourceStructureError("ibja calibration invalid"),
        grt=SourceNetworkError("grt down"),
        malabar=SourceNetworkError("malabar down"),
    )
    _patch_kalyan(monkeypatch, {})

    with pytest.raises(RuntimeError, match="ALL national sources failed"):
        shadow_fusion.run_shadow_cycle()


def test_one_kalyan_city_down_falls_back_to_national_derived(monkeypatch):
    _patch_national(monkeypatch)
    cities = list(shadow_fusion.KALYAN_CITIES)
    down_city = cities[0]
    healthy_results = {c: _reading("kalyan", 13100, city=c) for c in cities}
    healthy_results[down_city] = SourceNetworkError("kalyan down for this city")
    _patch_kalyan(monkeypatch, healthy_results)

    result = shadow_fusion.run_shadow_cycle()

    assert down_city in result["kalyan_failures"]
    assert result["cities"][down_city]["coverage"] == "national_derived"
    for c in cities:
        if c != down_city:
            assert result["cities"][c]["coverage"] == "city_specific"


def test_structure_vs_network_failure_distinguishable(monkeypatch):
    _patch_national(monkeypatch, grt=SourceStructureError("grt page redesigned"))
    _patch_kalyan(
        monkeypatch,
        {city: _reading("kalyan", 13100, city=city) for city in shadow_fusion.KALYAN_CITIES},
    )

    result = shadow_fusion.run_shadow_cycle()

    assert result["national_failures"]["grt"].startswith("structure:")


def test_output_written_to_disk(monkeypatch, tmp_path):
    _patch_national(monkeypatch)
    _patch_kalyan(
        monkeypatch,
        {city: _reading("kalyan", 13100, city=city) for city in shadow_fusion.KALYAN_CITIES},
    )

    shadow_fusion.run_shadow_cycle()

    written = shadow_fusion.SHADOW_OUTPUT_PATH
    assert written.exists()
    assert "national_benchmark" in written.read_text(encoding="utf-8")


def test_output_ends_with_exactly_one_trailing_newline(monkeypatch):
    # Regression test: a file missing its trailing newline fails pre-commit's
    # end-of-file-fixer hook, which stalls every CI cycle's bot-PR-sync step
    # (found 2026-07-19, PR #262 timed out this way -- see ml.shadow_fusion's
    # _write_output).
    _patch_national(monkeypatch)
    _patch_kalyan(
        monkeypatch,
        {city: _reading("kalyan", 13100, city=city) for city in shadow_fusion.KALYAN_CITIES},
    )

    shadow_fusion.run_shadow_cycle()

    raw = shadow_fusion.SHADOW_OUTPUT_PATH.read_bytes()
    assert raw.endswith(b"\n")
    assert not raw.endswith(b"\n\n")
