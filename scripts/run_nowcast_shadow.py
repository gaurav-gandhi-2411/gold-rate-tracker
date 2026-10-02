"""scripts/run_nowcast_shadow.py -- G3: forward shadow of the R2 morning-fix nowcast.

R2 (#1957, scripts/analysis_nowcast.py) found that adding IBJA's morning (AM) fix to the
current PM-only estimate (M3_am_pm vs M0_current) cut same-day error from Rs 45 to Rs 35/g on
the history it was chosen on (significant under BH, not Bonferroni). GG decision G3: run it in
shadow for 4 weeks from merge, and prepare a promotion PR only if it holds on days that did not
exist when it was chosen.

Each run (weekly, from weekly-backtest.yml) scores every day after SHADOW_AFTER that has a
Tanishq reading and is not yet in the log. Each day is predicted walk-forward from same-day
pairs dated strictly before it, which is exactly what the site could have computed that day.
Days already logged are never rewritten: the log is append-only, so a later code change cannot
quietly re-score the window. The summary compares M3 against M0 on the same days with the same
one-sided HAC test R2 used.

SHADOW ONLY: writes data/nowcast_shadow_log.json and nothing else; the site keeps using M0.

Leak guard (ADR 061), REPORT mode: each logged day records the IBJA inputs that became known at
or after the reading it estimates (the target is the day's last Tanishq reading; an IBJA fix is
known at max(its assumed publication, this repo's fetched_at), ml.known_at). The registered M0/M3
protocol is unchanged -- the report is how the leak is documented, not fixed (ADR 061 F2).

GG decision F2 (2026-09-26, ADR 061 amendment A2): the 2026-10-22 decision compares M3 with M0
only on CERTIFIED same-day days -- days whose IBJA inputs were all known strictly before the
reading they estimate. Predictions are still logged for every day, unchanged; the uncertified
days are excluded from the decision comparison and reported alongside (`all_same_day`). A logged
day without the field (logged by the pre-#2090 runner) is certified at summary time from the same
persisted timestamps, never by rewriting the logged row.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.known_at import capture_known_at, ibja_known_at
from ml.leak_guard import KnownInput, LeakGuard

# Merge date of the shadow; only days strictly after it count (forward-only).
SHADOW_AFTER = "2026-09-24"
# 4 weeks from merge (G3). The promotion PR is prepared after this date, if M3 holds.
WINDOW_ENDS = "2026-10-22"
LOG_PATH = ROOT / "data" / "nowcast_shadow_log.json"
ARMS = ("M0_current", "M3_am_pm")


def _load_r2() -> Any:
    """The R2 analysis script is the single definition of M0/M3; reuse it, don't copy it."""
    path = ROOT / "scripts" / "analysis_nowcast.py"
    spec = importlib.util.spec_from_file_location("analysis_nowcast", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["analysis_nowcast"] = mod
    spec.loader.exec_module(mod)
    return mod


def target_timestamps() -> dict[str, str]:
    """UTC date -> timestamp of its last Tanishq 22K reading (the value load_truth scores)."""
    raw = json.loads((ROOT / "data" / "prices.json").read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for r in raw:
        if r.get("22k") is not None:
            out[str(r["timestamp"])[:10]] = str(r["timestamp"])
    return out


def ibja_fetched_at() -> dict[str, str | None]:
    ib = pd.read_parquet(ROOT / "data" / "ibja_rates.parquet")
    return {
        str(d)[:10]: (None if pd.isna(f) else str(f))
        for d, f in zip(ib["date"], ib["fetched_at"], strict=True)
    }


def inputs_known_after_target(
    guard: LeakGuard, target_ts: str | None, ibja_date: str, fetched_at: str | None
) -> list[str]:
    """The IBJA inputs (AM, PM of ibja_date) not known strictly before the target reading.
    No target timestamp means the day cannot be certified: reported, never passed silently."""
    if target_ts is None:
        guard.n_unchecked += 1
        return ["target_timestamp_missing"]
    inputs = [
        KnownInput(
            f"ibja_{fix}@{ibja_date}", f"ibja_{fix}", ibja_known_at(ibja_date, fix, fetched_at)
        )
        for fix in ("am", "pm")
    ]
    bad = guard.check(capture_known_at(target_ts), inputs, context=ibja_date)
    return [v.input.source for v in bad]


def new_rows(
    r2: Any,
    logged: set[str],
    today_utc: str,
    guard: LeakGuard | None = None,
    targets: dict[str, str] | None = None,
    fetched: dict[str, str | None] | None = None,
) -> list[dict[str, Any]]:
    """Walk-forward M0/M3 predictions for unlogged, completed days after SHADOW_AFTER.
    Today (UTC) is skipped: its truth would be the latest Tanishq reading so far, not the
    last reading of the day."""
    truth = r2.load_truth()
    ibja = r2.load_ibja()
    # M0/M3 do not use the global-move adjustment; an empty series leaves it unused.
    m = r2.build(truth, ibja, pd.Series(dtype=float, index=pd.DatetimeIndex([])))
    preds = r2.predict_all(m)
    guard = guard or LeakGuard("nowcast shadow same-day IBJA inputs", mode="report")
    targets = target_timestamps() if targets is None else targets
    fetched = ibja_fetched_at() if fetched is None else fetched
    rows = []
    for k in range(len(m)):
        day = m.loc[k, "date"].strftime("%Y-%m-%d")
        if day <= SHADOW_AFTER or day >= today_utc or day in logged:
            continue
        ibja_day = m.loc[k, "ibja_date"].strftime("%Y-%m-%d")
        rows.append(
            {
                "date": day,
                "ibja_date": ibja_day,
                "inputs_known_after_target": inputs_known_after_target(
                    guard, targets.get(day), ibja_day, fetched.get(ibja_day)
                ),
                "gap_days": int(m.loc[k, "gap_days"]),
                "truth_rs_per_g": float(m.loc[k, "t22"]),
                **{
                    arm: (None if np.isnan(preds[arm][k]) else round(float(preds[arm][k]), 2))
                    for arm in ARMS
                },
            }
        )
    return rows


def _mae_dm(ok: list[dict[str, Any]]) -> dict[str, Any]:
    """M3 vs M0 on the given same-day days, with R2's one-sided HAC Diebold-Mariano test."""
    from ml.direction.evaluate_reframed import diebold_mariano_test

    if len(ok) < 3:
        return {}
    y = np.array([d["truth_rs_per_g"] for d in ok])
    e0 = np.abs(np.array([d["M0_current"] for d in ok]) - y)
    e3 = np.abs(np.array([d["M3_am_pm"] for d in ok]) - y)
    dm = diebold_mariano_test(e3.tolist(), e0.tolist(), 2, alternative="less")
    return {
        "mae_m0_rs_per_g": float(e0.mean()),
        "mae_m3_rs_per_g": float(e3.mean()),
        "p_one_sided_m3_better": dm["p_value"],
        "effective_n": dm["effective_n"],
        "first_day": ok[0]["date"],
        "last_day": ok[-1]["date"],
    }


def summarise(
    days: list[dict[str, Any]],
    certify: Callable[[dict[str, Any]], list[str]] | None = None,
) -> dict[str, Any]:
    """M3 vs M0 on same-day days where both exist (R2's headline comparison).

    Decision numbers (top level) use certified days only (GG decision F2): no IBJA input known
    at or after the target reading. `certify(day)` supplies that list for a logged day that has
    no `inputs_known_after_target` field; without it such a day counts as uncertified.
    `all_same_day` keeps the uncertified days in, for comparison only."""
    same_day = [
        d
        for d in days
        if d["gap_days"] == 0 and d["M0_current"] is not None and d["M3_am_pm"] is not None
    ]

    def late(d: dict[str, Any]) -> list[str]:
        if "inputs_known_after_target" in d:
            return list(d["inputs_known_after_target"])
        return certify(d) if certify is not None else ["not_certified"]

    ok = [d for d in same_day if not late(d)]
    out: dict[str, Any] = {
        "n_same_day": len(ok),
        "n_same_day_excluded_late_ibja": len(same_day) - len(ok),
        "n_all_logged": len(days),
        "n_days_input_known_after_target": sum(1 for d in days if late(d)),
    }
    out.update(_mae_dm(ok))
    if len(same_day) != len(ok):
        out["all_same_day"] = {"n_same_day": len(same_day), **_mae_dm(same_day)}
    return out


def main() -> int:
    log: dict[str, Any] = (
        json.loads(LOG_PATH.read_text(encoding="utf-8"))
        if LOG_PATH.exists()
        else {"shadow_after": SHADOW_AFTER, "window_ends": WINDOW_ENDS, "days": [], "runs": []}
    )
    logged = {d["date"] for d in log["days"]}
    now_dt = datetime.now(UTC)
    now = now_dt.isoformat()
    added = new_rows(_load_r2(), logged, now_dt.strftime("%Y-%m-%d"))
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=ROOT
    ).stdout.strip()
    for row in added:
        row["logged_at_utc"] = now
        row["git_sha"] = sha
    log["days"] = sorted([*log["days"], *added], key=lambda d: d["date"])
    targets, fetched = target_timestamps(), ibja_fetched_at()
    certify_guard = LeakGuard("nowcast shadow certification (summary only)", mode="report")

    def certify(d: dict[str, Any]) -> list[str]:
        return inputs_known_after_target(
            certify_guard, targets.get(d["date"]), d["ibja_date"], fetched.get(d["ibja_date"])
        )

    summary = summarise(log["days"], certify)
    log["runs"].append({"run_at_utc": now, "git_sha": sha, "added": len(added), **summary})
    LOG_PATH.write_text(json.dumps(log, indent=2) + "\n", encoding="utf-8")
    print(
        f"nowcast shadow [G3, after {SHADOW_AFTER}, window ends {WINDOW_ENDS}]: "
        f"added {len(added)} day(s); " + json.dumps(summary)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
