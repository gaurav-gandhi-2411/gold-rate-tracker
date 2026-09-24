"""scripts/run_weekly_range_shadow.py -- forward shadow of the weekly price range (item 5).

Nothing a user sees depends on this. Each weekly run (weekly-backtest.yml):
  1. issues the "1d" and "week" ranges (ml.weekly_range) for every IBJA publication day after
     SHADOW_AFTER that has none yet, using only data known on that day;
  2. scores every issued range whose window is now complete (same completeness rule as the
     backtest), once.
Append-only: an issued range is never recomputed, and a score is never changed, so a later code
or data change cannot re-score the forward record. The promotion PR quotes coverage from this
log: n, Wilson 95% CI and the "N times out of 10" figure (rounded down).
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

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.range_forecast.data import load_ibja_price_series, load_proxy_price_series
from ml.range_forecast.metrics import wilson_ci
from ml.weekly_range import (
    HORIZONS,
    WEEK_CALENDAR_DAYS,
    base_range,
    complete_windows,
    conformal_scale,
    issue_days,
    score,
    times_out_of_ten,
)

SHADOW_AFTER = "2026-09-24"  # only as-of days strictly after this count (forward-only)
LOG_PATH = ROOT / "data" / "weekly_range_shadow_log.json"


def _issue(proxy: pd.Series, ibja: pd.Series, horizon: str, t: pd.Timestamp) -> dict | None:
    """The range the site would have shown on publication day t (data known at t only)."""
    known = ibja[ibja.index <= t]
    windows = [w for w in complete_windows(known, horizon) if w.days[-1] < t]
    scores = []
    for w in windows:
        base = base_range(proxy, w.as_of, issue_days(horizon))
        if base is not None:
            scores.append(score(w, *base))
    s = conformal_scale(scores)
    base = base_range(proxy, t, issue_days(horizon))
    if s is None or base is None:
        return None
    price = float(ibja.loc[t])
    lo, hi = base
    return {
        "as_of": t.strftime("%Y-%m-%d"),
        "horizon": horizon,
        # 1d: the next weekday (IBJA's next expected publication; a holiday pushes the scored
        # day later, see complete_windows). Week: t + 7 calendar days.
        "window_end": (
            t + (pd.offsets.BDay(1) if horizon == "1d" else pd.Timedelta(days=WEEK_CALENDAR_DAYS))
        ).strftime("%Y-%m-%d"),
        "ibja_pm_916": price,
        "scale": s,
        "lo": round(price * math.exp(s * lo), 1),
        "hi": round(price * math.exp(s * hi), 1),
        "n_cal": len(scores),
    }


def _score(entry: dict, ibja: pd.Series) -> dict | None:
    t = pd.Timestamp(entry["as_of"])
    match = [w for w in complete_windows(ibja, entry["horizon"]) if w.as_of == t]
    if not match:
        return None  # window not complete yet (or crosses a hole: never scored)
    w = match[0]
    prices = [float(ibja.loc[d]) for d in w.days]
    return {
        "scored_days": [d.strftime("%Y-%m-%d") for d in w.days],
        "path_low": min(prices),
        "path_high": max(prices),
        "inside": entry["lo"] <= min(prices) and max(prices) <= entry["hi"],
    }


def summary(entries: list[dict]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for h in HORIZONS:
        done = [e for e in entries if e["horizon"] == h and e.get("result")]
        k = sum(e["result"]["inside"] for e in done)
        n = len(done)
        out[h] = {"n_issued": sum(e["horizon"] == h for e in entries), "n_scored": n}
        if n:
            lo, hi = wilson_ci(k, n)
            out[h].update(
                {
                    "coverage": k / n,
                    "wilson_95": [lo, hi],
                    "times_out_of_10": times_out_of_ten(k / n),
                }
            )
    return out


def main() -> int:
    warnings.filterwarnings("ignore")
    log: dict[str, Any] = (
        json.loads(LOG_PATH.read_text(encoding="utf-8"))
        if LOG_PATH.exists()
        else {"shadow_after": SHADOW_AFTER, "entries": [], "runs": []}
    )
    proxy = load_proxy_price_series()
    ibja = load_ibja_price_series()
    now = datetime.now(UTC).isoformat()
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=ROOT
    ).stdout.strip()
    have = {(e["as_of"], e["horizon"]) for e in log["entries"]}
    issued = 0
    for t in ibja.index[ibja.index > pd.Timestamp(SHADOW_AFTER)]:
        for h in HORIZONS:
            if (t.strftime("%Y-%m-%d"), h) in have:
                continue
            entry = _issue(proxy, ibja, h, t)
            if entry is not None:
                log["entries"].append({**entry, "issued_at_utc": now, "git_sha": sha})
                issued += 1
    scored = 0
    for e in log["entries"]:
        if e.get("result") is None and (res := _score(e, ibja)) is not None:
            e["result"] = {**res, "scored_at_utc": now}
            scored += 1
    log["entries"].sort(key=lambda e: (e["as_of"], e["horizon"]))
    summ = summary(log["entries"])
    log["runs"].append({"run_at_utc": now, "git_sha": sha, "issued": issued, "scored": scored})
    log["summary"] = summ
    LOG_PATH.write_text(json.dumps(log, indent=2) + "\n", encoding="utf-8")
    print(f"weekly range shadow [after {SHADOW_AFTER}]: issued {issued}, scored {scored}; {summ}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
