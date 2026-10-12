"""A single GitHub 5xx must not leave a bot data PR without its required statuses.

2026-10-12 12:27Z: `gh api .../statuses/<sha>` returned HTTP 502 once in post-required-check-status,
the step had no retry, so bot PR #2740 stayed unmergeable until the next sync (14:29Z, two hours of
stale data on the site). Both status-forwarding steps now retry.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINT = (ROOT / ".github/workflows/lint.yml").read_text(encoding="utf-8")
GUARD = (ROOT / ".github/workflows/scraper-dependency-guard.yml").read_text(encoding="utf-8")


def _forward_step(text: str, anchor: str) -> str:
    return text.split(anchor, 1)[1][:1600]  # the step body (it is the last thing in its job)


def test_lint_status_forward_retries_and_still_fails_when_persistent() -> None:
    step = _forward_step(LINT, "Post lint + pwa-js as commit statuses on the head SHA")
    assert (
        "for attempt in 1 2 3 4 5" in step and "sleep $((attempt * 15))" in step and "-lt 5" in step
    )
    assert "return 1" in step  # five failures in a row still fail the job
    assert step.count("gh api") == 1  # only inside the retrying function, never bare


def test_guard_status_forward_retries_and_still_fails_when_persistent() -> None:
    step = _forward_step(GUARD, "Forward the result as a commit status")
    assert (
        "for attempt in 1 2 3 4 5" in step and "sleep $((attempt * 15))" in step and "-lt 5" in step
    )
    assert "exit 1" in step and "exit 0" in step
    assert step.count("gh api") == 1
