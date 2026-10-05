"""scripts/check_adr066_promotion.py -- the ADR 066 promotion check, run mechanically.

ADR 066 (docs/adr/066-nextfix-intraday-shadow.md, "Promotion rule (set before any data)") says a
window may switch from "hold the fix" to the hourly world-price pass-through only if ALL of these
hold, in a separate PR. This script evaluates them, per window, and changes nothing:

  1. >= 14 calendar days of shadow entries AND >= 8 resolved base fixes in that window.
     "Resolved" = scored by ml.nextfix_intraday.score, which drops entries logged at or after their
     target fix was published (logged_after_target, ADR 066 scoring correction 2026-10-05); the
     number dropped is reported as n_excluded_logged_after_target. "Calendar days" = inclusive span
     from the first to the last shadow entry in that window (the stricter reading of the ADR; the
     count of distinct days is reported next to it).
  2. In the backtest, the CHOSEN beta's 95% CI for the change against holding lies entirely below 0
     AND its Diebold-Mariano p-value < 0.05.
       CHOSEN BETA (the ADR does not define it; defined here, once, mechanically): the beta in
       {0.5, 1.0} with the lower backtest MAE in that window (ties -> 0.5). A window passes
       condition 2 only on the chosen beta. The other beta is reported too, never substituted.
  3. The shadow agrees: same sign of the change vs holding as the backtest, and the shadow's point
     estimate lies inside the backtest's 95% CI (chosen beta).
  4. The window's range re-fitted on the pass-through errors keeps 80% coverage (ADR 065): the
     walk-forward volatility-scaled split-conformal recipe of ml.nextfix.flat_record (NOMINAL,
     CONFORMAL_WINDOW, MIN_CONFORMAL, EWMA vol with VOL_HALFLIFE) is applied to the chosen beta's
     errors over the backtest decisions (one per base fix, the latest decision before the target):
     the pass-through forecast plays the role of the held "base", the realised fix the "target".
     Coverage is reported with a Wilson 95% interval; pass if that interval contains 0.80.
  5. Direction additionally needs ml.direction.gate.decide_direction_signal to ship on that
     window's own record (hard up/down calls = sign of x_since_fix vs the realised direction of the
     fix, on the same backtest decisions). Reported as `direction_line`; it gates any direction
     line, not the range promotion.

Backtest source: recomputed from the cached hourly bars when data/macro_intraday.parquet exists
(`python ml/macro.py` writes it; gitignored), else data/nextfix_intraday_backtest.json. The JSON
holds only per-window summaries, so under the fallback conditions 4 and 5 cannot be evaluated and
FAIL CLOSED ("not evaluable"), never pass.

Scope: after_morning_rate and after_afternoon_rate only. after_us_close is the live model's window
(ADR 065) and is out of scope. Read-only: nothing under ml/ or data/ is touched; the only write is
reports/adr066_check_<date>.json. Promotion itself is GG's decision and a separate PR (ADR 066).

Usage: python scripts/check_adr066_promotion.py [--now ISO] [--data-dir D] [--out-dir D]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from ml import nextfix, nextfix_intraday
from ml.direction.evaluate import compute_direction_metrics
from ml.direction.gate import decide_direction_signal

WINDOWS = ("after_morning_rate", "after_afternoon_rate")
OUT_OF_SCOPE = ("after_us_close",)
CANDIDATE_BETAS = (0.5, 1.0)  # beta 0 is the live "hold" forecast
MIN_SHADOW_DAYS = 14
MIN_RESOLVED_FIXES = 8
DM_ALPHA = 0.05
NO_DATA = "no data"
NOTE_PROMOTION = (
    "Read-only analysis. Promotion is GG's decision and, per ADR 066, a separate PR; this report "
    "changes nothing live."
)


# -- helpers ------------------------------------------------------------------------------------


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def _load_json(path: Path) -> dict | None:
    """The parsed object, or None when missing / malformed / not an object (fail closed)."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _num(x: object) -> float | None:
    return float(x) if isinstance(x, (int, float)) and math.isfinite(x) else None  # type: ignore[arg-type]


def _parse_t(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# -- shadow side --------------------------------------------------------------------------------


def clean_entries(raw: object) -> list[dict]:
    """Shadow entries that carry every field score() reads; anything else is dropped, not guessed."""
    if not isinstance(raw, list):
        return []
    need = ("logged_at", "base_at", "base", "window", "target_kind", "x_since_fix", "pred")
    out = []
    for e in raw:
        if not isinstance(e, dict) or any(k not in e for k in need):
            continue
        if not isinstance(e["pred"], dict) or any(
            f"beta_{b}" not in e["pred"] for b in nextfix_intraday.BETAS
        ):
            continue
        out.append({**e, "t": e.get("t") or e["logged_at"]})
    return out


def shadow_window_stats(entries: list[dict], window: str) -> dict:
    """Condition-1 inputs and the shadow's own score for one window."""
    in_win = [e for e in entries if e["window"] == window]
    days = sorted({str(e["logged_at"])[:10] for e in in_win})
    span = (
        (datetime.fromisoformat(days[-1]) - datetime.fromisoformat(days[0])).days + 1 if days else 0
    )
    sc = nextfix_intraday.score(entries).get(window, {})
    return {
        "n_entries": len(in_win),
        "distinct_days": len(days),
        "calendar_span_days": span,
        "n_resolved_base_fixes": int(sc.get("n_fixes", 0)),
        "n_excluded_logged_after_target": int(sc.get("n_excluded_logged_after_target", 0)),
        "score": sc,
    }


# -- backtest side ------------------------------------------------------------------------------


def backtest_rows(events: list, g: pd.Series) -> list[dict]:
    """Every hourly decision of ml.nextfix_intraday.backtest(), but returning the rows.

    Same loop as nextfix_intraday.backtest (which only returns scores); a test asserts that
    score(rows) equals backtest()'s published scores so the two cannot drift apart."""
    rows: list[dict] = []
    if g.empty:
        return rows
    start = g.index.min().to_pydatetime() + timedelta(hours=1)
    end = g.index.max().to_pydatetime()
    t = start.replace(minute=15, second=0, microsecond=0)
    while t <= end:
        fc = nextfix_intraday.forecast_at(events, g, t)
        if fc is not None:
            nx = nextfix_intraday.next_fix_after(events, _parse_t(fc["base_at"]), fc["target_kind"])
            if nx is not None and nx[0] > t:
                rows.append({**fc, "t": nextfix_intraday._iso(t), "target": round(nx[3], 2)})
        t += timedelta(hours=1)
    return rows


def last_decision_per_fix(rows: list[dict], window: str) -> list[dict]:
    """One row per base fix (its latest decision), as score(key='base_at') uses, oldest first."""
    last: dict = {}
    for r in sorted(
        (
            r
            for r in rows
            if r["window"] == window
            and r.get("target") is not None
            and not nextfix_intraday.logged_after_target(r)
        ),
        key=lambda r: r["t"],
    ):
        last[r["base_at"]] = r
    return sorted(last.values(), key=lambda r: r["base_at"])


def load_backtest(data_dir: Path) -> dict:
    """{'source', 'scores' (per window), 'rows' (or None)} -- recomputed from bars when cached."""
    parquet = data_dir / nextfix_intraday.INTRADAY_FILE
    ibja = data_dir / nextfix.IBJA_PATH.name
    if parquet.exists() and ibja.exists():
        try:
            events = nextfix.fix_events(nextfix.load_ibja_full(ibja))
            g = nextfix_intraday.load_bars(data_dir)
            rows = backtest_rows(events, g)
            if rows:
                return {
                    "source": "recomputed_from_cached_hourly_bars",
                    "scores": nextfix_intraday.score(rows),
                    "rows": rows,
                    "bars_from": nextfix_intraday._iso(g.index.min().to_pydatetime()),
                    "bars_to": nextfix_intraday._iso(g.index.max().to_pydatetime()),
                }
        except Exception as exc:
            fallback_note = f"recompute failed ({type(exc).__name__}: {exc}); used JSON"
        else:
            fallback_note = "recompute produced no decisions; used JSON"
    else:
        fallback_note = "data/macro_intraday.parquet absent; used JSON"
    bt = _load_json(data_dir / nextfix_intraday.BACKTEST_PATH.name)
    scores = (bt or {}).get("scored_on_last_decision_per_fix")
    return {
        "source": "data/nextfix_intraday_backtest.json",
        "fallback_reason": fallback_note,
        "scores": scores if isinstance(scores, dict) else {},
        "rows": None,
        "bars_from": (bt or {}).get("bars_from"),
        "bars_to": (bt or {}).get("bars_to"),
    }


def beta_summary(bt_win: dict, beta: float) -> dict:
    ci = bt_win.get(f"change_beta_{beta}_ci95")
    return {
        "beta": beta,
        "mae": _num(bt_win.get(f"mae_beta_{beta}")),
        "change_pct": _num(bt_win.get(f"change_beta_{beta}_pct")),
        "ci95": ci if isinstance(ci, list) and len(ci) == 2 else None,
        "dm_p": _num(bt_win.get(f"dm_p_beta_{beta}")),
    }


def choose_beta(bt_win: dict) -> float | None:
    """The beta in {0.5, 1.0} with the lower backtest MAE (ties -> 0.5); None if MAE missing."""
    maes = {b: _num(bt_win.get(f"mae_beta_{b}")) for b in CANDIDATE_BETAS}
    if any(v is None for v in maes.values()):
        return None
    return min(CANDIDATE_BETAS, key=lambda b: (maes[b], b))  # type: ignore[arg-type,return-value]


def cond2(s: dict) -> dict:
    ci, p = s["ci95"], s["dm_p"]
    if ci is None or p is None:
        return {"pass": False, "detail": "backtest CI / DM p missing"}
    ok = ci[1] < 0 and p < DM_ALPHA
    return {
        "pass": bool(ok),
        "ci95_upper": ci[1],
        "dm_p": p,
        "detail": f"CI [{ci[0]}, {ci[1]}] (need upper < 0), DM p {p} (need < {DM_ALPHA})",
    }


# -- conditions 4 and 5 (need the decision rows) -------------------------------------------------


def cond4(rows: list[dict] | None, window: str, beta: float) -> dict:
    """80% range coverage of the walk-forward conformal band on the pass-through errors."""
    if rows is None:
        return {"pass": False, "detail": "not evaluable: needs the cached hourly bars"}
    dec = last_decision_per_fix(rows, window)
    pairs = pd.DataFrame(
        {
            "d0": [pd.Timestamp(r["base_date"]) for r in dec],
            "base": [float(r["pred"][f"beta_{beta}"]) for r in dec],  # the forecast being ranged
            "target": [float(r["target"]) for r in dec],
        }
    )
    if len(pairs):
        pairs["y"] = np.log(pairs["target"] / pairs["base"])
    rec = nextfix.flat_record(pairs) if len(pairs) else {"n": 0, "ready": False}
    n_need = nextfix.MIN_CONFORMAL + 1
    if not rec.get("ready"):
        return {
            "pass": False,
            "n_pairs": len(pairs),
            "detail": f"not evaluable: {len(pairs)} decisions, need >= {n_need} for "
            f"{nextfix.MIN_CONFORMAL} scored points",
        }
    lo, hi = rec["range_coverage_ci95"]
    ok = lo <= nextfix.NOMINAL <= hi
    return {
        "pass": bool(ok),
        "n_pairs": len(pairs),
        "n_scored": rec["n"],
        "coverage": rec["range_coverage"],
        "wilson95": [lo, hi],
        "nominal": nextfix.NOMINAL,
        "mean_width": rec["range_mean_width"],
        "detail": f"coverage {rec['range_coverage']} on n={rec['n']}, Wilson95 [{lo}, {hi}] "
        f"(need to contain {nextfix.NOMINAL})",
    }


def direction_line(rows: list[dict] | None, window: str) -> dict:
    """decide_direction_signal on this window's own pass-through direction calls."""
    if rows is None:
        return {"ship": False, "detail": "not evaluable: needs the cached hourly bars"}
    dec = [
        r
        for r in last_decision_per_fix(rows, window)
        if r["target"] != r["base"] and r["x_since_fix"] != 0
    ]
    if not dec:
        return {"ship": False, "n": 0, "detail": "no moved fixes"}
    y_true = [int(r["target"] > r["base"]) for r in dec]
    y_prob = [1.0 if r["x_since_fix"] > 0 else 0.0 for r in dec]  # hard calls, no probabilities
    m = compute_direction_metrics(y_true, y_prob, f"adr066_{window}_passthrough")
    decision = decide_direction_signal(
        {"n_test_folds": len(dec), "logistic_metrics": m, "window": window}
    )
    return {
        "ship": bool(decision["ship"]),
        "n": len(dec),
        "accuracy": round(m["accuracy"], 3),
        "always_up_accuracy": round(m["always_up_accuracy"], 3),
        "p_value": round(m["p_value"], 4),
        "ece": round(m["ece"], 3),
        "reason": decision["reason"],
        "detail": "hard up/down calls as probabilities 1.0/0.0; decide_direction_signal: "
        + decision["reason"],
    }


# -- per-window evaluation ----------------------------------------------------------------------


def evaluate_window(window: str, entries: list[dict], bt: dict) -> dict:
    sh = shadow_window_stats(entries, window)
    bt_win = bt["scores"].get(window) if isinstance(bt.get("scores"), dict) else None
    out: dict = {"window": window, "shadow": {k: v for k, v in sh.items() if k != "score"}}
    if not entries or not isinstance(bt_win, dict) or not bt_win.get("ready"):
        out["verdict"] = f"{NO_DATA} (shadow entries: {len(entries)}, backtest ready: "
        out["verdict"] += f"{bool(isinstance(bt_win, dict) and bt_win.get('ready'))})"
        out["conditions"] = {}
        return out

    chosen = choose_beta(bt_win)
    other = next((b for b in CANDIDATE_BETAS if b != chosen), None)
    out["backtest"] = {
        "n_fixes": bt_win.get("n_fixes"),
        "chosen_beta": chosen,
        "chosen_beta_rule": "lower backtest MAE of {0.5, 1.0} in this window (ties -> 0.5)",
        "chosen": beta_summary(bt_win, chosen) if chosen is not None else None,
        "other_beta_reported_only": beta_summary(bt_win, other) if other is not None else None,
        "other_beta_cond2": cond2(beta_summary(bt_win, other)) if other is not None else None,
    }
    c1_ok = (
        sh["calendar_span_days"] >= MIN_SHADOW_DAYS
        and sh["n_resolved_base_fixes"] >= MIN_RESOLVED_FIXES
    )
    conds: dict = {
        "1_shadow_length": {
            "pass": bool(c1_ok),
            "calendar_span_days": sh["calendar_span_days"],
            "need_days": MIN_SHADOW_DAYS,
            "n_resolved_base_fixes": sh["n_resolved_base_fixes"],
            "need_fixes": MIN_RESOLVED_FIXES,
            "n_excluded_logged_after_target": sh["n_excluded_logged_after_target"],
            "detail": f"{sh['calendar_span_days']}/{MIN_SHADOW_DAYS} days, "
            f"{sh['n_resolved_base_fixes']}/{MIN_RESOLVED_FIXES} resolved base fixes",
        }
    }
    if chosen is None:
        for k in ("2_backtest_ci_dm", "3_shadow_agrees", "4_range_coverage"):
            conds[k] = {"pass": False, "detail": "chosen beta undefined (backtest MAE missing)"}
    else:
        s = beta_summary(bt_win, chosen)
        conds["2_backtest_ci_dm"] = cond2(s)
        sh_chg = _num(sh["score"].get(f"change_beta_{chosen}_pct"))
        if sh_chg is None or s["ci95"] is None or s["change_pct"] is None:
            conds["3_shadow_agrees"] = {
                "pass": False,
                "detail": "no shadow point estimate yet (score needs >= 5 resolved fixes)",
            }
        else:
            same = (sh_chg < 0) == (s["change_pct"] < 0) and sh_chg != 0 and s["change_pct"] != 0
            inside = s["ci95"][0] <= sh_chg <= s["ci95"][1]
            conds["3_shadow_agrees"] = {
                "pass": bool(same and inside),
                "shadow_change_pct": sh_chg,
                "backtest_change_pct": s["change_pct"],
                "same_sign": bool(same),
                "inside_backtest_ci": bool(inside),
                "detail": f"shadow {sh_chg}% vs backtest {s['change_pct']}% CI {s['ci95']}",
            }
        conds["4_range_coverage"] = cond4(bt.get("rows"), window, chosen)
    out["conditions"] = conds
    out["direction_line"] = {
        "required_for_any_direction_line": True,
        **direction_line(bt.get("rows"), window),
    }

    failed = [k for k, v in conds.items() if not v["pass"]]
    if not c1_ok:
        out["verdict"] = (
            f"too early (n={sh['n_resolved_base_fixes']} resolved base fixes, "
            f"{sh['calendar_span_days']} calendar days; need >= {MIN_RESOLVED_FIXES} and "
            f">= {MIN_SHADOW_DAYS}). Also failing: {[k for k in failed if k != '1_shadow_length']}"
        )
    elif failed:
        out["verdict"] = (
            "not yet (fails: " + "; ".join(f"{k}: {conds[k]['detail']}" for k in failed) + ")"
        )
    else:
        out["verdict"] = (
            "promotable (conditions 1-4 pass on beta "
            f"{chosen}); direction line additionally needs ship=True: "
            f"{out['direction_line']['ship']}"
        )
    return out


def run_check(data_dir: Path, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    shadow = _load_json(data_dir / nextfix_intraday.SHADOW_PATH.name)
    entries = clean_entries((shadow or {}).get("entries"))
    bt = load_backtest(data_dir)
    files = {
        name: _sha256(data_dir / name)
        for name in (
            nextfix_intraday.SHADOW_PATH.name,
            nextfix_intraday.BACKTEST_PATH.name,
            nextfix.IBJA_PATH.name,
            nextfix_intraday.INTRADAY_FILE,
        )
    }
    return {
        "adr": "066",
        "run_at": nextfix_intraday._iso(now),
        "repo_sha": _git_sha(),
        "data_file_sha256": files,
        "backtest_source": bt["source"],
        "backtest_fallback_reason": bt.get("fallback_reason"),
        "backtest_bars": [bt.get("bars_from"), bt.get("bars_to")],
        "shadow_entries_loaded": len(entries),
        "out_of_scope": {
            "windows": list(OUT_OF_SCOPE),
            "reason": "after_us_close is the live model's window (ADR 065), not evaluated here.",
        },
        "windows": {w: evaluate_window(w, entries, bt) for w in WINDOWS},
        "note": NOTE_PROMOTION,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--now", help="override run time (ISO, UTC); default: now")
    ap.add_argument("--data-dir", type=Path, default=_REPO / "data")
    ap.add_argument("--out-dir", type=Path, default=_REPO / "reports")
    args = ap.parse_args(argv)
    now = _parse_t(args.now) if args.now else datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    res = run_check(args.data_dir, now)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    path = args.out_dir / f"adr066_check_{now.strftime('%Y-%m-%d')}.json"
    path.write_text(json.dumps(res, indent=1) + "\n")
    print(f"ADR 066 promotion check @ {res['run_at']}  sha {res['repo_sha'][:10]}")
    print(
        f"backtest source: {res['backtest_source']}  shadow entries: {res['shadow_entries_loaded']}"
    )
    for w, r in res["windows"].items():
        print(f"\n[{w}] {r['verdict']}")
        for k, v in r.get("conditions", {}).items():
            print(f"   {'PASS' if v['pass'] else 'FAIL'} {k}: {v['detail']}")
        if "backtest" in r:
            b = r["backtest"]
            print(
                f"   chosen beta {b['chosen_beta']} ({b['chosen_beta_rule']}); other beta "
                f"{b['other_beta_reported_only']} -> cond2 {b['other_beta_cond2']}"
            )
        if "direction_line" in r:
            d = r["direction_line"]
            print(
                f"   direction line (required for any direction line): ship={d['ship']} "
                f"- {d['detail']}"
            )
    print(f"\nout of scope: {', '.join(OUT_OF_SCOPE)} (live model's window, ADR 065)")
    print(NOTE_PROMOTION)
    print(f"written: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
