"""scripts/analysis_fhs_ranges.py -- ADR 056: measure FHS (filtered historical simulation)
adaptive ranges against the pre-registered baselines and success gates.

FROZEN 2026-09-25 (docs/adr/056-fhs-adaptive-ranges.md) before this script was ever run on
real data. Two views, both walk-forward with embargo >= horizon (a window only counts once
its own outcome day has passed -- ml.weekly_range.complete_windows/conformal_scale's own
"matured windows only" rule, reused unchanged; the decision-day view's `< d` filter is the
same rule applied at a displayed decision day instead of an IBJA row):

  * View A (IBJA-native, horizons "1d" and "week"): ml.fhs_ranges.fhs_walk_forward vs ml.
    weekly_range.walk_forward -- both "plain historical simulation" (its raw_hit/raw_width_
    pct: unconditional quantiles, scale fixed at 1) and "weekly-range method" (its
    calibrated hit/width_pct: ADR 043's own method), on the identical ml.weekly_range.
    complete_windows(ibja, horizon) rows FHS itself walks.
  * View B (decision-day, horizon "1d" only): ml.fhs_ranges.fhs_next_day_range_pct_series
    vs the live displayed range (data/metrics_history.json lower/upper), ADR 047 v2 (ml.
    next_day_range.next_day_range_pct), and plain historical simulation (ml.weekly_range.
    base_range at scale 1, applied at the decision day) -- all on the same resolved
    decision days and the same actual_next_22k truth ADR 047 used.

Winkler (interval) score's scalar "actual", per window/day: the price known at the window's
end / the decision day's next reading. For View A "week", this is the window's endpoint
price, not the whole 5-day path the coverage hit/miss itself checks -- an explicit, pre-
registered simplification applied IDENTICALLY to every candidate in the comparison (FHS,
plain HS, weekly-range method), so it cannot favor one candidate over another; only the
Wilson/Kupiec/Christoffersen coverage figures use the true path-containment hit.

One-sided Diebold-Mariano (HAC, lag = horizon - 1) of FHS's Winkler loss vs each in-scope
baseline's, reusing ml.direction.evaluate_reframed.diebold_mariano_test verbatim. BH +
Bonferroni (ml.direction.stats_corrections, reused) across the full 14-test family: View A
"1d" (2 baselines x 2 FHS methods = 4) + View A "week" (2 baselines x 2 methods = 4) + View
B (3 baselines x 2 methods = 6). Width hard gate (25%, ADR 047's own gate, reused verbatim)
applies ONLY at View B vs "live" -- ADR 047's own reference -- since there is no displayed
weekly range to use as a width reference at horizon "week" (see ADR 056's pre-registration).

Usage:
    python scripts/analysis_fhs_ranges.py --freeze-commit-sha <sha> --adr-sha256 <hash>
        [--out reports/fhs_ranges/results.json]
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import binomtest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.direction.evaluate_reframed import diebold_mariano_test
from ml.direction.stats_corrections import benjamini_hochberg, bonferroni
from ml.fhs_ranges import VOL_METHODS, fhs_next_day_range_pct_series, fhs_walk_forward
from ml.metrics import ADR_022_FIX_DATE, METRICS_PATH
from ml.next_day_range import next_day_range_pct
from ml.range_forecast.data import load_ibja_price_series, load_proxy_price_series
from ml.range_forecast.metrics import (
    christoffersen_independence_test,
    kupiec_pof_test,
    wilson_ci,
    winkler_score,
)
from ml.weekly_range import LEVEL, base_range, complete_windows
from ml.weekly_range import walk_forward as weekly_range_walk_forward

FREEZE_DATE = "2026-09-25"
WIDTH_GATE_RATIO = 1.25  # ADR 047's own hard gate, reused verbatim
OUT_PATH = ROOT / "reports" / "fhs_ranges" / "results.json"
_MON_THU = frozenset({0, 1, 2, 3})
_HORIZON_H = {"1d": 1, "week": 5}


def _coverage_block(hits: np.ndarray, widths: np.ndarray | None = None) -> dict[str, Any]:
    n = len(hits)
    if n == 0:
        return {
            "n": 0,
            "k": 0,
            "coverage": None,
            "wilson95": [None, None],
            "p_below_nominal": None,
            "kupiec_p": None,
            "christoffersen_p": None,
            "mean_width": None,
        }
    hits = np.asarray(hits, dtype=bool)
    k = int(hits.sum())
    lo, hi = wilson_ci(k, n)
    exceed = (~hits).astype(int)
    out: dict[str, Any] = {
        "n": n,
        "k": k,
        "coverage": k / n,
        "wilson95": [lo, hi],
        # One-sided: H0 true coverage = nominal, H1 true coverage < nominal -- the concern is
        # under-coverage, a too-wide range is a cost not a correctness failure (ADR 047's framing).
        "p_below_nominal": float(binomtest(k, n, LEVEL, alternative="less").pvalue),
        "kupiec_p": kupiec_pof_test(exceed, 1.0 - LEVEL)["p_value"],
        "christoffersen_p": christoffersen_independence_test(exceed)["p_value"],
    }
    if widths is not None:
        out["mean_width"] = float(np.mean(widths))
    return out


def _dm(fhs_loss: list[float], baseline_loss: list[float], horizon: int) -> dict[str, Any]:
    return diebold_mariano_test(fhs_loss, baseline_loss, horizon=horizon, alternative="less")


# ---------------------------------------------------------------------------
# View A: IBJA-native walk-forward (both horizons)
# ---------------------------------------------------------------------------


def view_a(proxy: pd.Series, ibja: pd.Series, horizon: str, method: str) -> dict[str, Any]:
    windows = complete_windows(ibja, horizon)
    fhs_df = fhs_walk_forward(proxy, ibja, horizon, method).set_index("as_of")
    wr_df = weekly_range_walk_forward(proxy, ibja, horizon).set_index("as_of")

    rows: list[dict[str, Any]] = []
    for w in windows:
        if w.as_of not in fhs_df.index or w.as_of not in wr_df.index:
            continue
        f, r = fhs_df.loc[w.as_of], wr_df.loc[w.as_of]
        if pd.isna(f["scale"]) or pd.isna(r["scale"]):
            continue
        actual_price = float(ibja.loc[w.days[-1]])
        rows.append(
            {
                "as_of": w.as_of,
                "actual_price": actual_price,
                "fhs_lo": w.price * math.exp(f["scale"] * f["base_lo"]),
                "fhs_hi": w.price * math.exp(f["scale"] * f["base_hi"]),
                "fhs_hit": bool(f["hit"]),
                "fhs_width_pct": float(f["width_pct"]),
                "plain_lo": w.price * math.exp(r["base_lo"]),
                "plain_hi": w.price * math.exp(r["base_hi"]),
                "plain_hit": bool(r["raw_hit"]),
                "plain_width_pct": float(r["raw_width_pct"]),
                "weekly_lo": w.price * math.exp(r["scale"] * r["base_lo"]),
                "weekly_hi": w.price * math.exp(r["scale"] * r["base_hi"]),
                "weekly_hit": bool(r["hit"]),
                "weekly_width_pct": float(r["width_pct"]),
            }
        )
    df = pd.DataFrame(rows)
    n = len(df)
    if n == 0:
        empty = _coverage_block(np.array([], dtype=bool))
        return {
            "n": 0,
            "fhs": empty,
            "plain_historical_simulation": empty,
            "weekly_range_method": empty,
            "width_ratio_vs_weekly_range": None,
            "width_ratio_vs_plain_hs": None,
            "winkler_mean": None,
            "dm_vs_plain_hs": None,
            "dm_vs_weekly_range": None,
        }

    actual_np = df["actual_price"].to_numpy(dtype=float)
    fhs_w = winkler_score(
        actual_np, df["fhs_lo"].to_numpy(dtype=float), df["fhs_hi"].to_numpy(dtype=float), LEVEL
    )
    plain_w = winkler_score(
        actual_np, df["plain_lo"].to_numpy(dtype=float), df["plain_hi"].to_numpy(dtype=float), LEVEL
    )
    weekly_w = winkler_score(
        actual_np,
        df["weekly_lo"].to_numpy(dtype=float),
        df["weekly_hi"].to_numpy(dtype=float),
        LEVEL,
    )
    h = _HORIZON_H[horizon]

    return {
        "n": n,
        "first_as_of": str(df["as_of"].min()) if "as_of" in df else None,
        "fhs": _coverage_block(df["fhs_hit"].to_numpy(), df["fhs_width_pct"].to_numpy()),
        "plain_historical_simulation": _coverage_block(
            df["plain_hit"].to_numpy(), df["plain_width_pct"].to_numpy()
        ),
        "weekly_range_method": _coverage_block(
            df["weekly_hit"].to_numpy(), df["weekly_width_pct"].to_numpy()
        ),
        "width_ratio_vs_weekly_range": float(
            df["fhs_width_pct"].mean() / df["weekly_width_pct"].mean()
        ),
        "width_ratio_vs_plain_hs": float(df["fhs_width_pct"].mean() / df["plain_width_pct"].mean()),
        "winkler_mean": {
            "fhs": float(fhs_w.mean()),
            "plain_hs": float(plain_w.mean()),
            "weekly_range": float(weekly_w.mean()),
        },
        "dm_vs_plain_hs": _dm(fhs_w.tolist(), plain_w.tolist(), h),
        "dm_vs_weekly_range": _dm(fhs_w.tolist(), weekly_w.tolist(), h),
    }


# ---------------------------------------------------------------------------
# View B: decision-day (horizon "1d" only)
# ---------------------------------------------------------------------------


def _resolved_decisions(min_decision_date: str = ADR_022_FIX_DATE) -> list[dict[str, Any]]:
    """Same filter as ml.metrics.compute_band_coverage / scripts/run_next_day_range_shadow.py:
    resolved decisions on/after ADR 022's fix date (pre-fix decisions were scored under a
    different, since-corrected band)."""
    if not METRICS_PATH.exists():
        return []
    raw = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return []
    return [
        e
        for e in raw
        if e.get("outcome") not in ("pending", None)
        and isinstance(e.get("lower"), (int, float))
        and isinstance(e.get("upper"), (int, float))
        and isinstance(e.get("actual_next_22k"), (int, float))
        and isinstance(e.get("current_22k"), (int, float))
        and e.get("decision_date", "") >= min_decision_date
    ]


def view_b(proxy: pd.Series, ibja: pd.Series, method: str) -> dict[str, Any]:
    decisions = sorted(_resolved_decisions(), key=lambda e: e["decision_date"])
    dates = [pd.Timestamp(e["decision_date"]) for e in decisions]
    fhs_pct = fhs_next_day_range_pct_series(proxy, ibja, dates, method)

    rows: list[dict[str, Any]] = []
    for e, d in zip(decisions, dates, strict=True):
        p, truth = float(e["current_22k"]), float(e["actual_next_22k"])
        pct = fhs_pct.get(d)
        v2 = next_day_range_pct(proxy, ibja, d)
        proxy_before = proxy[proxy.index < d]
        plain_base = base_range(proxy_before, d, 1)
        if pct is None or v2 is None or plain_base is None:
            continue
        plain_lo, plain_hi = math.exp(plain_base[0]) - 1.0, math.exp(plain_base[1]) - 1.0
        rows.append(
            {
                "decision_date": e["decision_date"],
                "weekday": d.strftime("%A"),
                "actual": truth,
                "fhs_lo": p * (1 + pct[0]),
                "fhs_hi": p * (1 + pct[1]),
                "fhs_hit": p * (1 + pct[0]) <= truth <= p * (1 + pct[1]),
                "fhs_width": p * (pct[1] - pct[0]),
                "live_lo": float(e["lower"]),
                "live_hi": float(e["upper"]),
                "live_hit": float(e["lower"]) <= truth <= float(e["upper"]),
                "live_width": float(e["upper"]) - float(e["lower"]),
                "v2_lo": p * (1 + v2[0]),
                "v2_hi": p * (1 + v2[1]),
                "v2_hit": p * (1 + v2[0]) <= truth <= p * (1 + v2[1]),
                "v2_width": p * (v2[1] - v2[0]),
                "plain_lo": p * (1 + plain_lo),
                "plain_hi": p * (1 + plain_hi),
                "plain_hit": p * (1 + plain_lo) <= truth <= p * (1 + plain_hi),
                "plain_width": p * (plain_hi - plain_lo),
            }
        )
    df = pd.DataFrame(rows)
    n = len(df)
    if n == 0:
        empty = _coverage_block(np.array([], dtype=bool))
        return {
            "n": 0,
            "fhs": empty,
            "live": empty,
            "adr047_v2": empty,
            "plain_historical_simulation": empty,
            "width_gate": None,
            "success": None,
            "by_stratum": None,
            "dm_vs_live": None,
            "dm_vs_v2": None,
            "dm_vs_plain_hs": None,
        }

    actual_np = df["actual"].to_numpy(dtype=float)
    fhs_w = winkler_score(
        actual_np, df["fhs_lo"].to_numpy(dtype=float), df["fhs_hi"].to_numpy(dtype=float), LEVEL
    )
    live_w = winkler_score(
        actual_np, df["live_lo"].to_numpy(dtype=float), df["live_hi"].to_numpy(dtype=float), LEVEL
    )
    v2_w = winkler_score(
        actual_np, df["v2_lo"].to_numpy(dtype=float), df["v2_hi"].to_numpy(dtype=float), LEVEL
    )
    plain_w = winkler_score(
        actual_np, df["plain_lo"].to_numpy(dtype=float), df["plain_hi"].to_numpy(dtype=float), LEVEL
    )

    fhs_block = _coverage_block(df["fhs_hit"].to_numpy(), df["fhs_width"].to_numpy())
    live_block = _coverage_block(df["live_hit"].to_numpy(), df["live_width"].to_numpy())
    v2_block = _coverage_block(df["v2_hit"].to_numpy(), df["v2_width"].to_numpy())
    plain_block = _coverage_block(df["plain_hit"].to_numpy(), df["plain_width"].to_numpy())

    width_ok = fhs_block["mean_width"] <= WIDTH_GATE_RATIO * live_block["mean_width"]
    wlo, whi = fhs_block["wilson95"]
    coverage_contains_nominal = wlo is not None and wlo <= LEVEL <= whi
    coverage_ge_live = fhs_block["coverage"] >= live_block["coverage"]
    success = {
        "coverage_wilson_ci_contains_nominal": coverage_contains_nominal,
        "coverage_ge_live": coverage_ge_live,
        "width_not_more_than_25pct_wider_than_live": width_ok,
        "overall": bool(coverage_contains_nominal and coverage_ge_live and width_ok),
    }

    def _by(mask: pd.Series) -> dict[str, Any]:
        return {
            "fhs": _coverage_block(
                df.loc[mask, "fhs_hit"].to_numpy(), df.loc[mask, "fhs_width"].to_numpy()
            ),
            "live": _coverage_block(
                df.loc[mask, "live_hit"].to_numpy(), df.loc[mask, "live_width"].to_numpy()
            ),
        }

    is_mon_thu = df["decision_date"].apply(lambda x: pd.Timestamp(x).weekday() in _MON_THU)
    by_stratum = {"mon_thu": _by(is_mon_thu), "fri_sun": _by(~is_mon_thu)}

    return {
        "n": n,
        "first_decision_date": df["decision_date"].min(),
        "last_decision_date": df["decision_date"].max(),
        "fhs": fhs_block,
        "live": live_block,
        "adr047_v2": v2_block,
        "plain_historical_simulation": plain_block,
        "width_gate": {
            "ratio": WIDTH_GATE_RATIO,
            "fhs_mean_width": fhs_block["mean_width"],
            "live_mean_width": live_block["mean_width"],
            "passes": width_ok,
        },
        "success": success,
        "by_stratum": by_stratum,
        "winkler_mean": {
            "fhs": float(fhs_w.mean()),
            "live": float(live_w.mean()),
            "adr047_v2": float(v2_w.mean()),
            "plain_hs": float(plain_w.mean()),
        },
        "dm_vs_live": _dm(fhs_w.tolist(), live_w.tolist(), 1),
        "dm_vs_v2": _dm(fhs_w.tolist(), v2_w.tolist(), 1),
        "dm_vs_plain_hs": _dm(fhs_w.tolist(), plain_w.tolist(), 1),
    }


# ---------------------------------------------------------------------------
# Winkler DM family: BH + Bonferroni across all 14 tests
# ---------------------------------------------------------------------------


def winkler_family(results: dict[str, Any]) -> dict[str, Any]:
    tests: list[dict[str, Any]] = []
    for method in VOL_METHODS:
        for horizon in ("1d", "week"):
            va = results["view_a"][horizon][method]
            if va["n"] == 0:
                continue
            tests.append(
                {
                    "label": f"{method}/view_a/{horizon}/vs_plain_hs",
                    "p_value": va["dm_vs_plain_hs"]["p_value"],
                }
            )
            tests.append(
                {
                    "label": f"{method}/view_a/{horizon}/vs_weekly_range",
                    "p_value": va["dm_vs_weekly_range"]["p_value"],
                }
            )
        vb = results["view_b"][method]
        if vb["n"] == 0:
            continue
        tests.append({"label": f"{method}/view_b/vs_live", "p_value": vb["dm_vs_live"]["p_value"]})
        tests.append({"label": f"{method}/view_b/vs_v2", "p_value": vb["dm_vs_v2"]["p_value"]})
        tests.append(
            {"label": f"{method}/view_b/vs_plain_hs", "p_value": vb["dm_vs_plain_hs"]["p_value"]}
        )

    p_values = [t["p_value"] for t in tests]
    bon = bonferroni(p_values)
    bh = benjamini_hochberg(p_values)
    for i, t in enumerate(tests):
        t["bonferroni_significant"] = bon["significant"][i]
        t["bh_significant"] = bh["significant"][i]
    return {
        "m": len(tests),
        "alpha": 0.05,
        "bonferroni_threshold": bon["threshold"],
        "n_bh_significant": bh["n_significant"],
        "tests": tests,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    ap.add_argument("--freeze-commit-sha", default=None)
    ap.add_argument("--adr-sha256", default=None)
    args = ap.parse_args()

    proxy = load_proxy_price_series()
    ibja = load_ibja_price_series()

    results: dict[str, Any] = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "git_sha": subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=ROOT
        ).stdout.strip(),
        "nominal": LEVEL,
        "freeze_date": FREEZE_DATE,
        "pre_registration": {
            "adr_path": "docs/adr/056-fhs-adaptive-ranges.md",
            "adr_sha256": args.adr_sha256,
            "freeze_commit_sha": args.freeze_commit_sha,
        },
        "width_gate_ratio": WIDTH_GATE_RATIO,
        "proxy_date_range": [str(proxy.index.min().date()), str(proxy.index.max().date())],
        "ibja_date_range": [str(ibja.index.min().date()), str(ibja.index.max().date())],
        "view_a": {
            "1d": {m: view_a(proxy, ibja, "1d", m) for m in VOL_METHODS},
            "week": {m: view_a(proxy, ibja, "week", m) for m in VOL_METHODS},
        },
        "view_b": {m: view_b(proxy, ibja, m) for m in VOL_METHODS},
    }
    results["winkler_family"] = winkler_family(results)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2, default=str) + "\n", encoding="utf-8")

    for method in VOL_METHODS:
        vb = results["view_b"][method]
        va1 = results["view_a"]["1d"][method]
        vaw = results["view_a"]["week"][method]
        print(
            f"[fhs/{method}] view_b n={vb['n']} coverage={vb['fhs']['coverage']} "
            f"success={vb.get('success', {}).get('overall') if vb['n'] else None} | "
            f"view_a 1d n={va1['n']} coverage={va1['fhs']['coverage']} | "
            f"view_a week n={vaw['n']} coverage={vaw['fhs']['coverage']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
