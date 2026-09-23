"""ml.direction.preregistration — GG spec item 4: the #1903/ADR-034 "config J"
candidate (calibrated LightGBM + class_weight="balanced" at h2) was found
significant on the SAME 159/161 folds that were used to diagnose the
flatline and select this fix from 10 candidates tried (ADR 034). Bonferroni
across those 10 configs covers the number of configs tried — it does not
cover the fact that the folds themselves chose the winning config. That is
a real, separate validity gap: this module freezes the exact test BEFORE any
new fold is scored, so a future confirmation is evaluated on data that could
not have influenced the choice of config.

Two shadow arms, both gated on this SAME frozen config/test/threshold:

  1. LIVE arm (`run_live_arm`): re-scores PREREGISTERED_CONFIG on the live
     h2 dataset (`ml.direction.dataset.build_dataset`) every time it is
     called (e.g. from the weekly-backtest.yml cron) — walk-forward folds
     accumulate one at a time as new IBJA labels mature, so each call adds
     at most a handful of genuinely-new folds versus the ADR-034 snapshot.

  2. PROXY arm (`run_proxy_arm`): scores the SAME config (model type,
     calibration, class-weighting) on `ml.inr_proxy_labels`' realigned
     proxy history (2013-2026, n=5014 days), restricted to the dead-zone
     threshold ADR 032 established (only days where the proxy's own implied
     move is >= 100 Rs/gram — the range where proxy-vs-real-IBJA direction
     agreement is 85-100%, not the ~44-54% coin-flip range below it).
     CAVEAT, stated plainly: the proxy label is inherently a SAME-DAY
     (h1-equivalent) direction, not h2 — ADR 034 explicitly found config J's
     effect does NOT replicate at h1 on live IBJA data (49.08% vs 50.92%,
     p=0.453). This proxy-arm result is therefore evidence about whether
     "calibrated GBM + balanced" helps on much more history at h1-equivalent
     framing — a related but DIFFERENT question from the h2 finding being
     pre-registered here, not a replication of it. Reported separately,
     never pooled with the live h2 arm's n.

Neither arm is wired into ml.direction.gate or any user-facing path — see
ml.direction.gate.is_signal_promoted (ADR 036) for the separate, required,
human-approved promotion step. This module writes only to
data/preregistered_h2_shadow_results.json, an append-only shadow log.

PREREGISTRATION FROZEN at 2026-09-23 (docs/adr/038): PREREGISTERED_N_FOR_POWER
below is computed ONCE from the ADR-034 config-J data and MUST NOT be
recomputed from future folds — recomputing the power target from the same
data being accumulated toward it would silently move the goalposts and
defeat the entire purpose of pre-registration.

AMENDMENT A1 (2026-09-23, approved by GG, made BEFORE any post-registration
day was scored — see docs/adr/038 "Amendment A1"): the original protocol
inherited two flaws from the walk-forward it copied. (1) No embargo: train
was every earlier row, so at h2 each fold trained on ~2 rows whose outcomes
had not matured on the test date. (2) It scored every fold, including the
161 folds that SELECTED config J — so the "confirmation" re-counted the very
data it was meant to be independent of. The amended live arm (a) trains only
on rows whose label_date_h2 is strictly before the test as_of_date
(EMBARGO_LABEL_DATE_COL), and (b) scores only test days with as_of_date after
CONFIRMATORY_AFTER_AS_OF. The model configuration, the test, alpha and
PREREGISTERED_N_FOR_POWER are unchanged (frozen). Note: 135.9 was derived
from the leaky effect size; with the embargo, the same 161 folds show no
edge at all (57.76% vs 59.01% always-up, p=0.776), so the true effect — if
any — is smaller than the power target assumes, and the test is optimistic
about its own power. Kept frozen anyway: re-deriving it now would be the
goalpost-moving this module exists to prevent.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ml.direction.config_sweep import run_config_sweep
from ml.direction.dataset import FEATURE_COLS, build_dataset
from ml.direction.evaluate_reframed import diebold_mariano_test

# --- The frozen configuration (ADR 034's "config J") -----------------------
PREREGISTERED_CONFIG: dict = {
    "model": "gbm",
    "class_weight": "balanced",
    "calibrate_gbm": True,
    "label_col": "label_binary_h2",
    "horizon": 2,
    "min_train_size": 20,
    "feature_cols": list(
        FEATURE_COLS
    ),  # live default features only — no M1 drivers (ADR 034: I < F)
}

# One-sided (candidate misclassification loss < always-up misclassification
# loss), HAC-corrected at lag = horizon - 1 = 1, alpha = 0.05. A single
# pre-registered hypothesis — no multiplicity correction needed (that's for
# a FAMILY of configs; this freezes exactly one).
ALTERNATIVE = "less"
ALPHA = 0.05

# Frozen 2026-09-23 from the ADR-034-reproducing run (docs/adr/038):
# n=161, effective_n=119.38, mean_diff=-0.06832, long_run_var=0.10260,
# dm_stat=-2.7065, p=0.00340 (one-sided HAC-DM, lag=1, vs always-up
# misclassification loss). n_for_power at 80%/alpha=0.05 for THIS effect
# size, on the effective_n scale: 135.9. The 161-fold sample that produced
# it was itself nominally underpowered for 80% detection at this effect
# size (119.38 < 135.9) despite reaching p=0.0034 — power calculations
# describe average-case detection, not a guaranteed miss below the
# threshold; reported here exactly as computed, not smoothed over.
# DO NOT recompute from accumulating shadow data (see module docstring).
PREREGISTERED_N_FOR_POWER: float = 135.9

# Amendment A1 (docs/adr/038). Protocol id stored on every logged run so the
# append-only log shows which protocol produced each entry.
PROTOCOL_VERSION = "adr038-A1"
# Training row j is usable for test day i only if label_date_h2[j] < as_of[i]
# (the outcome had matured). At h2 this is an embargo of >= 2 trading days.
EMBARGO_LABEL_DATE_COL = "label_date_h2"
# Registration date. Only test days strictly after it count toward the
# confirmatory test; every earlier day (incl. the 161 selection folds) is
# training data only.
CONFIRMATORY_AFTER_AS_OF = "2026-09-23"

DEAD_ZONE_THRESHOLD_RS_PER_GRAM = 100.0

SHADOW_RESULTS_PATH = Path("data/preregistered_h2_shadow_results.json")


def _misclassification_loss(y_true: list[int], y_prob: list[float]) -> list[float]:
    """0/1 loss at the standard 0.5 threshold — matches ml.direction.gate's
    own accuracy-based G4 condition, not a Brier/log-loss surrogate."""
    return [float((p >= 0.5) != bool(t)) for t, p in zip(y_true, y_prob, strict=True)]


def _always_up_loss(y_true: list[int]) -> list[float]:
    """Misclassification loss of the "always predict up" baseline: wrong
    exactly when the real outcome was down (y_true == 0)."""
    return [float(t == 0) for t in y_true]


def score_config(y_true: list[int], y_prob: list[float], horizon: int) -> dict:
    """Runs the frozen one-sided HAC-DM test on a walk-forward's raw output.
    Shared by both arms so the test itself is byte-identical either way."""
    loss_model = _misclassification_loss(y_true, y_prob)
    loss_baseline = _always_up_loss(y_true)
    dm = diebold_mariano_test(loss_model, loss_baseline, horizon=horizon, alternative=ALTERNATIVE)
    accuracy = float(np.mean([1.0 - v for v in loss_model])) if loss_model else float("nan")
    baseline_accuracy = (
        float(np.mean([1.0 - v for v in loss_baseline])) if loss_baseline else float("nan")
    )
    return {
        "n": dm["n"],
        "effective_n": dm["effective_n"],
        "dm_stat": dm["dm_stat"],
        "p_value": dm["p_value"],
        "mean_diff": dm["mean_diff"],
        "gamma_0": dm["gamma_0"],
        "long_run_var": dm["long_run_var"],
        "accuracy": accuracy,
        "always_up_accuracy": baseline_accuracy,
        "significant_at_05": bool(dm["p_value"] is not None and dm["p_value"] < ALPHA),
    }


def run_live_arm(dataset: pd.DataFrame | None = None) -> dict:
    """Re-scores PREREGISTERED_CONFIG on the live h2 dataset. `dataset` may
    be injected for tests; fetched fresh via build_dataset() otherwise."""
    if dataset is None:
        dataset = build_dataset()
    cfg = PREREGISTERED_CONFIG
    result = run_config_sweep(
        dataset,
        feature_cols=cfg["feature_cols"],
        label_col=cfg["label_col"],
        model=cfg["model"],
        class_weight=cfg["class_weight"],
        calibrate_gbm=cfg["calibrate_gbm"],
        min_train_size=cfg["min_train_size"],
        return_raw=True,
        embargo_label_date_col=EMBARGO_LABEL_DATE_COL,
        score_after_as_of=CONFIRMATORY_AFTER_AS_OF,
    )
    raw = result["raw"]
    scored = score_config(raw["y_true"], raw["y_prob"], horizon=cfg["horizon"])
    scored["arm"] = "live_h2"
    scored["protocol_version"] = PROTOCOL_VERSION
    scored["embargo_label_date_col"] = EMBARGO_LABEL_DATE_COL
    scored["confirmatory_after_as_of"] = CONFIRMATORY_AFTER_AS_OF
    scored["scored_as_of_dates"] = raw["as_of_date"]
    # Audit evidence for the embargo: for every scored day, the latest label
    # date used in its training set. Each must be < the scored as_of_date.
    scored["train_max_label_dates"] = raw["train_max_label_date"]
    return scored


def build_proxy_deadzone_dataset(
    label_path: Path | None = None,
    threshold_rs_per_gram: float = DEAD_ZONE_THRESHOLD_RS_PER_GRAM,
) -> pd.DataFrame:
    """Realigned proxy history (ml.inr_proxy_labels), restricted to the
    dead-zone threshold (ADR 032: only trust direction where the proxy's own
    implied day-over-day move is >= threshold_rs_per_gram — 85-100% real-IBJA
    agreement there, vs ~44-54% below it). Features: the M1 calendar drivers
    (india_vix, wedding/budget/duty-event windows) — the only features
    computable for the full 2013-2026 proxy history; the live feature
    store's snapshot-derived features do not exist this far back.
    """
    from ml.direction.config_sweep import augment_with_m1_drivers
    from ml.inr_proxy_labels import LABEL_OUTPUT_PATH

    path = label_path or LABEL_OUTPUT_PATH
    label_df = pd.read_parquet(path)
    level = label_df["label_22k_per_10g"]
    move_per_10g = level.diff()
    move_per_gram = move_per_10g / 10.0
    dates = pd.DatetimeIndex(label_df.index)

    out = pd.DataFrame(
        {
            "as_of_date": dates.strftime("%Y-%m-%d"),
            "move_per_gram": move_per_gram.to_numpy(),
        }
    )
    out["label_binary_deadzone"] = np.where(
        out["move_per_gram"] > threshold_rs_per_gram,
        1.0,
        np.where(out["move_per_gram"] < -threshold_rs_per_gram, 0.0, np.nan),
    )
    out = out.dropna(subset=["label_binary_deadzone"]).reset_index(drop=True)
    out = augment_with_m1_drivers(out)
    return out


def run_proxy_arm(dataset: pd.DataFrame | None = None) -> dict:
    """Scores the frozen config (model type/calibration/class-weight only —
    feature set differs, see build_proxy_deadzone_dataset) on the realigned
    proxy's dead-zone-thresholded history. h1-equivalent framing — see the
    module docstring's caveat; never pooled with run_live_arm's n."""
    if dataset is None:
        dataset = build_proxy_deadzone_dataset()
    from ml.direction.config_sweep import M1_DRIVER_COLS

    cfg = PREREGISTERED_CONFIG
    result = run_config_sweep(
        dataset,
        feature_cols=M1_DRIVER_COLS,
        label_col="label_binary_deadzone",
        model=cfg["model"],
        class_weight=cfg["class_weight"],
        calibrate_gbm=cfg["calibrate_gbm"],
        min_train_size=cfg["min_train_size"],
        return_raw=True,
    )
    raw = result["raw"]
    # horizon=1: the proxy label is a same-day (t vs t-1) direction, not h2.
    scored = score_config(raw["y_true"], raw["y_prob"], horizon=1)
    scored["arm"] = "proxy_deadzone_h1_equivalent"
    # Exploratory, never confirmatory (ADR 038): scores the full 2013-2026
    # proxy history, not only post-registration days. Its label is same-day,
    # so every earlier training row has matured by the test day — the h1
    # embargo holds by construction.
    scored["protocol_version"] = PROTOCOL_VERSION
    scored["confirmatory"] = False
    return scored


def append_shadow_result(result: dict, path: Path = SHADOW_RESULTS_PATH) -> dict:
    """Append-only log — never overwrites prior entries, so a full history of
    every scored run is preserved for audit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        history = json.loads(path.read_text(encoding="utf-8"))
    else:
        history = {"preregistered_n_for_power": PREREGISTERED_N_FOR_POWER, "runs": []}
    entry = dict(result)
    entry["scored_at_utc"] = datetime.now(UTC).isoformat()
    # The 135.9-effective-fold target is h2-specific (ADR 038) -- only the
    # live_h2 arm's progress toward it is meaningful; the proxy arm uses a
    # different horizon/feature set and is never compared to this threshold.
    # effective_n is None when fewer than 2 post-registration days have been
    # scored (early weeks) — that is "not reached", not an error.
    if entry.get("arm") == "live_h2":
        entry["reached_preregistered_n"] = (
            result.get("effective_n") or 0.0
        ) >= PREREGISTERED_N_FOR_POWER
    else:
        entry["reached_preregistered_n"] = None
    history["runs"].append(entry)
    path.write_text(json.dumps(history, indent=2, default=str) + "\n", encoding="utf-8")
    return history
