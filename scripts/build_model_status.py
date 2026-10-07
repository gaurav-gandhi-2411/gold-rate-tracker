"""scripts/build_model_status.py -- the one-screen weekly note for GG (docs/MODEL_STATUS.md).

Computes, from the committed per-day records only, how the live model is doing on real (forward)
days, every pending challenger comparison under the frozen ADR 072 rule, the forward days still
needed and the calendar date a decision first becomes possible, and anything that fired (demotion,
promotion). Writes:
  * data/model_status_weekly.json   machine-readable (data/ so the bot-pr-sync allow-list is unchanged);
  * docs/MODEL_STATUS.md            plain language, rendered ONLY from that JSON.

Every number is computed here from data/nextfix_p3_oos.json, data/nextfix_oos.json and
data/nextfix_p3_variants_oos.json (VERIFIED). A model with no forward days shows "no live days yet",
never a figure. Backtest numbers are never shown here.

Usage:
    python scripts/build_model_status.py                 # compute -> JSON + markdown
    python scripts/build_model_status.py --json-only     # weekly job
    python scripts/build_model_status.py --render-only   # markdown from the committed JSON
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATA = ROOT / "data"
OUT_JSON = DATA / "model_status_weekly.json"
OUT_MD = ROOT / "docs" / "MODEL_STATUS.md"

NAMES = {
    "p3": "Live model (world gold move x a fitted pass-through)",
    "ensemble": "Earlier model (ridge + small neural nets), now running in the background",
    "p3_roll60": "Live model re-fitted on the last 60 days only",
    "p3_monday": "Live model with its own Monday setting",
}
# Challengers that exist in the plan but have no day-by-day record in the same format yet.
NOT_YET = {
    "hourly": "Hourly world-price model (own check on 2026-10-16, then it joins)",
    "p3_hourly": "Live model averaged with the hourly model (joins after the hourly model does)",
}


def _load(name: str) -> Any:
    p = DATA / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _records() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    p3 = _load("nextfix_p3_oos.json")
    if p3:
        out["p3"] = p3["folds"]
    ens = _load("nextfix_oos.json")
    if ens:
        out["ensemble"] = ens["folds"]
    var = _load("nextfix_p3_variants_oos.json")
    if var:
        out.update({k: v for k, v in var.get("variants", {}).items()})
    return out


def _live_summary(folds: list[dict], since: str) -> dict[str, Any]:
    from ml import promotion as pr

    live = sorted(pr.forward_days(folds, since).values(), key=lambda f: f["d0"])
    n = len(live)
    out: dict[str, Any] = {"n": n, "since": since}
    if n == 0:
        return out
    model = [abs(f["pm1"] - f["pm0"] * math.exp(f["ret"])) for f in live]
    hold = [abs(f["pm1"] - f["pm0"]) for f in live]
    moved = [f for f in live if f["pm1"] != f["pm0"]]
    hits = pr.range_hits(folds)
    cov = [hits[f["d0"]] for f in live if f["d0"] in hits]
    out.update(
        {
            "mae_model": round(sum(model) / n, 1),
            "mae_hold": round(sum(hold) / n, 1),
            "change_pct": round(100 * (sum(model) / sum(hold) - 1), 1) if sum(hold) else None,
            "direction_hit": round(
                sum((f["p_up"] > 0.5) == (f["pm1"] > f["pm0"]) for f in moved) / len(moved), 3
            )
            if moved
            else None,
            "direction_n": len(moved),
            "coverage": round(sum(cov) / len(cov), 3) if cov else None,
            "coverage_n": len(cov),
            "last_day": live[-1]["d0"],
        }
    )
    return out


def compute(now: datetime | None = None) -> dict[str, Any]:
    from ml import promotion as pr

    now = now or datetime.now(UTC)
    recs = _records()
    champ_state = pr.load_champion(DATA / pr.CHAMPION_FILE)
    champ = champ_state["champion"]
    out: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": now.isoformat(),
        "rule_sha256": pr.rule_sha256(),
        "common_start": pr.RULE["common_start"],
        "champion": champ,
        "champion_state_unreadable": bool(champ_state.get("unreadable")),
        "champion_history": champ_state["history"],
    }
    out["live"] = _live_summary(recs[champ], pr.RULE["common_start"]) if champ in recs else {"n": 0}
    if champ in recs:
        dec = pr.decide(champ, recs)
        rows = {}
        first = out["live"].get("last_day") or pr.RULE["common_start"]
        for cid, r in dec["challengers"].items():
            per_day = 5 / 7
            n_have = r["n"]
            r["first_reachable"] = pr.first_reachable(n_have, r.get("n_required"), first, per_day)
            r["promotable"] = bool(r.get("promotable"))
            rows[cid] = r
        out["challengers"] = rows
        out["promote"] = dec["promote"]
    out["not_yet"] = NOT_YET
    out["timeliness"] = _load("input_timeliness_weekly.json")
    dem = _load("model_demotion_state.json")
    out["demotion"] = (
        {k: dem.get(k) for k in ("demoted", "since", "reasons", "last_checked")}
        if isinstance(dem, dict)
        else None
    )
    return out


# --- rendering (plain language, numbers only from the JSON) --------------------------------------
def _money(x: float | None) -> str:
    return "n/a" if x is None else f"Rs.{x:,.0f}"


def _timeliness_lines(t: dict[str, Any] | None) -> list[str]:
    """Plain sentences from data/input_timeliness_weekly.json; nothing is guessed when it is absent."""
    if not isinstance(t, dict):
        return ["No timeliness report has been produced yet."]
    out: list[str] = []
    sl = t.get("tanishq_slots") or {}
    if sl.get("slots"):
        out.append(
            f"- Jeweller price visits: **{sl['served']} of {sl['slots']}** scheduled visits since "
            f"the schedule began (2026-10-05) ran within {sl['window_minutes']} minutes of "
            f"their time ({sl['missed']} did not; the report cannot tell a laptop that was "
            "off from a failed visit)."
        )
    else:
        out.append("- Jeweller price visits: not measured this week.")
    ow = t.get("overnight_window") or {}
    if ow.get("available") and ow.get("nights"):
        med = ow.get("median_minutes_after_us_close")
        out.append(
            f"- Overnight model forecast: published on **{ow['nights_with_model_forecast']} of "
            f"{ow['nights']}** nights"
            + (f", typically {med} minutes after the US gold close." if med is not None else ".")
        )
    else:
        out.append("- Overnight model forecast: not measured this week.")
    return out


def render(s: dict[str, Any]) -> str:
    live = s.get("live", {})
    lines = [
        "# Model status",
        "",
        f"Updated {s['generated_at'][:10]}. Computed from real forecast days only; "
        "no backtest figure appears here. Rule: ADR 072 (frozen, hash "
        f"`{s['rule_sha256'][:12]}`).",
        "",
        "## What is live and how it is doing",
        "",
    ]
    n = live.get("n", 0)
    if n == 0:
        lines.append(
            f"The live model has no scored real days yet (counting from {s['common_start']}). "
            "Nothing is claimed until it has some."
        )
    else:
        lines += [
            f"Real days scored since {live['since']}: **{n}** (latest {live['last_day']}).",
            "",
            f"- Average miss on the next official rate: **{_money(live['mae_model'])}** per gram "
            f"vs **{_money(live['mae_hold'])}** if we had just repeated the last rate "
            f"({live['change_pct']:+.1f}%).",
        ]
        if live.get("direction_hit") is not None:
            lines.append(
                f"- Right about up or down on **{live['direction_hit'] * 100:.0f}%** of "
                f"{live['direction_n']} days the rate moved."
            )
        if live.get("coverage") is not None:
            lines.append(
                f"- The stated range held the actual rate on **{live['coverage'] * 100:.0f}%** of "
                f"{live['coverage_n']} days (target 80%)."
            )
        if n < 20:
            lines += ["", f"Only {n} days so far: too few to say whether this is good or bad."]
    lines += ["", "## What is being tested", ""]
    rows = s.get("challengers", {})
    if rows:
        lines += [
            "| Challenger | Real days | Days needed | Earliest decision | Error vs live | Verdict |",
            "|---|---|---|---|---|---|",
        ]
        for cid, r in rows.items():
            need = r.get("n_required")
            if r["status"] != "scored":
                gain, verdict = "n/a", "too early"
            else:
                gain = f"{r['gain'] * 100:+.1f}%"
                verdict = (
                    "qualifies"
                    if r["promotable"]
                    else ("not yet" if need and r["n"] < need else "no")
                )
            lines.append(
                f"| {NAMES.get(cid, cid)} | {r['n']} | {need if need else 'not yet known'} | "
                f"{r.get('first_reachable') or 'not yet known'} | {gain} | {verdict} |"
            )
        for text in s.get("not_yet", {}).values():
            lines.append(f"| {text} | 0 | not yet known | not yet known | n/a | not started |")
        lines += [
            "",
            "A challenger replaces the live model only if it beats it by at least 5%, is "
            "statistically clear after allowing for several challengers, keeps its range at "
            "target and is not worse on up/down. Days needed is sized from how noisy the daily "
            "difference actually is. No conclusion before the earliest decision date.",
        ]
    else:
        lines.append("No challenger has real days yet.")
    lines += ["", "## Were the inputs on time?", ""]
    lines += _timeliness_lines(s.get("timeliness"))
    lines += ["", "## Dates", ""]
    lines += [
        "- 2026-10-16: hourly world-price check (ADR 066), exactly as written in advance.",
        "- 2026-10-22: morning-rate vs last-reading decision for the weekend estimate (ADR 063).",
    ]
    lines += ["", "## Anything that fired", ""]
    dem = s.get("demotion")
    if dem is None:
        lines.append("- Automatic fallback: no state file read.")
    elif dem.get("demoted"):
        lines.append(
            f"- **The live model is switched off** (fallback to repeating the last rate) since {dem['since']}."
        )
    else:
        lines.append(
            f"- Automatic fallback has not triggered (last checked {str(dem.get('last_checked'))[:10]})."
        )
    hist = s.get("champion_history", [])
    lines.append(
        f"- Live model changes: {len(hist)} (current: {s['champion']})."
        if hist
        else "- Live model changes: none."
    )
    if s.get("champion_state_unreadable"):
        lines.append("- **The live-model file could not be read; the safe default is in use.**")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json-only", action="store_true")
    ap.add_argument("--render-only", action="store_true")
    args = ap.parse_args()
    if args.render_only:
        s = json.loads(OUT_JSON.read_text(encoding="utf-8"))
    else:
        s = compute()
        text = json.dumps(s, indent=1, default=float) + "\n"
        OUT_JSON.write_text(text, encoding="utf-8", newline="\n")  # LF on Windows too
    if not args.json_only:
        OUT_MD.write_text(render(s), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
