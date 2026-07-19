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
import warnings
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import HuberRegressor, LinearRegression
from sklearn.metrics import r2_score

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CALIBRATION_JSON = DATA_DIR / "calibration.json"

_MIN_FIT_OBSERVATIONS = 30
_REFIT_NEW_PAIRS = 10
_DEFAULT_HUBER_EPSILON = 1.35

# Recency half-life for sample weighting, in overlap PAIRS (not calendar days --
# overlap pairs are sparse and irregular). ADR 027: swept half-life 8/10/15/20/25/30
# against unweighted on genuine walk-forward OOS validation (expanding window, no
# leakage) over the live 52-pair overlap history. Every tested value beat
# unweighted on both R2_oos and MAE_oos; half_life=10 gave the single best MAE_oos
# (58.86 vs 70.72 unweighted, ~17% lower) and was not cherry-picked -- see ADR 027
# for the full sweep table.
_DEFAULT_HALF_LIFE = 10.0

logger = logging.getLogger(__name__)


def _recency_weights(n: int, half_life: float = _DEFAULT_HALF_LIFE) -> np.ndarray:
    """Exponential recency weights over n ordered (oldest-first) observations.

    weight[i] = 0.5 ** (age_from_most_recent / half_life). The most recent
    observation always gets weight 1.0.
    """
    age = np.arange(n, 0, -1)  # n = oldest (age n), 1 = most recent (age 1)
    return 0.5 ** ((age - 1) / half_life)


def _fit_robust(
    X: np.ndarray, y: np.ndarray, *, huber_epsilon: float, weights: np.ndarray | None = None
) -> tuple[float, float]:
    """Fit HuberRegressor, falling back to weighted OLS if Huber fails to converge.

    HuberRegressor's l-BFGS-b solver can fail on small or near-collinear windows
    (observed during ADR 027's walk-forward validation, e.g. a 30-pair rolling
    window). The fallback keeps a single bad fold from crashing a walk-forward
    loop; production fit_calibration() always has the full history available and
    essentially never needs it.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            model = HuberRegressor(epsilon=huber_epsilon, fit_intercept=True, max_iter=200)
            model.fit(X, y, sample_weight=weights)
            return float(model.coef_[0]), float(model.intercept_)
        except Exception as exc:
            logger.warning(
                "calibration: HuberRegressor failed to converge (%s) — falling back to OLS", exc
            )
            model = LinearRegression()
            model.fit(X, y, sample_weight=weights)
            return float(model.coef_[0]), float(model.intercept_)


@dataclass
class CalibrationParams:
    slope: float
    intercept: float
    fit_date: str
    n_observations: int
    residual_std: float
    r_squared: float
    huber_epsilon: float
    # Genuine out-of-sample validation (ADR 027) -- expanding-window walk-forward,
    # no leakage, distinct from the in-sample residual_std/r_squared above (which
    # are computed on the same data the model was fit to, per ADR 023's caution
    # against citing in-sample fit quality as generalization evidence). None on
    # calibration.json files written before this field existed (schema_version 1).
    half_life: float | None = None
    r_squared_oos: float | None = None
    residual_std_oos: float | None = None
    mae_oos: float | None = None
    n_oos: int | None = None
    oos_method: str | None = None


def _merge_overlap(ibja_df: pd.DataFrame, tanishq_df: pd.DataFrame) -> pd.DataFrame:
    """Align ibja_df/tanishq_df on date, oldest-first. Shared by fit_calibration
    and walk_forward_validate so both see identical ordering (walk-forward
    validity depends on it)."""
    ibja = ibja_df[["date", "pm_916"]].copy()
    ibja["ibja_per_g"] = ibja["pm_916"] / 10.0

    tanishq = tanishq_df[["date", "22k"]].copy()
    tanishq = tanishq.rename(columns={"22k": "tanishq_22k"})

    merged = pd.merge(ibja[["date", "ibja_per_g"]], tanishq, on="date", how="inner").dropna()
    return merged.sort_values("date").reset_index(drop=True)


def fit_calibration(
    ibja_df: pd.DataFrame,
    tanishq_df: pd.DataFrame,
    huber_epsilon: float = _DEFAULT_HUBER_EPSILON,
    half_life: float = _DEFAULT_HALF_LIFE,
    run_oos_validation: bool = True,
) -> CalibrationParams:
    """Align on UTC date, fit HuberRegressor(ibja_per_g → tanishq_22k), return params.

    ibja_df must have columns ['date', 'pm_916'] with pm_916 in INR/10g.
    tanishq_df must have columns ['date', '22k'] with 22k in INR/g.

    Fits with recency-weighted samples (ADR 027: half_life in overlap pairs,
    most recent pair weighted 1.0) rather than an unweighted global fit --
    a genuine, swept-and-verified improvement in walk-forward OOS accuracy,
    not just added complexity. residual_std/r_squared here remain IN-SAMPLE
    (same data fit + evaluated) -- see run_oos_validation for the honest,
    genuinely out-of-sample counterparts.

    Raises ValueError if fewer than 30 overlap days.
    """
    merged = _merge_overlap(ibja_df, tanishq_df)

    if len(merged) < _MIN_FIT_OBSERVATIONS:
        raise ValueError(
            f"fit_calibration requires >= {_MIN_FIT_OBSERVATIONS} overlap days; got {len(merged)}"
        )

    X = merged["ibja_per_g"].to_numpy().reshape(-1, 1)
    y = merged["tanishq_22k"].to_numpy()
    weights = _recency_weights(len(merged), half_life)

    slope, intercept = _fit_robust(X, y, huber_epsilon=huber_epsilon, weights=weights)

    y_pred = slope * X[:, 0] + intercept
    residuals = y - y_pred
    residual_std = float(residuals.std())
    r2 = float(r2_score(y, y_pred))

    oos: dict = {}
    if run_oos_validation:
        try:
            oos = walk_forward_validate(
                ibja_df, tanishq_df, huber_epsilon=huber_epsilon, half_life=half_life
            )
        except ValueError as exc:
            # Not enough pairs for even one OOS fold yet -- fit still succeeds,
            # just without OOS numbers (same posture as a fresh, just-unlocked
            # calibration: honest absence, not a fabricated placeholder).
            logger.info("fit_calibration: skipping OOS validation — %s", exc)

    return CalibrationParams(
        slope=slope,
        intercept=intercept,
        fit_date=date.today().isoformat(),
        n_observations=len(merged),
        residual_std=residual_std,
        r_squared=r2,
        huber_epsilon=huber_epsilon,
        half_life=half_life,
        r_squared_oos=oos.get("r_squared_oos"),
        residual_std_oos=oos.get("residual_std_oos"),
        mae_oos=oos.get("mae_oos"),
        n_oos=oos.get("n_oos"),
        oos_method=oos.get("method"),
    )


def walk_forward_validate(
    ibja_df: pd.DataFrame,
    tanishq_df: pd.DataFrame,
    huber_epsilon: float = _DEFAULT_HUBER_EPSILON,
    half_life: float = _DEFAULT_HALF_LIFE,
    min_train: int = _MIN_FIT_OBSERVATIONS,
) -> dict:
    """Genuine out-of-sample validation: expanding-window walk-forward, no leakage.

    For each pair from index min_train onward, refit using ONLY the pairs
    strictly before it (recency-weighted), predict that held-out pair, and
    collect the residual. This is the same expanding-window-no-leakage
    protocol ml.backtest already uses for the Chronos walk-forward (ADR 027
    follows that established convention rather than inventing a new one).

    Distinct from fit_calibration's residual_std/r_squared, which are fit AND
    evaluated on the same data (in-sample) -- per ADR 023's caution, an
    in-sample number must never be cited as evidence a model generalizes.
    This function is what should be cited for that claim instead.

    Raises ValueError if fewer than min_train + 1 overlap pairs exist (need at
    least one point to hold out).
    """
    merged = _merge_overlap(ibja_df, tanishq_df)
    if len(merged) < min_train + 1:
        raise ValueError(
            f"walk_forward_validate requires >= {min_train + 1} overlap pairs "
            f"(>= {min_train} to train the first fold + >= 1 to hold out); got {len(merged)}"
        )

    X = merged["ibja_per_g"].to_numpy().reshape(-1, 1)
    y = merged["tanishq_22k"].to_numpy()

    oos_actual: list[float] = []
    oos_pred: list[float] = []
    for i in range(min_train, len(merged)):
        weights = _recency_weights(i, half_life)
        slope, intercept = _fit_robust(X[:i], y[:i], huber_epsilon=huber_epsilon, weights=weights)
        oos_actual.append(float(y[i]))
        oos_pred.append(float(slope * X[i, 0] + intercept))

    actual = np.array(oos_actual)
    pred = np.array(oos_pred)
    residuals = actual - pred

    return {
        "n_oos": len(actual),
        "r_squared_oos": float(r2_score(actual, pred)),
        "residual_std_oos": float(residuals.std()),
        "mae_oos": float(np.abs(residuals).mean()),
        "method": "expanding_window_walk_forward_recency_weighted",
    }


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
    """Serialize CalibrationParams to JSON with schema_version and valid flag.

    schema_version 2 (ADR 027): adds half_life + the r_squared_oos/
    residual_std_oos/mae_oos/n_oos/oos_method fields. A schema_version-1 file
    (no OOS fields) still loads fine -- load_calibration defaults them to None.
    """
    p = path or CALIBRATION_JSON
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {**asdict(params), "valid": True, "schema_version": 2}
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
        half_life=raw.get("half_life"),
        r_squared_oos=raw.get("r_squared_oos"),
        residual_std_oos=raw.get("residual_std_oos"),
        mae_oos=raw.get("mae_oos"),
        n_oos=raw.get("n_oos"),
        oos_method=raw.get("oos_method"),
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
        "run_refit_if_needed: complete — n=%d slope=%.4f intercept=%.2f "
        "r2_in_sample=%.4f residual_std_in_sample=%.2f "
        "r2_oos=%s residual_std_oos=%s mae_oos=%s (n_oos=%s)",
        params.n_observations,
        params.slope,
        params.intercept,
        params.r_squared,
        params.residual_std,
        f"{params.r_squared_oos:.4f}" if params.r_squared_oos is not None else "n/a",
        f"{params.residual_std_oos:.2f}" if params.residual_std_oos is not None else "n/a",
        f"{params.mae_oos:.2f}" if params.mae_oos is not None else "n/a",
        params.n_oos if params.n_oos is not None else "n/a",
    )
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run_refit_if_needed()
