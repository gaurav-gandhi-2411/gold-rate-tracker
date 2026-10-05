"""ml.nextfix_intraday -- shadow: does the hourly world price improve on holding the latest fix?

ADR 066 (GG 2026-10-02). Shadow only: nothing here reaches the page or forecast.json.

Between IBJA's fixes, ml.nextfix holds the latest fix (ADR 065): after an AM fix until that day's
PM fix, and after a PM fix until the US close. Daily data has no edge in those windows; the world
price keeps moving, though, and ml.macro already caches 1-hour GC=F / USD-INR bars
(data/macro_intraday.parquet, last 60 days, bars labelled by their START in UTC).

Pass-through forecast. With G(t) = gold_usd x usd_inr read as the close of the last 1-hour bar that
ENDED by t (the ml.drivers convention, ADR 058), and the latest fix B published at t_B, the next fix
is forecast as B x (G(now) / G(t_B)) ** beta for beta in BETAS. beta = 0 is the live forecast (hold).

Two outputs, both under data/:
  * nextfix_intraday_shadow.json -- one entry per check-price run: the forecasts made then; later
    runs fill in the next fix once IBJA publishes it. Scored per base fix (the latest entry before
    the target was published), so several runs on one day count once.
  * nextfix_intraday_backtest.json -- every hour of the last ~60 days of bars replayed: same
    forecasts, scored against the fixes that followed. Recomputed each run (cheap arithmetic).

Promotion needs at least 14 days of shadow entries AND the backtest showing the pass-through beats
holding with a confidence interval that excludes zero, in a separate PR (ADR 066).
"""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from ml import nextfix

logger = logging.getLogger(__name__)

DATA_DIR = nextfix.DATA_DIR
SHADOW_PATH = DATA_DIR / "nextfix_intraday_shadow.json"
BACKTEST_PATH = DATA_DIR / "nextfix_intraday_backtest.json"
INTRADAY_FILE = "macro_intraday.parquet"
BETAS = (0.0, 0.5, 1.0)
MAX_BAR_GAP = timedelta(hours=6)  # a reading older than this before t is no reading
SHADOW_KEEP_DAYS = 120


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat().replace("+00:00", "Z")


def load_bars(data_dir: Path = DATA_DIR) -> pd.Series:
    """G(bar end) = gold_usd x usd_inr for each 1-hour bar, indexed by bar END (UTC)."""
    path = data_dir / INTRADAY_FILE
    if not path.exists():
        return pd.Series(dtype=float)
    bars = pd.read_parquet(path)
    idx = pd.DatetimeIndex(pd.to_datetime(bars.index))
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    g = (bars["gold_usd"] * bars["usd_inr"]).astype(float)
    g.index = idx + pd.Timedelta(hours=1)
    return g.dropna().sort_index()


def value_at(g: pd.Series, t: datetime) -> tuple[float, datetime] | None:
    """Close of the last bar that ended at or before ``t`` (within MAX_BAR_GAP), and its end."""
    if g.empty:
        return None
    ts = pd.Timestamp(t)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    pos = int(g.index.searchsorted(ts, side="right")) - 1
    if pos < 0:
        return None
    end = pd.Timestamp(g.index[pos])
    if ts - end > MAX_BAR_GAP:
        return None
    return float(g.iloc[pos]), end.to_pydatetime()


def _window(kind: str, fix_date: pd.Timestamp, t: datetime) -> str:
    """Which ml.nextfix window ``t`` falls in, given the latest fix (kind, date) before it."""
    if kind == "am":
        return "after_morning_rate"
    us_close = nextfix._at(fix_date, nextfix.US_CLOSE_UTC)
    return "after_afternoon_rate" if t < us_close else "after_us_close"


def forecast_at(events: list, g: pd.Series, t: datetime) -> dict | None:
    """The shadow forecasts at ``t`` from the latest fix published by then, or None."""
    past = [e for e in events if e[0] <= t]
    if not past:
        return None
    t_b, kind, d, base = past[-1]
    at_fix, at_now = value_at(g, t_b), value_at(g, t)
    if at_fix is None or at_now is None:
        return None
    x = math.log(at_now[0] / at_fix[0])
    return {
        "base_kind": kind,
        "base_date": d.strftime("%Y-%m-%d"),
        "base_at": _iso(t_b),
        "base": round(base, 2),
        "window": _window(kind, d, t),
        "target_kind": "pm" if kind == "am" else "am",
        "g_fix": round(at_fix[0], 2),
        "g_fix_bar_end": _iso(at_fix[1]),
        "g_now": round(at_now[0], 2),
        "g_now_bar_end": _iso(at_now[1]),
        "x_since_fix": x,
        "hours_since_fix": round((t - t_b).total_seconds() / 3600.0, 2),
        "pred": {f"beta_{b}": round(base * math.exp(b * x), 2) for b in BETAS},
    }


def next_fix_after(events: list, base_at: datetime, kind: str) -> tuple | None:
    """The first fix of ``kind`` published after ``base_at`` (within MAX_GAP_DAYS)."""
    for e in events:
        if e[0] > base_at and e[1] == kind:
            if (e[0] - base_at) > timedelta(days=nextfix.MAX_GAP_DAYS + 1):
                return None
            return e
    return None


# ── shadow log ───────────────────────────────────────────────────────────────────────────────────


def load_shadow(path: Path = SHADOW_PATH) -> list[dict]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    return list(data.get("entries", [])) if isinstance(data, dict) else []


def save_shadow(entries: list[dict], path: Path = SHADOW_PATH, summary: dict | None = None) -> None:
    payload = {
        "schema_version": 1,
        "note": "Shadow only (ADR 066): hourly world-price forecasts of the next IBJA fix.",
        "summary": summary or {},
        "entries": entries,
    }
    path.write_text(json.dumps(payload, indent=1) + "\n")


def update_shadow(entries: list[dict], events: list, g: pd.Series, now: datetime) -> list[dict]:
    """Append this run's forecast, resolve entries whose next fix is out, drop very old ones."""
    out = [
        e for e in entries if e.get("logged_at", "") >= _iso(now - timedelta(days=SHADOW_KEEP_DAYS))
    ]
    fc = forecast_at(events, g, now)
    if fc is not None and not any(e.get("logged_at") == _iso(now) for e in out):
        out.append({"logged_at": _iso(now), **fc, "target": None, "target_date": None})
    for e in out:
        if e.get("target") is not None:
            continue
        base_at = datetime.fromisoformat(e["base_at"].replace("Z", "+00:00"))
        nx = next_fix_after(events, base_at, e["target_kind"])
        if nx is not None:
            e["target"] = round(nx[3], 2)
            e["target_date"] = nx[2].strftime("%Y-%m-%d")
    return out


# ── scoring ──────────────────────────────────────────────────────────────────────────────────────


def _cluster_ci(
    members: list[np.ndarray], stat, b: int | None = None, seed: int = 0
) -> list[float]:
    """95% bootstrap interval of ``stat(row indices)``, resampling whole base fixes (clusters).

    Decisions made within one fix window share most of their information, so resampling rows (or
    short blocks of rows) would overstate the evidence. Each draw picks base fixes with replacement
    and keeps all of a picked fix's rows."""
    rng = np.random.default_rng(seed)
    b = b or nextfix.BOOTSTRAP_B
    vals = []
    for _ in range(b):
        pick = rng.integers(0, len(members), size=len(members))
        vals.append(stat(np.concatenate([members[c] for c in pick])))
    lo, hi = np.nanpercentile(vals, [2.5, 97.5])
    return [float(lo), float(hi)]


def logged_after_target(r: dict) -> bool:
    """True when the decision was made at or after the target fix was published.

    Such an entry is not a forecast: the answer was already out. It happens when a run builds its
    events from IBJA rows fetched before the newest fix (2026-10-05 07:13Z: the AM fix, published
    06:30Z, was the target of an entry whose base was still the previous PM fix). Rows without a
    ``target_date`` (the every-hour backtest, which only keeps decisions made before the target) are
    never flagged. Scoring correctness fix (ADR 066 note, 2026-10-05); the pre-registered promotion
    rule is unchanged.
    """
    date, kind, t = r.get("target_date"), r.get("target_kind"), r.get("t")
    if not date or kind not in ("am", "pm") or not t:
        return False
    clock = nextfix.IBJA_AM_PUBLISH_UTC if kind == "am" else nextfix.IBJA_PM_PUBLISH_UTC
    published = nextfix._at(pd.Timestamp(date), clock)
    return datetime.fromisoformat(str(t).replace("Z", "+00:00")) >= published


def score(rows: list[dict], key: str = "base_at") -> dict:
    """Error of each beta vs holding (beta 0), per window.

    ``key="base_at"``: one row per base fix (its latest decision), so a day with many runs counts
    once. Any other ``key``: every decision counts (one row per ``key``), but the confidence
    interval and the Diebold-Mariano test still treat each base fix as ONE observation (cluster
    bootstrap; DM on per-fix mean errors). Reports MAE per beta, the change vs holding with its
    95% CI, and the direction hit rate of sign(x_since_fix) when the fix moved.
    """
    resolved = [r for r in rows if r.get("target") is not None]
    out: dict = {}
    for window in ("after_morning_rate", "after_afternoon_rate", "after_us_close"):
        last: dict = {}
        in_window = [r for r in resolved if r["window"] == window]
        n_late = sum(1 for r in in_window if logged_after_target(r))
        for r in sorted((r for r in in_window if not logged_after_target(r)), key=lambda r: r["t"]):
            last[r[key]] = r
        rs = list(last.values())
        fixes = sorted({r["base_at"] for r in rs})
        if len(fixes) < 5:
            out[window] = {
                "n_fixes": len(fixes),
                "n_decisions": len(rs),
                "n_excluded_logged_after_target": n_late,
                "ready": False,
            }
            continue
        pos = {f: i for i, f in enumerate(fixes)}
        cl = np.array([pos[r["base_at"]] for r in rs])
        members = [np.flatnonzero(cl == i) for i in range(len(fixes))]
        tgt = np.array([r["target"] for r in rs])
        errs = {b: np.abs(np.array([r["pred"][f"beta_{b}"] for r in rs]) - tgt) for b in BETAS}
        hold = errs[0.0]
        res: dict = {
            "n_fixes": len(fixes),
            "n_decisions": len(rs),
            "n_excluded_logged_after_target": n_late,
            "ready": True,
        }
        for b in BETAS:
            res[f"mae_beta_{b}"] = round(float(errs[b].mean()), 1)
        hold_by_fix = np.array([hold[m].mean() for m in members])
        for b in BETAS[1:]:
            e = errs[b]

            def _chg(ix: np.ndarray, e: np.ndarray = e, hold: np.ndarray = hold) -> float:
                return 100.0 * (e[ix].mean() / max(hold[ix].mean(), 1e-9) - 1.0)

            res[f"change_beta_{b}_pct"] = round(_chg(np.arange(len(rs))), 1)
            res[f"change_beta_{b}_ci95"] = [round(v, 1) for v in _cluster_ci(members, _chg)]
            e_by_fix = np.array([e[m].mean() for m in members])
            res[f"dm_p_beta_{b}"] = round(nextfix.diebold_mariano_p(e_by_fix, hold_by_fix), 4)
        moved = np.array([r["target"] != r["base"] for r in rs])
        hit = np.array([(r["x_since_fix"] > 0) == (r["target"] > r["base"]) for r in rs])
        nz = moved & np.array([r["x_since_fix"] != 0 for r in rs])
        res["direction_hit_rate"] = round(float(hit[nz].mean()), 3) if nz.any() else None
        res["direction_n"] = int(nz.sum())
        out[window] = res
    return out


def backtest(events: list, g: pd.Series) -> dict:
    """Replay every hour covered by the bars: forecast at each hour, score against the next fix."""
    if g.empty:
        return {"ready": False, "reason": "no_intraday_bars"}
    start = g.index.min().to_pydatetime() + timedelta(hours=1)
    end = g.index.max().to_pydatetime()
    rows = []
    t = start.replace(minute=15, second=0, microsecond=0)
    while t <= end:
        fc = forecast_at(events, g, t)
        if fc is not None:
            nx = next_fix_after(
                events,
                datetime.fromisoformat(fc["base_at"].replace("Z", "+00:00")),
                fc["target_kind"],
            )
            if nx is not None and nx[0] > t:
                rows.append({**fc, "t": _iso(t), "target": round(nx[3], 2)})
        t += timedelta(hours=1)
    by_hour = score(rows)
    for r in rows:  # also scored per hour-of-decision, to see where the edge is
        r["hour_bucket"] = r["base_at"] + "|" + r["t"]
    return {
        "ready": bool(rows),
        "bars_from": _iso(g.index.min().to_pydatetime()),
        "bars_to": _iso(end),
        "n_decisions": len(rows),
        "scored_on_last_decision_per_fix": by_hour,
        "scored_on_every_hour": score(rows, key="hour_bucket"),
    }


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────────


def run(now: datetime | None = None, data_dir: Path = DATA_DIR) -> dict:
    now = now or datetime.now(UTC)
    ibja_path = data_dir / nextfix.IBJA_PATH.name
    if not ibja_path.exists():
        return {"ready": False, "reason": "no_ibja"}
    events = nextfix.fix_events(nextfix.load_ibja_full(ibja_path))
    g = load_bars(data_dir)
    entries = update_shadow(load_shadow(data_dir / SHADOW_PATH.name), events, g, now)
    for e in entries:
        e.setdefault("t", e["logged_at"])
    summary = {"generated_at": _iso(now), "n_entries": len(entries), **score(entries)}
    save_shadow(entries, data_dir / SHADOW_PATH.name, summary)
    bt = {"generated_at": _iso(now), **backtest(events, g)}
    (data_dir / BACKTEST_PATH.name).write_text(json.dumps(bt, indent=1) + "\n")
    return {"shadow": summary, "backtest": bt}


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    out = run()
    print(json.dumps(out, indent=1, default=str)[:4000])


if __name__ == "__main__":
    main()
