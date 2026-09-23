"""scripts/run_preregistered_h2_shadow.py — weekly shadow scoring for the
GG spec item 4 / ADR 038 pre-registration. Wired into weekly-backtest.yml
as an additive, continue-on-error step; appends both arms' results to
data/preregistered_h2_shadow_results.json (append-only audit log).

SHADOW ONLY: never writes to data/direction_baseline.json, never touches
ml.direction.gate. Exits 0 even on a per-arm failure (e.g. a transient
yfinance outage for the proxy arm's india_vix fetch) so this never blocks
the weekly backtest commit -- failures are printed, not swallowed silently.
"""

from __future__ import annotations

from ml.direction.preregistration import (
    PREREGISTERED_N_FOR_POWER,
    append_shadow_result,
    run_live_arm,
    run_proxy_arm,
)


def main() -> int:
    for arm_name, run_arm in (("live_h2", run_live_arm), ("proxy_deadzone", run_proxy_arm)):
        try:
            result = run_arm()
        except Exception as exc:  # shadow-only, never block the weekly commit
            print(f"{arm_name}: FAILED ({exc!r}) -- skipping this run, not fatal")
            continue
        append_shadow_result(result)
        if arm_name == "live_h2":
            reached = result.get("effective_n", 0) >= PREREGISTERED_N_FOR_POWER
            print(
                f"{arm_name}: n={result['n']} effective_n={result['effective_n']:.2f} "
                f"p={result['p_value']:.5f} significant={result['significant_at_05']} "
                f"reached_preregistered_n={reached} (target={PREREGISTERED_N_FOR_POWER}, "
                "h2-specific -- see ADR 038)"
            )
        else:
            # Proxy arm has a different horizon/feature set (ADR 038's caveat) --
            # its own n_for_power target is a separate question, not computed here;
            # logged for trend visibility only, never compared to the h2 threshold.
            print(
                f"{arm_name}: n={result['n']} effective_n={result['effective_n']:.2f} "
                f"p={result['p_value']:.5f} significant={result['significant_at_05']} "
                "(h1-equivalent framing -- not comparable to the h2 pre-registered target)"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
