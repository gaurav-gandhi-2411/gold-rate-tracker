"""scripts/analysis_leak_guard_findings.py -- what the ADR 061 leak guard finds when wired in.

Analysis only; writes reports/leak_guard/findings.json. No network. Reproduce:
    python scripts/analysis_leak_guard_findings.py

F1  direction walk-forward (ml.direction.evaluate), test-row features, report mode: which
    features were published after the fold's prediction moment (end of IST day as_of_date).
F2  R2 same-day nowcast (scripts/analysis_nowcast.py, the basis of the G3 shadow): same-day IBJA
    AM/PM inputs known at/after the Tanishq reading being estimated, and M0 / M3 same-day MAE
    with and without those days (exploratory: R2's registered numbers are not changed).
F3  feature-store provenance: live_pit rows whose IBJA value was published after capture_utc.

Timestamp conventions: ml/known_at.py. Every count is VERIFIED by this script on the committed
data at the recorded git_sha.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.direction.dataset import build_dataset
from ml.direction.evaluate import run_walk_forward
from ml.known_at import IBJA_FIELDS, at_utc, capture_known_at, ibja_known_at
from ml.leak_guard import KnownInput, LeakGuard

OUT = ROOT / "reports" / "leak_guard" / "findings.json"


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def f1_direction_features() -> dict[str, Any]:
    ds = build_dataset(verbose=False)
    out: dict[str, Any] = {}
    for h, col in (("h1", "label_binary_h1"), ("h2", "label_binary_h2")):
        wf = run_walk_forward(ds, label_col=col)
        out[h] = {
            "n_test_folds": wf["n_test_folds"],
            "logistic_accuracy": wf["logistic_metrics"]["accuracy"],
            "always_up": wf["always_up_baseline_accuracy"],
            "leak_guard": wf["leak_guard"],
        }
    src = ds["source"].value_counts().to_dict()
    out["dataset_rows_by_source"] = {str(k): int(v) for k, v in src.items()}
    return out


def f2_r2_nowcast() -> dict[str, Any]:
    r2 = _load("analysis_nowcast")
    shadow = _load("run_nowcast_shadow")
    m = r2.build(
        r2.load_truth(), r2.load_ibja(), pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    )
    preds = r2.predict_all(m)
    targets = shadow.target_timestamps()
    fetched = shadow.ibja_fetched_at()
    public = LeakGuard("r2 same-day IBJA, public publish times", mode="report")
    repo = LeakGuard("r2 same-day IBJA, incl. this repo's fetched_at", mode="report")
    rows = []
    for k in range(len(m)):
        if m.loc[k, "gap_days"] != 0 or np.isnan(preds["M0_current"][k]):
            continue
        day = m.loc[k, "date"].strftime("%Y-%m-%d")
        ib = m.loc[k, "ibja_date"].strftime("%Y-%m-%d")
        t = capture_known_at(targets[day])
        pub = public.check(
            t, [KnownInput(f, f"ibja_{f}", ibja_known_at(ib, f)) for f in ("am", "pm")], context=day
        )
        rep = repo.check(
            t,
            [
                KnownInput(f, f"ibja_{f}", ibja_known_at(ib, f, fetched.get(ib)))
                for f in ("am", "pm")
            ],
            context=day,
        )
        rows.append(
            {
                "day": day,
                "y": float(m.loc[k, "t22"]),
                "m0": float(preds["M0_current"][k]),
                "m3": float(preds["M3_am_pm"][k]),
                "leak_public": bool(pub),
                "leak_repo": bool(rep),
            }
        )
    df = pd.DataFrame(rows)
    df = df[np.isfinite(df["m3"])]

    def mae(frame: pd.DataFrame) -> dict[str, float | int]:
        return {
            "n": len(frame),
            "mae_m0": round(float((frame["m0"] - frame["y"]).abs().mean()), 2),
            "mae_m3": round(float((frame["m3"] - frame["y"]).abs().mean()), 2),
        }

    return {
        "scored_same_day_days": len(df),
        "leaky_days_public": sorted(df.loc[df["leak_public"], "day"]),
        "leaky_days_repo_fetch": sorted(df.loc[df["leak_repo"], "day"]),
        "guard_public": public.summary(),
        "guard_repo_fetch": repo.summary(),
        "mae_all_same_day": mae(df),
        "mae_excluding_public_leaks": mae(df[~df["leak_public"]]),
        "mae_excluding_repo_fetch_leaks": mae(df[~df["leak_repo"]]),
        "note": "exploratory; R2 (reports/r2_nowcast_results.json) is not re-registered",
    }


def f3_repaired_live_rows() -> dict[str, Any]:
    s = pd.read_parquet(ROOT / "data" / "feature_store" / "snapshots.parquet")
    live = s[s["source"] == "live_pit"]
    out: dict[str, Any] = {"live_rows": len(live)}
    for col, clock in IBJA_FIELDS.items():
        pub = [at_utc(d, clock) for d in live[f"{col}_asof_date"]]
        cap = [capture_known_at(c) for c in live["capture_utc"]]
        late = [(c, p) for c, p in zip(cap, pub, strict=True) if p >= c]
        out[col] = {
            "rows_published_after_capture": len(late),
            "first_capture": min(c for c, _ in late).isoformat() if late else None,
            "last_capture": max(c for c, _ in late).isoformat() if late else None,
            "max_hours_after_capture": (
                round(max((p - c).total_seconds() for c, p in late) / 3600, 2) if late else None
            ),
        }
    return out


def main() -> int:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=ROOT
    ).stdout.strip()
    res = {
        "git_sha": sha,
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "F1_direction_test_row_features": f1_direction_features(),
        "F2_r2_same_day_nowcast": f2_r2_nowcast(),
        "F3_live_rows_ibja_after_capture": f3_repaired_live_rows(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1, default=str) + "\n", encoding="utf-8")
    print(json.dumps(res["F2_r2_same_day_nowcast"], indent=1, default=str))
    print(json.dumps(res["F3_live_rows_ibja_after_capture"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
