"""Price-history integrity check for the site's 22K estimate series (GG P0, 2026-11-05).

The live trend chart once showed one-day "jumps" of Rs.1,500-2,000 that never happened: sampled
history with month-long gaps, drawn on a slot-per-row axis. This check catches the data-side
shapes of that failure before they are published:

  (i)   MOVES: a move above MOVE_THRESHOLD_PCT between adjacent IBJA days (<= 4 calendar days
        apart) must be listed in data/price_move_explanations.json with a cause and evidence
        (a duty change, a market move confirmed by COMEX). Anything else is a splice or a basis
        error.
  (ii)  REFERENCE: a deterministic sample of rows is compared with an independent landed price,
        COMEX GC=F (previous US close) / 31.1035 x USD/INR x (1 + CBIC duty) x 0.916. A ratio
        outside 1 +/- REFERENCE_TOLERANCE flags the row. Needs the network (yfinance); if the
        reference cannot be fetched the check says "could not verify", never "pass".
  (iii) GAPS: the published chart file (data/ibja_derived_prices.json) must not contain a gap
        longer than MAX_PUBLISHED_GAP_DAYS (scripts/build_ibja_derived_prices.PUBLIC_CHART_FROM).

REPORT-ONLY (.claude/CLAUDE.md, 2026-10-30: a new check that can stop a production run starts
non-blocking). It prints, writes the job summary and exits 0. --strict (exit 1 on a finding or
on "could not verify") is for the later blocking PR, after 5 clean real runs over >= 24 h.

    python scripts/check_price_history_integrity.py [--data-dir data] [--reference] [--strict]
"""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import math
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Derived from real daily IBJA history (2026-06-01..2026-11-05, 109 adjacent-day pairs, measured
# 2026-11-05): |move| p50 0.78%, p95 2.19%, p99 2.93%, max 3.53%. 3.0% sits at the p99, so a
# normal day never trips it and the four real >3% days since 2025 are each on the explained list.
MOVE_THRESHOLD_PCT = 3.0
ADJACENT_MAX_DAYS = 4  # IBJA's longest normal break: a weekend plus a holiday
# Across all 287 rows (2022-2026) the landed-reference ratio was 0.9685..1.0413 (mean 1.003-1.012
# by year, sd ~1.1%); most of the spread is the reference's timing (previous US close vs IBJA's
# 17:00 IST fix). 6% leaves room for that and still catches a 10% basis error (e.g. 24K vs 22K,
# 9.2%) or a per-10 g / per-gram mix-up.
REFERENCE_TOLERANCE = 0.06
REFERENCE_SAMPLE = 40
MAX_PUBLISHED_GAP_DAYS = 7
TROY_OZ_G = 31.1035
PURITY_22K = 0.916


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "build_ibja_derived_prices", ROOT / "scripts" / "build_ibja_derived_prices.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _day(row: dict) -> date:
    return date.fromisoformat(row["timestamp"][:10])


def unexplained_moves(
    rows: list[dict], explanations: list[dict], threshold: float = MOVE_THRESHOLD_PCT
) -> list[dict]:
    """Adjacent-day moves above threshold with no matching explanation (same date, same sign,
    within 0.5 pp of the listed move). Pure."""
    listed = {e["date"]: float(e["move_pct"]) for e in explanations}
    out = []
    for prev, cur in itertools.pairwise(rows):
        gap = (_day(cur) - _day(prev)).days
        if gap > ADJACENT_MAX_DAYS or not prev["22k"]:
            continue
        pct = (cur["22k"] / prev["22k"] - 1.0) * 100.0
        if abs(pct) <= threshold:
            continue
        day = cur["timestamp"][:10]
        known = listed.get(day)
        if (
            known is not None
            and math.copysign(1, known) == math.copysign(1, pct)
            and abs(known - pct) <= 0.5
        ):
            continue
        out.append({"date": day, "prev_date": prev["timestamp"][:10], "move_pct": round(pct, 2)})
    return out


def published_gaps(rows: list[dict], max_days: int = MAX_PUBLISHED_GAP_DAYS) -> list[dict]:
    """Gaps longer than max_days between consecutive rows of the published chart file. Pure."""
    return [
        {"from": a["timestamp"][:10], "to": b["timestamp"][:10], "days": (_day(b) - _day(a)).days}
        for a, b in itertools.pairwise(rows)
        if (_day(b) - _day(a)).days > max_days
    ]


def sample_rows(rows: list[dict], n: int = REFERENCE_SAMPLE) -> list[dict]:
    """Deterministic sample across the whole history: every k-th row plus the last 10. Pure."""
    if len(rows) <= n:
        return list(rows)
    step = max(1, (len(rows) - 10) // (n - 10))
    picked = rows[: len(rows) - 10 : step] + rows[-10:]
    seen: set[str] = set()
    return [r for r in picked if not (r["timestamp"] in seen or seen.add(r["timestamp"]))]


def duty_on(day: date, duty_rows: list[dict]) -> float:
    pct = 0.0
    for r in sorted(duty_rows, key=lambda r: r["effective_date"]):
        if date.fromisoformat(r["effective_date"]) <= day:
            pct = float(r["total_duty_pct"])
    return pct


def reference_flags(
    rows: list[dict], gold_usd: dict[date, float], usd_inr: dict[date, float], duty_rows: list[dict]
) -> tuple[list[dict], int]:
    """(rows whose 22K is outside 1 +/- REFERENCE_TOLERANCE of the landed price, n compared).
    gold_usd/usd_inr map trading day -> close. Gold uses the last US close BEFORE the IBJA day
    (the 17:00 IST fix precedes that day's US close); USD/INR the last close on or before. Pure."""
    g_days = sorted(gold_usd)
    f_days = sorted(usd_inr)
    flags, n = [], 0
    for r in rows:
        d = _day(r)
        g = [x for x in g_days if x < d]
        f = [x for x in f_days if x <= d]
        if not g or not f or (d - g[-1]).days > 7:
            continue
        landed = (
            gold_usd[g[-1]] / TROY_OZ_G * usd_inr[f[-1]] * (1 + duty_on(d, duty_rows) / 100.0)
        ) * PURITY_22K
        ratio = r["22k"] / landed
        n += 1
        if abs(ratio - 1.0) > REFERENCE_TOLERANCE:
            flags.append(
                {
                    "date": d.isoformat(),
                    "22k": r["22k"],
                    "landed": round(landed),
                    "ratio": round(ratio, 4),
                }
            )
    return flags, n


def fetch_reference(start: date) -> tuple[dict[date, float], dict[date, float]]:
    import yfinance as yf  # network; only with --reference

    def closes(ticker: str) -> dict[date, float]:
        df = yf.download(
            ticker,
            start=(start - timedelta(days=10)).isoformat(),
            progress=False,
            auto_adjust=False,
        )
        s = df["Close"].squeeze()
        return {ts.date(): float(v) for ts, v in s.items() if math.isfinite(float(v))}

    return closes("GC=F"), closes("INR=X")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    ap.add_argument("--reference", action="store_true", help="also run (ii), needs the network")
    ap.add_argument("--strict", action="store_true", help="exit 1 on any finding (not yet used)")
    args = ap.parse_args(argv)
    d = args.data_dir
    lines: list[str] = []
    findings = 0
    unverified = 0

    explanations = json.loads((d / "price_move_explanations.json").read_text(encoding="utf-8"))[
        "moves"
    ]
    full: list[dict] | None = None
    try:
        import pandas as pd

        build = _load_builder()
        calibration = json.loads((d / "calibration.json").read_text(encoding="utf-8"))
        full = build.build_derived_prices(pd.read_parquet(d / "ibja_rates.parquet"), calibration)
    except (OSError, ValueError, KeyError, ImportError) as err:
        lines.append(f"(i) moves: COULD NOT VERIFY -- full IBJA history unreadable ({err!r:.120})")
        unverified += 1
    if full is not None:
        bad = unexplained_moves(full, explanations)
        findings += len(bad)
        lines.append(
            f"(i) moves: {len(full)} rows, threshold {MOVE_THRESHOLD_PCT}% on adjacent days -- "
            + ("OK" if not bad else f"{len(bad)} UNEXPLAINED: {bad}")
        )

    published = json.loads((d / "ibja_derived_prices.json").read_text(encoding="utf-8"))
    gaps = published_gaps(published)
    findings += len(gaps)
    lines.append(
        f"(iii) published gaps: {len(published)} rows, max allowed {MAX_PUBLISHED_GAP_DAYS} d -- "
        + ("OK" if not gaps else f"{len(gaps)} GAP(S): {gaps}")
    )

    if args.reference:
        series = full if full is not None else published
        sample = sample_rows(series)
        try:
            gold, fx = fetch_reference(_day(sample[0]))
            duty_rows = json.loads((d / "duty_cbic.json").read_text(encoding="utf-8"))["rows"]
            flags, n = reference_flags(sample, gold, fx, duty_rows)
        except Exception as err:  # any fetch/parse failure is "could not verify", never "pass"
            lines.append(f"(ii) reference: COULD NOT VERIFY ({type(err).__name__}: {err!s:.120})")
            unverified += 1
        else:
            if n < len(sample) // 2:
                lines.append(f"(ii) reference: COULD NOT VERIFY -- only {n}/{len(sample)} compared")
                unverified += 1
            findings += len(flags)
            lines.append(
                f"(ii) reference: {n} sampled rows vs COMEX x USD/INR x duty x 0.916, "
                f"tolerance {REFERENCE_TOLERANCE:.0%} -- "
                + ("OK" if not flags else f"{len(flags)} OUTSIDE: {flags}")
            )

    verdict = "FINDINGS" if findings else ("COULD NOT VERIFY" if unverified else "OK")
    report = [f"price-history integrity (report-only): {verdict}", *lines]
    print("\n".join(report))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("### Price-history integrity (report-only)\n\n")
            fh.write("\n".join(f"- {x}" for x in report) + "\n")
    if args.strict and (findings or unverified):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
