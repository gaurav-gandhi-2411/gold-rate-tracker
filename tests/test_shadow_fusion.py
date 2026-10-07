"""Tests for ml.shadow_fusion — the orchestrator, all sources mocked.

Kalyan and the per-city layer were retired 2026-10-05 (ADR 070); this driver now fuses the
national sources only (IBJA, GRT, Malabar).
"""

from __future__ import annotations

import json
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


def test_all_sources_healthy_produces_full_output(monkeypatch):
    _patch_national(monkeypatch)

    result = shadow_fusion.run_shadow_cycle()

    assert result["national_benchmark"] is not None
    assert result["national_failures"] == {}
    assert set(result["national_benchmark"]["sources_used"]) == {"ibja", "grt", "malabar"}


def test_one_national_source_down_still_produces_output(monkeypatch):
    _patch_national(monkeypatch, grt=SourceNetworkError("grt timed out"))

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

    with pytest.raises(RuntimeError, match="ALL national sources failed"):
        shadow_fusion.run_shadow_cycle()


def test_structure_vs_network_failure_distinguishable(monkeypatch):
    _patch_national(monkeypatch, grt=SourceStructureError("grt page redesigned"))

    result = shadow_fusion.run_shadow_cycle()

    assert result["national_failures"]["grt"].startswith("structure:")


def test_output_written_to_disk(monkeypatch):
    _patch_national(monkeypatch)

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

    shadow_fusion.run_shadow_cycle()

    raw = shadow_fusion.SHADOW_OUTPUT_PATH.read_bytes()
    assert raw.endswith(b"\n")
    assert not raw.endswith(b"\n\n")


def test_kalyan_is_retired_from_the_shadow_cycle(monkeypatch):
    """ADR 070: no Kalyan fetcher, no Kalyan/city keys in the output, no Kalyan snapshot rows."""
    _patch_national(monkeypatch)
    persisted: list[dict] = []
    monkeypatch.setattr(
        shadow_fusion,
        "append_snapshot_rows",
        lambda rows, store_path=None: persisted.extend(rows) or len(rows),
    )

    result = shadow_fusion.run_shadow_cycle()

    assert set(shadow_fusion._NATIONAL_FETCHERS) == {"ibja", "grt", "malabar"}
    assert not hasattr(shadow_fusion, "fetch_kalyan_city")
    assert not hasattr(shadow_fusion, "SHADOW_KALYAN_CITIES")
    assert "kalyan_failures" not in result and "cities" not in result
    assert {row["source"] for row in persisted} == {"ibja", "grt", "malabar"}
    assert all(row["city"] is None for row in persisted)
    # The on-disk summary is valid JSON with the national benchmark populated.
    on_disk = json.loads(shadow_fusion.SHADOW_OUTPUT_PATH.read_text(encoding="utf-8"))
    assert on_disk["national_benchmark"]["value"] == pytest.approx(
        result["national_benchmark"]["value"]
    )
    assert on_disk["snapshot_rows_persisted"] == 3
