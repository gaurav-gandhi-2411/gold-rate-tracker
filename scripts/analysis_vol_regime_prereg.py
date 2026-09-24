"""scripts/analysis_vol_regime_prereg.py -- runs the ADR 044 pre-registered test exactly as frozen.

Momentum when calm, reversal when volatile, on COMEX history this project has never used
(GC=F 2000-08-30 .. 2012-12-31; GLD 2004-11-18 .. 2012-12-31 as a roll-free robustness check).
Downloads each series once into reports/ and reuses the saved file afterwards. Every constant
below is fixed by ADR 044; changing one makes the result exploratory.

Usage: python scripts/analysis_vol_regime_prereg.py [--out reports/vol_regime_prereg_results.json]
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
from scipy.stats import norm

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.direction.evaluate_reframed import diebold_mariano_test
from ml.direction.stats_corrections import benjamini_hochberg

VOL_WINDOW = 20
MEDIAN_WINDOW = 252
DM_HORIZON = 6  # Newey-West lag 5
ALPHA = 0.05
SERIES = {
    "gcf": ("GC=F", "2000-08-30", "2012-12-31", "reports/vol_regime_prereg_gcf_2000_2012.csv"),
    "gld": ("GLD", "2004-11-18", "2012-12-31", "reports/vol_regime_prereg_gld_2004_2012.csv"),
}


def load(key: str) -> tuple[pd.Series, str]:
    """The frozen snapshot; downloaded only if it does not exist yet."""
    ticker, start, end, rel = SERIES[key]
    path = ROOT / rel
    if not path.exists():
        import yfinance as yf

        raw = yf.download(
            ticker,
            start=start,
            end=(pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
            auto_adjust=True,
            progress=False,
            threads=False,
        )
        close = raw["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close = close.dropna()
        close.index = pd.to_datetime(close.index).tz_localize(None)
        pd.DataFrame({"date": close.index.strftime("%Y-%m-%d"), "close": close.to_numpy()}).to_csv(
            path, index=False, float_format="%.6f"
        )
    df = pd.read_csv(path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    return pd.Series(df["close"].to_numpy(dtype=float), index=pd.to_datetime(df["date"])), sha


def frame(close: pd.Series) -> pd.DataFrame:
    """Scored days t: regime known at t, rule forecast for r_{t+1}, and the outcome."""
    r = np.log(close).diff().dropna()
    vol = r.rolling(VOL_WINDOW).std(ddof=1)
    prev_median = vol.shift(1).rolling(MEDIAN_WINDOW).median()
    calm = vol < prev_median
    nxt = r.shift(-1)
    df = pd.DataFrame({"r": r, "vol": vol, "med": prev_median, "calm": calm, "r_next": nxt})
    df = df.dropna(subset=["vol", "med", "r_next"])
    same = (df["r"] > 0) | (df["r"] == 0)
    df["pred_up"] = np.where(df["calm"], same, ~same | (df["r"] == 0))
    df["up"] = df["r_next"] > 0
    return df


def rule_vs_up(df: pd.DataFrame) -> dict[str, Any]:
    loss_rule = (df["pred_up"] != df["up"]).astype(float).tolist()
    loss_up = (~df["up"]).astype(float).tolist()
    dm = diebold_mariano_test(loss_rule, loss_up, horizon=DM_HORIZON, alternative="less")
    n = len(df)
    acc = 1 - float(np.mean(loss_rule))
    return {
        "n": n,
        "effective_n": dm["effective_n"],
        "rule_accuracy": acc,
        "always_up_accuracy": 1 - float(np.mean(loss_up)),
        "edge_points_vs_always_up": (acc - (1 - float(np.mean(loss_up)))) * 100,
        "p_one_sided_vs_always_up": dm["p_value"],
        "p_one_sided_vs_50pct": float(1 - norm.cdf((acc - 0.5) / math.sqrt(0.25 / n))),
    }


def lag1(df: pd.DataFrame, positive: bool) -> dict[str, Any]:
    rho = float(np.corrcoef(df["r"], df["r_next"])[0, 1])
    n = len(df)
    z = math.atanh(rho) * math.sqrt(n - 3)
    p = float(1 - norm.cdf(z)) if positive else float(norm.cdf(z))
    return {"n": n, "lag1_autocorrelation": rho, "p_one_sided": p}


def vr20_as_d4(r: np.ndarray) -> dict[str, Any]:
    """Lo-MacKinlay VR(20) with the robust z, line for line as ADR 040 D4 computed it
    (scripts/analysis_direction_diagnosis.py, shard_series.stats) -- construction flaws included,
    on purpose: S5/S6 ask whether D4's own statistic replicates on unseen data."""
    r = r[np.isfinite(r)]
    n = len(r)
    c = r - r.mean()
    qq = 20
    sums = np.convolve(r, np.ones(qq), mode="valid")
    m = r.mean()
    var_q = float(np.sum((sums - qq * m) ** 2)) / (qq * (n - qq + 1) * (1 - qq / n))
    var_1 = float(np.sum((r - m) ** 2)) / (n - 1)
    ratio = var_q / var_1
    delta = [
        float(np.sum((c[j:] ** 2) * (c[:-j] ** 2)) / (np.sum(c**2) ** 2)) for j in range(1, qq)
    ]
    theta = sum((2 * (qq - j) / qq) ** 2 * delta[j - 1] for j in range(1, qq))
    z = (ratio - 1) / math.sqrt(theta)
    return {"n": n, "vr20": ratio, "z_robust": z}


def d4_regimes(close: pd.Series) -> tuple[dict[str, Any], dict[str, Any]]:
    """S5/S6: D4's split -- rolling 20-day std including the day itself, full-sample median."""
    r = np.log(close).diff().dropna()
    vol = r.rolling(20).std()
    med = vol.median()
    low = vr20_as_d4(r[vol <= med].to_numpy())
    high = vr20_as_d4(r[vol > med].to_numpy())
    low["p_one_sided"] = float(1 - norm.cdf(low["z_robust"]))  # H1: VR > 1
    high["p_one_sided"] = float(norm.cdf(high["z_robust"]))  # H1: VR < 1
    return low, high


def run(key: str) -> dict[str, Any]:
    close, sha = load(key)
    df = frame(close)
    out: dict[str, Any] = {
        "ticker": SERIES[key][0],
        "snapshot": SERIES[key][3],
        "snapshot_sha256": sha,
        "first_scored": str(df.index.min().date()),
        "last_scored": str(df.index.max().date()),
        "calm_share": float(df["calm"].mean()),
        "P1": rule_vs_up(df),
    }
    if key == "gcf":
        calm, vol = df[df["calm"]], df[~df["calm"]]
        sec = {
            "S1_calm_lag1_positive": lag1(calm, positive=True),
            "S2_volatile_lag1_negative": lag1(vol, positive=False),
            "S3_calm_rule_vs_up": rule_vs_up(calm),
            "S4_volatile_rule_vs_up": rule_vs_up(vol),
        }
        sec["S5_d4_vr20_low_vol_above_1"], sec["S6_d4_vr20_high_vol_below_1"] = d4_regimes(close)
        ps = [
            sec["S1_calm_lag1_positive"]["p_one_sided"],
            sec["S2_volatile_lag1_negative"]["p_one_sided"],
            sec["S3_calm_rule_vs_up"]["p_one_sided_vs_always_up"],
            sec["S4_volatile_rule_vs_up"]["p_one_sided_vs_always_up"],
            sec["S5_d4_vr20_low_vol_above_1"]["p_one_sided"],
            sec["S6_d4_vr20_high_vol_below_1"]["p_one_sided"],
        ]
        bh = benjamini_hochberg(ps)
        for name, sig in zip(sec, bh["significant"], strict=True):
            sec[name]["bh_significant"] = bool(sig)
        out["secondary"] = sec
        out["P1"]["confirmed"] = bool(out["P1"]["p_one_sided_vs_always_up"] < ALPHA)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "vol_regime_prereg_results.json")
    args = ap.parse_args()
    res = {
        "adr": "044",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "git_sha": subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=ROOT
        ).stdout.strip(),
        "primary": run("gcf"),
        "robustness_gld": run("gld"),
    }
    args.out.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
