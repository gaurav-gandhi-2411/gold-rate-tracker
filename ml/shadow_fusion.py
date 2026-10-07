"""Shadow-mode driver for the national retail-price fusion (ADR 026).

Fetches the national sources (IBJA, GRT, Malabar), fuses a national benchmark, persists
PIT snapshots, and writes a shadow output summary --
WITHOUT touching ``data/forecast.json``, ``app.js``, or anything the live
site displays. This is Phase C: run silently, accumulate history, validate
against ground truth over time, before any promotion decision (Phase D).

Kalyan and the per-city layer were retired 2026-10-05 (ADR 070).

A single source failing is normal (ADR 025's precedent, extended to all
sources uniformly) and is not, by itself, a failure of this script.
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

from ml.fusion import FusedBenchmark, fuse_national_benchmark
from ml.fusion_snapshot_store import append_snapshot_rows
from ml.retailers import KNOWN_RETAILERS as RETAILER_NAMES
from ml.retailers import is_enabled
from ml.sources.base import SourceNetworkError, SourceReading, SourceStructureError
from ml.sources.grt import fetch_grt
from ml.sources.ibja import fetch_ibja_calibrated
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
        if name in RETAILER_NAMES and not is_enabled(name):
            # ADR 059 takedown switch: not fetched, not recorded. Not a failure.
            logger.info("shadow_fusion: %s disabled in config/retailers.json", name)
            continue
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

    snapshot_rows = [
        _reading_to_snapshot_row(r, capture_utc, as_of_date) for r in national_readings
    ]
    n_persisted = append_snapshot_rows(snapshot_rows)

    output: dict = {
        "capture_utc": capture_utc,
        "as_of_date": as_of_date,
        "national_failures": national_failures,
        "snapshot_rows_persisted": n_persisted,
    }

    if not national_readings:
        output["national_benchmark"] = None
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
