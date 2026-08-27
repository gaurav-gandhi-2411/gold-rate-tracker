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

# Nominal coverage level for the displayed est_low/est_high band, as an EXPLICIT
# constant rather than an implicit Gaussian sigma. 80% matches the nominal level
# ml.metrics.compute_band_coverage already uses for the separate headline.lower/
# upper conformal PI (data/coverage_metrics.json), giving one consistent "likely
# range" vocabulary across both bands on the page. Chosen from a strict walk-
# forward audit (fit on pairs strictly before each scored date, band from
# recency-WEIGHTED empirical quantiles of that same static fit's in-sample
# residuals -- see _weighted_percentile -- no future leakage): at n=65 scored
# days (45 same-day + 20 asof-matched carry-forward, mirroring exactly which
# days ml.inference._try_ibja_calibrated would display this tier on),
# empirical coverage was 70.8/83.1/92.3% against nominal 68/80/90%
# respectively -- essentially matching nominal within Wilson-CI noise at this
# sample size. See evaluate_empirical_band_coverage's docstring and the PR
# that introduced this constant for the full table (session dated 2026-08-27).
NOMINAL_COVERAGE_PCT = 80
_RESIDUAL_QUANTILE_LEVELS: tuple[int, ...] = (68, 80, 90)

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


def _weighted_percentile(values: np.ndarray, weights: np.ndarray, percentile: float) -> float:
    """Weighted analogue of np.percentile (linear interpolation on the weighted CDF).

    Exists because the residual quantile that sizes the displayed band must use
    the SAME recency weights as the slope/intercept fit those residuals came
    from -- an audit (session dated 2026-08-27) found that using
    np.percentile's plain UNweighted quantile on a recency-WEIGHTED fit's
    residuals silently let older, larger, less-relevant residuals inflate the
    band: mean |residual| in the early half of each walk-forward training
    window measured ~21% larger than the late half (99.5 vs 82.4 Rs/g,
    n=45 windows), so an unweighted quantile is stale-dispersion-inflated
    relative to the fit's own effective (recency-weighted) sample. Switching
    to this weighted quantile closed a measured 45.3%-vs-68.3% (the pre-PR
    Gaussian-sigma band) / 84.6%-vs-80% (this PR's first unweighted-quantile
    draft, n=65) -type over-coverage gap down to within Wilson-CI noise at
    every nominal level tested -- see fit_calibration's own docstring for
    the numbers.
    """
    order = np.argsort(values)
    v = values[order]
    w = weights[order]
    cum_w = np.cumsum(w)
    cum_w = cum_w / cum_w[-1]
    target = percentile / 100.0
    idx = int(np.searchsorted(cum_w, target))
    idx = min(idx, len(v) - 1)
    return float(v[idx])


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
    # Empirical |residual| quantiles of the in-sample fit residuals above
    # (residual_std's own array, not resampled) at _RESIDUAL_QUANTILE_LEVELS,
    # keyed by level as a string ("68"/"80"/"90"). This is what actually sizes
    # the displayed band now (ml.inference reads residual_abs_quantiles[str(
    # NOMINAL_COVERAGE_PCT)]) -- replacing a Gaussian one-sigma assumption
    # (residual_std_oos applied as if it were a symmetric normal band) that a
    # walk-forward coverage audit found badly miscalibrated: 45.3% observed vs
    # 68.3% nominal at n=75 (session dated 2026-08-27). residual_std_oos/mae_oos
    # above remain persisted for their own honest purpose (ADR 027's OOS fit-
    # quality reporting) but are no longer used to size the band.
    residual_abs_quantiles: dict[str, float] | None = None


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
    abs_residuals = np.abs(residuals)
    # Weighted quantile, using the SAME recency weights the fit above used --
    # see _weighted_percentile's docstring for why an unweighted quantile here
    # would silently inflate the band with stale, larger, less-relevant
    # residuals from early in the window.
    residual_abs_quantiles = {
        str(level): _weighted_percentile(abs_residuals, weights, level)
        for level in _RESIDUAL_QUANTILE_LEVELS
    }

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
        residual_abs_quantiles=residual_abs_quantiles,
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


# Must match ml.inference._IBJA_DISPLAY_MAX_AGE_DAYS. Not imported directly to
# avoid a circular import (ml.inference imports NOMINAL_COVERAGE_PCT from this
# module); kept in sync by the assertion in
# tests/test_calibration.py::test_max_age_days_matches_inference_constant.
_SCORING_MAX_IBJA_AGE_DAYS = 14


def evaluate_empirical_band_coverage(
    ibja_df: pd.DataFrame,
    tanishq_df: pd.DataFrame,
    level: int,
    huber_epsilon: float = _DEFAULT_HUBER_EPSILON,
    half_life: float = _DEFAULT_HALF_LIFE,
    min_train: int = _MIN_FIT_OBSERVATIONS,
    max_age_days: int = _SCORING_MAX_IBJA_AGE_DAYS,
) -> dict:
    """Walk-forward coverage of an empirical-quantile band, matching PRODUCTION
    exactly -- not the same thing walk_forward_validate measures.

    TRAINING set: same-day IBJA/Tanishq pairs only (via _merge_overlap's exact
    date join) -- identical to what fit_calibration fits on. SCORING set: every
    Tanishq daily reading date, asof-matched (backward) to the most recent IBJA
    date at or before it, gated at max_age_days -- this mirrors what
    ml.inference._try_ibja_calibrated actually does in production (uses
    whatever the LATEST available IBJA row is, not requiring an exact same-day
    match, with the same max-age gate), so this function scores every day
    production would have actually displayed a tier-2 estimate on, not only
    the subset where IBJA happened to publish same-day.

    At each scored date t: fit slope/intercept on same-day training pairs
    STRICTLY BEFORE t (date < t, recency-weighted -- static, as production
    does; no future information reaches the fit at any step), form a band from
    the EMPIRICAL |residual| quantile at `level`, weighted with the SAME
    recency weights as the fit (_weighted_percentile, not np.percentile -- an
    audit found an unweighted quantile here systematically over-covers,
    because it lets older/larger/less-relevant residuals from early in each
    expanding window inflate the band relative to the fit's own effective
    (recency-weighted) sample -- see the PR that introduced _weighted_
    percentile for the full simulation-based audit), then score whether t's
    actual tanishq_22k falls within predicted_t +/- that band.

    Distinct from walk_forward_validate: that function's residual_std_oos/
    mae_oos summarize the sequence of single-point OOS residuals themselves
    (a genuinely-out-of-sample fit-quality estimate, same-day training points
    only). This function instead asks the honest production question -- "if I
    size today's band from whatever static fit is currently live, how often
    does the real reading actually land inside it, on every day production
    would have shown this tier". Measured on the real ibja_rates.parquet/
    prices.json overlap (n=65 scored days: n=45 same-day + n=20 asof-matched
    carry-forward, after the min_train warmup): weighted-quantile coverage is
    70.8/83.1/92.3% against nominal 68/80/90% (Wilson 95% CI at 80% nominal:
    [72.2%, 90.3%], n=65). half_life=10 was NOT selected by tuning against
    this coverage number -- it is the pre-existing value the point-estimate
    fit already uses (ADR 027, chosen for MAE_oos, before this coverage
    audit existed); a diagnostic sweep across half_life in {5,8,10,15,20,25,
    30} on this exact scoring set showed 10 is not uniquely best (15/20/25
    look as good or better at some levels), and a held-out check restricted
    to the 15 most recent scored dates (2026-08-12 to 2026-08-27, all after
    ADR 027's own 2026-07-17 sweep-data cutoff, so genuinely untouched by
    that selection) gave 46.7/80.0/93.3% at half_life=10 -- noisy at this
    small n but not systematically over-covering the way the unweighted
    quantile did. See tests/test_calibration.py's regression test for the
    ongoing check.

    Returns {"n": int, "n_in_band": int, "coverage": float | None}.
    coverage is None when n == 0 (not enough history to score even one day).
    """
    same_day = _merge_overlap(ibja_df, tanishq_df)
    X = same_day["ibja_per_g"].to_numpy().reshape(-1, 1)
    y = same_day["tanishq_22k"].to_numpy()
    same_day_dates = pd.to_datetime(same_day["date"]).to_numpy()

    ibja_sorted = ibja_df[["date", "pm_916"]].dropna(subset=["pm_916"]).copy()
    ibja_sorted["date_dt"] = pd.to_datetime(ibja_sorted["date"])
    ibja_sorted["ibja_per_g"] = ibja_sorted["pm_916"] / 10.0
    ibja_sorted = ibja_sorted.sort_values("date_dt")

    tanishq_sorted = tanishq_df[["date", "22k"]].copy()
    tanishq_sorted["date_dt"] = pd.to_datetime(tanishq_sorted["date"])
    tanishq_sorted = tanishq_sorted.sort_values("date_dt")

    scoring = pd.merge_asof(
        tanishq_sorted,
        ibja_sorted[["date_dt", "ibja_per_g", "date"]].rename(columns={"date": "ibja_date"}),
        on="date_dt",
        direction="backward",
    )
    scoring = scoring.dropna(subset=["ibja_per_g"])
    scoring["gap_days"] = (scoring["date_dt"] - pd.to_datetime(scoring["ibja_date"])).dt.days
    scoring = (
        scoring[scoring["gap_days"] < max_age_days].sort_values("date_dt").reset_index(drop=True)
    )

    n_in_band = 0
    n_scored = 0
    for _, srow in scoring.iterrows():
        t = np.datetime64(srow["date_dt"])
        train_mask = same_day_dates < t  # strictly before -- no leakage
        n_train = int(train_mask.sum())
        if n_train < min_train:
            continue

        X_train = X[train_mask]
        y_train = y[train_mask]
        weights = _recency_weights(n_train, half_life)
        slope, intercept = _fit_robust(
            X_train, y_train, huber_epsilon=huber_epsilon, weights=weights
        )

        train_pred = slope * X_train[:, 0] + intercept
        train_abs_residuals = np.abs(y_train - train_pred)
        band_half_width = _weighted_percentile(train_abs_residuals, weights, level)

        pred_t = slope * srow["ibja_per_g"] + intercept
        n_scored += 1
        if abs(srow["22k"] - pred_t) <= band_half_width:
            n_in_band += 1

    coverage = (n_in_band / n_scored) if n_scored > 0 else None
    return {"n": n_scored, "n_in_band": n_in_band, "coverage": coverage}


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
    schema_version 3: adds residual_abs_quantiles, which now sizes the
    displayed band (see NOMINAL_COVERAGE_PCT) in place of residual_std_oos.
    A schema_version <3 file still loads fine -- ml.inference falls back to
    the old Gaussian-sigma band when residual_abs_quantiles is absent.
    """
    p = path or CALIBRATION_JSON
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {**asdict(params), "valid": True, "schema_version": 3}
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
        residual_abs_quantiles=raw.get("residual_abs_quantiles"),
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
