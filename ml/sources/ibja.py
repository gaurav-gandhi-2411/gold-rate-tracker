"""IBJA-calibrated national gold-rate adapter for the fusion engine (ADR 026).

This is deliberately a small, independent read of the same two artifacts
``ml.inference._select_price_source`` uses (``calibration.json`` +
``ibja_rates.parquet``), applying the identical calibration math
(``calibrated_22k = slope * ibja_per_g + intercept``). It does NOT reuse
``_select_price_source`` itself: that function additionally gates on
Tanishq-scrape freshness (only computing a calibrated value when Tanishq is
stale) because it decides what to *display*. The fusion engine wants "what
does IBJA-calibrated currently imply" unconditionally, as one of several
inputs to cross-check against each other — dragging in the display-gating
logic would produce the wrong semantics (silently skipping IBJA whenever
Tanishq happens to be fresh, which is irrelevant to fusion).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ml.sources.base import SourceReading, SourceStructureError

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

_IBJA_PUBLISH_UTC = (11, 30)  # ~17:00 IST, same convention as ml.inference


def fetch_ibja_calibrated(data_dir: Path = DATA_DIR) -> SourceReading:
    """Compute the current IBJA-calibrated 22K estimate.

    Raises :class:`SourceStructureError` if calibration isn't valid yet or
    the IBJA parquet is missing/empty — both mean "this source can't
    currently produce a reading," which the fusion driver treats the same
    way as any other source's failure (log, skip for this cycle).
    """
    calibration_path = data_dir / "calibration.json"
    try:
        calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SourceStructureError(
            f"ibja: calibration.json not found at {calibration_path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise SourceStructureError("ibja: calibration.json is not valid JSON") from exc

    if not calibration.get("valid"):
        raise SourceStructureError(
            "ibja: calibration.valid is False — not enough overlap pairs yet"
        )

    slope = calibration.get("slope")
    intercept = calibration.get("intercept")
    if slope is None or intercept is None:
        raise SourceStructureError("ibja: calibration.json missing slope/intercept")

    try:
        import pandas as pd

        ibja_df = pd.read_parquet(data_dir / "ibja_rates.parquet")
        valid_rows = ibja_df[ibja_df["pm_916"].notna()].sort_values("date")
    except FileNotFoundError as exc:
        raise SourceStructureError("ibja: ibja_rates.parquet not found") from exc
    except Exception as exc:
        raise SourceStructureError(f"ibja: could not read ibja_rates.parquet: {exc}") from exc

    if valid_rows.empty:
        raise SourceStructureError("ibja: no non-null pm_916 rows in ibja_rates.parquet")

    latest = valid_rows.iloc[-1]
    ibja_date_str = str(latest["date"])[:10]
    pm_916 = float(latest["pm_916"])
    ibja_per_g = pm_916 / 10.0
    calibrated_22k = round(slope * ibja_per_g + intercept)

    try:
        y, m, d = int(ibja_date_str[:4]), int(ibja_date_str[5:7]), int(ibja_date_str[8:10])
        observed_at = datetime(y, m, d, *_IBJA_PUBLISH_UTC, tzinfo=UTC)
    except ValueError as exc:
        raise SourceStructureError(f"ibja: unparseable date {ibja_date_str!r}") from exc

    return SourceReading(
        source="ibja",
        city=None,
        rate_22k=float(calibrated_22k),
        observed_at=observed_at,
        attribution=f"IBJA-calibrated estimate (pm_916={pm_916:.1f}, {ibja_date_str})",
    )
