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


def _expand_hour_field(field: str) -> list[int]:
    """Expands a cron hour-field to the list of hours it fires at. Handles
    the shapes this repo's own crons actually use: `*`, `*/N`, `A-B/N`,
    `A-B`, `H1,H2,...`, and a bare hour."""
    if field == "*":
        return list(range(24))
    if "/" in field:
        base, step_str = field.split("/")
        step = int(step_str)
        if base == "*":
            start, end = 0, 23
        elif "-" in base:
            start, end = (int(x) for x in base.split("-"))
        else:
            start = end = int(base)
        return list(range(start, end + 1, step))
    if "-" in field:
        start, end = (int(x) for x in field.split("-"))
        return list(range(start, end + 1))
    if "," in field:
        return [int(x) for x in field.split(",")]
    return [int(field)]


def test_check_price_schedule_trigger_still_present():
    """The fix must not accidentally drop check-price.yml's own schedule.

    AD1 (audit 2026-09-10): checks the INVARIANT this test actually exists
    for -- a schedule trigger present, firing 8x/day (the ~3-hourly design
    target) -- rather than one exact cron string. The previous version
    hardcoded `"7 */3 * * *"` verbatim and broke on AD1's own legitimate
    cron-phase change (06:00 UTC contention fix, PR #1541) despite that
    change not violating anything this test is meant to guard -- the
    schedule was never dropped, just phase-shifted. A test asserting the
    literal string, not the property, produces exactly this kind of
    unrelated-looking failure on every future adjustment (e.g. if AF4b's
    7-day re-measurement finds a different shift is needed).
    """
    wf = _load_workflow("check-price.yml")
    triggers = (
        wf["on"] if "on" in wf else wf[True]
    )  # PyYAML parses bare `on:` as True in some versions
    assert "schedule" in triggers
    crons = [entry["cron"] for entry in triggers["schedule"]]
    assert len(crons) == 1
    minute_field, hour_field, dom, month, dow = crons[0].split()
    assert (dom, month, dow) == ("*", "*", "*"), f"expected a daily pattern, got {crons[0]!r}"
    assert minute_field.isdigit() and 0 <= int(minute_field) <= 59
    hours = _expand_hour_field(hour_field)
    assert len(hours) == 8, (
        f"expected an 8x/day (~3-hourly) cadence, got {len(hours)} slots: {hours}"
    )


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
