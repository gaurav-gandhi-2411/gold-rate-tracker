"""Run the repo's leak guard (ml.leak_guard / ml.known_at, ADR 061) over every ml.nextfix forecast.

ml.nextfix does not call the guard itself. This replays the model's walk-forward: for each
out-of-sample fold (decision day D, decision moment = US close of D + margin) it lists every input
the forecast used -- the features, the history behind the rolling basis, and every training pair
(features and the label that makes the pair trainable) -- with the instant it became known, and
asserts each is known strictly before the decision moment.

Two runs:
  * ``repo_conventions``: the clocks ml.known_at defines today (usd_inr measured: 20:00 UTC of D);
  * ``legacy_conservative``: usd_inr known at 23:59 UTC of D (the convention before 2026-10-05),
    kept to show what the guard said then. The measured clock came from hourly-bar matching
    (reports/model_audit_2026-10/inrx_daily_clock.json, scripts/audit_inrx_daily_clock.py).
A negative control (a deliberately leaky input) must be flagged, or the run is not trusted.

    python scripts/audit_nextfix_leak.py [--out reports/model_audit_2026-10/leak_audit.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ml import known_at as ka
from ml import nextfix as nf
from ml.leak_guard import KnownInput, LeakGuard, TimingLeakError

# Latest UTC hour of D at which the daily INR=X value was seen to be set (hourly-bar match over
# the last ~60 days). Early-day: 0-9 UTC; 12:00 leaves margin.


def decision_moment(d0: pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(nf._at(d0, nf.US_CLOSE_UTC))


def pair_inputs(row: pd.Series, usdinr_clock: ka.Clock, *, with_label: bool) -> list[KnownInput]:
    """Every input one pair contributes: its features, and (training pairs) its label."""
    d0, d_prev = pd.Timestamp(row["d0"]), pd.Timestamp(row["d0"]) - pd.Timedelta(days=1)
    items = [
        KnownInput("ibja_pm(D)", "ibja", ka.ibja_known_at(d0, "pm")),
        KnownInput("gold_usd(D)", "comex", ka.comex_daily_known_at(d0)),
        KnownInput("gold_usd(D-1)", "comex", ka.comex_daily_known_at(d_prev)),
        KnownInput("usd_inr(D)", "usdinr", ka.at_utc(d0, usdinr_clock)),
        KnownInput("usd_inr(D-1)", "usdinr", ka.at_utc(d_prev, usdinr_clock)),
    ]
    if pd.notna(row.get("pmprev")):
        items.append(KnownInput("ibja_pm(prev day)", "ibja", ka.ibja_known_at(d_prev, "pm")))
    if with_label:
        items.append(KnownInput("label ibja_pm(D1)", "ibja", ka.ibja_known_at(row["d1"], "pm")))
    return items


def audit(folds: list[dict], pairs: pd.DataFrame, usdinr_clock: ka.Clock, name: str) -> dict:
    guard = LeakGuard(name, mode="report")
    for f in folds:
        d0 = pd.Timestamp(f["d0"])
        moment = decision_moment(d0)
        row = pairs[pairs["d0"] == d0]
        if row.empty:
            guard.n_unchecked += 1
            continue
        inputs = pair_inputs(row.iloc[0], usdinr_clock, with_label=False)
        # the rolling basis reads the previous BASIS_WINDOW days of (world, ibja) too
        hist = pairs[pairs["d0"] < d0].tail(nf.BASIS_WINDOW)
        train = pairs[pairs["d1"].notna() & (pairs["d1"] <= d0)]
        for _, r in hist.iterrows():
            inputs += pair_inputs(r, usdinr_clock, with_label=False)
        for _, r in train.iterrows():
            inputs += pair_inputs(r, usdinr_clock, with_label=True)
        guard.check(moment, inputs, context=f"D={f['d0']}")
    return guard.summary()


def negative_control(pairs: pd.DataFrame) -> bool:
    """A forecast that used its own target fix must be flagged. True when the guard fires."""
    row = pairs[pairs["d1"].notna()].iloc[-1]
    leaky = [KnownInput("TARGET ibja_pm(D1)", "ibja", ka.ibja_known_at(row["d1"], "pm"))]
    try:
        LeakGuard("control", mode="raise").check(decision_moment(row["d0"]), leaky)
    except TimingLeakError:
        return True
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/model_audit_2026-10/leak_audit.json")
    args = ap.parse_args()
    try:
        from ml.macro import load_macro_features

        macro = load_macro_features()
    except Exception as exc:
        print(f"macro cache unavailable ({exc})", file=sys.stderr)
        macro = None
    full = nf.load_ibja_full()
    pairs = nf.build_pairs(
        full.dropna(subset=["pm"]).reset_index(drop=True), nf.global_series(macro)
    )
    folds = nf.load_oos()
    out = {
        "n_folds": len(folds),
        "decision_moment": "US close of D + margin = 22:15 UTC on D (ml.nextfix.US_CLOSE_UTC)",
        "negative_control_flagged": negative_control(pairs),
        "repo_conventions": audit(folds, pairs, ka.MACRO_DAILY_CLOCKS["usd_inr"], "nextfix/repo"),
        "legacy_conservative": audit(
            folds, pairs, ka.USDINR_SNAPSHOT_CONSERVATIVE, "nextfix/legacy"
        ),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1, default=str) + "\n")
    print(json.dumps(out, indent=1, default=str))
    if not out["negative_control_flagged"]:
        raise SystemExit("negative control was NOT flagged: the guard cannot be trusted")


if __name__ == "__main__":
    main()
