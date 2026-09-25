"""scripts/run_wait_or_buy_shadow.py -- forward shadow of F2 "buy now or wait?" (ADR 049).

Nothing a user sees depends on this. Each weekly run (weekly-backtest.yml):
  1. issues the N=1/2/7 card numbers for every real IBJA publication day after
     CONFIRMATORY_AFTER that has none yet, using only data known on that day;
  2. scores every issued entry whose N-ahead target day has now matured, once.
Append-only: an issued entry is never recomputed, and a score is never changed, so a
later code or data change cannot re-score the forward record. The promotion PR
quotes ADR 049's success criterion from this log. Also writes
data/wait_or_buy_today.json: the most recently issued entries (one per N),
unchanged since the last run except for freshly matured `result` blocks.
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
from ml.wait_or_buy import (
    N_VALUES,
    build_sentence,
    endpoint_base_range,
    outcome_windows,
    outcomes_frame,
    prob_lower_stats,
    realized_vol_today,
    rolling_realized_vol,
    rs_bounds,
    vol_category,
    vol_percentile,
)
from ml.weekly_range import complete_windows, conformal_scale, endpoint_windows, score

CONFIRMATORY_AFTER = "2026-09-24"  # this ADR's commit date; only as-of days strictly after count
LOG_PATH = ROOT / "data" / "wait_or_buy_shadow.json"
TODAY_PATH = ROOT / "data" / "wait_or_buy_today.json"
YEARS_SPAN = (pd.Timestamp("2026-09-24") - pd.Timestamp("2013-01-01")).days / 365.25  # proxy span


def _matured_windows(known: pd.Series, n: int, before: pd.Timestamp) -> list:
    """Candidate windows for horizon n whose outcome matured strictly before
    `before` -- see ml/weekly_range.py's ENDPOINT-horizons section docstring for
    why n=1/n=7 reuse the existing path/endpoint-identical functions and n=2 uses
    the new endpoint-only ones (ml.wait_or_buy.endpoint_base_range dispatches the
    matching base-range shape)."""
    if n == 1:
        return [w for w in complete_windows(known, "1d") if w.days[-1] < before]
    if n == 7:
        return [w for w in complete_windows(known, "week") if w.days[-1] < before]
    return [w for w in endpoint_windows(known, n) if w.days[-1] < before]


def _issue_range(proxy: pd.Series, known: pd.Series, n: int, t: pd.Timestamp) -> dict | None:
    cand = _matured_windows(known, n, t)
    scores = []
    for w in cand:
        b = endpoint_base_range(proxy, w.as_of, n)
        if b is not None:
            scores.append(score(w, *b))
    s = conformal_scale(scores)
    base = endpoint_base_range(proxy, t, n)
    if s is None or base is None:
        return None
    lo, hi = base
    price = float(known.loc[t])
    lo_rs, hi_rs, x_rs = rs_bounds(price, s, lo, hi)
    return {
        "scale": s,
        "lo": round(price * math.exp(s * lo), 1),
        "hi": round(price * math.exp(s * hi), 1),
        "lo_rs": round(lo_rs, 1),
        "hi_rs": round(hi_rs, 1),
        "x_rs": round(x_rs, 1),
        "n_cal": len(scores),
    }


def _issue(
    proxy: pd.Series, ibja: pd.Series, ibja_vol_ref: pd.Series, n: int, t: pd.Timestamp
) -> dict | None:
    """The card's numbers for horizon n as they would show on decision day t (data
    known at or before t only)."""
    known = ibja[ibja.index <= t]
    prob = prob_lower_stats(outcomes_frame(known, n), n)
    if prob.get("n", 0) == 0:
        return None
    rng = _issue_range(proxy, known, n, t)
    today_vol = realized_vol_today(known, t)
    pctile = (
        vol_percentile(ibja_vol_ref[ibja_vol_ref.index <= t], t, today_vol)
        if today_vol is not None
        else None
    )
    category = vol_category(pctile)
    x_rs = rng["x_rs"] if rng else 0.0
    sentence = build_sentence(n, prob, x_rs, category, YEARS_SPAN)
    return {
        "as_of": t.strftime("%Y-%m-%d"),
        "n": n,
        "price_t": float(known.loc[t]),
        "prob": prob,
        "range": rng,
        "volatility": {"realized_vol_20d": today_vol, "percentile": pctile, "category": category},
        "sentence": sentence,
    }


def _score(entry: dict, ibja: pd.Series) -> dict | None:
    t = pd.Timestamp(entry["as_of"])
    match = next((o for o in outcome_windows(ibja, entry["n"]) if o.as_of == t), None)
    if match is None:
        return None  # target day hasn't matured yet (or its chain crosses a hole: never scored)
    rng = entry.get("range")
    inside = bool(rng and rng["lo"] <= match.price_target <= rng["hi"]) if rng else None
    return {
        "target_date": match.target.strftime("%Y-%m-%d"),
        "price_target": match.price_target,
        "lower": bool(match.lower),
        "inside_range": inside,
    }


def main() -> int:
    warnings.filterwarnings("ignore")
    log: dict[str, Any] = (
        json.loads(LOG_PATH.read_text(encoding="utf-8"))
        if LOG_PATH.exists()
        else {"confirmatory_after": CONFIRMATORY_AFTER, "entries": [], "runs": []}
    )
    proxy = load_proxy_price_series()
    ibja = load_ibja_price_series()
    ibja_vol_ref = rolling_realized_vol(proxy)  # ADR 049 (c): the long-run reference is the proxy
    now = datetime.now(UTC).isoformat()
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=ROOT
    ).stdout.strip()

    have = {(e["as_of"], e["n"]) for e in log["entries"]}
    issued = 0
    for t in ibja.index[ibja.index > pd.Timestamp(CONFIRMATORY_AFTER)]:
        for n in N_VALUES:
            if (t.strftime("%Y-%m-%d"), n) in have:
                continue
            entry = _issue(proxy, ibja, ibja_vol_ref, n, t)
            if entry is not None:
                log["entries"].append(
                    {**entry, "issued_at_utc": now, "git_sha": sha, "result": None}
                )
                issued += 1

    scored = 0
    for e in log["entries"]:
        if e.get("result") is None and (res := _score(e, ibja)) is not None:
            e["result"] = {**res, "scored_at_utc": now}
            scored += 1

    log["entries"].sort(key=lambda e: (e["as_of"], e["n"]))
    log["runs"].append({"run_at_utc": now, "git_sha": sha, "issued": issued, "scored": scored})
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text(json.dumps(log, indent=2, default=str) + "\n", encoding="utf-8")

    # "What the card would show today" is a live computation on the latest real IBJA
    # day, independent of CONFIRMATORY_AFTER (that gate is only for what counts toward
    # the forward-shadow confirmatory record above) -- it always reflects the current
    # price, even before the shadow's own confirmatory window has started.
    latest_t = ibja.index.max()
    horizons = {}
    for n in N_VALUES:
        entry = _issue(proxy, ibja, ibja_vol_ref, n, latest_t)
        if entry is not None:
            horizons[str(n)] = {k: v for k, v in entry.items() if k not in ("as_of", "n")}
    today = {
        "as_of": latest_t.strftime("%Y-%m-%d"),
        "generated_at_utc": now,
        "git_sha": sha,
        "horizons": horizons,
    }
    TODAY_PATH.write_text(json.dumps(today, indent=2, default=str) + "\n", encoding="utf-8")

    print(
        f"[adr049] issued={issued} scored={scored} entries={len(log['entries'])} today={today['as_of']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
