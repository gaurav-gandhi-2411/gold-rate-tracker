"""scripts/gg_digest.py -- the plain-words daily digest GG gets while away.

It reads only aggregates the workflows already publish in the repo (data/*.json written by
check-price and weekly-backtest), never a raw or decrypted series, and never a price. The workflow
(daily-digest.yml) sends the text to the private ntfy topic as a file.

    python scripts/gg_digest.py daily --out "$RUNNER_TEMP/digest.txt" [--merged merged.json] [--now ISO]

Every line degrades to "unknown (<reason>)" instead of failing: a digest with a missing part is
still worth sending, and the missing part is itself the news.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
STALE_H = 8.0  # the page shows "not fresh" past 8 h (docs/TANISHQ_TIMED_VISITS.md)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe(fn: Callable[[], str]) -> str:
    try:
        return fn()
    except (OSError, ValueError, KeyError, TypeError) as err:
        return f"unknown ({type(err).__name__})"


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC)


def last_reading(root: Path) -> datetime:
    """Timestamp of the newest successful Tanishq reading (data/tanishq_scrape_outcomes.jsonl)."""
    last = None
    for line in (
        (root / "data" / "tanishq_scrape_outcomes.jsonl").read_text(encoding="utf-8").splitlines()
    ):
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("outcome") == "success":
            t = _parse(row["timestamp"])
            last = t if last is None or t > last else last
    if last is None:
        raise ValueError("no successful reading")
    return last


def freshness_line(root: Path, now: datetime) -> str:
    def build() -> str:
        age = (now - last_reading(root)).total_seconds() / 3600
        flag = "STALE" if age > STALE_H else "fresh"
        forecast_age = (
            now - _parse(_load(root / "data" / "forecast.json")["predicted_at"])
        ).total_seconds() / 3600
        return f"Data: {flag}, last Tanishq reading {age:.1f} h ago; forecast {forecast_age:.1f} h old."

    return _safe(build)


def scrape_line(root: Path) -> str:
    def build() -> str:
        d = _load(root / "data" / "tanishq_scrape_success_rate.json")
        return f"Scrapes (7 days): {d['n_success']} of {d['n']} succeeded."

    return _safe(build)


def live_line(root: Path) -> str:
    def build() -> str:
        live = _load(root / "data" / "model_status_weekly.json")["live"]
        n = int(live["n"])
        text = (
            f"P3 live record ({n} days, to {live['last_day']}): average miss Rs.{live['mae_model']:.0f} vs "
            f"Rs.{live['mae_hold']:.0f} for repeating the last rate; direction right "
            f"{round(live['direction_hit'] * live['direction_n'])} of {live['direction_n']}; range held "
            f"{round(live['coverage'] * live['coverage_n'])} of {live['coverage_n']} (target 80%)."
        )
        return text + (" Too few days to judge." if n < 20 else "")

    return _safe(build)


def alerts_line(root: Path) -> str:
    def build() -> str:
        dem = _load(root / "data" / "model_demotion_state.json")
        nf = _load(root / "data" / "forecast.json").get("next_fix") or {}
        champ = (nf.get("champion") or {}).get("id", "unknown")
        lm = (nf.get("demotion") or {}).get("live_monitor") or {}
        parts = [f"champion {champ}", "demoted" if dem.get("demoted") else "not demoted"]
        if lm.get("checked"):
            parts.append(
                f"live monitor {lm.get('n_live')} live days, error breach {'yes' if lm.get('error_breach') else 'no'}, "
                f"range alert {'yes' if lm.get('coverage_alert') else 'no'}"
            )
        else:
            parts.append("live monitor not checked")
        return "Models: " + "; ".join(parts) + "."

    return _safe(build)


def merged_line(merged: list[dict[str, Any]] | None) -> str:
    if merged is None:
        return "Merged today: unknown (no list)."
    human = [
        m for m in merged if not str(m.get("headRefName", "")).startswith(("bot/", "dependabot/"))
    ]
    if not human:
        return f"Merged today: no non-bot PRs ({len(merged)} bot PRs)."
    return (
        "Merged today: " + "; ".join(f"#{m['number']} {m['title'][:70]}" for m in human[:6]) + "."
    )


def parked_line(root: Path) -> str:
    def build() -> str:
        text = (root / "docs" / "FOR_GG.md").read_text(encoding="utf-8")
        section = text.split("## 2.", 1)[1] if "## 2." in text else ""
        rows = [ln for ln in section.splitlines() if ln.startswith("| ") and ln[2:3].isdigit()]
        return f"Parked for GG: {len(rows)} item(s) in docs/FOR_GG.md section 2."

    return _safe(build)


def daily(root: Path, now: datetime, merged: list[dict[str, Any]] | None) -> str:
    return "\n".join(
        [
            freshness_line(root, now),
            scrape_line(root),
            live_line(root),
            alerts_line(root),
            merged_line(merged),
            parked_line(root),
        ]
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="GG's daily digest text")
    ap.add_argument("mode", choices=("daily",))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--merged", type=Path, help="JSON list of PRs merged today (gh pr list --json)")
    ap.add_argument("--now", help="ISO time instead of the clock (tests)")
    ap.add_argument("--root", type=Path, default=ROOT)
    args = ap.parse_args(argv)
    now = _parse(args.now) if args.now else datetime.now(UTC)
    merged = None
    if args.merged is not None:
        try:
            merged = _load(args.merged)
        except (OSError, ValueError):
            merged = None
    text = daily(args.root, now, merged)
    args.out.write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)  # aggregates only (counts, ages, averages already public in docs/MODEL_STATUS.md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
