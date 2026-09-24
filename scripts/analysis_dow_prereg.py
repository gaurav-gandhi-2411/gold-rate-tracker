"""scripts/analysis_dow_prereg.py -- the ADR 052 pre-registered day-of-week test, exactly as frozen.

Question: is the price reliably cheaper on some weekday, in particular is an Indian Friday cheaper
than the same week's Tuesday? Confirmatory data this project's weekday analysis never touched:
COMEX GC=F 2000-08-30..2012-12-31 (primary) and GLD 2004-11-18..2012-12-31 (roll-free check), from
the frozen snapshots ADR 044 saved in reports/. Plus the forward IBJA arm (weeks after the
registration date), read only once it has >= FORWARD_MIN_WEEKS weeks.

Weekday mapping (frozen): an Indian buying day sees the previous US session's close, so Indian
Tuesday <-> COMEX Monday close and Indian Friday <-> COMEX Thursday close.

D3 (2026-09-25): the frozen snapshot CSVs are no longer committed to this public repo (same
reasoning as ADR 044/analysis_vol_regime_prereg.py). `load_close` delegates to
`analysis_vol_regime_prereg.load`, the single fetch-and-SHA-256-verify implementation shared by
both scripts, rather than keeping a second, driftable copy of the frozen hashes.

Usage: python scripts/analysis_dow_prereg.py [--out reports/dow_prereg_results.json]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SNAPSHOT_KEYS = ("gcf", "gld")
REGISTERED_AFTER = "2026-09-24"
FORWARD_MIN_WEEKS = 52
ALPHA_PRIMARY = 0.025  # Bonferroni over P1 and P2
ALPHA_FORWARD = 0.05
HAC_LAG_DAILY = 5
HAC_LAG_WEEKLY = 1
# COMEX weekday (Mon=0) whose close an Indian buyer sees on the named Indian day
INDIAN_TUE_SEES = 0
INDIAN_FRI_SEES = 3


def _vol_regime_module() -> Any:
    """Loads analysis_vol_regime_prereg.py by path (it is not a package) so `load_close` can
    reuse its single fetch-and-SHA-256-verify implementation. Same loading pattern
    tests/test_analysis_dow_prereg.py already uses for this script itself."""
    spec = importlib.util.spec_from_file_location(
        "analysis_vol_regime_prereg", ROOT / "scripts" / "analysis_vol_regime_prereg.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_close(key: str) -> pd.Series:
    """Re-downloads and SHA-256-verifies the ADR 044 frozen snapshot for `key` (D3: the CSV is
    no longer committed) via analysis_vol_regime_prereg.load. Raises loudly on a mismatch --
    see that function's docstring."""
    close, _sha256 = _vol_regime_module().load(key)
    close.index = close.index.tz_localize(None) if close.index.tz is not None else close.index
    return close.dropna().sort_index()


def hac_se(x: np.ndarray, lag: int) -> float:
    x = np.asarray(x, dtype=float)
    n = len(x)
    d = x - x.mean()
    lrv = float(d @ d / n)
    for k in range(1, lag + 1):
        lrv += 2 * (1 - k / (lag + 1)) * float(d[k:] @ d[:-k] / n)
    return math.sqrt(max(lrv, 0.0) / n)


def hac_wald(X: np.ndarray, y: np.ndarray, restrict: list[int], lag: int) -> tuple[float, int]:
    """OLS with a Newey-West (Bartlett) covariance; chi-square Wald test that the coefficients
    at `restrict` are all zero. Returns (p_value, degrees of freedom)."""
    from scipy.stats import chi2

    n = len(y)
    xtx_inv = np.linalg.inv(X.T @ X)
    beta = xtx_inv @ X.T @ y
    u = y - X @ beta
    xu = X * u[:, None]
    s = xu.T @ xu
    for k in range(1, lag + 1):
        g = xu[k:].T @ xu[:-k]
        s += (1 - k / (lag + 1)) * (g + g.T)
    cov = xtx_inv @ s @ xtx_inv * n / (n - X.shape[1])
    b = beta[restrict]
    stat = float(b @ np.linalg.inv(cov[np.ix_(restrict, restrict)]) @ b)
    return float(chi2.sf(stat, len(restrict))), len(restrict)


def joint_weekday_test(close: pd.Series) -> dict[str, Any]:
    """P1: daily log returns on weekday dummies (Monday is the base), HAC (Newey-West, lag 5)
    Wald test that all weekday means are equal."""
    r = np.log(close).diff().dropna()
    dow = r.index.dayofweek.to_numpy()
    days = sorted(set(dow) - {0})
    X = np.column_stack([np.ones(len(r))] + [(dow == d).astype(float) for d in days])
    p, df = hac_wald(X, r.to_numpy(), list(range(1, X.shape[1])), HAC_LAG_DAILY)
    means = r.groupby(r.index.dayofweek).mean()
    return {
        "n_days": len(r),
        "p_value": p,
        "df": df,
        "mean_return_bp_by_weekday": {int(k): float(v * 1e4) for k, v in means.items()},
        "passes": bool(p < ALPHA_PRIMARY),
    }


def weekly_pair(close: pd.Series, early: int, late: int) -> pd.Series:
    """log(close on weekday `late` / close on weekday `early`) within the same ISO week."""
    df = close.to_frame("p")
    df["wk"] = df.index.to_period("W-SUN")
    piv = df.pivot_table(index="wk", columns=df.index.dayofweek, values="p", aggfunc="last")
    if early not in piv.columns or late not in piv.columns:
        return pd.Series(dtype=float)  # e.g. the forward arm before its first full week
    piv = piv.dropna(subset=[early, late])
    return np.log(piv[late] / piv[early])


def wilson(k: int, n: int, z: float = 1.959964) -> list[float] | None:
    if n == 0:
        return None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [c - h, c + h]


def later_cheaper_test(x: pd.Series, alpha: float, lag: int) -> dict[str, Any]:
    """One-sided: mean log(later/earlier) < 0 (waiting is cheaper), HAC t-test."""
    from scipy.stats import norm

    v = x.to_numpy(dtype=float)
    n = len(v)
    if n < 10:
        return {"n_weeks": n, "p_one_sided": None, "passes": False}
    se = hac_se(v, lag)
    z = v.mean() / se if se > 0 else 0.0
    p = float(norm.cdf(z))
    k = int((v < 0).sum())
    return {
        "n_weeks": n,
        "mean_pct": float(v.mean() * 100),
        "ci95_pct": [float((v.mean() - 1.96 * se) * 100), float((v.mean() + 1.96 * se) * 100)],
        "share_weeks_cheaper": k / n,
        "share_wilson95": wilson(k, n),
        "p_one_sided": p,
        "passes": bool(p < alpha),
    }


def forward_ibja() -> dict[str, Any]:
    ib = pd.read_parquet(ROOT / "data" / "ibja_rates.parquet").dropna(subset=["pm_916"])
    s = pd.Series(ib["pm_916"].to_numpy(dtype=float), index=pd.to_datetime(ib["date"]))
    s = s[s.index > pd.Timestamp(REGISTERED_AFTER)].sort_index()
    x = weekly_pair(s, 1, 4)  # actual Indian Tuesday and Friday IBJA PM fixes
    res = later_cheaper_test(x, ALPHA_FORWARD, HAC_LAG_WEEKLY)
    res["read_allowed"] = len(x) >= FORWARD_MIN_WEEKS
    if not res["read_allowed"]:
        res["passes"] = False
    return res


def run() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in SNAPSHOT_KEYS:
        close = load_close(key)
        out[key] = {
            "first": str(close.index.min().date()),
            "last": str(close.index.max().date()),
            "P1_joint_weekday": joint_weekday_test(close),
            "P2_indian_fri_vs_tue": later_cheaper_test(
                weekly_pair(close, INDIAN_TUE_SEES, INDIAN_FRI_SEES), ALPHA_PRIMARY, HAC_LAG_WEEKLY
            ),
        }
    out["forward_ibja"] = forward_ibja()
    g = out["gcf"]
    comex_passes = g["P1_joint_weekday"]["passes"] or g["P2_indian_fri_vs_tue"]["passes"]
    out["verdict"] = {
        "comex_primary_passes": comex_passes,
        "forward_ibja_passes": out["forward_ibja"]["passes"],
        "product_may_advise_a_weekday": bool(comex_passes and out["forward_ibja"]["passes"]),
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "dow_prereg_results.json")
    args = ap.parse_args()
    res = run()
    res["git_sha"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=ROOT
    ).stdout.strip()
    res["generated_at_utc"] = datetime.now(UTC).isoformat()
    args.out.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
