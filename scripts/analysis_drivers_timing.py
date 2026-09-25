"""Before/after evidence for the ml/drivers.py timing fix (ADR 058 A16).

Part 1 -- which clock explains IBJA's fix-to-fix moves? For every consecutive pair of IBJA fixes
2024-10-08..latest (Yahoo keeps 730 days of 1-hour bars), gold x USD/INR is read three ways:
  old_same_date     daily bars sharing the IBJA date (the pre-fix join; GC=F settles ~6.5 h
                    AFTER the PM fix)
  daily_lagged      the latest daily bars public at the fix (GC=F settle of the previous NY day;
                    INR=X taken as known 23:59 UTC of its date)
  intraday_at_fix   Close of the last 1-hour bar that ended by the fix instant (the fix shipped)
and the premium residual Delta ln(IBJA) - Delta ln(gold x FX) is summarised: its SD (smaller =
the global drivers explain more of the move), the correlation of the two changes, and direction
agreement.

Part 2 -- what the user would have seen. Replays compute_driver_attribution over the last ~60
days, once with the pre-fix code (loaded from git) and once with the fixed code. Simulated run
time: 21:00 UTC on each IBJA date E (after that day's COMEX settle, so the pre-fix code sees the
FINAL same-date bar; a daytime run would see a moving bar -- not replayed). Inputs: IBJA rows
dated <= E; daily macro dated <= E from ml.macro.fetch_macro_features (the live download path,
to a temp cache); 1-hour bars that ENDED by the run time; prices.json readings <= the run time.
The displayed text is derived with a port of app.js renderDriverContext's branch logic
(thresholds 2.0% driver / 1.0% premium / Rs 10 headline).

Usage: python scripts/analysis_drivers_timing.py [--days 60]
Writes reports/drivers_timing/before_after.json and prints the tables.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml import drivers as new_drivers
from ml.macro import INTRADAY_TICKERS, _extract_close, fetch_macro_features

OUT = ROOT / "reports" / "drivers_timing" / "before_after.json"
OLD_REF = "51769bd6"  # origin/master when this fix branched: the pre-fix ml/drivers.py
HOURLY_START = "2024-10-01"  # inside Yahoo's 730-day window for 1-hour bars


def _load_old_module(tmp: Path):
    src = subprocess.run(
        ["git", "show", f"{OLD_REF}:ml/drivers.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    path = tmp / "drivers_old.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("drivers_old", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def displayed(ctx: dict) -> dict:
    """Port of app.js renderDriverContext: which catalogue keys render, with which numbers."""
    ds = ctx.get("driver_state")
    if not ctx.get("macro_fresh") or not ds:
        return {"hidden": True}
    w7, w30 = ctx["windows"].get("7d"), ctx["windows"].get("30d")
    inr, gold = ds["usd_inr_30d_pct_change"], ds["gold_usd_30d_pct_change"]
    prem_avail = w30 is not None and isinstance(w30.get("delta_pct_premium"), (int, float))
    prem30 = w30["delta_pct_premium"] if prem_avail else 0.0
    inr_moved, gold_moved = abs(inr) > 2.0, abs(gold) > 2.0
    prem_moved = prem_avail and abs(prem30) > 1.0

    headline = None
    keys = ("total_move_rs_per_g", "usdinr_contrib_rs_per_g", "gold_usd_contrib_rs_per_g")
    if w7 and w7.get("attribution_valid") and all(isinstance(w7.get(k), float | int) for k in keys):
        # JS Math.round rounds .5 up; floor(x + 0.5) matches it
        total = math.floor(w7["total_move_rs_per_g"] + 0.5)
        inr_pt = abs(math.floor(w7["usdinr_contrib_rs_per_g"] + 0.5))
        gold_pt = abs(math.floor(w7["gold_usd_contrib_rs_per_g"] + 0.5))
        up = total >= 0
        if inr_pt >= gold_pt and inr_pt > 10:
            key = "driverUpInrDominant" if up else "driverDownInrDominant"
        elif gold_pt > 10:
            key = "driverUpGoldDominant" if up else "driverDownGoldDominant"
        else:
            key = "driverUpMixed" if up else "driverDownMixed"
        headline = {"key": key, "total": abs(total), "gold": gold_pt, "inr": inr_pt}

    if inr_moved or gold_moved:
        parts = []
        if inr_moved:
            k = "driverRupeeWeakened" if inr > 0 else "driverRupeeStrengthened"
            parts.append(f"{k}:{abs(inr):.1f}")
        if gold_moved:
            parts.append(f"{'driverGoldUp' if gold > 0 else 'driverGoldDown'}:{abs(gold):.1f}")
        state = " + ".join(parts)
    elif prem_moved:
        state = "driverPremiumDominated"
    elif not prem_avail:
        state = "driverStateUnavailable"
    else:
        state = "driverAllFlat"
    return {"hidden": False, "headline": headline, "state": state}


def _residual_stats(df: pd.DataFrame) -> dict:
    df = df.dropna(subset=["ibja_10g", "gold_usd", "usd_inr"])
    d_ibja = np.log(df["ibja_10g"]).diff()
    d_glob = (np.log(df["gold_usd"]) + np.log(df["usd_inr"])).diff()
    d_prem = d_ibja - d_glob
    moved = d_ibja.abs() > 1e-9
    return {
        "n_pairs": int(d_ibja.notna().sum()),
        "sd_premium_residual_pct": round(float(d_prem.std() * 100), 3),
        "mean_abs_premium_residual_pct": round(float(d_prem.abs().mean() * 100), 3),
        "corr_ibja_vs_global": round(float(d_ibja.corr(d_glob)), 3),
        "direction_agreement_pct": round(
            float((np.sign(d_ibja[moved]) == np.sign(d_glob[moved])).mean() * 100), 1
        ),
        "n_direction": int(moved.sum()),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    args = ap.parse_args()
    import yfinance as yf

    ibja_all = pd.read_parquet(ROOT / "data" / "ibja_rates.parquet")
    ibja_all["date_parsed"] = pd.to_datetime(ibja_all["date"])
    last = ibja_all["date_parsed"].max()
    end = (last + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    prices = json.loads((ROOT / "data" / "prices.json").read_text())
    ibja = new_drivers._load_ibja(ROOT / "data")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        old_drivers = _load_old_module(tmp)
        macro = fetch_macro_features("2024-07-01", end, cache_path=tmp / "macro_full.parquet")
        raw_h = yf.download(
            list(INTRADAY_TICKERS.values()),
            start=HOURLY_START,
            end=end,
            interval="1h",
            auto_adjust=True,
            progress=False,
            threads=False,
        )
        raw_h.index = pd.to_datetime(raw_h.index, utc=True)
        hourly = _extract_close(raw_h, INTRADAY_TICKERS).dropna(how="all")

        # ---- Part 1: explanatory power of each clock ----
        daily = macro[["gold_usd", "usd_inr"]].copy()
        daily.index = pd.DatetimeIndex(daily.index).tz_localize(None)
        variants = {
            "old_same_date": ibja.join(daily, how="inner"),
            "daily_lagged": new_drivers._align_macro_to_fixes(ibja, daily),
            "intraday_at_fix": new_drivers._align_intraday_to_fixes(ibja, hourly),
        }
        start = variants["intraday_at_fix"].index.min() + pd.Timedelta(days=7)
        common = set.intersection(*(set(v.index[v.index >= start]) for v in variants.values()))
        part1 = {
            "window": [min(common).strftime("%Y-%m-%d"), max(common).strftime("%Y-%m-%d")],
            "note": "same IBJA dates for all three; consecutive rows in the IBJA series",
            **{k: _residual_stats(v.loc[sorted(common)]) for k, v in variants.items()},
        }

        # ---- Part 2: replay what the section showed ----
        replay = ibja_all[
            (ibja_all["date_parsed"] > last - pd.Timedelta(days=args.days))
            & ibja_all["pm_916"].notna()
        ]["date_parsed"].tolist()
        rows = []
        for e in replay:
            run_at = pd.Timestamp(e.date(), tz="UTC") + pd.Timedelta(hours=21)
            dd = tmp / e.strftime("%Y%m%d")
            dd.mkdir()
            ibja_all[ibja_all["date_parsed"] <= e].drop(columns=["date_parsed"]).to_parquet(
                dd / "ibja_rates.parquet", index=False
            )
            macro[macro.index <= pd.Timestamp(e.date(), tz="UTC")].to_parquet(
                dd / "macro_cache.parquet"
            )
            hourly[hourly.index + pd.Timedelta(hours=1) <= run_at].to_parquet(
                dd / "macro_intraday.parquet"
            )
            (dd / "prices.json").write_text(
                json.dumps([p for p in prices if pd.Timestamp(p["timestamp"]) <= run_at])
            )
            old = old_drivers.compute_driver_attribution(data_dir=dd, macro_staleness_days=0.0)
            new = new_drivers.compute_driver_attribution(data_dir=dd, macro_staleness_days=0.0)
            for c in (old, new):
                c.pop("computed_at", None)
            rows.append(
                {
                    "date": e.strftime("%Y-%m-%d"),
                    "old": {"ctx": old, "display": displayed(old)},
                    "new": {"ctx": new, "display": displayed(new)},
                }
            )

    def win(r: dict, side: str, w: str) -> dict:
        return r[side]["ctx"]["windows"][w]

    part2: dict = {"n_days": len(rows), "first": rows[0]["date"], "last": rows[-1]["date"]}
    for w in ("7d", "30d"):
        o = np.array([win(r, "old", w)["premium_share_pct"] or np.nan for r in rows], dtype=float)
        n = np.array([win(r, "new", w)["premium_share_pct"] or np.nan for r in rows], dtype=float)
        ov = np.array([win(r, "old", w)["attribution_valid"] for r in rows])
        nv = np.array([win(r, "new", w)["attribution_valid"] for r in rows])
        part2[w] = {
            "premium_share_median_old": round(float(np.nanmedian(o)), 1),
            "premium_share_median_new": round(float(np.nanmedian(n)), 1),
            "share_over_15pct_old": int(np.sum(o > 15)),
            "share_over_15pct_new": int(np.sum(n > 15)),
            "attribution_valid_old": int(ov.sum()),
            "attribution_valid_new": int(nv.sum()),
            "valid_false_to_true": int(np.sum(~ov & nv)),
            "valid_true_to_false": int(np.sum(ov & ~nv)),
            "median_abs_share_change_pp": round(float(np.nanmedian(np.abs(n - o))), 1),
        }
    d_old = [r["old"]["display"] for r in rows]
    d_new = [r["new"]["display"] for r in rows]
    part2["display"] = {
        "headline_shown_old": sum(bool(d.get("headline")) for d in d_old),
        "headline_shown_new": sum(bool(d.get("headline")) for d in d_new),
        "local_factors_state_old": sum(d.get("state") == "driverPremiumDominated" for d in d_old),
        "local_factors_state_new": sum(d.get("state") == "driverPremiumDominated" for d in d_new),
        "days_any_display_change": sum(a != b for a, b in zip(d_old, d_new, strict=True)),
        "days_headline_key_changed": sum(
            (a.get("headline") or {}).get("key") != (b.get("headline") or {}).get("key")
            for a, b in zip(d_old, d_new, strict=True)
        ),
        "days_state_changed": sum(
            a.get("state") != b.get("state") for a, b in zip(d_old, d_new, strict=True)
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "method": "see scripts/analysis_drivers_timing.py docstring",
                "old_code_ref": OLD_REF,
                "part1_explanatory_power": part1,
                "part2_replay_summary": part2,
                "part2_rows": rows,
            },
            indent=1,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"part1": part1, "part2": part2}, indent=1))
    print("\ndate | 7d share old/new | valid old/new | headline old -> new | state old -> new")
    for r in rows:
        h = [(r[s]["display"].get("headline") or {}) for s in ("old", "new")]
        print(
            f"{r['date']} | {win(r, 'old', '7d')['premium_share_pct']}/"
            f"{win(r, 'new', '7d')['premium_share_pct']} | "
            f"{win(r, 'old', '7d')['attribution_valid']}/{win(r, 'new', '7d')['attribution_valid']}"
            f" | {h[0].get('key')} {h[0].get('total')},{h[0].get('gold')},{h[0].get('inr')} -> "
            f"{h[1].get('key')} {h[1].get('total')},{h[1].get('gold')},{h[1].get('inr')} | "
            f"{r['old']['display'].get('state')} -> {r['new']['display'].get('state')}"
        )


if __name__ == "__main__":
    main()
