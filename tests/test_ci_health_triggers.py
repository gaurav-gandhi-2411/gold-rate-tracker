"""The CI health monitor must not depend on GitHub's cron alone, and must judge finished runs only."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CI_HEALTH = (ROOT / ".github" / "workflows" / "ci-health.yml").read_text(encoding="utf-8")
CHECK_PRICE = (ROOT / ".github" / "workflows" / "check-price.yml").read_text(encoding="utf-8")


def test_ci_health_judges_completed_lint_runs_only() -> None:
    # A run still in progress has jobs with no conclusion, which read as "missing" and paged GG.
    queries = re.findall(r"workflows/lint\.yml/runs\?[^\"]*", CI_HEALTH)
    assert queries and all("status=completed" in q for q in queries)


def test_check_price_starts_ci_health_when_it_is_stale() -> None:
    step = CHECK_PRICE.split("Start the CI health check if it has not run lately", 1)[1]
    step = step.split("\n      - name:", 1)[0]
    assert "gh workflow run ci-health.yml" in step
    assert "if: always()" in step and "continue-on-error: true" in step
    assert "-gt 7200" in step  # started only when the last run is more than 2 hours old
    # the dispatch needs actions: write at the workflow level
    assert re.search(r"^\s+actions: write", CHECK_PRICE, re.M)


def test_ci_health_keeps_its_own_schedule_and_manual_trigger() -> None:
    assert "schedule:" in CI_HEALTH and "workflow_dispatch:" in CI_HEALTH
