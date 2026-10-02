"""check-price.yml builds its data on the tip of master, not on the commit that triggered the run.

Runs of the `check` job queue behind each other (concurrency group `check-price`). A queued run
used to check out its trigger commit; by the time it ran, the run ahead of it had merged newer
data, so its own data commit conflicted in bot-pr-sync's rebase and the run failed
(2026-10-02 09:01Z, run 36987403933)."""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "check-price.yml"


def test_check_job_checks_out_master_and_runs_one_at_a_time():
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = wf["jobs"]["check"]
    assert job["concurrency"] == {"group": "check-price", "cancel-in-progress": False}
    checkouts = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout@")]
    assert checkouts, "check job has no checkout step"
    assert checkouts[0].get("with", {}).get("ref") == "master"
