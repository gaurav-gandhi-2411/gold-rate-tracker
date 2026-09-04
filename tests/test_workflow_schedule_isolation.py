"""Guard against re-coupling check-price.yml's schedule to a self-hosted job.

fix/selfhosted-job-cannot-block-schedule (audit 2026-09): measured 14 of 28
missed check-price.yml scheduled ticks over a 6.93-day post-#1222 window
coincided with a PRIOR check-price.yml run whose scrape-tanishq-selfhosted
job was still non-completed at that exact timestamp -- a GitHub Actions
workflow RUN stays non-`completed` until every one of its jobs finishes,
independent of `needs:` or per-job `concurrency:` groups, and a self-hosted
job with no runner available can sit `queued` for up to 24h. Moving that job
into its own workflow file (scrape-tanishq-selfhosted.yml) removes
check-price.yml from that job's queue state entirely.

This test does not (cannot) verify GitHub's own scheduler behavior -- that's
confirmed by the live "expected vs actual fires over the following 24h"
measurement in the PR, not by anything runnable here. What it guards against
is the specific regression of someone re-adding a `self-hosted` job to
check-price.yml later without realizing why that's the one thing this fix
depends on never happening again.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = ROOT / ".github" / "workflows"


def _load_workflow(name: str) -> dict:
    with (WORKFLOWS_DIR / name).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _runs_on_labels(job: dict) -> list[str]:
    runs_on = job.get("runs-on", [])
    if isinstance(runs_on, str):
        return [runs_on]
    return list(runs_on)


def test_check_price_has_no_self_hosted_job():
    wf = _load_workflow("check-price.yml")
    for job_name, job in wf["jobs"].items():
        labels = _runs_on_labels(job)
        assert "self-hosted" not in labels, (
            f"check-price.yml job {job_name!r} runs on self-hosted "
            f"({labels}) -- a self-hosted job here can sit queued for hours "
            "with no runner available, holding this workflow's run open and "
            "suppressing its own next scheduled tick (measured: 14 of 28 "
            "missed ticks over a 6.93-day window coincided with exactly "
            "this). Give it its own workflow file instead."
        )


def test_check_price_schedule_trigger_still_present():
    """The fix must not accidentally drop check-price.yml's own schedule."""
    wf = _load_workflow("check-price.yml")
    triggers = (
        wf["on"] if "on" in wf else wf[True]
    )  # PyYAML parses bare `on:` as True in some versions
    assert "schedule" in triggers
    crons = [entry["cron"] for entry in triggers["schedule"]]
    assert crons == ["7 */3 * * *"]


def test_scrape_tanishq_selfhosted_lives_in_its_own_workflow():
    wf = _load_workflow("scrape-tanishq-selfhosted.yml")
    assert "scrape-tanishq-selfhosted" in wf["jobs"]
    job = wf["jobs"]["scrape-tanishq-selfhosted"]
    assert "self-hosted" in _runs_on_labels(job)


def test_scrape_tanishq_selfhosted_has_independent_schedule():
    """The whole point of the split: this job's queue state must not depend
    on check-price.yml's run lifecycle at all -- confirmed structurally by
    it having its own `schedule` trigger, not a `workflow_run`/`needs`
    dependency on check-price.yml."""
    wf = _load_workflow("scrape-tanishq-selfhosted.yml")
    triggers = wf["on"] if "on" in wf else wf[True]
    assert "schedule" in triggers
    assert "workflow_run" not in triggers

    job = wf["jobs"]["scrape-tanishq-selfhosted"]
    assert "needs" not in job
