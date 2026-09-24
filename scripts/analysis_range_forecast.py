"""scripts/analysis_range_forecast.py -- R1 shadow research: "how much could
22K move in the next N days", shaped for .github/workflows/analysis.yml.

  --list-shards            JSON list of model-family shard keys
  --shard KEY --out DIR    run one model family across both datasets and all
                            horizons, write DIR/KEY.json
  --aggregate DIR --out F  metrics + significance tests across every shard -> F

Shards are model families (one per ml.range_forecast model module), so the
slow ones (garch, chronos) run in parallel with the fast ones on CI rather
than serializing the whole analysis behind the slowest model. Every shard
covers BOTH datasets -- "proxy" and "ibja" -- and all 4 horizons (1/5/10/20),
raw AND conformal-calibrated, at both 80% and 90% central levels.

IBJA PROTOCOL (2026-09-24 fix -- see ml.range_forecast.data's DATA CAVEAT
docstring): every model FORECASTS ONLY from the daily proxy series
(ml.inr_proxy_labels, 2013-2026; per ADR 032 only ~44-54% direction-reliable
below ~50 Rs/gram moves, but genuinely daily throughout) -- IBJA is NEVER
walked forward independently, because data/ibja_rates.parquet is not daily
before 2025-Q2 (median gap 5-18 calendar days) and a naive per-row horizon
there silently spans weeks. The "proxy" dataset entry is that walk-forward's
own forecasts scored against the proxy's own forward return, unchanged. The
"ibja" dataset entry RESCORES the identical proxy-fitted forecasts (same
bounds) against the real IBJA h-day log return, kept only for as-of days
that are genuine IBJA rows whose h-IBJA-row-ahead window stays inside one
dense segment (ml.range_forecast.data.dense_segments, gaps <= 4 calendar
days) -- this is the product question "do proxy-fitted forecasts work on
real IBJA prices?", not "what does IBJA's own history look like on its own."
IBJA-arm conformal calibration uses matured IBJA-scored errors, falling back
to the proxy arm's own calibration (ml.range_forecast.conformal.
apply_ibja_fallback) below IBJA_FALLBACK_MIN_N matured errors.

SHADOW RESEARCH ONLY: nothing here touches ml.direction.gate or any
published data file.

--smoke / --max-rows: local verification only, on a small slice with a
reduced min_train_size/refit_every and a horizon subset (see SMOKE_HORIZONS)
to keep local wall-clock bounded -- the FULL run (default settings, all 4
horizons, full history) is dispatched on GitHub Actions, not run locally.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np
    import pandas as pd

# Run as `python scripts/<name>.py` from the repo root: add the root for `import ml`.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MODEL_SHARDS: list[str] = [
    "historical_vol",
    "historical_simulation",
    "ewma",
    "garch",
    "har",
    "quantile_gbm",
    "chronos",
]
DATASETS: list[str] = ["proxy", "ibja"]
LEVELS: tuple[float, ...] = (0.8, 0.9)
FULL_HORIZONS: tuple[int, ...] = (1, 5, 10, 20)
SMOKE_HORIZONS: tuple[int, ...] = (1, 5)  # local smoke only -- bounds wall-clock
# Only the proxy arm is walked forward (see the IBJA PROTOCOL note above) --
# min_train_size/refit_every apply to that one walk-forward only. The earlier
# design ran IBJA independently with its own (shorter) warm-up; that is now
# superseded by rescoring the proxy forecasts against real IBJA instead of
# giving IBJA its own walk-forward at all.
FULL_MIN_TRAIN_SIZE = 250
FULL_REFIT_EVERY = 21
SMOKE_MIN_TRAIN_SIZE = 80
SMOKE_REFIT_EVERY = 12
CONFORMAL_WINDOW = 250
ALPHA = 0.05
BASELINE_MODELS: tuple[str, str] = ("historical_vol", "historical_simulation")
PRIMARY_BASELINE = "historical_vol"  # the task brief's "the naive baseline" for the success flag


def _git_sha() -> str:
    out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    return out.stdout.strip()


# ---------------------------------------------------------------------------
# Shard: run one model family across both datasets and every requested horizon
# ---------------------------------------------------------------------------


def _run_model(
    key: str,
    price: pd.Series,
    dataset: str,
    horizon: int,
    min_train_size: int,
    refit_every: int,
    features: pd.DataFrame | None = None,
    pipeline: object | None = None,
) -> dict:
    if key == "historical_vol":
        from ml.range_forecast.baselines import VOL_WINDOW, historical_vol_forecast_set

        window = min(VOL_WINDOW, min_train_size)
        return historical_vol_forecast_set(
            price, dataset, horizon, levels=LEVELS, vol_window=window, min_train_size=min_train_size
        )
    if key == "historical_simulation":
        from ml.range_forecast.baselines import HS_WINDOW, historical_simulation_forecast_set

        window = min(HS_WINDOW, max(min_train_size * 2, 100))
        min_sample = max(10, min(min_train_size, 60))
        return historical_simulation_forecast_set(
            price, dataset, horizon, levels=LEVELS, hs_window=window, min_train_size=min_sample
        )
    if key == "ewma":
        from ml.range_forecast.baselines import ewma_forecast_set

        return ewma_forecast_set(
            price, dataset, horizon, levels=LEVELS, min_train_size=min_train_size
        )
    if key == "garch":
        from ml.range_forecast.garch import garch_forecast_set

        return garch_forecast_set(
            price,
            dataset,
            horizon,
            levels=LEVELS,
            refit_every=refit_every,
            min_train_size=min_train_size,
        )
    if key == "har":
        from ml.range_forecast.har import har_forecast_set

        return har_forecast_set(
            price,
            dataset,
            horizon,
            levels=LEVELS,
            refit_every=refit_every,
            min_train_size=min_train_size,
        )
    if key == "quantile_gbm":
        from ml.range_forecast.quantile_gbm import quantile_gbm_forecast_set

        return quantile_gbm_forecast_set(
            price,
            dataset,
            horizon,
            levels=LEVELS,
            refit_every=refit_every,
            min_train_size=min_train_size,
            features=features,
        )
    if key == "chronos":
        from ml.range_forecast.chronos_model import chronos_forecast_set

        return chronos_forecast_set(
            price,
            dataset,
            horizon,
            levels=LEVELS,
            min_train_size=min_train_size,
            pipeline=pipeline,
        )
    raise ValueError(f"unknown model shard {key!r}")


def _serialize_conformal(cal: dict) -> dict:
    return {
        "lo": cal["calibrated_lo"].tolist(),
        "hi": cal["calibrated_hi"].tolist(),
        "q_hat": cal["q_hat"].tolist(),
        "n_matured": cal["n_matured"].tolist(),
    }


def run_shard(key: str, out_dir: Path, max_rows: int | None = None, smoke: bool = False) -> Path:
    import numpy as np
    from ml.range_forecast.conformal import (
        IBJA_FALLBACK_MIN_N,
        apply_ibja_fallback,
        walk_forward_conformal,
    )
    from ml.range_forecast.data import (
        load_ibja_price_series,
        load_proxy_price_series,
        score_against_ibja,
    )

    if key not in MODEL_SHARDS:
        raise SystemExit(f"unknown shard {key!r}; expected one of {MODEL_SHARDS}")

    min_train_size = SMOKE_MIN_TRAIN_SIZE if smoke else FULL_MIN_TRAIN_SIZE
    refit_every = SMOKE_REFIT_EVERY if smoke else FULL_REFIT_EVERY
    horizons = SMOKE_HORIZONS if smoke else FULL_HORIZONS

    result: dict = {
        "model": key,
        "git_sha": _git_sha(),
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "smoke": smoke,
        "max_rows": max_rows,
        "min_train_size": min_train_size,
        "refit_every": refit_every,
        "horizons_run": list(horizons),
        "levels": list(LEVELS),
        "ibja_fallback_min_n": IBJA_FALLBACK_MIN_N,
        "datasets": {},
    }

    t_shard0 = time.time()
    # Only the proxy series is ever walked forward -- see the IBJA PROTOCOL note
    # in the module docstring. IBJA is loaded in full (not `max_rows`-truncated:
    # it is already short, and truncating it would starve the rescoring step of
    # dense segments to score against).
    proxy_price = load_proxy_price_series()
    ibja_price = load_ibja_price_series()
    if max_rows is not None:
        proxy_price = proxy_price.tail(max_rows)

    if len(proxy_price) < min_train_size + max(horizons) + 5:
        msg = f"insufficient proxy rows ({len(proxy_price)}) for min_train_size={min_train_size}"
        result["datasets"]["proxy"] = {"error": msg}
        result["datasets"]["ibja"] = {"error": "proxy arm failed; ibja is derived from it"}
        print(f"{key}: SKIPPED ({msg})", flush=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{key}.json"
        path.write_text(json.dumps(result, default=str) + "\n", encoding="utf-8")
        return path

    proxy_entry: dict = {
        "rows": len(proxy_price),
        "as_of_min": str(proxy_price.index.min()),
        "as_of_max": str(proxy_price.index.max()),
        "horizons": {},
    }
    ibja_entry: dict = {
        "rows": len(ibja_price),
        "as_of_min": str(ibja_price.index.min()) if len(ibja_price) else None,
        "as_of_max": str(ibja_price.index.max()) if len(ibja_price) else None,
        "horizons": {},
    }

    shared_features = None
    shared_pipeline = None
    if key == "quantile_gbm":
        from ml.range_forecast.data import build_driver_features_for_price

        t0 = time.time()
        shared_features = build_driver_features_for_price(proxy_price)
        print(f"{key}/proxy: built driver features in {time.time() - t0:.0f}s", flush=True)
    if key == "chronos":
        from ml.range_forecast.chronos_model import chronos_available, load_pipeline

        if not chronos_available():
            msg = "chronos-forecasting/torch not available in this Python environment"
            result["datasets"]["proxy"] = {"error": msg}
            result["datasets"]["ibja"] = {"error": msg}
            print(f"{key}: SKIPPED (chronos/torch unavailable)", flush=True)
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"{key}.json"
            path.write_text(json.dumps(result, default=str) + "\n", encoding="utf-8")
            return path
        t0 = time.time()
        shared_pipeline = load_pipeline()
        print(f"{key}: loaded Chronos pipeline in {time.time() - t0:.0f}s", flush=True)

    for h in horizons:
        t0 = time.time()
        raw_proxy = _run_model(
            key,
            proxy_price,
            "proxy",
            h,
            min_train_size,
            refit_every,
            shared_features,
            shared_pipeline,
        )
        elapsed = time.time() - t0
        print(
            f"{key}/proxy/h{h}: {len(raw_proxy['as_of_date'])} forecasts in {elapsed:.0f}s",
            flush=True,
        )

        positions = np.asarray(raw_proxy["as_of_position"])
        actual = np.asarray(raw_proxy["actual_return"])
        conformal_proxy: dict = {}
        for lv in LEVELS:
            lv_key = str(lv)
            lo = np.asarray(raw_proxy["levels"][lv_key]["lo"])
            hi = np.asarray(raw_proxy["levels"][lv_key]["hi"])
            conformal_proxy[lv_key] = walk_forward_conformal(
                positions, h, lo, hi, actual, lv, window=CONFORMAL_WINDOW
            )
        proxy_entry["horizons"][str(h)] = {
            "raw": raw_proxy,
            "conformal": {lk: _serialize_conformal(c) for lk, c in conformal_proxy.items()},
            "elapsed_sec": elapsed,
        }

        # --- IBJA arm: rescore the SAME proxy-fitted forecasts against real IBJA ---
        raw_ibja = score_against_ibja(raw_proxy, ibja_price, h)
        print(
            f"{key}/ibja/h{h}: {len(raw_ibja['as_of_date'])} IBJA-scored forecasts "
            f"(dense-segment-eligible, of {len(raw_proxy['as_of_date'])} proxy forecasts)",
            flush=True,
        )
        ibja_positions = np.asarray(raw_ibja["as_of_position"])
        ibja_actual = np.asarray(raw_ibja["actual_return"])
        source_idx = raw_ibja["source_proxy_index"]
        conformal_ibja_out: dict = {}
        for lv in LEVELS:
            lv_key = str(lv)
            lo = np.asarray(raw_ibja["levels"][lv_key]["lo"])
            hi = np.asarray(raw_ibja["levels"][lv_key]["hi"])
            cal_ibja = walk_forward_conformal(
                ibja_positions,
                h,
                lo,
                hi,
                ibja_actual,
                lv,
                window=CONFORMAL_WINDOW,
                min_calibration_n=IBJA_FALLBACK_MIN_N,
            )
            merged = apply_ibja_fallback(cal_ibja, conformal_proxy[lv_key], source_idx)
            conformal_ibja_out[lv_key] = {
                "lo": merged["calibrated_lo"].tolist(),
                "hi": merged["calibrated_hi"].tolist(),
                "q_hat": merged["q_hat"].tolist(),
                "n_matured": merged["n_matured"].tolist(),
                "source": merged["source"],
            }
        ibja_entry["horizons"][str(h)] = {
            "raw": raw_ibja,
            "conformal": conformal_ibja_out,
            "elapsed_sec": 0.0,  # rescoring an already-computed forecast set is cheap
        }

    result["datasets"]["proxy"] = proxy_entry
    result["datasets"]["ibja"] = ibja_entry

    result["elapsed_sec_total"] = time.time() - t_shard0
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{key}.json"
    path.write_text(json.dumps(result, default=str) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Aggregate: metrics + Kupiec/Christoffersen + Winkler/QLIKE + DM + corrections
# ---------------------------------------------------------------------------


def _variant_metrics(
    as_of_date: list[str],
    horizon: int,
    level: float,
    actual: np.ndarray,
    lo: np.ndarray,
    hi: np.ndarray,
    current_price: np.ndarray,
    scale: np.ndarray,
) -> dict:
    import numpy as np
    from ml.range_forecast.metrics import (
        christoffersen_independence_test,
        kupiec_pof_test,
        qlike,
        stride_subsample,
        wilson_ci,
        winkler_score,
    )

    valid = ~(np.isnan(lo) | np.isnan(hi))
    n_valid = int(valid.sum())
    if n_valid == 0:
        return {"n": 0}

    a, lo_v, hi_v, px, sc = actual[valid], lo[valid], hi[valid], current_price[valid], scale[valid]
    dates_valid = [d for d, v in zip(as_of_date, valid, strict=True) if v]
    hits = (a < lo_v) | (a > hi_v)  # exceedance = outside the interval
    nominal_p = 1.0 - level

    kupiec = kupiec_pof_test(hits, nominal_p)
    strided_hits = stride_subsample(hits.astype(int), horizon)
    christoffersen = christoffersen_independence_test(strided_hits)

    coverage = 1.0 - float(hits.mean())
    ci_lo, ci_hi = wilson_ci(int((~hits).sum()), n_valid)
    coverage_in_ci = bool(ci_lo <= level <= ci_hi)

    price_lo = px * np.exp(lo_v)
    price_hi = px * np.exp(hi_v)
    width_pct = float(np.mean((np.exp(hi_v) - np.exp(lo_v)) * 100.0))
    width_rupees = float(np.mean(price_hi - price_lo))

    w = winkler_score(a, lo_v, hi_v, level)
    ql = qlike(a**2, sc**2)

    return {
        "n": n_valid,
        "n_stride_independence": christoffersen["n"],
        "coverage": coverage,
        "coverage_wilson_ci_95": [ci_lo, ci_hi],
        "coverage_within_binomial_ci": coverage_in_ci,
        "kupiec": kupiec,
        "christoffersen": christoffersen,
        "kupiec_not_rejected": kupiec["p_value"] is not None and kupiec["p_value"] > ALPHA,
        "mean_width_pct": width_pct,
        "mean_width_rupees": width_rupees,
        "winkler_mean": float(w.mean()),
        "qlike_mean": float(ql.mean()),
        "as_of_date": dates_valid,
        "winkler_scores": w.tolist(),
        "qlike_scores": ql.tolist(),
    }


def _load_shards(in_dir: Path) -> dict[str, dict]:
    shards: dict[str, dict] = {}
    for key in MODEL_SHARDS:
        path = in_dir / f"{key}.json"
        if path.exists():
            shards[key] = json.loads(path.read_text(encoding="utf-8"))
    return shards


def _dm_vs_baseline(
    model_dates: list[str],
    model_winkler: list[float],
    baseline_dates: list[str],
    baseline_winkler: list[float],
    horizon: int,
) -> dict | None:
    """One-sided (model beats baseline) DM test on the Winkler-score loss
    differential, aligned on the intersection of as_of_date between the two
    (their forecast sets need not cover exactly the same days -- e.g.
    quantile_gbm skips early NaN-feature rows, and chronos may be strided
    if `stride` was overridden above its default of 1)."""
    from ml.direction.evaluate_reframed import diebold_mariano_test

    baseline_map = dict(zip(baseline_dates, baseline_winkler, strict=True))
    paired_a, paired_b = [], []
    for d, wa in zip(model_dates, model_winkler, strict=True):
        if d in baseline_map:
            paired_a.append(wa)
            paired_b.append(baseline_map[d])
    if len(paired_a) < 2:
        return None
    dm = diebold_mariano_test(paired_a, paired_b, horizon, alternative="less")
    dm["n_paired"] = len(paired_a)
    return dm


def aggregate(in_dir: Path, out_path: Path) -> dict:
    from ml.direction.stats_corrections import benjamini_hochberg, bonferroni

    shards = _load_shards(in_dir)
    missing = [k for k in MODEL_SHARDS if k not in shards]

    # Pass 1: per (model, dataset, horizon, level, variant) metrics.
    rows: list[dict] = []
    variant_lookup: dict[tuple[str, str, int, float, str], dict] = {}
    for model, shard in shards.items():
        for dataset, dataset_entry in shard.get("datasets", {}).items():
            if "error" in dataset_entry:
                rows.append({"model": model, "dataset": dataset, "error": dataset_entry["error"]})
                continue
            for h_str, h_entry in dataset_entry.get("horizons", {}).items():
                horizon = int(h_str)
                raw = h_entry["raw"]
                conformal = h_entry["conformal"]
                actual = _np().asarray(raw["actual_return"])
                current_price = _np().asarray(raw["current_price"])
                scale = _np().asarray(raw["scale"])
                as_of_date = raw["as_of_date"]

                for level in LEVELS:
                    lv_key = str(level)
                    for variant_name, bounds in (
                        ("raw", raw["levels"][lv_key]),
                        ("conformal", conformal[lv_key]),
                    ):
                        lo = _np().asarray(bounds["lo"], dtype=float)
                        hi = _np().asarray(bounds["hi"], dtype=float)
                        m = _variant_metrics(
                            as_of_date, horizon, level, actual, lo, hi, current_price, scale
                        )
                        row = {
                            "model": model,
                            "dataset": dataset,
                            "horizon": horizon,
                            "level": level,
                            "variant": variant_name,
                            **{
                                k: v
                                for k, v in m.items()
                                if k not in ("as_of_date", "winkler_scores", "qlike_scores")
                            },
                        }
                        rows.append(row)
                        variant_lookup[(model, dataset, horizon, level, variant_name)] = m

    # Pass 2: DM tests vs each baseline (same dataset/horizon/level/variant).
    dm_rows: list[dict] = []
    for (model, dataset, horizon, level, variant), m in variant_lookup.items():
        if model in BASELINE_MODELS or m.get("n", 0) < 2:
            continue
        for baseline in BASELINE_MODELS:
            bm = variant_lookup.get((baseline, dataset, horizon, level, variant))
            if bm is None or bm.get("n", 0) < 2:
                continue
            dm = _dm_vs_baseline(
                m["as_of_date"],
                m["winkler_scores"],
                bm["as_of_date"],
                bm["winkler_scores"],
                horizon,
            )
            if dm is None:
                continue
            qlike_ratio = (
                (m["qlike_mean"] / bm["qlike_mean"])
                if bm.get("qlike_mean") not in (None, 0)
                else None
            )
            dm_rows.append(
                {
                    "model": model,
                    "dataset": dataset,
                    "horizon": horizon,
                    "level": level,
                    "variant": variant,
                    "baseline": baseline,
                    "dm_stat": dm["dm_stat"],
                    "p_one_sided": dm["p_value"],
                    "effective_n": dm["effective_n"],
                    "n_paired": dm["n_paired"],
                    "qlike_ratio_vs_baseline": qlike_ratio,
                }
            )

    ps = [r["p_one_sided"] for r in dm_rows if r["p_one_sided"] is not None]
    bon = bonferroni(ps, alpha=ALPHA) if ps else {}
    bh = benjamini_hochberg(ps, alpha=ALPHA) if ps else {}
    p_idx = 0
    for r in dm_rows:
        if r["p_one_sided"] is None:
            r["significant_uncorrected"] = None
            r["significant_bonferroni"] = None
            r["significant_bh"] = None
            continue
        r["significant_uncorrected"] = r["p_one_sided"] < ALPHA
        r["significant_bonferroni"] = bool(bon["significant"][p_idx])
        r["significant_bh"] = bool(bh["significant"][p_idx])
        p_idx += 1

    # Pass 3: success flag per (model, dataset, horizon, level, variant) --
    # coverage OK AND significantly narrower (BH-corrected) than the PRIMARY baseline.
    dm_by_key = {
        (r["model"], r["dataset"], r["horizon"], r["level"], r["variant"], r["baseline"]): r
        for r in dm_rows
    }
    for row in rows:
        if "error" in row or row["model"] in BASELINE_MODELS:
            continue
        key = (
            row["model"],
            row["dataset"],
            row["horizon"],
            row["level"],
            row["variant"],
            PRIMARY_BASELINE,
        )
        dm = dm_by_key.get(key)
        coverage_ok = bool(row.get("kupiec_not_rejected")) and bool(
            row.get("coverage_within_binomial_ci")
        )
        narrower_significant = bool(dm and dm.get("significant_bh"))
        row["success_vs_primary_baseline"] = coverage_ok and narrower_significant
        row["coverage_ok"] = coverage_ok

    # Pass 4: sub-period breakdown (every model x horizon x level x variant,
    # not just a hand-picked "best" -- the report identifies the best from this table).
    from ml.range_forecast.data import SUB_PERIODS, sub_period_mask

    for (model, dataset, horizon, level, variant), m in variant_lookup.items():
        if m.get("n", 0) == 0:
            continue
        dates_arr = _np().asarray(m["as_of_date"])
        sub: dict = {}
        for name, start, end in SUB_PERIODS:
            mask = sub_period_mask(dates_arr, start, end)
            if mask.sum() < 5:
                sub[name] = None
                continue
            w = _np().asarray(m["winkler_scores"])[mask]
            sub[name] = {"n": int(mask.sum()), "winkler_mean": float(w.mean())}
        for row in rows:
            if (
                row.get("model") == model
                and row.get("dataset") == dataset
                and row.get("horizon") == horizon
                and row.get("level") == level
                and row.get("variant") == variant
            ):
                row["sub_periods"] = sub
                break

    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "git_sha": _git_sha(),
        "alpha": ALPHA,
        "levels": list(LEVELS),
        "primary_baseline": PRIMARY_BASELINE,
        "baseline_models": list(BASELINE_MODELS),
        "conformal_window": CONFORMAL_WINDOW,
        "dm_test": "one-sided HAC Diebold-Mariano on per-forecast Winkler score, lag = h-1",
        "family_size_dm": len(ps),
        "bonferroni_threshold": bon.get("threshold"),
        "shards_expected": MODEL_SHARDS,
        "shards_missing": missing,
        "shard_meta": {
            k: {kk: vv for kk, vv in v.items() if kk != "datasets"} for k, v in shards.items()
        },
        "rows": rows,
        "dm_rows": dm_rows,
    }
    out_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")

    for row in rows:
        if "error" in row:
            print(f"{row['model']:16s} {row['dataset']:6s} ERROR {row['error']}")
            continue
        print(
            f"{row['model']:16s} {row['dataset']:6s} h={row['horizon']:2d} L={row['level']:.2f} "
            f"{row['variant']:9s} n={row.get('n', 0):5d} cov={row.get('coverage', float('nan')):.4f} "
            f"kupiec_p={row.get('kupiec', {}).get('p_value')} "
            f"winkler={row.get('winkler_mean', float('nan')):.5f} "
            f"success={row.get('success_vs_primary_baseline')}"
        )
    if missing:
        print(f"MISSING SHARDS: {missing}")
    return report


def _np():
    import numpy as np

    return np


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-shards", action="store_true")
    ap.add_argument("--shard")
    ap.add_argument("--aggregate", type=Path)
    ap.add_argument("--out", type=Path, default=Path("out"))
    ap.add_argument("--max-rows", type=int, help="smoke runs only: keep the last N rows")
    ap.add_argument(
        "--smoke", action="store_true", help="reduced min_train_size/refit_every/horizons"
    )
    args = ap.parse_args()

    if args.list_shards:
        print(json.dumps(MODEL_SHARDS))
        return 0
    if args.shard:
        print(f"wrote {run_shard(args.shard, args.out, args.max_rows, args.smoke)}")
        return 0
    if args.aggregate:
        report = aggregate(args.aggregate, args.out)
        return 1 if report["shards_missing"] else 0
    ap.error("one of --list-shards, --shard, --aggregate is required")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
