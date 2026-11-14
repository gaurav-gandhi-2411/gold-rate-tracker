"""Price-history integrity check for the site's 22K estimate series (GG P0, 2026-11-05).

The live trend chart once showed one-day "jumps" of Rs.1,500-2,000 that never happened: sampled
history with month-long gaps, drawn on a slot-per-row axis. This check catches the data-side
shapes of that failure before they are published:

  (i)   MOVES: a move above MOVE_THRESHOLD_PCT between adjacent IBJA days (<= 4 calendar days
        apart) must be listed in data/price_move_explanations.json with a cause and evidence
        (a duty change, a market move confirmed by COMEX). Anything else is a splice or a basis
        error. The 3.0% threshold comes from real daily IBJA history: 109 adjacent-day pairs
        2026-06-01..2026-11-05 had |move| p50 0.78%, p95 2.19%, p99 2.93%, max 3.53%.
  (ii)  REFERENCE: a deterministic sample of rows is compared with an independent landed price,
        COMEX GC=F (previous US close) / 31.1035 x USD/INR x (1 + CBIC duty) x 0.916. A ratio
        outside 1 +/- REFERENCE_TOLERANCE flags the row. Needs the network (yfinance); if the
        reference cannot be fetched the check says "could not verify", never "pass".
  (iii) GAPS: the published chart file (data/ibja_derived_prices.json) must not contain a gap
        longer than MAX_PUBLISHED_GAP_DAYS (scripts/build_ibja_derived_prices.PUBLIC_CHART_FROM).

BLOCKING since the flip PR (2026-11-14; it started report-only per .claude/CLAUDE.md, 2026-10-30,
and had 39 clean real runs of 40 check-price runs 2026-11-07..13). With --strict it exits 1 only
on a finding that means the PUBLISHED series is wrong: a gap in the chart file (iii) or a basis
error against the reference (ii), the latter only when at least MIN_BLOCKING_REFERENCE_FLAGS (3)
sampled rows are outside the tolerance AND at least half the sample had a reference (one bad
yfinance tick or a truncated response must not stop the sync). It deliberately does NOT block on (i) an unexplained move (a real
>3% market day would then stop the site's data until someone listed it; four such days happened
since 2025) or on "could not verify" (the reference needs the network). Those are printed, written
to the job summary and recorded in the ops log. Without --strict it always exits 0.

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
MIN_BLOCKING_REFERENCE_FLAGS = 3  # --strict blocks on (ii) only from this many rows outside
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


def valid_rows(rows: object) -> bool:
    """A list of >= 2 rows, each with an ISO timestamp and a positive numeric 22k. Pure."""
    return (
        isinstance(rows, list)
        and len(rows) >= 2
        and all(
            isinstance(r, dict)
            and isinstance(r.get("timestamp"), str)
            and len(r["timestamp"]) >= 10
            and isinstance(r.get("22k"), (int, float))
            and r["22k"] > 0
            for r in rows
        )
    )


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
    ap.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 on a gap in the chart file or a reference basis error (not on moves or "
        "'could not verify'; see the module docstring)",
    )
    args = ap.parse_args(argv)
    d = args.data_dir
    lines: list[str] = []
    findings = 0
    blocking = 0  # findings that mean the published series is wrong: gaps (iii), basis errors (ii)
    unverified = 0

    # Every input read below fails closed: an unreadable or malformed file is "COULD NOT VERIFY"
    # for the parts that need it, never "OK" and never a traceback (verifier, #3595).
    def could_not(part: str, err: object) -> None:
        nonlocal unverified
        lines.append(f"{part}: COULD NOT VERIFY -- {err!s:.160}")
        unverified += 1

    full: list[dict] | None = None
    try:
        explanations = json.loads((d / "price_move_explanations.json").read_text(encoding="utf-8"))[
            "moves"
        ]
        import pandas as pd

        build = _load_builder()
        calibration = json.loads((d / "calibration.json").read_text(encoding="utf-8"))
        full = build.build_derived_prices(pd.read_parquet(d / "ibja_rates.parquet"), calibration)
        if not valid_rows(full):
            raise ValueError(f"built history has {len(full)} usable row(s)")
    except Exception as err:  # any unreadable input -> could not verify, never pass
        could_not("(i) moves", f"{type(err).__name__}: {err}")
        full = None
    if full is not None:
        bad = unexplained_moves(full, explanations)
        findings += len(bad)
        lines.append(
            f"(i) moves: {len(full)} rows, threshold {MOVE_THRESHOLD_PCT}% on adjacent days -- "
            + ("OK" if not bad else f"{len(bad)} UNEXPLAINED: {bad}")
        )

    published: list[dict] | None = None
    try:
        published = json.loads((d / "ibja_derived_prices.json").read_text(encoding="utf-8"))
        if not valid_rows(published):
            raise ValueError("published chart file is not a list of >= 2 {timestamp, 22k} rows")
    except Exception as err:
        could_not("(iii) published gaps", f"{type(err).__name__}: {err}")
        published = None
    if published is not None:
        gaps = published_gaps(published)
        findings += len(gaps)
        blocking += len(gaps)
        lines.append(
            f"(iii) published gaps: {len(published)} rows, max allowed {MAX_PUBLISHED_GAP_DAYS} d"
            " -- " + ("OK" if not gaps else f"{len(gaps)} GAP(S): {gaps}")
        )

    if args.reference:
        series, which = (full, "full history") if full is not None else (published, "")
        if series is None:
            could_not("(ii) reference", "no readable series to sample")
        else:
            if not which:
                which = "PUBLISHED FILE ONLY (full history unreadable, so recent rows only)"
            sample = sample_rows(series)
            try:
                gold, fx = fetch_reference(_day(sample[0]))
                duty_rows = json.loads((d / "duty_cbic.json").read_text(encoding="utf-8"))["rows"]
                flags, n = reference_flags(sample, gold, fx, duty_rows)
            except Exception as err:  # any fetch/parse failure is "could not verify", never pass
                could_not("(ii) reference", f"{type(err).__name__}: {err}")
            else:
                findings += len(flags)
                # A basis error (24K vs 22K, per-10g vs per-gram) shifts MANY rows; one bad
                # yfinance tick flags one. Block only on a systematic pattern with real coverage.
                if n >= len(sample) // 2 and len(flags) >= MIN_BLOCKING_REFERENCE_FLAGS:
                    blocking += len(flags)
                if n < len(sample) // 2:
                    could_not("(ii) reference", f"only {n}/{len(sample)} rows had a reference")
                else:
                    lines.append(
                        f"(ii) reference: {n} sampled rows of the {which} vs COMEX x USD/INR x"
                        f" duty x 0.916, tolerance {REFERENCE_TOLERANCE:.0%} -- "
                        + ("OK" if not flags else f"{len(flags)} OUTSIDE: {flags}")
                    )
                if flags and n < len(sample) // 2:
                    lines.append(f"(ii) reference: {len(flags)} OUTSIDE: {flags}")

    verdict = "FINDINGS" if findings else ("COULD NOT VERIFY" if unverified else "OK")
    mode = "blocking on gaps and basis errors" if args.strict else "report-only"
    report = [f"price-history integrity ({mode}): {verdict}", *lines]
    print("\n".join(report))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"### Price-history integrity ({mode})\n\n")
            fh.write("\n".join(f"- {x}" for x in report) + "\n")
    if args.strict and (findings or unverified) and not blocking:
        # not blocking, but someone should look: the ops log carries it (never the phone)
        _tell_ops(f"{verdict}: " + " | ".join(x for x in lines if "-- OK" not in x))
    return 1 if args.strict and blocking else 0


def _tell_ops(body: str) -> None:
    """Best effort: one ops-log entry; never raises, never changes the exit code."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import ops_log

        ops_log.record("price-history-integrity", "price history needs a look", body[:480])
    except Exception as err:  # the check's verdict must not depend on logging
        print(f"ops_log: warning: {err!s:.120}")


if __name__ == "__main__":
    sys.exit(main())
