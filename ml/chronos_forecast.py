"""Chronos-Bolt-Tiny inference path for IBJA-916-PM gold rate forecasting.

This module is a parallel inference path — it does NOT replace ml/inference.py.
It writes exclusively to data/chronos_probe.json; it never touches forecast.json.
FORECAST_ENGINE env var stays "legacy" until PR H flips it to "chronos".

Design:
- forecast_ibja: converts IBJA series → 5-day p10/p50/p90 forecast (IBJA level, INR/g)
- chronos_to_tanishq: applies ml.calibration to convert IBJA forecast → Tanishq level
- run_probe: CI entry point, writes timing + forecast metadata to data/chronos_probe.json
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CHRONOS_PROBE_JSON = DATA_DIR / "chronos_probe.json"
IBJA_PARQUET = DATA_DIR / "ibja_rates.parquet"
CALIBRATION_JSON = DATA_DIR / "calibration.json"

# Pinned revision SHA — amazon/chronos-bolt-tiny on HuggingFace (2025-11-21)
CHRONOS_BOLT_TINY_MODEL_ID = "amazon/chronos-bolt-tiny"
CHRONOS_BOLT_TINY_REVISION = "a0e552de83495b5c28c14c71c374f3e33280b340"

DEFAULT_HORIZON = 5
DEFAULT_QUANTILES = [0.1, 0.5, 0.9]
_MIN_CONTEXT_DAYS = 8

logger = logging.getLogger(__name__)


def load_chronos_pipeline(
    model_id: str = CHRONOS_BOLT_TINY_MODEL_ID,
    revision: str | None = CHRONOS_BOLT_TINY_REVISION,
):
    """Load ChronosBoltPipeline from HuggingFace Hub (cached after first download)."""
    from chronos import ChronosBoltPipeline

    return ChronosBoltPipeline.from_pretrained(
        model_id,
        revision=revision,
        device_map="cpu",
    )


def forecast_ibja(
    pipeline,
    ibja_series: pd.Series,
    horizon: int = DEFAULT_HORIZON,
    quantile_levels: list[float] = DEFAULT_QUANTILES,
) -> pd.DataFrame:
    """Forecast IBJA-916-PM forward `horizon` days.

    Parameters
    ----------
    ibja_series : date-indexed daily Series, values in INR/g (pm_916 / 10).
    horizon : number of days to forecast.
    quantile_levels : quantiles to compute; default [0.1, 0.5, 0.9].

    Returns
    -------
    DataFrame with columns ['date', 'p10', 'p50', 'p90'] and `horizon` rows.
    Dates are calendar days (weekends/holidays included in the step sequence).
    """
    import torch

    if len(ibja_series) < _MIN_CONTEXT_DAYS:
        raise ValueError(
            f"insufficient IBJA history for Chronos forecast: "
            f"need >= {_MIN_CONTEXT_DAYS}, got {len(ibja_series)}"
        )

    context = torch.tensor(ibja_series.values, dtype=torch.float32)

    quantiles_t, _ = pipeline.predict_quantiles(
        inputs=context,
        prediction_length=horizon,
        quantile_levels=quantile_levels,
    )
    # quantiles_t shape: (1, horizon, n_quantiles) for single series input
    q = quantiles_t.squeeze(0).numpy()  # (horizon, n_quantiles)

    last_date = pd.Timestamp(ibja_series.index[-1])
    forecast_dates = [
        (last_date + timedelta(days=i + 1)).strftime("%Y-%m-%d") for i in range(horizon)
    ]

    col_map = {0.1: "p10", 0.5: "p50", 0.9: "p90"}
    df = pd.DataFrame({"date": forecast_dates})
    for qi, level in enumerate(quantile_levels):
        col = col_map.get(level, f"p{int(level * 100)}")
        df[col] = q[:, qi].tolist()

    return df


def chronos_to_tanishq(
    ibja_forecast: pd.DataFrame,
    calib_params,
) -> pd.DataFrame:
    """Apply calibration to convert IBJA-level forecast to Tanishq-level.

    ibja_forecast has columns ['date', 'p10', 'p50', 'p90'] in INR/g IBJA.
    Returns same schema with Tanishq-level values.
    """
    from ml.calibration import apply_calibration

    result = ibja_forecast.copy()
    for col in ["p10", "p50", "p90"]:
        if col in result.columns:
            result[col] = apply_calibration(result[col], calib_params)
    return result


def run_probe(
    ibja_parquet_path: Path | None = None,
    calibration_json_path: Path | None = None,
    output_path: Path | None = None,
) -> dict:
    """Run the Chronos probe: load data, forecast, apply calibration, write probe JSON.

    Always writes output_path even on failure — status field communicates the outcome.
    Returns the probe dict.
    """
    from ml.calibration import CalibrationParams, load_calibration

    ibja_path = ibja_parquet_path or IBJA_PARQUET
    calib_path = calibration_json_path or CALIBRATION_JSON
    out_path = output_path or CHRONOS_PROBE_JSON

    probe: dict = {
        "probed_at": datetime.now(UTC).isoformat(),
        "status": "unknown",
        "wall_clock_ms": {},
        "ibja_context_days": 0,
        "ibja_last_date": None,
        "ibja_last_value": None,
        "horizon": DEFAULT_HORIZON,
        "ibja_forecast": [],
        "calibration_applied": False,
        "calibration_valid": False,
        "tanishq_forecast": None,
        "model_version": f"{CHRONOS_BOLT_TINY_MODEL_ID}@{CHRONOS_BOLT_TINY_REVISION[:8]}",
        "schema_version": 1,
    }

    t_total_start = time.monotonic()

    # --- Load IBJA history ---
    if not ibja_path.exists():
        probe["status"] = "insufficient_context"
        _write_probe(probe, out_path)
        return probe

    ibja_df = pd.read_parquet(ibja_path)
    if ibja_df.empty or "pm_916" not in ibja_df.columns:
        probe["status"] = "insufficient_context"
        _write_probe(probe, out_path)
        return probe

    ibja_df = ibja_df.sort_values("date").dropna(subset=["pm_916"])
    ibja_series = ibja_df.set_index("date")["pm_916"] / 10.0  # INR/g

    probe["ibja_context_days"] = len(ibja_series)
    probe["ibja_last_date"] = str(ibja_series.index[-1])
    probe["ibja_last_value"] = round(float(ibja_series.iloc[-1]), 2)

    if len(ibja_series) < _MIN_CONTEXT_DAYS:
        probe["status"] = "insufficient_context"
        _write_probe(probe, out_path)
        return probe

    # --- Load calibration ---
    calib_params: CalibrationParams | None = None
    calib_valid = False
    try:
        raw_calib = json.loads(calib_path.read_text())
        calib_valid = bool(raw_calib.get("valid", False))
        probe["calibration_valid"] = calib_valid
        if calib_valid:
            calib_params = load_calibration(calib_path)
    except Exception as exc:
        logger.warning("chronos_probe: could not load calibration: %s", exc)
        probe["calibration_valid"] = False

    # --- Load pipeline ---
    t_load = time.monotonic()
    try:
        pipeline = load_chronos_pipeline()
    except Exception as exc:
        logger.error("chronos_probe: pipeline load failed: %s", exc)
        probe["status"] = "model_load_failed"
        probe["wall_clock_ms"]["pipeline_load"] = int((time.monotonic() - t_load) * 1000)
        probe["wall_clock_ms"]["total"] = int((time.monotonic() - t_total_start) * 1000)
        _write_probe(probe, out_path)
        return probe
    probe["wall_clock_ms"]["pipeline_load"] = int((time.monotonic() - t_load) * 1000)

    # --- Forecast ---
    t_fc = time.monotonic()
    try:
        ibja_fc = forecast_ibja(pipeline, ibja_series)
    except Exception as exc:
        logger.error("chronos_probe: forecast failed: %s", exc)
        probe["status"] = "forecast_failed"
        probe["wall_clock_ms"]["forecast"] = int((time.monotonic() - t_fc) * 1000)
        probe["wall_clock_ms"]["total"] = int((time.monotonic() - t_total_start) * 1000)
        _write_probe(probe, out_path)
        return probe
    probe["wall_clock_ms"]["forecast"] = int((time.monotonic() - t_fc) * 1000)

    probe["ibja_forecast"] = [
        {
            "day": i + 1,
            "date": row["date"],
            "p10": round(row["p10"], 2),
            "p50": round(row["p50"], 2),
            "p90": round(row["p90"], 2),
        }
        for i, row in ibja_fc.iterrows()
    ]

    # --- Calibration ---
    t_calib = time.monotonic()
    if calib_valid and calib_params is not None:
        try:
            tanishq_fc = chronos_to_tanishq(ibja_fc, calib_params)
            probe["calibration_applied"] = True
            probe["tanishq_forecast"] = [
                {
                    "day": i + 1,
                    "date": row["date"],
                    "p10": round(row["p10"], 2),
                    "p50": round(row["p50"], 2),
                    "p90": round(row["p90"], 2),
                }
                for i, row in tanishq_fc.iterrows()
            ]
        except Exception as exc:
            logger.warning("chronos_probe: calibration application failed: %s", exc)
    probe["wall_clock_ms"]["calibration"] = int((time.monotonic() - t_calib) * 1000)

    probe["wall_clock_ms"]["total"] = int((time.monotonic() - t_total_start) * 1000)
    probe["status"] = "success"
    _write_probe(probe, out_path)
    logger.info(
        "chronos_probe: success — %d context days, wall_clock_total=%dms",
        probe["ibja_context_days"],
        probe["wall_clock_ms"]["total"],
    )
    return probe


def _write_probe(probe: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(probe, indent=2))


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Chronos-Bolt-Tiny IBJA probe")
    parser.add_argument(
        "--probe", action="store_true", help="Run probe and write chronos_probe.json"
    )
    args = parser.parse_args()
    if args.probe:
        result = run_probe()
        raise SystemExit(0 if result["status"] in ("success", "insufficient_context") else 1)
    else:
        parser.print_help()
        raise SystemExit(1)
