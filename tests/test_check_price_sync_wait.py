"""check-price waits 30 minutes for its data-sync bot PR, inside a job timeout that leaves room.

2026-11-01: check-price run 41287213214 failed at "Sync data via bot PR". Bot PR #3423's required
statuses landed about 51 s after scripts/bot_pr_merge.sh stopped waiting at 15 minutes, because the
status-forwarding job waited 8.5 minutes for a runner. Of 100 bot-PR lint runs the median took
7 minutes and the longest 37 (FOR_GG item 9, applied at GG's go on 2026-11-03).
"""

from __future__ import annotations

from pathlib import Path

import yaml

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "check-price.yml"
# Room for every step outside the sync wait. Measured over the last 60 check-price runs
# (2026-11-03, step timestamps): before the sync median 1.1 min, max 1.4; after it max 0.13; the
# sync step overruns its wait by at most 0.3 min. So the worst case at a 30-min wait is about
# 32 min. 20 min of room leaves headroom for a cold install or a slow scrape.
WORK_BEFORE_SYNC = 20


def _job_and_sync() -> tuple[dict, dict]:
    wf = yaml.safe_load(WF.read_text(encoding="utf-8"))
    found = [
        (job, step)
        for job in wf["jobs"].values()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).endswith("/bot-pr-sync")
    ]
    assert len(found) == 1, "check-price should merge its data through exactly one bot-pr-sync"
    return found[0]


def test_check_price_waits_30_minutes_for_its_bot_pr():
    _, step = _job_and_sync()
    assert int(step["with"]["wait-minutes"]) >= 30


def test_the_job_timeout_leaves_room_for_the_work_and_the_wait():
    job, step = _job_and_sync()
    wait = int(step["with"]["wait-minutes"])
    assert int(job["timeout-minutes"]) >= wait + WORK_BEFORE_SYNC
