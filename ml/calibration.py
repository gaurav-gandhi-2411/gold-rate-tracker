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
            f"fit_calibration requires >= {_MIN_FIT_OBSERVATIONS} overlap days; got {len(merged)}"
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
    p.write_text(json.dumps(payload, indent=2) + "\n")
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


def run_refit_if_needed(data_dir: Path | None = None) -> bool:
    """Check current overlap pair count and refit calibration if warranted.

    Triggers on two conditions:
    - Initial unlock: calibration not yet valid AND overlap_count >= 30.
    - Periodic refit: calibration already valid AND 10+ new pairs since last fit.

    Returns True if a refit was performed, False if skipped.
    Raises on refit failure so the CI step exits non-zero.
    """
    _data = data_dir or DATA_DIR
    ibja_path = _data / "ibja_rates.parquet"
    prices_path = _data / "prices.json"
    cal_path = _data / "calibration.json"

    try:
        with open(cal_path) as f:
            calib: dict = json.load(f)
    except FileNotFoundError:
        calib = {}
    except Exception as exc:
        logger.error("run_refit_if_needed: could not read calibration.json: %s", exc)
        calib = {}

    if not ibja_path.exists():
        logger.info("run_refit_if_needed: ibja_rates.parquet not found — skipping")
        return False

    try:
        import pandas as pd

        ibja_df = pd.read_parquet(ibja_path)
    except Exception as exc:
        logger.error("run_refit_if_needed: could not read ibja_rates.parquet: %s", exc)
        return False

    if not prices_path.exists():
        logger.info("run_refit_if_needed: prices.json not found — skipping")
        return False

    try:
        with open(prices_path) as f:
            prices_raw = json.load(f)
    except Exception as exc:
        logger.error("run_refit_if_needed: could not read prices.json: %s", exc)
        return False

    if not isinstance(prices_raw, list) or not prices_raw:
        logger.info("run_refit_if_needed: prices.json is empty — skipping")
        return False

    # Build tanishq_df: one reading per UTC calendar day (take last reading if multiple).
    # Timestamps in prices.json are UTC ISO-8601; first 10 chars give YYYY-MM-DD.
    rows = [
        {"date": r["timestamp"][:10], "22k": float(r["22k"])}
        for r in prices_raw
        if r.get("timestamp") and r.get("22k") is not None
    ]
    if not rows:
        logger.info("run_refit_if_needed: no valid readings in prices.json — skipping")
        return False

    import pandas as pd

    tanishq_df = pd.DataFrame(rows).sort_values("date").groupby("date").last().reset_index()

    # Only count IBJA dates where pm_916 is non-null — null rows are dropped by
    # fit_calibration's dropna(), so including them would trigger a refit that
    # then raises ValueError("requires >= 30 overlap days; got N<30").
    ibja_valid_dates = set(ibja_df.loc[ibja_df["pm_916"].notna(), "date"].tolist())
    tanishq_dates = set(tanishq_df["date"].tolist())
    overlap_count = len(ibja_valid_dates & tanishq_dates)
    logger.info("run_refit_if_needed: %d valid overlap pairs (pm_916 non-null)", overlap_count)

    if overlap_count < _MIN_FIT_OBSERVATIONS:
        logger.info(
            "run_refit_if_needed: %d < %d overlap pairs — skipping refit",
            overlap_count,
            _MIN_FIT_OBSERVATIONS,
        )
        return False

    last_fit_observations = int(calib.get("n_observations") or 0)
    cal_valid = bool(calib.get("valid", False))
    try:
        last_fit_date = date.fromisoformat(calib.get("fit_date", "2000-01-01"))
    except ValueError:
        last_fit_date = date(2000, 1, 1)

    # Initial unlock: valid=False but threshold reached.
    # Periodic refit: valid=True and enough new pairs have accumulated.
    needs_refit = (not cal_valid and overlap_count >= _MIN_FIT_OBSERVATIONS) or should_refit(
        last_fit_date, overlap_count, last_fit_observations
    )

    if not needs_refit:
        logger.info(
            "run_refit_if_needed: no refit needed (overlap=%d, last_fit_n=%d, valid=%s)",
            overlap_count,
            last_fit_observations,
            cal_valid,
        )
        return False

    logger.info(
        "run_refit_if_needed: refitting (overlap=%d, last_fit_n=%d, valid=%s)",
        overlap_count,
        last_fit_observations,
        cal_valid,
    )
    params = fit_calibration(ibja_df, tanishq_df)
    save_calibration(params, path=cal_path)
    logger.info(
        "run_refit_if_needed: complete — n=%d slope=%.4f intercept=%.2f r2=%.4f residual_std=%.2f",
        params.n_observations,
        params.slope,
        params.intercept,
        params.r_squared,
        params.residual_std,
    )
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run_refit_if_needed()
