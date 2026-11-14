"""Record in the ops log when the live next-fix model is down (report-only, always exits 0).

2026-11-12: the live model failed on every check-price run for about 32 h and nothing said so,
because `Run inference` is `continue-on-error` and a failed next_fix block only writes
``next_fix = {"active": false, "reason": ...}`` into data/forecast.json while the headline falls
back to the plain hold. This reads the forecast the run just wrote and records one ops-log entry
(no push) when ``next_fix.active`` is not true or the file cannot be read.

    python scripts/check_next_fix_active.py [--forecast data/forecast.json]

It never fails the run (the data sync must not depend on it) and never prints prices.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = "next-fix"
TITLE = "live model next_fix is down"


def verdict(forecast_path: Path) -> tuple[bool, str]:
    """(ok, reason). ok is True only when next_fix.active is the boolean true."""
    try:
        doc = json.loads(forecast_path.read_text(encoding="utf-8"))
        block = doc["next_fix"]
        active = block["active"]
    except (OSError, ValueError, KeyError, TypeError) as err:
        return False, f"forecast.json unreadable or without next_fix.active ({type(err).__name__})"
    if active is True:
        return True, "active"
    raw = block.get("reason") if isinstance(block, dict) else None
    reason = str(raw)[:120] if raw else "no reason recorded"
    return False, f"next_fix.active={active!r}, reason: {reason}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--forecast", type=Path, default=ROOT / "data" / "forecast.json")
    args = ap.parse_args(argv)
    ok, reason = verdict(args.forecast)
    print(f"next-fix health: {'OK' if ok else 'DOWN'} -- {reason}")
    if not ok:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import ops_log

            ops_log.record(
                SOURCE,
                TITLE,
                f"{reason}. The headline is the plain hold until it recovers; the check-price log "
                "has the traceback (`next_fix: failed`). See docs/CLOSEOUT_PLAN.md, incident runbook.",
            )
        except Exception as err:  # the verdict above must not depend on logging
            print(f"ops_log: warning: {err!s:.120}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
