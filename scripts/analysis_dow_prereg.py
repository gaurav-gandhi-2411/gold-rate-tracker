"""scripts/analysis_dow_prereg.py -- the ADR 052 pre-registered day-of-week test, exactly as frozen.

Question: is the price reliably cheaper on some weekday, in particular is an Indian Friday cheaper
than the same week's Tuesday? Confirmatory data this project's weekday analysis never touched:
COMEX GC=F 2000-08-30..2012-12-31 (primary) and GLD 2004-11-18..2012-12-31 (roll-free check), from
the frozen snapshots ADR 044 saved in reports/. Plus the forward IBJA arm (weeks after the
registration date), read only once it has >= FORWARD_MIN_WEEKS weeks.

Weekday mapping (frozen): an Indian buying day sees the previous US session's close, so Indian
Tuesday <-> COMEX Monday close and Indian Friday <-> COMEX Thursday close.

Usage: python scripts/analysis_dow_prereg.py [--out reports/dow_prereg_results.json]
       python scripts/analysis_dow_prereg.py --check   # compare with the registered result

The snapshots are stored encrypted since ADR 060; decrypt them first with scripts/data_crypt.py.
"""

from __future__ import annotations

import argparse
import hashlib
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

SNAPSHOTS = {
    "gcf": "reports/vol_regime_prereg_gcf_2000_2012.csv",
    "gld": "reports/vol_regime_prereg_gld_2004_2012.csv",
}
REGISTERED_AFTER = "2026-09-24"
FORWARD_MIN_WEEKS = 52
ALPHA_PRIMARY = 0.025  # Bonferroni over P1 and P2
ALPHA_FORWARD = 0.05
HAC_LAG_DAILY = 5
HAC_LAG_WEEKLY = 1
# COMEX weekday (Mon=0) whose close an Indian buyer sees on the named Indian day
INDIAN_TUE_SEES = 0
INDIAN_FRI_SEES = 3


VOL_REGISTERED_RESULTS = ROOT / "reports" / "vol_regime_prereg_results.json"
VOL_RESULT_BLOCK = {"gcf": "primary", "gld": "robustness_gld"}
REGISTERED_RESULTS = ROOT / "reports" / "dow_prereg_results.json"


def load_close(key: str) -> pd.Series:
    """The ADR 044 frozen snapshot, verified against the SHA-256 ADR 044's registered result
    recorded. Stored encrypted since ADR 060: decrypt it first (see data_crypt.py)."""
    path = ROOT / SNAPSHOTS[key]
    if not path.exists():
        raise SystemExit(
            f"{SNAPSHOTS[key]} is missing. It is stored encrypted (ADR 060): run "
            f"`python scripts/data_crypt.py decrypt {SNAPSHOTS[key]}` with DATA_ENC_KEY set."
        )
    vol = json.loads(VOL_REGISTERED_RESULTS.read_text(encoding="utf-8"))
    pinned = vol[VOL_RESULT_BLOCK[key]]["snapshot_sha256"]
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if sha != pinned:
        raise SystemExit(f"{SNAPSHOTS[key]}: SHA-256 {sha} is not the frozen {pinned}")
    df = pd.read_csv(path)
    s = pd.Series(df["close"].to_numpy(dtype=float), index=pd.to_datetime(df["date"]))
    s.index = s.index.tz_localize(None) if s.index.tz is not None else s.index
    return s.dropna().sort_index()


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
    for key in SNAPSHOTS:
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


def check_against_registered(res: dict[str, Any], registered: dict[str, Any]) -> list[str]:
    """Differences in the frozen-snapshot blocks (gcf, gld). forward_ibja and verdict are left
    out: the forward arm reads IBJA weeks after the registration date, so it grows by design."""
    diffs: list[str] = []

    def walk(a: Any, b: Any, where: str) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b)):
                if k not in a or k not in b:
                    diffs.append(f"{where}.{k}: present in only one side")
                else:
                    walk(a[k], b[k], f"{where}.{k}")
        elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            for i, (x, y) in enumerate(zip(a, b, strict=True)):
                walk(x, y, f"{where}[{i}]")
        elif isinstance(a, float) and isinstance(b, float):
            if not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12):
                diffs.append(f"{where}: {a!r} != {b!r}")
        elif a != b:
            diffs.append(f"{where}: {a!r} != {b!r}")

    for block in SNAPSHOTS:
        walk(res[block], registered[block], block)
    return diffs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=REGISTERED_RESULTS)
    ap.add_argument(
        "--check",
        action="store_true",
        help="re-run and compare the frozen-snapshot blocks with the registered result; "
        "writes nothing, exits 1 on any difference",
    )
    args = ap.parse_args()
    res = run()
    if args.check:
        registered = json.loads(REGISTERED_RESULTS.read_text(encoding="utf-8"))
        # JSON round trip first: the registered file has string keys where a fresh run has ints.
        diffs = check_against_registered(json.loads(json.dumps(res)), registered)
        for d in diffs:
            print(f"MISMATCH {d}")
        g = res["gcf"]
        print(
            f"ADR 052 --check: {'REPRODUCED' if not diffs else 'DIFFERS'} "
            f"(registered result at {registered.get('git_sha', '?')[:8]}); GC=F P1 p="
            f"{g['P1_joint_weekday'].get('p_value')} P2 n_weeks="
            f"{g['P2_indian_fri_vs_tue']['n_weeks']} p_one_sided="
            f"{g['P2_indian_fri_vs_tue']['p_one_sided']}"
        )
        return 1 if diffs else 0
    res["git_sha"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=ROOT
    ).stdout.strip()
    res["generated_at_utc"] = datetime.now(UTC).isoformat()
    args.out.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
