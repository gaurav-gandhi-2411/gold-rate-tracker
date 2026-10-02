"""scripts/analysis_proxy_subperiods.py — sub-period stability of the frozen
config J (ADR 034/038) on the INR proxy dead-zone history, and the effect of
the proxy arm's same-day India VIX feature.

The pre-registered proxy arm (ml.direction.preregistration.run_proxy_arm)
labels day t by the proxy's own t-1 -> t move and uses india_vix AT day t as a
feature: a same-day close predicting a same-day move. This script scores
config J twice on the same dead-zone rows — as registered, and with the India
VIX close of the last trading day strictly before t — and splits each by
calendar sub-period.

Test: one-sided HAC Diebold-Mariano (lag 0, h1-equivalent label) on 0/1 loss
vs (a) the per-fold training-majority class [primary] and (b) always-up.
Bonferroni and BH across the sub-period x variant family. Shadow research:
writes reports/proxy_subperiods_config_j.json only.

Run: python scripts/analysis_proxy_subperiods.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import warnings
from datetime import UTC, datetime
from pathlib import Path

# Run as `python scripts/<name>.py` from the repo root: add the root for `import ml`.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import yfinance as yf
from ml.direction.config_sweep import M1_DRIVER_COLS, run_config_sweep
from ml.direction.evaluate_reframed import diebold_mariano_test
from ml.direction.preregistration import (
    PREREGISTERED_CONFIG,
    build_proxy_deadzone_dataset,
)
from ml.direction.stats_corrections import benjamini_hochberg, bonferroni

OUTPUT_PATH = ROOT / "reports" / "proxy_subperiods_config_j.json"
SUB_PERIODS = [
    ("2013-2017", "2013-01-01", "2018-01-01"),
    ("2018-2021", "2018-01-01", "2022-01-01"),
    ("2022-2026", "2022-01-01", "2027-01-01"),
    ("all", "2000-01-01", "2100-01-01"),
]


def _test(y: np.ndarray, prob: np.ndarray, majority: np.ndarray) -> dict:
    wrong = ((prob >= 0.5).astype(int) != y).astype(float)
    maj_wrong = (majority != y).astype(float)
    up_wrong = (1 - y).astype(float)
    dm = diebold_mariano_test(wrong.tolist(), maj_wrong.tolist(), 1, alternative="less")
    dm_up = diebold_mariano_test(wrong.tolist(), up_wrong.tolist(), 1, alternative="less")
    n = len(y)
    return {
        "n": n,
        "effective_n": dm["effective_n"],
        "accuracy": float(1 - wrong.mean()) if n else None,
        "majority_class_accuracy": float(1 - maj_wrong.mean()) if n else None,
        "p_one_sided_vs_majority": dm["p_value"],
        "always_up_accuracy": float(1 - up_wrong.mean()) if n else None,
        "p_one_sided_vs_always_up": dm_up["p_value"],
    }


def _score(dataset: pd.DataFrame, label: str) -> dict:
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
    y = np.asarray(raw["y_true"], dtype=int)
    prob = np.asarray(raw["y_prob"], dtype=float)
    dates = np.asarray(raw["as_of_date"])
    # Per-fold training majority: every earlier dead-zone row is training data
    # (same-day label, so each has matured by the next test day).
    labels_all = dataset["label_binary_deadzone"].astype(int).to_numpy()
    as_of_all = dataset["as_of_date"].astype(str).to_numpy()
    majority = np.array([int(labels_all[as_of_all < d].mean() >= 0.5) for d in dates], dtype=int)
    out: dict = {"variant": label}
    for name, lo, hi in SUB_PERIODS:
        mask = (dates >= lo) & (dates < hi)
        out[name] = _test(y[mask], prob[mask], majority[mask])
    return out


def _prior_trading_day_vix(as_of_dates: pd.Series) -> list[float]:
    """India VIX close of the last trading day strictly BEFORE each date --
    known before that date's own move, unlike the registered same-day value."""
    dates = pd.to_datetime(as_of_dates)
    start = (dates.min() - pd.Timedelta(days=15)).strftime("%Y-%m-%d")
    end = (dates.max() + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    raw = yf.download(
        "^INDIAVIX", start=start, end=end, auto_adjust=True, progress=False, threads=False
    )
    close = raw[("Close", "^INDIAVIX")] if isinstance(raw.columns, pd.MultiIndex) else raw["Close"]
    close = close.dropna()
    close.index = pd.to_datetime(close.index).tz_localize(None)
    values = []
    for d in dates:
        earlier = close[close.index < d]
        values.append(float(earlier.iloc[-1]) if len(earlier) else np.nan)
    return values


def main() -> int:
    warnings.filterwarnings("ignore")
    registered = build_proxy_deadzone_dataset()
    lagged = registered.copy()
    lagged["india_vix"] = _prior_trading_day_vix(registered["as_of_date"])
    n_missing = int(lagged["india_vix"].isna().sum())

    variants = [
        _score(registered, "registered (same-day india_vix)"),
        _score(lagged, "india_vix = prior trading day close"),
    ]
    family = [
        (v["variant"], name, v[name]["p_one_sided_vs_majority"])
        for v in variants
        for name, *_ in SUB_PERIODS
        if v[name]["p_one_sided_vs_majority"] is not None
    ]
    ps = [p for *_, p in family]
    bon, bh = bonferroni(ps), benjamini_hochberg(ps)
    corrections = [
        {"variant": v, "period": n, "p": p, "bonferroni": bool(b), "bh": bool(h)}
        for (v, n, p), b, h in zip(family, bon["significant"], bh["significant"], strict=True)
    ]
    out = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "git_sha": subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
        ).stdout.strip(),
        "rows": len(registered),
        "lagged_variant_rows_missing_vix": n_missing,
        "as_of_range": [str(registered["as_of_date"].min()), str(registered["as_of_date"].max())],
        "variants": variants,
        "bonferroni_threshold": bon["threshold"],
        "corrections": corrections,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(out, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
