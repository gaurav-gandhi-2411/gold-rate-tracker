"""scripts/ci_health_judge.py -- decide which required checks ci-health.yml should judge on master.

Branch protection lists required contexts, but some are produced only on pull-request heads (or on
bot-PR dispatches), never by a push to master: ``scraper-dependency-guard`` is one. Looking for such a
context in master's lint.yml run reads as "missing" and pages GG on every run (2026-10-10, runs
38063493406 and 38082416402). Naming the exception in the workflow would break again on the next
PR-only check, so it is derived instead:

  * a required context counts as PR-ONLY when no job of the last few completed master lint.yml runs
    carries that name (``--history``); it is reported but not judged (branch protection enforces it
    at merge time);
  * every other required context is judged on the LATEST completed master run (``--latest``): any
    conclusion but ``success``, or absence from that run, is a failure.

Fails CLOSED (exit 2) on anything unreadable or implausible: no required contexts, an empty latest
run, an empty history, or nothing left to judge after PR-only contexts are set aside (a renamed
``lint`` job would otherwise turn every master check into "PR-only" and silence the monitor).

Exit codes: 0 all judged contexts green; 1 at least one failing; 2 cannot judge. The one-line result
is printed as ``OK ...`` / ``FAILING: ...`` / ``CANNOT JUDGE: ...``, plus ``PR-ONLY: ...`` when any.

    python scripts/ci_health_judge.py --required required.txt --latest jobs.json --history hist.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def judge(
    required: list[str], latest: list[dict[str, Any]], history: list[list[str]]
) -> tuple[int, str, list[str]]:
    """(exit code, result line, PR-only contexts)."""
    required = [c for c in required if c]
    if not required:
        return 2, "CANNOT JUDGE: no required contexts", []
    if not latest:
        return 2, "CANNOT JUDGE: the latest master lint run has no jobs", []
    produced = {str(j.get("name")) for j in latest}
    for names in history:
        produced.update(str(n) for n in names)
    if not history or not produced:
        return 2, "CANNOT JUDGE: no job history for master lint runs", []
    pr_only = [c for c in required if c not in produced]
    judged = [c for c in required if c in produced]
    if not judged:
        return 2, "CANNOT JUDGE: no required context is produced by master runs", pr_only
    failing: list[str] = []
    for context in judged:
        matches = [j for j in latest if j.get("name") == context]
        conclusion = str(matches[-1].get("conclusion") or "missing") if matches else "missing"
        if conclusion != "success":
            failing.append(f"{context} ({conclusion})")
    if failing:
        return 1, "FAILING: " + ", ".join(failing), pr_only
    return 0, "OK: " + ", ".join(judged), pr_only


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    ap.add_argument("--required", type=Path, required=True, help="one context per line")
    ap.add_argument("--latest", type=Path, required=True, help="JSON list of {name, conclusion}")
    ap.add_argument("--history", type=Path, required=True, help="JSON list of lists of job names")
    args = ap.parse_args(argv)
    try:
        required = args.required.read_text(encoding="utf-8").split("\n")
        latest = _load(args.latest)
        history = _load(args.history)
        if not isinstance(latest, list) or not isinstance(history, list):
            raise ValueError("latest and history must be JSON lists")
    except (OSError, ValueError) as err:
        print(f"CANNOT JUDGE: unreadable input ({type(err).__name__})")
        return 2
    code, line, pr_only = judge([c.strip() for c in required], latest, history)
    if pr_only:
        print(
            "PR-ONLY (not produced by master runs, enforced at merge time): " + ", ".join(pr_only)
        )
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
