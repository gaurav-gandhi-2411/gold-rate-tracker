"""Shadow-mode driver for the Kalyan-anchored city fusion (ADR 026).

Fetches all registered sources, fuses a national benchmark and per-city
prices, persists PIT snapshots, and writes a shadow output summary --
WITHOUT touching ``data/forecast.json``, ``app.js``, or anything the live
site displays. This is Phase C: run silently, accumulate history, validate
against ground truth over time, before any promotion decision (Phase D).

A single source failing is normal (ADR 025's precedent, extended to all
four sources uniformly) and is not, by itself, a failure of this script.
Only "every national source failed this cycle" is treated as a real
problem (there is then no benchmark to fuse at all) -- this is the one
condition that makes the script exit non-zero, so CI can surface it
distinctly from routine partial degradation.

Usage:
    python -m ml.shadow_fusion
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from ml.fusion import FusedBenchmark, fuse_city_price, fuse_national_benchmark
from ml.fusion_snapshot_store import append_snapshot_rows
from ml.sources.base import SourceNetworkError, SourceReading, SourceStructureError
from ml.sources.grt import fetch_grt
from ml.sources.ibja import fetch_ibja_calibrated
from ml.sources.kalyan import KALYAN_CITIES, fetch_kalyan_city
from ml.sources.malabar import fetch_malabar

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SHADOW_OUTPUT_PATH = DATA_DIR / "shadow_fusion_output.json"

logger = logging.getLogger(__name__)

# National-level source fetchers. Registering a new national source later
# (ADR 026 Option 2) is adding an entry here -- the fusion math (ml.fusion)
# never changes.
_NATIONAL_FETCHERS: dict[str, Callable[[], SourceReading]] = {
    "ibja": fetch_ibja_calibrated,
    "grt": fetch_grt,
    "malabar": fetch_malabar,
}


def _reading_to_snapshot_row(reading: SourceReading, capture_utc: str, as_of_date: str) -> dict:
    return {
        "capture_utc": capture_utc,
        "as_of_date": as_of_date,
        "schema_version": 1,
        "source": reading.source,
        "city": reading.city,
        "rate_22k": reading.rate_22k,
        "observed_at": reading.observed_at.isoformat(),
        "attribution": reading.attribution,
    }


def _fetch_national_readings() -> tuple[list[SourceReading], dict[str, str]]:
    """Fetch every registered national source. Returns (readings, failures).

    ``failures`` maps source name -> a short description of what went
    wrong, distinguishing network vs. structure failures (ADR 026) -- never
    silently swallowed into a single generic bucket.
    """
    readings: list[SourceReading] = []
    failures: dict[str, str] = {}
    for name, fetch_fn in _NATIONAL_FETCHERS.items():
        try:
            readings.append(fetch_fn())
        except SourceNetworkError as exc:
            failures[name] = f"network: {exc}"
            logger.warning("shadow_fusion: %s failed (network): %s", name, exc)
        except SourceStructureError as exc:
            failures[name] = f"structure: {exc}"
            logger.warning(
                "shadow_fusion: %s failed (structure — may need attention): %s", name, exc
            )
    return readings, failures


def _fetch_kalyan_readings() -> tuple[dict[str, SourceReading], dict[str, str]]:
    """Fetch every registered Kalyan city. Returns (readings by city, failures by city)."""
    readings: dict[str, SourceReading] = {}
    failures: dict[str, str] = {}
    for city in KALYAN_CITIES:
        try:
            readings[city] = fetch_kalyan_city(city).reading
        except SourceNetworkError as exc:
            failures[city] = f"network: {exc}"
            logger.warning("shadow_fusion: kalyan/%s failed (network): %s", city, exc)
        except SourceStructureError as exc:
            failures[city] = f"structure: {exc}"
            logger.warning(
                "shadow_fusion: kalyan/%s failed (structure — may need attention): %s", city, exc
            )
    return readings, failures


def run_shadow_cycle() -> dict:
    """Run one fetch -> fuse -> persist cycle. Returns the shadow output dict.

    Raises :class:`RuntimeError` only when every national source failed
    this cycle (no benchmark could be fused at all) -- the one condition
    treated as a real problem rather than routine partial degradation.
    """
    now = datetime.now(UTC)
    capture_utc = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    as_of_date = now.date().isoformat()

    national_readings, national_failures = _fetch_national_readings()
    kalyan_readings, kalyan_failures = _fetch_kalyan_readings()

    snapshot_rows = [
        _reading_to_snapshot_row(r, capture_utc, as_of_date) for r in national_readings
    ]
    snapshot_rows += [
        _reading_to_snapshot_row(r, capture_utc, as_of_date) for r in kalyan_readings.values()
    ]
    n_persisted = append_snapshot_rows(snapshot_rows)

    output: dict = {
        "capture_utc": capture_utc,
        "as_of_date": as_of_date,
        "national_failures": national_failures,
        "kalyan_failures": kalyan_failures,
        "snapshot_rows_persisted": n_persisted,
    }

    if not national_readings:
        output["national_benchmark"] = None
        output["cities"] = {}
        _write_output(output, SHADOW_OUTPUT_PATH)
        raise RuntimeError(
            f"shadow_fusion: ALL national sources failed this cycle — {national_failures}"
        )

    national: FusedBenchmark = fuse_national_benchmark(national_readings)
    output["national_benchmark"] = {
        "value": national.value,
        "band_half_width": national.band_half_width,
        "disagreement": national.disagreement,
        "sources_used": list(national.sources_used),
        "weights_used": national.weights_used,
    }

    cities_output: dict = {}
    for city in KALYAN_CITIES:
        city_reading = kalyan_readings.get(city)
        fused = fuse_city_price(city_reading, national, city=city)
        cities_output[city] = {
            "value": fused.value,
            "band_half_width": fused.band_half_width,
            "coverage": fused.coverage,
            "attribution": fused.attribution,
            "markup": fused.markup,
        }
    output["cities"] = cities_output

    _write_output(output, SHADOW_OUTPUT_PATH)
    return output


def _write_output(output: dict, path: Path = SHADOW_OUTPUT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Trailing newline required: pre-commit's end-of-file-fixer hook (lint.yml)
    # rewrites any file missing one and fails the run — every shadow-fusion CI
    # cycle would otherwise regenerate this file without one and get stuck at
    # the bot-PR-sync step forever (found 2026-07-19, PR #262 timed out this way).
    path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    result = run_shadow_cycle()
    print(json.dumps(result, indent=2))
