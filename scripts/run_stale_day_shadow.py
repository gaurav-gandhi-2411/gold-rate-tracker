"""scripts/run_stale_day_shadow.py -- ADR 048 shadow scoring: does a stale-IBJA day (weekend,
holiday) do better with its own carry-forward estimate and its own band, instead of the production
IBJA-calibrated one? Nothing here changes ml.inference or what a user sees.

Scores ml.stale_day_estimate.build_scoring_table over the production scoring set (ml.calibration.
evaluate_empirical_band_coverage's days), split at FROZEN_AT (this PR's freeze date, ADR 048):
"retrospective" is every day at or before it (exploratory -- its numbers were already seen while
writing the ADR); "forward" is every day strictly after it (confirmatory -- accrues one weekly-
backtest.yml run at a time). Both strata (IBJA day / stale day) and the pooled set are reported for
each split; see the ADR for the frozen primary/secondary hypotheses.

Writes data/stale_day_shadow.json: {schema_version, generated_at_utc, git_sha, frozen_at,
retrospective, forward}.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.range_forecast.metrics import wilson_ci
from ml.stale_day_estimate import DayResult, build_scoring_table, fusion_benchmark_per_day

DATA = ROOT / "data"
OUT = DATA / "stale_day_shadow.json"

# ADR 048's freeze date -- the commit that carries the frozen ADR text and this script. A day at or
# before this is retrospective/exploratory (its numbers were seen before the freeze); a day
# strictly after is forward/confirmatory. Matches the SHADOW_AFTER pattern in
# scripts/run_weekly_range_shadow.py -- a date constant, not a commit SHA, so the split doesn't
# depend on knowing this very commit's own SHA in advance. The freeze commit's actual SHA is
# recorded in the ADR's Results section and in this file's own "git_sha" field on its first run.
FROZEN_AT = "2026-09-24"


# --- statistics (see scripts/analysis_scorecard.py for the shared pattern this mirrors) --------


def wilson(k: int, n: int) -> list[float] | None:
    if n == 0:
        return None
    lo, hi = wilson_ci(k, n)
    return [round(lo, 4), round(hi, 4)]


def p_below(k: int, n: int, nominal: float = 0.8) -> float | None:
    """Exact one-sided binomial P(X <= k | n, nominal): small = coverage reliably below target."""
    if n == 0:
        return None
    return float(sum(math.comb(n, i) * nominal**i * (1 - nominal) ** (n - i) for i in range(k + 1)))


def hac_mean_ci(x: np.ndarray, lag: int = 1) -> dict[str, Any]:
    """Mean with a Newey-West (Bartlett, `lag`) 95% interval."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 3:
        return {"n": n, "mean": float(x.mean()) if n else None, "ci95": None}
    d = x - x.mean()
    lrv = float(d @ d / n)
    for k in range(1, lag + 1):
        lrv += 2 * (1 - k / (lag + 1)) * float(d[k:] @ d[:-k] / n)
    se = math.sqrt(max(lrv, 0.0) / n)
    return {"n": n, "mean": float(x.mean()), "ci95": [x.mean() - 1.96 * se, x.mean() + 1.96 * se]}


def dm_less(loss_model: np.ndarray, loss_base: np.ndarray) -> dict[str, Any]:
    from ml.direction.evaluate_reframed import diebold_mariano_test

    if len(loss_model) < 5:
        return {"p_one_sided": None, "effective_n": None}
    r = diebold_mariano_test(list(loss_model), list(loss_base), 2, alternative="less")
    return {"p_one_sided": r["p_value"], "effective_n": r["effective_n"]}


def holm(named_p: list[tuple[str, float | None]], alpha: float = 0.05) -> dict[str, bool]:
    """Holm step-down over named one-sided p-values. A None p-value is never significant."""
    ranked = sorted(((k, p) for k, p in named_p if p is not None), key=lambda kv: kv[1])
    out = {k: False for k, _ in named_p}
    stop = False
    for rank, (k, p) in enumerate(ranked):
        ok = (not stop) and p < alpha / (len(ranked) - rank)
        out[k] = ok
        stop = stop or not ok
    return out


# --- per-stratum summary -------------------------------------------------------------------------


def _mean_hw(rows: list[DayResult], key: str) -> float | None:
    vals = [getattr(r, key) for r in rows if getattr(r, key) is not None]
    return float(np.mean(vals)) if vals else None


def _mae_block(rows: list[DayResult], est_key: str) -> dict[str, Any]:
    err = np.array(
        [abs(getattr(r, est_key) - r.actual) for r in rows if getattr(r, est_key) is not None]
    )
    return hac_mean_ci(err, lag=1)


def _band_block(rows: list[DayResult], est_key: str, hw_key: str) -> dict[str, Any]:
    pairs = [
        (getattr(r, est_key), getattr(r, hw_key), r.actual)
        for r in rows
        if getattr(r, est_key) is not None and getattr(r, hw_key) is not None
    ]
    n = len(pairs)
    k = sum(1 for e, hw, a in pairs if abs(e - a) <= hw)
    return {
        "n": n,
        "k": k,
        "coverage": (k / n) if n else None,
        "wilson95": wilson(k, n),
        "p_below_80": p_below(k, n),
    }


def _estimator_block(rows: list[DayResult], est_key: str, hw_key: str) -> dict[str, Any]:
    sub = [r for r in rows if getattr(r, est_key) is not None]
    return {
        "n": len(sub),
        "mae": _mae_block(sub, est_key),
        "band": _band_block(sub, est_key, hw_key),
        "mean_half_width": _mean_hw(sub, hw_key),
    }


def _stratum(rows: list[DayResult], *, stale_only: bool) -> dict[str, Any]:
    """Everything scored for one stratum (ibja_day / stale) or the pooled set. DM tests and fallback
    counts are only meaningful for the stale stratum (S2 == S1 exactly on ibja_day, so a DM test
    there is a degenerate zero-variance comparison, not a real one)."""
    out: dict[str, Any] = {
        "n": len(rows),
        "s1": _estimator_block(rows, "s1_estimate", "s1_half_width"),
        "s2": _estimator_block(rows, "s2_estimate", "s2_half_width"),
    }
    s3_rows = [r for r in rows if r.s3_estimate is not None]
    if s3_rows:
        out["s3"] = _estimator_block(s3_rows, "s3_estimate", "s3_half_width")
    if stale_only and rows:
        e_s1 = np.array([abs(r.s1_estimate - r.actual) for r in rows])
        e_s2 = np.array([abs(r.s2_estimate - r.actual) for r in rows])
        out["primary_dm_s2_vs_s1"] = dm_less(e_s2, e_s1)
        if s3_rows:
            e_s3 = np.array([abs(r.s3_estimate - r.actual) for r in s3_rows])
            e_s2_sub = np.array([abs(r.s2_estimate - r.actual) for r in s3_rows])
            out["secondary_dm_s3_vs_s2"] = dm_less(e_s3, e_s2_sub)
        out["fallback_counts"] = {
            "s2_estimate_fallback": int(sum(r.s2_estimate_fallback for r in rows)),
            "s2_band_fallback": int(sum(r.s2_band_fallback for r in rows)),
            "s3_estimate_fallback": int(
                sum(
                    bool(r.s3_estimate_fallback) for r in rows if r.s3_estimate_fallback is not None
                )
            ),
            "s3_band_fallback": int(
                sum(bool(r.s3_band_fallback) for r in rows if r.s3_band_fallback is not None)
            ),
        }
    return out


def _block(rows: list[DayResult]) -> dict[str, Any]:
    ibja_rows = [r for r in rows if not r.stale]
    stale_rows = [r for r in rows if r.stale]
    return {
        "days": [rows[0].date, rows[-1].date] if rows else None,
        "n": len(rows),
        "strata": {
            "ibja_day": _stratum(ibja_rows, stale_only=False),
            "stale": _stratum(stale_rows, stale_only=True),
        },
        "pooled": _stratum(rows, stale_only=False),
    }


def _split(results: list[DayResult], frozen_at: str) -> tuple[list[DayResult], list[DayResult]]:
    cutoff = pd.Timestamp(frozen_at)
    retro = [r for r in results if pd.Timestamp(r.date) <= cutoff]
    fwd = [r for r in results if pd.Timestamp(r.date) > cutoff]
    return retro, fwd


# --- data loading (mirrors scripts/analysis_scorecard.py's accuracy_band) -----------------------


def _load_tanishq() -> pd.DataFrame:
    raw = json.loads((DATA / "prices.json").read_text(encoding="utf-8"))
    rows = [(r["timestamp"], r["22k"]) for r in raw if r.get("22k") is not None]
    t = pd.DataFrame(rows, columns=["ts", "22k"]).sort_values("ts")
    t["date"] = t["ts"].str[:10]
    return t.groupby("date", as_index=False).last()[["date", "22k"]]


def run() -> dict[str, Any]:
    tanishq_df = _load_tanishq()
    ibja_df = pd.read_parquet(DATA / "ibja_rates.parquet")

    fusion_bench = None
    snap_path = DATA / "fusion_snapshots.parquet"
    if snap_path.exists():
        fusion_bench = fusion_benchmark_per_day(pd.read_parquet(snap_path))

    results = build_scoring_table(ibja_df, tanishq_df, fusion_bench)
    retro, fwd = _split(results, FROZEN_AT)

    out: dict[str, Any] = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "git_sha": subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=ROOT
        ).stdout.strip(),
        "frozen_at": FROZEN_AT,
        "retrospective": _block(retro),
        "forward": _block(fwd),
    }

    named_p: list[tuple[str, float | None]] = []
    for split_name in ("retrospective", "forward"):
        sec = out[split_name]["strata"]["stale"].get("secondary_dm_s3_vs_s2")
        if sec is not None:
            named_p.append((split_name, sec.get("p_one_sided")))
    sig = holm(named_p)
    for split_name, ok in sig.items():
        out[split_name]["strata"]["stale"]["secondary_dm_s3_vs_s2"]["holm_significant"] = ok
    out["holm"] = {"alpha": 0.05, "m": len(named_p), "family": [k for k, _ in named_p]}

    return out


def main() -> int:
    warnings.filterwarnings("ignore")
    out = run()
    OUT.write_text(json.dumps(out, indent=1, default=str) + "\n", encoding="utf-8")
    print(
        f"[adr048] retrospective n={out['retrospective']['n']} "
        f"(stale n={out['retrospective']['strata']['stale']['n']}) "
        f"forward n={out['forward']['n']} "
        f"(stale n={out['forward']['strata']['stale']['n']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
