"""Proof for ADR 069 before P3 goes live: it reproduces ADR 067's figures and passes the leak guard.

1. Rebuild P3's walk-forward record with the production function (ml.nextfix.predict_p3 via
   update_oos) and compare, on the same decision days (d0 <= 2026-09-30), with the figures ADR 067
   pre-registered and published (reports/model_audit_2026-10/parity.json, P3 rule).
2. Replay the leak guard over that record with the clocks ml.known_at defines today.

    python scripts/verify_p3_live.py [--out reports/model_audit_2026-10/p3_live_proof.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ml import known_at as ka
from ml import nextfix as nf

ROOT = Path(__file__).resolve().parent.parent
ADR067_LAST_D0 = "2026-09-30"  # the sample ADR 067 scored
TOL_MAE = 0.05  # Rs./g: rounding of the published figure (2 decimals) plus cache revisions


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/model_audit_2026-10/p3_live_proof.json")
    args = ap.parse_args()
    sys.path.insert(0, str(ROOT / "scripts"))
    from audit_nextfix_leak import audit, negative_control
    from ml.macro import load_macro_features

    full = nf.load_ibja_full()
    pairs = nf.build_pairs(
        full.dropna(subset=["pm"]).reset_index(drop=True), nf.global_series(load_macro_features())
    )
    folds = nf.update_oos(pairs, [], nf.predict_p3, forward_from=nf.P3_FORWARD_FROM)
    sample = [f for f in folds if f["d0"] <= ADR067_LAST_D0]
    pm0 = np.array([f["pm0"] for f in sample])
    pm1 = np.array([f["pm1"] for f in sample])
    ret = np.array([f["ret"] for f in sample])
    mae = float(np.abs(pm1 - pm0 * np.exp(ret)).mean())
    moved = pm1 != pm0
    acc = float((((np.array([f["p_up"] for f in sample]) > 0.5) == (pm1 > pm0))[moved]).mean())

    pub = json.loads((ROOT / "reports/model_audit_2026-10/parity.json").read_text())
    p3 = next(r for r in pub["retrospective"]["results"] if r["rule"] == "P3")
    guard = audit(sample, pairs, ka.MACRO_DAILY_CLOCKS["usd_inr"], "p3/repo")
    out = {
        "adr": "docs/adr/069-p3-live-forecast.md",
        "sample": {"n": len(sample), "last_d0": ADR067_LAST_D0, "adr067_n": p3["n"]},
        "mae": {"rebuilt": round(mae, 2), "adr067": p3["mae_rule"], "tolerance": TOL_MAE},
        "direction_accuracy": {"rebuilt": round(acc, 4), "adr067": p3["direction_acc_rule"]},
        "leak_guard": guard,
        "negative_control_flagged": negative_control(pairs),
        "n_folds_total": len(folds),
        "n_forward": sum(1 for f in folds if f.get("retro") is False),
        "pass": bool(
            len(sample) == p3["n"]
            and abs(mae - p3["mae_rule"]) <= TOL_MAE
            and abs(acc - p3["direction_acc_rule"]) < 5e-4
            and guard["n_violations"] == 0
            and negative_control(pairs)
        ),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1, default=str) + "\n")
    print(json.dumps(out, indent=1, default=str))
    if not out["pass"]:
        raise SystemExit("P3 did not reproduce ADR 067 / pass the guard")


if __name__ == "__main__":
    main()
