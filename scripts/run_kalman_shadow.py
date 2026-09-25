"""scripts/run_kalman_shadow.py -- ADR 055 forward shadow: append one Kalman nowcast per run.

Each run fits the noise parameters by MLE on every day strictly before the target UTC date,
filters through everything known now on the target date EXCEPT the target date's own Tanishq
readings, and appends one entry to reports/kalman_nowcast/shadow.json:

  {run_utc, target_date, day_type, point_rs_g, lo80_rs_g, hi80_rs_g, sd_log,
   readings_on_target_day, params, fit, prereg_sha256, code_commit}

Forward scoring (ADR 055 "Forward shadow"): the LAST entry per target_date, against the last
Tanishq 22K reading of that UTC date in data/prices.json (the scorecard's truth).

Not wired into any workflow (the orchestrator decides that). Shadow only: nothing user-facing
reads this file. No raw retailer prices are written.

Usage:
    python scripts/run_kalman_shadow.py [--date YYYY-MM-DD] [--out PATH]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.kalman_nowcast import (
    build_observations,
    default_initial_state,
    fit_params,
    nontrading_flags,
    predictive,
    run_filter,
)

OUT = ROOT / "reports" / "kalman_nowcast" / "shadow.json"


def _analysis() -> Any:
    name = "analysis_kalman_nowcast"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def shadow_entry(target: pd.Timestamp, now: datetime) -> dict[str, Any]:
    an = _analysis()
    nc = an._load_script("analysis_nowcast")
    truth = nc.load_truth()
    truth = truth[truth.index < target]  # the target day's own Tanishq is what we nowcast
    if truth.empty:
        raise SystemExit("no Tanishq history before the target date")
    days = pd.date_range(truth.index.min(), target, freq="D")
    start = (days[0] - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    end = (target + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    comex = an.load_comex(start, end)
    comex = comex[comex.index < target]  # a close dated c is assimilated on c + 1 <= target
    ibja = an.load_ibja_am_pm()
    ibja = ibja[ibja.index <= target]
    retail = an.load_retail()
    retail = retail[retail["day"] <= target]
    obs = build_observations(days, truth, ibja, retail, comex)
    nt = nontrading_flags(days)
    first_anchor = next(o.log_value for day in obs for o in day if o.source == "tanishq")
    init = default_initial_state(first_anchor)
    n = len(days)
    params, info = fit_params(obs[: n - 1], nt[: n - 1], init)
    res = run_filter(obs, nt, params, init)
    mean, var = float(res.pre_anchor_mean[-1]), float(res.pre_anchor_var[-1])
    pt, lo, hi = predictive(mean, var, params)
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        sha = None
    return {
        "run_utc": now.isoformat(timespec="seconds"),
        "target_date": str(target.date()),
        "day_type": "weekend" if target.dayofweek >= 5 else "weekday",
        "point_rs_g": round(pt, 2),
        "lo80_rs_g": round(lo, 2),
        "hi80_rs_g": round(hi, 2),
        "sd_log": (var + params["r_tanishq"]) ** 0.5,
        "readings_on_target_day": sorted({o.source for o in obs[-1]}),
        "params": params.values,
        "fit": info,
        "prereg_sha256": an.prereg_sha256(),
        "code_commit": sha,
    }


def append(entry: dict[str, Any], out: Path) -> int:
    rows: list[dict[str, Any]] = []
    if out.exists():
        rows = json.loads(out.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise SystemExit(f"{out} is not a JSON list; refusing to overwrite it")
    rows.append(entry)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="target UTC date (default: today UTC)")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    now = datetime.now(UTC)
    target = pd.Timestamp(args.date) if args.date else pd.Timestamp(now.date())
    entry = shadow_entry(target.normalize(), now)
    n = append(entry, args.out)
    print(f"[adr055-shadow] appended entry {n} for {entry['target_date']}: {json.dumps(entry)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
