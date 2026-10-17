"""docs-refresh must run on a schedule as well as after Check Gold Price, and every caller of
scripts/inject_metrics.py must name its mode.

2026-10-17: the README stayed stale for about 2 h after a data sync and docs-freshness failed an unrelated PR.
Cause: the workflow_run trigger did not fire after dispatched Check Gold Price runs (7 of 46 were followed by a
refresh, against 45 of 45 for schedule and push runs; counted with gh run list). The refresh now also runs every
15 minutes, which keeps the drift inside the docs-freshness grace window."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
REFRESH_INTERVAL_MIN = 15
PR_PIPELINE_MIN = 30  # lint takes about 12 min and the merge waits for it; padded


def _refresh() -> dict:
    return yaml.safe_load((WORKFLOWS / "docs-refresh.yml").read_text(encoding="utf-8"))


def _on(wf: dict) -> dict:
    return wf.get("on", wf.get(True))  # YAML 1.1 reads a bare `on` key as boolean True


def test_docs_refresh_runs_on_a_schedule_no_slower_than_the_interval():
    cron = _on(_refresh())["schedule"][0]["cron"]
    minute, *rest = cron.split()
    assert rest == ["*", "*", "*", "*"]
    step = int(re.fullmatch(r"\*/(\d+)", minute).group(1))
    assert step <= REFRESH_INTERVAL_MIN


def test_docs_refresh_still_follows_the_data_workflows_and_can_be_dispatched():
    on = _on(_refresh())
    assert set(on["workflow_run"]["workflows"]) == {
        "Check Gold Price",
        "Weekly Backtest",
        "Direction Signal Eval",
    }
    assert "workflow_dispatch" in on


def test_a_failed_triggering_run_is_skipped_but_schedule_and_dispatch_are_not():
    cond = _refresh()["jobs"]["refresh"]["if"]
    assert cond == (
        "github.event_name != 'workflow_run' || github.event.workflow_run.conclusion == 'success'"
    )


def test_the_grace_window_covers_the_refresh_interval_plus_the_pr_pipeline():
    lint = (WORKFLOWS / "lint.yml").read_text(encoding="utf-8")
    (grace,) = re.findall(r"inject_metrics\.py --check --grace-minutes (\d+)", lint)
    assert int(grace) >= REFRESH_INTERVAL_MIN + PR_PIPELINE_MIN


def test_every_workflow_caller_of_inject_metrics_names_its_mode():
    """Enumerated from the workflow files themselves (not a hand list)."""
    seen = []
    for p in sorted(WORKFLOWS.glob("*.yml")) + sorted(
        (ROOT / ".github" / "actions").rglob("*.yml")
    ):
        for line in p.read_text(encoding="utf-8").splitlines():
            if (
                "scripts/inject_metrics.py" in line
                and "python" in line
                and "#" not in line.split("python")[0]
            ):
                seen.append((p.name, line.strip()))
    assert seen, "no caller found: the enumeration is broken"
    for name, line in seen:
        assert re.search(r"inject_metrics\.py --(check|write)\b", line), (name, line)
    assert any("--write" in line for _, line in seen)  # docs-refresh writes
    assert any("--check" in line for _, line in seen)  # docs-freshness checks
