"""scripts/run_preregistered_h2_shadow.py — weekly shadow scoring for the
GG spec item 4 pre-registration: v2 (ADR 042) since 2026-09-24; v1 (ADR 038)
superseded. Wired into weekly-backtest.yml
as an additive, continue-on-error step; appends both arms' results to
data/preregistered_h2_shadow_results.json (append-only audit log).

SHADOW ONLY: never writes to data/direction_baseline.json, never touches
ml.direction.gate. Exits 0 even on a per-arm failure (e.g. a transient
yfinance outage for the proxy arm's india_vix fetch) so this never blocks
the weekly backtest commit -- failures are printed, not swallowed silently.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Run as `python scripts/<name>.py` from the repo root (weekly-backtest.yml does):
# sys.path[0] is then scripts/, so the repo root must be added for `import ml`.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.direction.preregistration import (
    PREREGISTERED_N_FOR_POWER,
    append_shadow_result,
    run_live_arm,
    run_proxy_arm,
)


def _fmt(value: float | None, spec: str) -> str:
    """None (fewer than 2 scored days) prints as "n/a" instead of crashing."""
    return "n/a" if value is None else format(value, spec)


def main() -> int:
    for arm_name, run_arm in (("live_h2", run_live_arm), ("proxy_deadzone", run_proxy_arm)):
        try:
            result = run_arm()
        except Exception as exc:  # shadow-only, never block the weekly commit
            print(f"{arm_name}: FAILED ({exc!r}) -- skipping this run, not fatal")
            continue
        append_shadow_result(result)
        if arm_name == "live_h2":
            reached = (result.get("effective_n") or 0.0) >= PREREGISTERED_N_FOR_POWER
            dates = result.get("scored_as_of_dates") or []
            span = f"{dates[0]}..{dates[-1]}" if dates else "none yet"
            print(
                f"{arm_name} [{result.get('protocol_version')}]: n={result['n']} "
                f"(post-{result.get('confirmatory_after_as_of')} days only, scored {span}, "
                f"embargo on {result.get('embargo_label_date_col')}, "
                f"consecutive-day labels={result.get('consecutive_day_labels')}) "
                f"effective_n={_fmt(result['effective_n'], '.2f')} "
                f"p={_fmt(result['p_value'], '.5f')} significant={result['significant_at_05']} "
                f"reached_preregistered_n={reached} (target={PREREGISTERED_N_FOR_POWER}, "
                "h2-specific -- see ADR 042)"
            )
        else:
            # Proxy arm has a different horizon/feature set (ADR 038's caveat) --
            # its own n_for_power target is a separate question, not computed here;
            # logged for trend visibility only, never compared to the h2 threshold.
            print(
                f"{arm_name}: n={result['n']} effective_n={_fmt(result['effective_n'], '.2f')} "
                f"p={_fmt(result['p_value'], '.5f')} significant={result['significant_at_05']} "
                "(h1-equivalent framing -- not comparable to the h2 pre-registered target)"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
