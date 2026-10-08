"""Size, wrongful-demotion and catch rate of the demotion monitor's ERROR rule, old vs new test.

ADR 068 amendment 2026-10-08 (finding F7): the error rule used a normal-tail Newey-West p-value
with 4 lags on a window of 40, which rejects a true null far more often than its nominal 5%. This
script measures that for the OLD test (normal tail, kept here as ``legacy_p``) and the NEW test
(``ml.demotion.hac_one_sided_p_worse``, Student-t tail), deterministically (seed 42).

  (a) single-check size: mean difference zero; t(4) innovations, AR(1) 0.0 / 0.3 / 0.5, n = 40; and
      the demotion-relevant null "model exactly as good as holding", resampled from the REAL P3
      loss-difference series (its own tails and autocorrelation), demeaned.
  (b) wrongful-demotion rate of a model WITH P3's backtest effect (real series, block bootstrap),
      full rule: persistence 3 consecutive daily checks, 121 daily checks.
  (c) catch rate: model exactly as good as holding, and 10% worse than holding: share demoted within
      40 and 121 checks and mean day of demotion.
  (d) the candidate tests that were compared when choosing the fix (single-check size).
  (e) direction and range rule sizes (exact binomial, computed exactly, not simulated).

Only the error rule changes; direction and range are untouched. Rolling windows and persistence
are exactly ``ml.demotion.error_breach`` / ``demotion_status``: window = last 40 folds, checks start
at ``min_folds`` = 30 (windows of 30..40 while the record is still short), persist = 3.

    python scripts/simulate_demotion_size.py [--paths 5000] [--single-paths 20000]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy.stats import binom, norm
from scipy.stats import t as student_t

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ml import nextfix as nf
from ml.demotion import DemotionParams, hac_one_sided_p_worse

SEED = 42
BLOCK = 5  # same moving-block length as scripts/simulate_demotion.py
N_CHECKS = 121  # daily checks, from the first one at min_folds
P = DemotionParams()
HORIZON = P.min_folds + N_CHECKS - 1  # folds needed for 121 checks
ALPHA = P.err_alpha


def _hac_t(x: np.ndarray, lags: int) -> tuple[np.ndarray, np.ndarray]:
    """Row-wise mean and t statistic with the Newey-West variance used in ml.demotion."""
    n = x.shape[1]
    m = x.mean(axis=1)
    dc = x - m[:, None]
    var = (dc * dc).sum(axis=1) / n
    for k in range(1, min(lags, n - 1) + 1):
        var = var + 2.0 * (1.0 - k / (lags + 1)) * (dc[:, k:] * dc[:, :-k]).sum(axis=1) / n
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(var > 0, m / np.sqrt(var / n), np.where(m > 0, np.inf, -np.inf))
    return m, t


def legacy_p(x: np.ndarray, lags: int) -> np.ndarray:
    """OLD test: normal tail (what ml.demotion shipped until 2026-10-08)."""
    return norm.sf(_hac_t(x, lags)[1])


def new_p(x: np.ndarray, lags: int) -> np.ndarray:
    """NEW test, vectorised: Student-t tail, ``lags`` df (parity-checked against ml.demotion)."""
    n = x.shape[1]
    return student_t.sf(_hac_t(x, lags)[1], df=max(1, min(lags, n - 1)))


def candidate_p(name: str, x: np.ndarray, lags: int) -> np.ndarray:
    """Candidate tests compared before choosing (all HAC, 4 lags unless named)."""
    n = x.shape[1]
    if name == "old_normal_nw4":
        return legacy_p(x, lags)
    if name == "t_df_n-1_nw4":
        return student_t.sf(_hac_t(x, lags)[1], df=n - 1)
    if name == "t_df_n-1_nw4_x_n/(n-1)":  # Hansen-Hodrick style variance rescale, then t(n-1)
        return student_t.sf(_hac_t(x, lags)[1] / np.sqrt(n / (n - 1)), df=n - 1)
    if name == "t_df_5_nw4":
        return student_t.sf(_hac_t(x, lags)[1], df=5)
    if name == "t_df_3_nw4":  # more conservative still; costs more power
        return student_t.sf(_hac_t(x, lags)[1], df=3)
    if name == "t_df_4_nw4 (CHOSEN)":
        return new_p(x, lags)
    raise ValueError(name)


def gen_ar_t(
    rng: np.random.Generator, paths: int, n: int, ar: float, df: float = 4.0
) -> np.ndarray:
    """Zero-mean AR(1) with unit-variance Student-t(df) innovations (burn-in 50)."""
    e = rng.standard_t(df, size=(paths, n + 50)) / np.sqrt(df / (df - 2.0))
    x = np.zeros_like(e)
    for i in range(1, e.shape[1]):
        x[:, i] = ar * x[:, i - 1] + e[:, i]
    return x[:, 50:]


def real_series() -> tuple[np.ndarray, np.ndarray]:
    """(d, hold_err) of the committed P3 record: d = |model error| - |hold error| per fold."""
    folds = nf.load_oos(nf.P3_OOS_PATH)
    model = np.array([abs(f["pm1"] - f["pm0"] * np.exp(f["ret"])) for f in folds])
    flat = np.array([abs(f["pm1"] - f["pm0"]) for f in folds])
    return model - flat, flat


def block_paths(rng: np.random.Generator, d: np.ndarray, paths: int, length: int) -> np.ndarray:
    """Moving-block bootstrap (blocks of 5) of ``d``: (paths, length)."""
    k = -(-length // BLOCK)
    starts = rng.integers(0, len(d) - BLOCK + 1, size=(paths, k))
    idx = (starts[:, :, None] + np.arange(BLOCK)[None, None, :]).reshape(paths, -1)[:, :length]
    return d[idx]


def breach_matrix(x: np.ndarray, p_fn) -> np.ndarray:
    """(paths, checks) bool: error-rule breach at each daily check t = min_folds .. len(x)."""
    cols = []
    for t in range(P.min_folds, x.shape[1] + 1):
        w = x[:, max(0, t - P.err_window) : t]
        cols.append((w.mean(axis=1) > 0) & (p_fn(w, P.hac_lags) < ALPHA))
    return np.stack(cols, axis=1)


def first_demotion_check(br: np.ndarray) -> np.ndarray:
    """1-based index of the check at which ``persist`` consecutive breaches complete, else 0."""
    paths, checks = br.shape
    run = np.zeros(paths, dtype=int)
    first = np.zeros(paths, dtype=int)
    for c in range(checks):
        run = np.where(br[:, c], run + 1, 0)
        hit = (run >= P.persist) & (first == 0)
        first[hit] = c + 1
    return first


def demotion_stats(first: np.ndarray) -> dict:
    """Share demoted within 40 / 121 checks and mean day (check index) among demoted paths."""
    out: dict = {}
    for horizon in (40, N_CHECKS):
        hit = (first > 0) & (first <= horizon)
        out[f"within_{horizon}"] = round(float(hit.mean()), 4)
        out[f"mean_day_within_{horizon}"] = (
            round(float(first[hit].mean()), 1) if hit.any() else None
        )
    return out


def lag1(x: np.ndarray) -> float:
    xc = x - x.mean()
    return float(xc[1:] @ xc[:-1] / (xc @ xc))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paths", type=int, default=5000)
    ap.add_argument("--single-paths", type=int, default=20000)
    ap.add_argument("--out", default="reports/model_audit_2026-10/demotion_test_size.json")
    args = ap.parse_args()
    rng = np.random.default_rng(SEED)
    d, hold = real_series()
    out: dict = {
        "seed": SEED,
        "paths": args.paths,
        "single_paths": args.single_paths,
        "checks": N_CHECKS,
        "params": {
            "window": P.err_window,
            "lags": P.hac_lags,
            "alpha": ALPHA,
            "persist": P.persist,
        },
        "source": "data/nextfix_p3_oos.json",
    }

    # self-check: the vectorised new test equals the shipped function
    chk = gen_ar_t(rng, 50, 40, 0.3)
    for row, pv in zip(chk, new_p(chk, P.hac_lags), strict=True):
        assert abs(hac_one_sided_p_worse(row, P.hac_lags) - pv) < 1e-12

    ratio = float(np.abs(d + hold).mean() / hold.mean())
    out["real_p3"] = {
        "n_folds": len(d),
        "mae_model_over_mae_hold": round(ratio, 4),
        "mean_loss_diff": round(float(d.mean()), 3),
        "sd_loss_diff": round(float(d.std(ddof=1)), 3),
        "lag1_autocorr": round(lag1(d), 3),
        "mean_hold_err": round(float(hold.mean()), 3),
    }

    # (a) single-check size, n = 40, 5% nominal
    size: dict = {}
    for label, make in (
        ("t4_ar0.0", lambda: gen_ar_t(rng, args.single_paths, 40, 0.0)),
        ("t4_ar0.3", lambda: gen_ar_t(rng, args.single_paths, 40, 0.3)),
        ("t4_ar0.5", lambda: gen_ar_t(rng, args.single_paths, 40, 0.5)),
        (
            "real_p3_demeaned (model exactly as good as holding)",
            lambda: block_paths(rng, d - d.mean(), args.single_paths, 40),
        ),
    ):
        x = make()
        size[label] = {
            "old": round(float((legacy_p(x, P.hac_lags) < ALPHA).mean()), 4),
            "new": round(float((new_p(x, P.hac_lags) < ALPHA).mean()), 4),
        }
    out["single_check_size_n40"] = size

    # (d) candidates
    cands: dict = {}
    null_sets = {f"ar{ar}": gen_ar_t(rng, args.single_paths, 40, ar) for ar in (0.0, 0.3, 0.5)}
    null_sets["real_p3_demeaned"] = block_paths(rng, d - d.mean(), args.single_paths, 40)
    for key, x in null_sets.items():
        for name in (
            "old_normal_nw4",
            "t_df_n-1_nw4",
            "t_df_n-1_nw4_x_n/(n-1)",
            "t_df_5_nw4",
            "t_df_4_nw4 (CHOSEN)",
            "t_df_3_nw4",
        ):
            cands.setdefault(name, {})[key] = round(
                float((candidate_p(name, x, P.hac_lags) < ALPHA).mean()), 4
            )
    out["candidate_single_check_size_n40"] = cands

    # sequential scenarios: 121 daily checks, persist 3
    scen: dict = {}
    syn = {
        "null_t4_ar0.3": gen_ar_t(rng, args.paths, HORIZON, 0.3),
        "null_t4_ar0.0": gen_ar_t(rng, args.paths, HORIZON, 0.0),
        "null_t4_ar0.5": gen_ar_t(rng, args.paths, HORIZON, 0.5),
    }
    real = {
        "null_real_as_good_as_holding": block_paths(rng, d - d.mean(), args.paths, HORIZON),
        "real_10pct_worse_than_holding": block_paths(
            rng, d - d.mean() + 0.1 * hold.mean(), args.paths, HORIZON
        ),
        "real_p3_effect (model WITH skill)": block_paths(rng, d, args.paths, HORIZON),
    }
    for label, x in {**syn, **real}.items():
        rec: dict = {}
        for tag, fn in (("old", legacy_p), ("new", new_p)):
            br = breach_matrix(x, fn)
            rec[tag] = {
                "any_single_check_breach_within_121": round(float(br.any(axis=1).mean()), 4),
                "full_rule": demotion_stats(first_demotion_check(br)),
            }
        scen[label] = rec
    out["sequential_121_checks_persist3"] = scen

    # (e) direction and range: exact size at the boundary of the null (cannot be simulated away)
    def exact_size(n: int, p0: float) -> float:
        ks = np.arange(0, n + 1)
        reject = binom.cdf(ks, n, p0) < 0.05
        return float(binom.pmf(ks[reject], n, p0).sum())

    out["direction_range_exact_size"] = {
        "direction_n40_true_acc_0.55": round(exact_size(40, P.dir_floor), 4),
        "direction_n30_true_acc_0.55": round(exact_size(30, P.dir_floor), 4),
        "range_n60_true_cov_0.75": round(exact_size(60, P.cov_nominal - P.cov_slack), 4),
        "range_n30_true_cov_0.75": round(exact_size(30, P.cov_nominal - P.cov_slack), 4),
    }

    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
