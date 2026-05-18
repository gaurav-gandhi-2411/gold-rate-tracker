"""Tanishq-vs-IBJA calibration layer.

Tanishq retail price displays PRE-GST. Verified 2026-05-19 by sampling
21 aligned trading days (2026-04-17 to 2026-05-18):
  typical ratio tanishq_22k / ibja_916_pm_per_gram ≈ 1.017 (median over 21 pairs).
  Single-day spot check: tanishq_22k = ₹14,345/g, ibja_916_pm = ₹14,448.9/g, ratio = 0.9928.
  The spot ratio is depressed by a lag artefact: IBJA dropped ~2% on 2026-05-15→18
  while Tanishq had not yet updated. Median 1.017 over 21 days is the representative value.
The calibration learned by this module captures markup only (no GST component).

Low-ratio outliers occur when IBJA moves sharply intraday and Tanishq's published
rate trails by one session. HuberRegressor is robust to these lag artefacts.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import pandas as pd
from sklearn.linear_model import HuberRegressor
from sklearn.metrics import r2_score

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CALIBRATION_JSON = DATA_DIR / "calibration.json"

_MIN_FIT_OBSERVATIONS = 30
_REFIT_NEW_PAIRS = 10
_DEFAULT_HUBER_EPSILON = 1.35

logger = logging.getLogger(__name__)


@dataclass
class CalibrationParams:
    slope: float
    intercept: float
    fit_date: str
    n_observations: int
    residual_std: float
    r_squared: float
    huber_epsilon: float


def fit_calibration(
    ibja_df: pd.DataFrame,
    tanishq_df: pd.DataFrame,
    huber_epsilon: float = _DEFAULT_HUBER_EPSILON,
) -> CalibrationParams:
    """Align on UTC date, fit HuberRegressor(ibja_per_g → tanishq_22k), return params.

    ibja_df must have columns ['date', 'pm_916'] with pm_916 in INR/10g.
    tanishq_df must have columns ['date', '22k'] with 22k in INR/g.

    Raises ValueError if fewer than 30 overlap days.
    """
    ibja = ibja_df[["date", "pm_916"]].copy()
    ibja["ibja_per_g"] = ibja["pm_916"] / 10.0

    tanishq = tanishq_df[["date", "22k"]].copy()
    tanishq = tanishq.rename(columns={"22k": "tanishq_22k"})

    merged = pd.merge(ibja[["date", "ibja_per_g"]], tanishq, on="date", how="inner").dropna()

    if len(merged) < _MIN_FIT_OBSERVATIONS:
        raise ValueError(
            f"fit_calibration requires >= {_MIN_FIT_OBSERVATIONS} overlap days; "
            f"got {len(merged)}"
        )

    X = merged["ibja_per_g"].to_numpy().reshape(-1, 1)
    y = merged["tanishq_22k"].to_numpy()

    model = HuberRegressor(epsilon=huber_epsilon, fit_intercept=True)
    model.fit(X, y)

    y_pred = model.predict(X)
    residuals = y - y_pred
    residual_std = float(residuals.std())
    r2 = float(r2_score(y, y_pred))

    return CalibrationParams(
        slope=float(model.coef_[0]),
        intercept=float(model.intercept_),
        fit_date=date.today().isoformat(),
        n_observations=len(merged),
        residual_std=residual_std,
        r_squared=r2,
        huber_epsilon=huber_epsilon,
    )


def apply_calibration(
    ibja_forecast: pd.Series | float,
    params: CalibrationParams,
) -> pd.Series | float:
    """Return tanishq_pred = slope * ibja_per_g + intercept.

    ibja_forecast is expected in INR/g (i.e., ibja_per_10g / 10).
    Vectorized for forecast trajectories.
    """
    return params.slope * ibja_forecast + params.intercept


def save_calibration(params: CalibrationParams, path: Path | None = None) -> None:
    """Serialize CalibrationParams to JSON with schema_version and valid flag."""
    p = path or CALIBRATION_JSON
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {**asdict(params), "valid": True, "schema_version": 1}
    p.write_text(json.dumps(payload, indent=2))
    logger.info("calibration: saved to %s (n=%d)", p, params.n_observations)


def load_calibration(path: Path | None = None) -> CalibrationParams:
    """Load CalibrationParams from JSON. Raises FileNotFoundError if missing."""
    p = path or CALIBRATION_JSON
    raw = json.loads(p.read_text())
    return CalibrationParams(
        slope=raw["slope"],
        intercept=raw["intercept"],
        fit_date=raw["fit_date"],
        n_observations=raw["n_observations"],
        residual_std=raw["residual_std"],
        r_squared=raw["r_squared"],
        huber_epsilon=raw["huber_epsilon"],
    )


def should_refit(
    last_fit_date: date,  # reserved for future time-based trigger; unused now
    current_overlap_count: int,
    last_fit_observations: int,
) -> bool:
    """Return True when 10 or more new aligned pairs have accumulated since last fit."""
    return (current_overlap_count - last_fit_observations) >= _REFIT_NEW_PAIRS
