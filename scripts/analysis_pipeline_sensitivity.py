"""scripts/analysis_pipeline_sensitivity.py -- brief item 7c: can the direction pipeline's
detection floor be lowered?

ADR 040 D3 planted a signal of known strength q (a day's label replaced, with probability q, by a
deterministic function of its features; an oracle then scores ~0.5 + q/2) and found the pipeline
reliably detects it only at q ~ 0.2 (oracle ~60%). D3 used one test (0/1 accuracy vs majority,
one-sided DM) and 5 seeds. This splits the floor into its two causes and tries the cheap levers:

  * TEST floor  -- the oracle's own probabilities P(y=1 | signal) scored on the same test days.
                   Nothing to learn: this is pure statistical power of the test.
  * LEARNING floor -- the real walk-forward model (D3's "logit") trained on the planted labels.
  * Two tests on each: D3's accuracy DM (0/1 loss vs majority) and a Brier DM (squared error of
    the probability vs climatology), which uses the probabilities instead of discarding them.
  * 20 seeds instead of 5, and a finer q grid, so the floor is located to +-0.05 in q.

Floor = the smallest q whose detection rate (one-sided p < 0.05) is >= 80%.
Reuses scripts/analysis_direction_diagnosis.py for data, walk-forward and signals, so the
comparison with ADR 040 D3 is like for like. Long: run via analysis.yml (GitHub-hosted).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import time
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _diag() -> Any:
    """Loaded lazily: the analysis workflow lists shards before installing ML dependencies."""
    if "analysis_direction_diagnosis" in sys.modules:
        return sys.modules["analysis_direction_diagnosis"]
    spec = importlib.util.spec_from_file_location(
        "analysis_direction_diagnosis", ROOT / "scripts" / "analysis_direction_diagnosis.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["analysis_direction_diagnosis"] = mod
    spec.loader.exec_module(mod)
    return mod


SEEDS = [42 + i for i in range(20)]
GRID = {
    "comex": (0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3),
    "inr": (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6),
}
SIGNAL = {"comex": "linear", "inr": "level"}
MODEL = "logit"
SHARDS = [f"{kind}_q{q}" for kind in ("comex", "inr") for q in GRID[kind]]


def tests(y: Any, p: Any, p_clim: Any, horizon: int) -> dict[str, Any]:
    diag = _diag()
    wrong = ((p >= 0.5).astype(int) != y).astype(float)
    wrong_maj = ((p_clim >= 0.5).astype(int) != y).astype(float)
    acc = diag._dm(wrong, wrong_maj, horizon)
    brier = diag._dm((p - y) ** 2, (p_clim - y) ** 2, horizon)
    return {
        "acc": float(1 - wrong.mean()),
        "p_accuracy_dm": acc["p_one_sided"],
        "p_brier_dm": brier["p_one_sided"],
    }


def run_cell(kind: str, q: float) -> dict[str, Any]:
    import numpy as np

    diag = _diag()
    d = diag.load(kind)
    sig = diag._signal(d, SIGNAL[kind])
    base_rate = float(d["y"].mean())
    runs = []
    for sd in SEEDS:
        rng = np.random.default_rng(sd)
        take = rng.random(len(d["y"])) < q
        y_inj = np.where(take, sig, d["y"])
        wf = diag.walk_forward(d, MODEL, y=y_inj)
        idx = wf["idx"]
        y_t = y_inj[idx]
        oracle = q * sig[idx] + (1 - q) * base_rate  # P(y = 1 | what the oracle knows)
        runs.append(
            {
                "seed": sd,
                "n": len(idx),
                "learned": tests(y_t, wf["p"], wf["p_clim"], d["horizon"]),
                "oracle": tests(y_t, oracle, wf["p_clim"], d["horizon"]),
            }
        )
    out: dict[str, Any] = {"dataset": kind, "q": q, "oracle_accuracy_approx": 0.5 + q / 2}
    for who in ("learned", "oracle"):
        for test in ("p_accuracy_dm", "p_brier_dm"):
            ps = [r[who][test] for r in runs]
            out[f"{who}_{test}_detection"] = float(
                np.mean([p is not None and p < 0.05 for p in ps])
            )
        out[f"{who}_mean_acc"] = float(np.mean([r[who]["acc"] for r in runs]))
    out["runs"] = runs
    print(kind, q, {k: v for k, v in out.items() if k.endswith("detection")}, flush=True)
    return out


def floors(cells: list[dict[str, Any]]) -> dict[str, Any]:
    res: dict[str, Any] = {}
    for kind in GRID:
        cs = sorted((c for c in cells if c["dataset"] == kind), key=lambda c: c["q"])
        for key in (
            "learned_p_accuracy_dm_detection",
            "learned_p_brier_dm_detection",
            "oracle_p_accuracy_dm_detection",
            "oracle_p_brier_dm_detection",
        ):
            hit = next((c["q"] for c in cs if c["q"] > 0 and c[key] >= 0.8), None)
            res[f"{kind}/{key.replace('_detection', '')}"] = {
                "floor_q": hit,
                "floor_oracle_accuracy": None if hit is None else 0.5 + hit / 2,
                "false_positive_rate_q0": next((c[key] for c in cs if c["q"] == 0), None),
            }
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-shards", action="store_true")
    ap.add_argument("--shard")
    ap.add_argument("--aggregate", type=Path)
    ap.add_argument("--out", type=Path, default=Path("out"))
    args = ap.parse_args()
    warnings.filterwarnings("ignore")
    if args.list_shards:
        print(json.dumps(SHARDS))
        return 0
    if args.shard:
        kind, qs = args.shard.split("_q")
        t0 = time.time()
        res = run_cell(kind, float(qs))
        res["seconds"] = round(time.time() - t0, 1)
        res["git_sha"] = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=ROOT
        ).stdout.strip()
        res["generated_at_utc"] = datetime.now(UTC).isoformat()
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / f"{args.shard}.json").write_text(json.dumps(res) + "\n", encoding="utf-8")
        return 0
    if args.aggregate:
        cells, missing = [], []
        for k in SHARDS:
            p = args.aggregate / f"{k}.json"
            if p.exists():
                cells.append(json.loads(p.read_text(encoding="utf-8")))
            else:
                missing.append(k)
        summary = {
            "floors": floors(cells),
            "cells": [{k: v for k, v in c.items() if k != "runs"} for c in cells],
            "missing": missing,
        }
        args.out.write_text(json.dumps({**summary, "runs": cells}) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=1))
        return 1 if missing else 0
    ap.error("need --list-shards, --shard or --aggregate")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
