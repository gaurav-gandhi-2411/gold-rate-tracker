"""Operating characteristics of the proposed demotion rules (ADR 068; ml/demotion.py).

For GG's threshold decision: how often does each rule wrongly demote a model that is still as
good as its record (false-demotion rate), and how long does it take to catch a model that has
lost its edge (detection delay), under daily re-checking. Seed 42.

Futures are simulated by a moving-block bootstrap (blocks of 5 days) of the committed
out-of-sample record. ``still_good``: the record as is. ``edge_lost``: the forecast columns
(ret, p_up) are block-shuffled against the outcomes, which keeps the model's marginal behaviour
and destroys any relationship with what happened.

    python scripts/simulate_demotion.py [--sims 400] [--horizon 120]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ml import nextfix as nf
from ml.demotion import DemotionParams, direction_breach, error_breach

SEED = 42
BLOCK = 5


def _blocks(n: int, length: int, rng: np.random.Generator) -> np.ndarray:
    k = -(-length // BLOCK)
    starts = rng.integers(0, n - BLOCK + 1, size=k)
    return (starts[:, None] + np.arange(BLOCK)[None, :]).ravel()[:length]


def simulate_path(
    folds: list[dict], horizon: int, edge_lost: bool, rng: np.random.Generator
) -> list[dict]:
    n = len(folds)
    base = [folds[i] for i in _blocks(n, horizon, rng)]
    if not edge_lost:
        return base
    fc = [folds[i] for i in _blocks(n, horizon, rng)]
    return [{**b, "ret": f["ret"], "p_up": f["p_up"]} for b, f in zip(base, fc, strict=True)]


def first_demotion_day(path: list[dict], p: DemotionParams) -> dict[str, int | None]:
    """First day (1-based) each rule has breached ``persist`` checks in a row, else None."""
    first: dict[str, int | None] = {"error": None, "direction": None, "any": None}
    run = {"error": 0, "direction": 0}
    for t in range(p.min_folds, len(path) + 1):
        for name, fn in (("error", error_breach), ("direction", direction_breach)):
            run[name] = run[name] + 1 if fn(path[:t], p)["breach"] else 0
            if run[name] >= p.persist and first[name] is None:
                first[name] = t
    days = [d for d in (first["error"], first["direction"]) if d is not None]
    first["any"] = min(days) if days else None
    return first


def summarise(res: list[dict[str, int | None]], horizon: int) -> dict:
    out = {}
    for k in ("error", "direction", "any"):
        hit = [r[k] for r in res if r[k] is not None]
        out[k] = {
            "share_demoted_within_horizon": round(len(hit) / len(res), 4),
            "median_days_to_demotion": float(np.median(hit)) if hit else None,
        }
    out["horizon_days"] = horizon
    out["n_sims"] = len(res)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sims", type=int, default=400)
    ap.add_argument("--horizon", type=int, default=120)
    ap.add_argument("--out", default="reports/model_audit_2026-10/demotion_simulation.json")
    args = ap.parse_args()
    folds = nf.load_oos()
    rng = np.random.default_rng(SEED)
    out: dict = {"source": "data/nextfix_oos.json", "n_folds": len(folds), "seed": SEED}
    grid = {
        "proposed": DemotionParams(),
        "persist_1": DemotionParams(persist=1),
        "persist_5": DemotionParams(persist=5),
        "window_60": DemotionParams(err_window=60, dir_window=60),
        "dir_floor_55": DemotionParams(dir_floor=0.55),
        "dir_floor_60": DemotionParams(dir_floor=0.60),
    }
    for gname, p in grid.items():
        for scen, lost in (("still_good", False), ("edge_lost", True)):
            res = [
                first_demotion_day(simulate_path(folds, args.horizon, lost, rng), p)
                for _ in range(args.sims)
            ]
            out.setdefault(gname, {})[scen] = summarise(res, args.horizon)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
