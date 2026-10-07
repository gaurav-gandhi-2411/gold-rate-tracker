"""Replay ml.ingest_checks over the full committed history of every source (item 2f).

Three measurements, written to reports/ingest_checks_replay_<date>.json:

1. HEAD replay: each evaluator over the committed file as it stands (the stores are append-only,
   so HEAD holds every row ever committed except rows later rewritten in place).
2. Revision-union replay: row-level checks over the union of DISTINCT rows across every git
   revision of the file (catches rows that were later overwritten, e.g. IBJA PM filled in, and
   the Kalyan placeholder rows if they were ever removed).
3. Fault injection: copies of real rows corrupted in a controlled way; every injected fault
   must be flagged `block`. Also a time-split check of the jump limits (limits derived from the
   first half of a series, false positives counted on the second half) because the committed
   limits were derived in-sample and an in-sample false-positive rate is optimistic by
   construction.

Usage: python scripts/replay_ingest_checks.py [--out reports/ingest_checks_replay_2026-10-05.json]
"""

from __future__ import annotations

import argparse
import copy
import io
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

from ml import ingest_checks as ic

# Real clock by default so a re-run on newer data never sees committed rows as "future";
# the report records the reference time it used. Override with --now for reproduction.
NOW = pd.Timestamp(datetime.now(UTC))


def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True).stdout


def _revisions(rel: str) -> list[str]:
    return _git("log", "--format=%H", "--", rel).decode().split()


def _summ(rep: ic.SourceReport) -> dict[str, Any]:
    return {
        "n_rows": rep.n_rows,
        "status": rep.status,
        "n_block": sum(v.severity == "block" for v in rep.violations),
        "n_warn": sum(v.severity == "warn" for v in rep.violations),
        "counts_by_code": rep.counts_by_code(),
        "block_rows_sample": [v.where for v in rep.violations if v.severity == "block"][:5],
    }


def head_replay() -> dict[str, Any]:
    data = ROOT / "data"
    reports = ic.evaluate_all(data, NOW)
    return {k: _summ(v) for k, v in reports.items()}


def _union_json(rel: str) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for h in _revisions(rel):
        try:
            rows = json.loads(_git("show", f"{h}:{rel}"))
        except (subprocess.CalledProcessError, ValueError):
            continue
        for r in rows:
            seen.setdefault(json.dumps(r, sort_keys=True), r)
    return list(seen.values())


def _union_parquet(rel: str) -> tuple[pd.DataFrame, int]:
    frames = []
    revs = _revisions(rel)
    for h in revs:
        try:
            frames.append(pd.read_parquet(io.BytesIO(_git("show", f"{h}:{rel}"))))
        except Exception:
            continue
    df = pd.concat(frames, ignore_index=True).drop_duplicates().reset_index(drop=True)
    return df, len(revs)


def revision_union_replay() -> dict[str, Any]:
    out: dict[str, Any] = {}
    rows = _union_json("data/prices.json")
    rows.sort(key=lambda r: str(r.get("timestamp")))
    rep = ic.check_tanishq_prices(rows, now=NOW, series=False, check_newest_staleness=False)
    out["tanishq_prices"] = {**_summ(rep), "revisions": len(_revisions("data/prices.json"))}
    for name, rel, fn in (
        ("ibja_rates", "data/ibja_rates.parquet", ic.check_ibja_rates),
        ("fusion_snapshots", "data/fusion_snapshots.parquet", ic.check_fusion_snapshots),
        ("feature_store", "data/feature_store/snapshots.parquet", ic.check_feature_store),
    ):
        df, n_rev = _union_parquet(rel)
        rep = fn(df, now=NOW, series=False, check_newest_staleness=False)
        out[name] = {**_summ(rep), "revisions": n_rev}
    return out


# ------------------------------------------------------------------ fault injection


def _inject() -> dict[str, dict[str, bool]]:
    """Each fault is applied to a copy of the newest committed row(s); True = flagged `block`."""
    data = ROOT / "data"
    results: dict[str, dict[str, bool]] = {}

    def blocked(rep: ic.SourceReport, *codes: str) -> bool:
        return any(v.severity == "block" and (not codes or v.code in codes) for v in rep.violations)

    def base_prices() -> list[dict[str, Any]]:
        return json.loads((data / "prices.json").read_text(encoding="utf-8"))[-30:]

    def run_prices(mut: Any, *codes: str, extra_row: bool = False) -> bool:
        rows = copy.deepcopy(base_prices())
        if extra_row:
            rows.append(copy.deepcopy(rows[-1]))
        mut(rows)
        rep = ic.check_tanishq_prices(rows, now=NOW, check_newest_staleness=False)
        return blocked(rep, *codes)

    def setk(k: str, v: Any) -> Any:
        def f(rows: list[dict[str, Any]]) -> None:
            rows[-1][k] = v

        return f

    last = base_prices()[-1]
    ts_last = pd.Timestamp(last["timestamp"])
    p: dict[str, bool] = {
        "per_10g_in_per_g_field": run_prices(
            setk("22k", last["22k"] * 10), ic.UNIT_SUSPECT_PER_10G
        ),
        "string_number": run_prices(setk("22k", str(last["22k"])), ic.VALUE_NOT_NUMERIC),
        "nan": run_prices(setk("22k", float("nan")), ic.VALUE_NOT_FINITE),
        "negative": run_prices(setk("22k", -13700), ic.VALUE_NON_POSITIVE),
        "zero": run_prices(setk("22k", 0), ic.VALUE_NON_POSITIVE),
        "epoch_placeholder": run_prices(
            setk("timestamp", "1970-01-01T00:00:00.000Z"), ic.TS_EPOCH_PLACEHOLDER
        ),
        "future_timestamp": run_prices(setk("timestamp", "2027-01-01T00:00:00.000Z"), ic.TS_FUTURE),
        "naive_timestamp": run_prices(
            setk("timestamp", (ts_last + pd.Timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")),
            ic.TS_NAIVE,
        ),
        "timestamp_before_previous": run_prices(
            setk("timestamp", (ts_last - pd.Timedelta(days=2)).isoformat()),
            ic.TS_NOT_AFTER_PREVIOUS,
        ),
        "duplicate_timestamp": run_prices(
            lambda r: None, ic.TS_NOT_AFTER_PREVIOUS, ic.DUPLICATE_KEY, extra_row=True
        ),
        "jump_x1.3": run_prices(setk("22k", round(last["22k"] * 1.3)), ic.JUMP_BLOCK),
        "karat_ratio_broken": run_prices(setk("24k", round(last["24k"] * 1.05)), ic.RATIO_BLOCK),
        "missing_field": run_prices(lambda r: r[-1].pop("24k"), ic.SCHEMA_MISSING_FIELD),
        "wrong_container": blocked(ic.check_tanishq_prices({"x": 1}, now=NOW)),
        "empty": blocked(ic.check_tanishq_prices([], now=NOW)),
    }
    results["tanishq_prices"] = p

    ibja = pd.read_parquet(data / "ibja_rates.parquet")

    def run_ibja(mut: Any, *codes: str) -> bool:
        df = ibja.iloc[-20:-1].copy().reset_index(drop=True)
        mut(df)
        return blocked(ic.check_ibja_rates(df, now=NOW, check_newest_staleness=False), *codes)

    def setcell(col: str, val: Any, row: int = -1) -> Any:
        def f(df: pd.DataFrame) -> None:
            df[col] = df[col].astype(object)
            df.loc[df.index[row], col] = val

        return f

    results["ibja_rates"] = {
        "per_g_in_per_10g_field": run_ibja(setcell("pm_916", 13569.4), ic.UNIT_SUSPECT_PER_G),
        "string_number": run_ibja(setcell("pm_916", "135694"), ic.VALUE_NOT_NUMERIC),
        "nan_am": run_ibja(setcell("am_916", float("nan")), ic.NULL_VALUE),
        "negative": run_ibja(setcell("pm_916", -135694.0), ic.VALUE_NON_POSITIVE),
        "future_date": run_ibja(setcell("date", "2027-01-01"), ic.TS_FUTURE),
        "epoch_date": run_ibja(setcell("date", "1970-01-01"), ic.TS_EPOCH_PLACEHOLDER),
        "bad_date_string": run_ibja(setcell("date", "05/10/2026"), ic.TS_UNPARSEABLE),
        "duplicate_date": run_ibja(setcell("date", str(ibja.iloc[-3]["date"])), ic.DUPLICATE_KEY),
        "jump_x1.5": run_ibja(setcell("pm_916", 135694.0 * 1.5), ic.JUMP_BLOCK),
        "purity_ratio_broken": run_ibja(setcell("pm_999", 135694.0 * 1.3), ic.RATIO_BLOCK),
        "future_fetched_at": run_ibja(
            setcell("fetched_at", "2027-01-01T00:00:00+00:00"), ic.TS_FUTURE
        ),
        "naive_fetched_at": run_ibja(setcell("fetched_at", "2026-10-01T13:11:02"), ic.TS_NAIVE),
        "empty": blocked(ic.check_ibja_rates(ibja.iloc[0:0], now=NOW)),
    }

    fs = pd.read_parquet(data / "fusion_snapshots.parquet")

    good_kalyan = fs[(fs["source"] == "kalyan") & fs["observed_at"].str.startswith("2026")].iloc[
        -8:
    ]
    window = pd.concat([fs.iloc[-40:], good_kalyan]).sort_values("capture_utc", kind="stable")

    def run_fusion(mut: Any, *codes: str) -> bool:
        df = window.copy().reset_index(drop=True)
        mut(df)
        return blocked(ic.check_fusion_snapshots(df, now=NOW, check_newest_staleness=False), *codes)

    def fset(col: str, val: Any, row: int = -1, src: str | None = None) -> Any:
        def f(df: pd.DataFrame) -> None:
            i = df.index[row] if src is None else df.index[df["source"] == src][row]
            df[col] = df[col].astype(object)
            df.loc[i, col] = val

        return f

    results["fusion_snapshots"] = {
        "kalyan_1969_placeholder": run_fusion(
            fset("observed_at", "1969-12-31T18:30:00+00:00", src="kalyan"), ic.TS_EPOCH_PLACEHOLDER
        ),
        "kalyan_1970_placeholder": run_fusion(
            fset("observed_at", "1970-01-01T00:00:00+00:00", src="kalyan"), ic.TS_EPOCH_PLACEHOLDER
        ),
        "future_observed_at": run_fusion(
            fset("observed_at", "2027-01-01T00:00:00+00:00", src="grt"), ic.TS_FUTURE
        ),
        "naive_observed_at": run_fusion(
            fset("observed_at", "2026-10-05T04:09:12", src="malabar"), ic.TS_NAIVE
        ),
        "per_10g_in_rate": run_fusion(
            fset("rate_22k", 136750.0, src="grt"), ic.UNIT_SUSPECT_PER_10G
        ),
        "string_rate": run_fusion(fset("rate_22k", "13675", src="grt"), ic.VALUE_NOT_NUMERIC),
        "nan_rate": run_fusion(fset("rate_22k", float("nan"), src="malabar"), ic.VALUE_NOT_FINITE),
        "negative_rate": run_fusion(fset("rate_22k", -13675.0, src="grt"), ic.VALUE_NON_POSITIVE),
        "jump_x1.3": run_fusion(fset("rate_22k", 13675.0 * 1.3, src="grt"), ic.JUMP_BLOCK),
        "national_source_with_city": run_fusion(
            fset("city", "Chennai", src="grt"), ic.SCHEMA_UNKNOWN_VALUE
        ),
        "unknown_source": run_fusion(fset("source", "mystery"), ic.SCHEMA_UNKNOWN_VALUE),
        "duplicate_row": blocked(
            ic.check_fusion_snapshots(
                pd.concat([window, window.iloc[[-1]]], ignore_index=True),
                now=NOW,
                check_newest_staleness=False,
            ),
            ic.DUPLICATE_KEY,
        ),
        "cross_source_disagreement": run_fusion(
            fset("rate_22k", 13675.0 * 1.15, src="malabar"), ic.CROSS_SOURCE_BLOCK, ic.JUMP_BLOCK
        ),
    }

    store = pd.read_parquet(data / "feature_store" / "snapshots.parquet")

    def run_store(mut: Any, *codes: str) -> bool:
        df = store.iloc[-15:].copy().reset_index(drop=True)
        mut(df)
        return blocked(ic.check_feature_store(df, now=NOW, check_newest_staleness=False), *codes)

    def sset(col: str, val: Any, row: int = -1) -> Any:
        def f(df: pd.DataFrame) -> None:
            df[col] = df[col].astype(object)
            df.loc[df.index[row], col] = val

        return f

    results["feature_store"] = {
        "gold_usd_per_oz_as_inr": run_store(sset("gold_usd", 4167.2 * 96.3), ic.VALUE_OUT_OF_RANGE),
        "usd_inr_string": run_store(sset("usd_inr", "96.3"), ic.VALUE_NOT_NUMERIC),
        "nan_core": run_store(sset("gold_usd", float("nan")), ic.NULL_VALUE),
        "negative_vix": run_store(sset("vix", -15.3), ic.VALUE_NON_POSITIVE),
        "gold_jump_x1.5": run_store(
            sset("gold_usd", 4167.2 * 1.5), ic.JUMP_BLOCK, ic.VALUE_OUT_OF_RANGE
        ),
        "asof_after_as_of": run_store(sset("gold_usd_asof_date", "2027-01-01"), ic.TS_FUTURE),
        "asof_bad_string": run_store(sset("dxy_asof_date", "yesterday"), ic.TS_UNPARSEABLE),
        "tanishq_per_10g": run_store(sset("tanishq_22k", 137200.0), ic.UNIT_SUSPECT_PER_10G),
        "tanishq_vs_ibja_off": run_store(sset("tanishq_22k", 17000.0), ic.CROSS_SOURCE_BLOCK),
        "capture_future": run_store(sset("capture_utc", "2027-01-01T00:00:00Z"), ic.TS_FUTURE),
        "capture_epoch": run_store(
            sset("capture_utc", "1970-01-01T00:00:00Z"), ic.TS_EPOCH_PLACEHOLDER
        ),
    }

    shadow = json.loads((data / "shadow_fusion_output.json").read_text(encoding="utf-8"))

    def run_shadow(mut: Any, *codes: str) -> bool:
        o = copy.deepcopy(shadow)
        mut(o)
        return blocked(
            ic.check_shadow_fusion_output(o, now=NOW, check_newest_staleness=False), *codes
        )

    def nb(k: str, v: Any) -> Any:
        def f(o: dict[str, Any]) -> None:
            o["national_benchmark"][k] = v

        return f

    results["shadow_fusion_output"] = {
        "benchmark_per_10g": run_shadow(nb("value", 137050.0), ic.UNIT_SUSPECT_PER_10G),
        "benchmark_string": run_shadow(nb("value", "13705"), ic.VALUE_NOT_NUMERIC),
        "benchmark_nan": run_shadow(nb("value", float("nan")), ic.VALUE_NOT_FINITE),
        "capture_future": run_shadow(
            lambda o: o.__setitem__("capture_utc", "2027-01-01T00:00:00Z"), ic.TS_FUTURE
        ),
        "capture_epoch": run_shadow(
            lambda o: o.__setitem__("capture_utc", "1970-01-01T00:00:00Z"), ic.TS_EPOCH_PLACEHOLDER
        ),
        "missing_key": run_shadow(lambda o: o.pop("national_failures"), ic.SCHEMA_MISSING_FIELD),
        "not_object": blocked(ic.check_shadow_fusion_output([], now=NOW)),
    }

    seeds = pd.read_parquet(data / "history_seed_inr22k_proxy.parquet")

    def run_seed(mut: Any, *codes: str) -> bool:
        df = seeds.iloc[-30:].copy()
        mut(df)
        return blocked(ic.check_history_seed(df, kind="proxy", now=NOW, path="x"), *codes)

    def seed_set(col: str, val: Any, row: int = -1) -> Any:
        def f(df: pd.DataFrame) -> None:
            df[col] = df[col].astype(object)
            df.iloc[row, df.columns.get_loc(col)] = val

        return f

    def dup_idx(df: pd.DataFrame) -> None:
        df.index = pd.DatetimeIndex([*df.index[:-1], df.index[-2]])

    results["history_seed_proxy"] = {
        "per_g_in_per_10g_field": run_seed(seed_set("proxy_22k_per_10g", 13972.0), ic.JUMP_BLOCK),
        "nan": run_seed(seed_set("proxy_22k_per_10g", float("nan")), ic.VALUE_NOT_FINITE),
        "string": run_seed(seed_set("proxy_22k_per_10g", "145978"), ic.VALUE_NOT_NUMERIC),
        "negative": run_seed(seed_set("proxy_22k_per_10g", -145978.0), ic.VALUE_NON_POSITIVE),
        "jump_x1.5": run_seed(seed_set("proxy_22k_per_10g", 145978.0 * 1.5), ic.JUMP_BLOCK),
        "duplicate_date": run_seed(dup_idx, ic.DUPLICATE_KEY),
        "future_date": run_seed(
            lambda df: setattr(
                df,
                "index",
                pd.DatetimeIndex([*df.index[:-1], pd.Timestamp("2027-01-01", tz="UTC")]),
            ),
            ic.TS_FUTURE,
        ),
        "epoch_date": run_seed(
            lambda df: setattr(
                df,
                "index",
                pd.DatetimeIndex([*df.index[:-1], pd.Timestamp("1970-01-01", tz="UTC")]),
            ),
            ic.TS_EPOCH_PLACEHOLDER,
        ),
        "naive_index": run_seed(
            lambda df: setattr(df, "index", df.index.tz_localize(None)), ic.TS_NAIVE
        ),
    }

    outcomes = [
        json.loads(x)
        for x in (data / "tanishq_scrape_outcomes.jsonl").read_text(encoding="utf-8").splitlines()
        if x.strip()
    ][-30:]

    def run_out(mut: Any, *codes: str) -> bool:
        rows = copy.deepcopy(outcomes)
        mut(rows)
        return blocked(
            ic.check_scrape_outcomes(rows, now=NOW, check_newest_staleness=False), *codes
        )

    results["tanishq_scrape_outcomes"] = {
        "epoch_timestamp": run_out(
            lambda r: r[-1].__setitem__("timestamp", "1970-01-01T00:00:00Z"),
            ic.TS_EPOCH_PLACEHOLDER,
        ),
        "future_timestamp": run_out(
            lambda r: r[-1].__setitem__("timestamp", "2027-01-01T00:00:00Z"), ic.TS_FUTURE
        ),
        "numeric_timestamp": run_out(
            lambda r: r[-1].__setitem__("timestamp", 1790000000), ic.TS_UNPARSEABLE
        ),
        "duplicate_timestamp": run_out(
            lambda r: r.append(copy.deepcopy(r[-1])), ic.DUPLICATE_KEY, ic.TS_NOT_AFTER_PREVIOUS
        ),
        "missing_outcome": run_out(lambda r: r[-1].pop("outcome"), ic.SCHEMA_MISSING_FIELD),
    }
    return results


# ------------------------------------------------------------------ time-split of jump limits


def _p999(x: np.ndarray) -> float:
    return float(np.quantile(x, 0.999))


def time_split() -> dict[str, Any]:
    """Derive the p99.9 step on the first half, count rows of the second half exceeding 2x/4x."""
    data = ROOT / "data"
    res: dict[str, Any] = {}

    def split(name: str, steps: pd.Series) -> None:
        steps = steps.dropna().abs()
        h = len(steps) // 2
        a, b = steps.iloc[:h].to_numpy(), steps.iloc[h:].to_numpy()
        p = _p999(a)
        res[name] = {
            "n_derive": len(a),
            "n_test": len(b),
            "p999_first_half": round(p, 4),
            "warn_limit": round(2 * p, 4),
            "block_limit": round(4 * p, 4),
            "test_over_warn": int((b > 2 * p).sum()),
            "test_over_block": int((b > 4 * p).sum()),
            "test_max_step": round(float(b.max()), 4),
        }

    prices = pd.DataFrame(json.loads((data / "prices.json").read_text(encoding="utf-8")))
    split("tanishq_22k_per_reading", np.log(prices["22k"]).diff())
    ibja = pd.read_parquet(data / "ibja_rates.parquet")
    ibja["d"] = pd.to_datetime(ibja["date"])
    ibja = ibja.sort_values("d")
    x = ibja.set_index("d")["pm_916"].dropna()
    gd = x.index.to_series().diff().dt.days.clip(lower=1)
    split("ibja_pm_916_gap_scaled", np.log(x).diff() / np.sqrt(gd))
    for kind, col in (("proxy", "proxy_22k_per_10g"), ("label", "label_22k_per_10g")):
        s = pd.read_parquet(data / f"history_seed_inr22k_{kind}.parquet")
        split(f"seed_{kind}_daily", np.log(s[col]).diff())
    fs = pd.read_parquet(data / "fusion_snapshots.parquet")
    fs["cap"] = pd.to_datetime(fs["capture_utc"], utc=True)
    g = fs[(fs["source"] == "grt")].sort_values("cap")
    split("fusion_grt_per_capture", np.log(g["rate_22k"]).diff())
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out", type=Path, default=ROOT / "reports" / "ingest_checks_replay_2026-10-05.json"
    )
    ap.add_argument("--now", help="ISO-8601 UTC reference clock")
    args = ap.parse_args()
    global NOW
    if args.now:
        NOW = pd.Timestamp(args.now)
    head_sha = _git("rev-parse", "HEAD").decode().strip()
    inj = _inject()
    detection = {
        src: {
            "n_faults": len(r),
            "n_flagged_block": sum(bool(v) for v in r.values()),
            "missed": [k for k, v in r.items() if not v],
        }
        for src, r in inj.items()
    }
    out = {
        "generated_for_commit": head_sha,
        "reference_now": NOW.isoformat(),
        "head_replay": head_replay(),
        "revision_union_replay": revision_union_replay(),
        "time_split_jump_limits": time_split(),
        "fault_injection": {"detail": inj, "summary": detection},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(out, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    print(
        json.dumps({k: v for k, v in out.items() if k != "fault_injection"}, indent=1, default=str)[
            :6000
        ]
    )
    print(json.dumps(detection, indent=1))
    return 0 if all(not d["missed"] for d in detection.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
