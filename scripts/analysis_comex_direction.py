"""scripts/analysis_comex_direction.py — the COMEX daily-direction evaluation
(M2 item 5, #1756), shaped for .github/workflows/analysis.yml.

  --list-shards            JSON list of target/horizon combinations
  --shard KEY --out DIR    run one combination, write DIR/KEY.json
  --aggregate DIR --out F  corrected statistics across all shards -> F

Ground truth is roll-adjusted GC=F (ml.direction.comex_daily), USD/oz. The
walk-forward is ml.direction.evaluate_reframed's embargo-aware harness:
a training row is used only if its label_date_hN is strictly before the
test row's as_of_date. buyer_decision uses the unit-free 0.5% dip variant
(add_buyer_decision_binary_pct); the rupee-threshold version refuses a USD
frame (ml.direction.price_units).

Primary test, per (combination, model): one-sided Diebold-Mariano on 0/1
misclassification loss vs the MAJORITY-CLASS baseline (each fold predicts
the majority label of its own training set), Newey-West HAC with lag = h - 1,
effective n = n * gamma_0 / long-run variance. Bonferroni and
Benjamini-Hochberg across the whole family. Fixed before any full run:
always-up is a straw baseline wherever "up" is the minority label (e.g.
buyer_decision, where 1 = "a dip comes"), so it is reported as secondary.
When up is the training majority the two baselines coincide. Shadow research only: nothing
here touches ml.direction.gate or any user-facing file.
"""

from __future__ import annotations

import argparse
import functools
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

# Run as `python scripts/<name>.py` from the repo root: add the root for `import ml`.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# (target, horizon). h1 is the daily variant; h10 re-tests ADR 037's
# detrended_h10 lead with ~20x the folds. Horizons are calendar days from the
# capture day (comex_daily's label construction).
COMBOS: list[tuple[str, int]] = [
    ("raw_binary", 1),
    ("deadzone", 1),
    ("detrended", 1),
    ("buyer_decision", 1),
    ("raw_binary", 10),
    ("detrended", 10),
]
MODELS = ("logistic_balanced", "gbm_calibrated", "ensemble")
MIN_TRAIN_SIZE = 250  # ~1 trading year warm-up
BUYER_DIP_PCT = 0.5  # ~ the INR series' Rs 50/g band at 2026 prices
# Fixed window end so every shard sees the same data (yfinance at run time).
END_DATE = "2026-09-23"
ALPHA = 0.05
# Sub-period stability: contiguous blocks of test as_of_date.
SUB_PERIODS: list[tuple[str, str, str]] = [
    ("2014-2017", "2014-01-01", "2018-01-01"),
    ("2018-2021", "2018-01-01", "2022-01-01"),
    ("2022-2026", "2022-01-01", "2027-01-01"),
]


def _key(target: str, horizon: int) -> str:
    return f"{target}_h{horizon}"


def _git_sha() -> str:
    out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    return out.stdout.strip()


def run_shard(key: str, out_dir: Path, max_rows: int | None = None) -> Path:
    from ml.direction.comex_daily import COMEX_FEATURE_COLS, build_comex_dataset
    from ml.direction.evaluate_reframed import TARGET_BUILDERS, run_walk_forward_reframed
    from ml.direction.reframed_targets import add_buyer_decision_binary_pct

    by_key = {_key(t, h): (t, h) for t, h in COMBOS}
    if key not in by_key:
        raise SystemExit(f"unknown shard {key!r}; expected one of {sorted(by_key)}")
    target, horizon = by_key[key]
    builders = dict(TARGET_BUILDERS)
    builders["buyer_decision"] = functools.partial(
        add_buyer_decision_binary_pct, dip_pct=BUYER_DIP_PCT
    )

    t0 = time.time()
    horizons = tuple(sorted({h for _, h in COMBOS}))
    df = build_comex_dataset(end=END_DATE, extra_horizons=horizons)
    if max_rows is not None:  # local smoke runs only; the report records it
        df = df.tail(max_rows).reset_index(drop=True)
    print(f"{key}: dataset {len(df)} rows built in {time.time() - t0:.0f}s", flush=True)
    t0 = time.time()
    result = run_walk_forward_reframed(
        df,
        target,
        horizon,
        feature_cols=COMEX_FEATURE_COLS,
        min_train_size=MIN_TRAIN_SIZE,
        target_builders=builders,
    )
    print(f"{key}: {result.get('n_test_folds')} folds in {time.time() - t0:.0f}s", flush=True)
    result["dataset"] = {
        "rows": len(df),
        "as_of_min": str(df["as_of_date"].min()),
        "as_of_max": str(df["as_of_date"].max()),
        "price_units": df.attrs.get("price_units"),
        "max_rows": max_rows,
    }
    result["git_sha"] = _git_sha()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{key}.json"
    path.write_text(json.dumps(result, default=str) + "\n", encoding="utf-8")
    return path


def _accuracy_test(y: np.ndarray, prob: np.ndarray, clim: np.ndarray, horizon: int) -> dict:
    """Primary: vs the per-fold training-majority classifier (climatology
    probability >= 0.5). Secondary: vs always-up."""
    from ml.direction.evaluate_reframed import diebold_mariano_test

    wrong = ((prob >= 0.5).astype(int) != y).astype(float)
    majority_wrong = ((clim >= 0.5).astype(int) != y).astype(float)
    up_wrong = (1 - y).astype(float)
    dm = diebold_mariano_test(wrong.tolist(), majority_wrong.tolist(), horizon, alternative="less")
    dm_up = diebold_mariano_test(wrong.tolist(), up_wrong.tolist(), horizon, alternative="less")
    return {
        "n": len(y),
        "effective_n": dm["effective_n"],
        "accuracy": float(1 - wrong.mean()) if len(y) else None,
        "majority_class_accuracy": float(1 - majority_wrong.mean()) if len(y) else None,
        "dm_stat": dm["dm_stat"],
        "p_one_sided": dm["p_value"],
        "always_up_accuracy": float(1 - up_wrong.mean()) if len(y) else None,
        "p_one_sided_vs_always_up": dm_up["p_value"],
    }


def aggregate(in_dir: Path, out_path: Path) -> dict:
    import numpy as np
    from ml.direction.stats_corrections import benjamini_hochberg, bonferroni

    expected = [_key(t, h) for t, h in COMBOS]
    shards: dict[str, dict] = {}
    for key in expected:
        path = in_dir / f"{key}.json"
        if path.exists():
            shards[key] = json.loads(path.read_text(encoding="utf-8"))
    missing = [k for k in expected if k not in shards]

    rows: list[dict] = []
    for key, res in shards.items():
        if "error" in res:
            rows.append({"combo": key, "error": res["error"]})
            continue
        horizon = int(res["horizon"])
        raw = res["raw"]
        y = np.asarray(raw["y_true"], dtype=int)
        clim = np.asarray(raw["climatology_probs"], dtype=float)
        as_of = np.asarray(raw["as_of_date"])
        violations = sum(
            1 for a, m in zip(raw["as_of_date"], raw["train_max_label_date"], strict=True) if m >= a
        )
        for model in MODELS:
            prob = np.asarray(raw["model_probs"][model], dtype=float)
            m = res[model]
            row = {
                "combo": key,
                "target": res["target"],
                "horizon": horizon,
                "model": model,
                **_accuracy_test(y, prob, clim, horizon),
                "brier": m["brier"],
                "climatology_brier": res["climatology_brier"],
                "bss_vs_climatology": m["brier_skill_score_vs_climatology"],
                "ece": m["ece"],
                "embargo_violations": violations,
                "hac_lag": max(0, horizon - 1),
                "sub_periods": {},
            }
            for name, lo, hi in SUB_PERIODS:
                mask = (as_of >= lo) & (as_of < hi)
                row["sub_periods"][name] = (
                    _accuracy_test(y[mask], prob[mask], clim[mask], horizon)
                    if mask.sum() >= 2
                    else None
                )
            rows.append(row)

    scored = [r for r in rows if r.get("p_one_sided") is not None]
    ps = [float(r["p_one_sided"]) for r in scored]
    bon = bonferroni(ps, alpha=ALPHA) if ps else {}
    bh = benjamini_hochberg(ps, alpha=ALPHA) if ps else {}
    for i, r in enumerate(scored):
        r["significant_bonferroni"] = bool(bon["significant"][i])
        r["significant_bh"] = bool(bh["significant"][i])
        r["significant_uncorrected"] = r["p_one_sided"] < ALPHA

    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "git_sha": _git_sha(),
        "end_date": END_DATE,
        "min_train_size": MIN_TRAIN_SIZE,
        "buyer_dip_pct": BUYER_DIP_PCT,
        "test": "one-sided HAC Diebold-Mariano, 0/1 loss vs majority class, lag = h-1",
        "family_size": len(ps),
        "bonferroni_threshold": bon.get("threshold"),
        "shards_expected": expected,
        "shards_missing": missing,
        "datasets": {k: v.get("dataset") for k, v in shards.items()},
        "rows": rows,
    }
    out_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    for r in rows:
        if "error" in r:
            print(f"{r['combo']}: ERROR {r['error']}")
            continue
        eff = r["effective_n"]
        print(
            f"{r['combo']:18s} {r['model']:18s} n={r['n']:5d} eff_n={eff:8.1f} "
            f"acc={r['accuracy']:.4f} maj={r['majority_class_accuracy']:.4f} "
            f"p1={r['p_one_sided']:.4f} up={r['always_up_accuracy']:.4f} "
            f"p1_up={r['p_one_sided_vs_always_up']:.4f} bonf={r['significant_bonferroni']} "
            f"bh={r['significant_bh']} brier={r['brier']:.4f} "
            f"bss={r['bss_vs_climatology']:.4f} ece={r['ece']:.4f} "
            f"embargo_viol={r['embargo_violations']}"
        )
    if missing:
        print(f"MISSING SHARDS: {missing}")
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-shards", action="store_true")
    ap.add_argument("--shard")
    ap.add_argument("--aggregate", type=Path)
    ap.add_argument("--out", type=Path, default=Path("out"))
    ap.add_argument("--max-rows", type=int, help="smoke runs only: keep the last N rows")
    args = ap.parse_args()
    if args.list_shards:
        print(json.dumps([_key(t, h) for t, h in COMBOS]))
        return 0
    if args.shard:
        print(f"wrote {run_shard(args.shard, args.out, args.max_rows)}")
        return 0
    if args.aggregate:
        report = aggregate(args.aggregate, args.out)
        return 1 if report["shards_missing"] else 0
    ap.error("one of --list-shards, --shard, --aggregate is required")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
