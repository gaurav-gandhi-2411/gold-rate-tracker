"""A bot sync that runs weekly or monthly must wait long enough for a contended lint.

2026-11-01: weekly-backtest (run 41288754101) opened bot PR #3424 and waited 10 minutes. Its lint
waited ~6 min for a runner and posted green at 13 min, after the sync had given up. Nothing retries
a weekly sync until the next week, so the week's backtest data never reached master and the PR sat
open with green checks. Frequent syncs (every few hours) recover on their next run via
carry-forward; a weekly or monthly one does not. So every workflow scheduled ONLY weekly or monthly
that merges through bot-pr-sync waits >= 30 minutes, and its job timeout leaves room for that wait.
Found by schedule and action use, not by a list of names, so a new rare sync is covered too.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
MIN_WAIT = 30


def _rare(wf: dict) -> bool:
    on = wf.get("on", wf.get(True, {}))  # PyYAML reads the bare key `on` as True
    crons = [c["cron"] for c in (on.get("schedule") or [])] if isinstance(on, dict) else []
    if not crons:
        return False
    # minute hour day-of-month month day-of-week: rare when every cron pins a day of the
    # week or of the month
    return all(c.split()[2] != "*" or c.split()[4] != "*" for c in crons)


def _syncs() -> list[tuple[str, dict, dict]]:
    out = []
    for p in sorted(WORKFLOWS.glob("*.yml")):
        wf = yaml.safe_load(p.read_text(encoding="utf-8"))
        if not _rare(wf):
            continue
        for job in wf["jobs"].values():
            for step in job.get("steps", []):
                if str(step.get("uses", "")).endswith("/bot-pr-sync"):
                    out.append((p.name, job, step))
    return out


def test_the_rare_syncs_are_found():
    names = {name for name, _, _ in _syncs()}
    assert {"weekly-backtest.yml", "eval-direction.yml", "monthly-ibja-backfill.yml"} <= names
    # a frequent sync is not "rare": check-price runs every 3 hours
    assert "check-price.yml" not in names


def test_a_rare_sync_waits_at_least_30_minutes_inside_its_job_timeout():
    for name, job, step in _syncs():
        wait = int(step.get("with", {}).get("wait-minutes", 15))
        assert wait >= MIN_WAIT, f"{name}: wait-minutes {wait} < {MIN_WAIT}"
        timeout = int(job.get("timeout-minutes", 360))
        # the work before the sync also needs time; at least 10 minutes of it
        assert timeout >= wait + 10, f"{name}: timeout {timeout} leaves no room for the wait"
