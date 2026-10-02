"""scripts/analysis_prereg_reference.py — reproducible reference figures for the
ADR 038 pre-registration (amendment A2).

The figures frozen on 2026-09-23 (p 0.00340, effective n 119.38) could not be
reproduced from any committed code and data: the committed pipeline gives
p 0.0043 / effective n 112.52, bit-identically, across repeated runs, two
library-version sets and three data snapshots. This script is the single
reproducible definition of the reference computation. It records a manifest
(input-file hashes, library versions, git SHA, per-fold digest) so any later
run can prove it computed the same thing.

Two registrations (both: config J on the selection folds, as_of <= 2026-09-18,
one-sided HAC-DM on 0/1 loss vs always-up, lag = h - 1 = 1):
  v2 (default, ADR 042, active): consecutive-IBJA-day labels, embargo on
      label_date_h2 -- the confirmatory protocol applied to the selection data.
  v1 (ADR 038 A2, superseded): the original protocol -- labels that bridge holes
      in the IBJA record, no embargo. Kept reproducible for the audit trail.

The figures depend on library versions (see ml.direction.preregistration), so
run --check in the pinned environment (ml/requirements-inference.lock).

Usage:
  python scripts/analysis_prereg_reference.py [--protocol v1|v2]          # print
  python scripts/analysis_prereg_reference.py [--protocol v1|v2] --check  # exit 1
                                              # unless it matches the frozen constants
  analysis.yml contract: --list-shards / --shard KEY --out DIR / --aggregate DIR --out F
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

INPUT_FILES = ["data/feature_store/snapshots.parquet", "data/ibja_rates.parquet"]
# Last as_of_date of the 161 selection folds (ADR 034/038).
REFERENCE_LAST_AS_OF = "2026-09-18"
LIBS = ["numpy", "pandas", "scikit-learn", "lightgbm", "scipy"]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compute_reference(protocol: str = "v2") -> dict:
    import warnings
    from importlib.metadata import version

    from ml.direction.config_sweep import run_config_sweep
    from ml.direction.dataset import build_dataset
    from ml.direction.preregistration import PREREGISTERED_CONFIG, score_config
    from ml.direction.stats_corrections import n_for_power

    warnings.filterwarnings("ignore")
    cfg = PREREGISTERED_CONFIG
    v2 = protocol == "v2"
    dataset = build_dataset(require_consecutive=v2)
    # Pinned to the selection folds: the input parquets gain rows every few
    # hours, and an unpinned reference would drift as new days are labelled.
    dataset = dataset[dataset["as_of_date"].astype(str) <= REFERENCE_LAST_AS_OF]
    result = run_config_sweep(
        dataset,
        feature_cols=cfg["feature_cols"],
        label_col=cfg["label_col"],
        model=cfg["model"],
        class_weight=cfg["class_weight"],
        calibrate_gbm=cfg["calibrate_gbm"],
        min_train_size=cfg["min_train_size"],
        return_raw=True,
        embargo_label_date_col="label_date_h2" if v2 else None,
    )
    raw = result["raw"]
    scored = score_config(raw["y_true"], raw["y_prob"], horizon=cfg["horizon"])
    # Same n-for-power construction as the original freeze: std of the loss
    # difference taken as sqrt(long-run variance).
    power_n = n_for_power(scored["mean_diff"], math.sqrt(scored["long_run_var"]))
    # Probabilities agree across platforms only to ~1e-10 (LightGBM/BLAS float
    # order differs between Windows and Linux runners), so the digest pins what
    # the test consumes -- date, label, 0/1 decision -- plus the probability to
    # 6 decimals. The first freeze used 10 decimals and failed on Linux with
    # every statistic identical.
    folds = [
        [d, int(y), int(float(p) >= 0.5), round(float(p), 6)]
        for d, y, p in zip(raw["as_of_date"], raw["y_true"], raw["y_prob"], strict=True)
    ]
    fold_digest = hashlib.sha256(json.dumps(folds).encode()).hexdigest()
    return {
        "protocol": protocol,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "git_sha": subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=ROOT
        ).stdout.strip(),
        "python": platform.python_version(),
        "libraries": {lib: version(lib) for lib in LIBS},
        # Informational: the files grow over time, so their hashes change;
        # the per-fold digest is what proves the same computation.
        "input_sha256": {f: _sha256(ROOT / f) for f in INPUT_FILES},
        "n": scored["n"],
        "effective_n": round(scored["effective_n"], 6),
        "mean_diff": round(scored["mean_diff"], 8),
        "gamma_0": round(scored["gamma_0"], 8),
        "long_run_var": round(scored["long_run_var"], 8),
        "dm_stat": round(scored["dm_stat"], 6),
        "p_value": round(scored["p_value"], 8),
        "accuracy": round(scored["accuracy"], 8),
        "always_up_accuracy": round(scored["always_up_accuracy"], 8),
        "n_for_power": round(power_n, 4),
        "first_as_of": folds[0][0],
        "last_as_of": folds[-1][0],
        "fold_digest": fold_digest,
        "folds": folds,
    }


def check(ref: dict) -> list[str]:
    """Compare against the frozen constants in ml.direction.preregistration."""
    from ml.direction import preregistration as pr

    frozen = pr.REFERENCE if ref.get("protocol", "v2") == "v2" else pr.REFERENCE_V1
    errors = []
    for key, want in frozen.items():
        got = ref.get(key)
        if isinstance(want, float):
            if got is None or abs(got - want) > 1e-6 * max(1.0, abs(want)):
                errors.append(f"{key}: frozen {want} != computed {got}")
        elif got != want:
            errors.append(f"{key}: frozen {want!r} != computed {got!r}")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--protocol", choices=["v1", "v2"], default="v2")
    ap.add_argument("--list-shards", action="store_true")
    ap.add_argument("--shard")
    ap.add_argument("--aggregate", type=Path)
    ap.add_argument("--out", type=Path, default=Path("out"))
    args = ap.parse_args()
    if args.list_shards:
        print(json.dumps(["reference"]))
        return 0
    if args.aggregate:
        ref = json.loads((args.aggregate / "reference.json").read_text(encoding="utf-8"))
        errors = check(ref)
        ref["check_errors"] = errors
        args.out.write_text(json.dumps(ref, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(ref, indent=2))
        return 1 if errors else 0
    ref = compute_reference(args.protocol)
    if args.shard:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "reference.json").write_text(json.dumps(ref, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(ref, indent=2))
    if args.check:
        errors = check(ref)
        for e in errors:
            print("MISMATCH:", e)
        print("REPRODUCED" if not errors else "NOT REPRODUCED")
        return 1 if errors else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
