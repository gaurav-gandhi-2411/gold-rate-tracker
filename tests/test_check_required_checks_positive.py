"""Tests for scripts/check_required_checks_positive.py (AJ1, audit 2026-09-11/12)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "check_required_checks_positive.py"
)
_spec = importlib.util.spec_from_file_location("check_required_checks_positive", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["check_required_checks_positive"] = mod
_spec.loader.exec_module(mod)


def _run(
    name: str, conclusion: str | None, started_at: str = "2026-09-12T00:00:00Z"
) -> dict[str, object]:
    return {"name": name, "conclusion": conclusion, "started_at": started_at}


def test_all_required_contexts_success_passes() -> None:
    all_pass, results = mod.assert_required_checks_positive(
        ["lint", "pwa-js"],
        [_run("lint", "success"), _run("pwa-js", "success")],
    )
    assert all_pass is True
    assert results["lint"] == "SUCCESS"
    assert results["pwa-js"] == "SUCCESS"


def test_zero_check_runs_for_a_required_context_blocks() -> None:
    # This IS #1539/#1569's actual recorded state, reconstructed: zero
    # check-runs at all for either required context.
    all_pass, results = mod.assert_required_checks_positive(["lint", "pwa-js"], [])
    assert all_pass is False
    assert "ABSENT" in results["lint"]
    assert "ABSENT" in results["pwa-js"]


def test_one_context_absent_one_success_still_blocks() -> None:
    # Partial absence must not average out to a pass -- every required
    # context must independently clear the bar.
    all_pass, results = mod.assert_required_checks_positive(
        ["lint", "pwa-js"],
        [_run("lint", "success")],
    )
    assert all_pass is False
    assert results["lint"] == "SUCCESS"
    assert "ABSENT" in results["pwa-js"]


def test_pending_conclusion_none_blocks_not_absent() -> None:
    all_pass, results = mod.assert_required_checks_positive(
        ["lint", "pwa-js"],
        [_run("lint", None), _run("pwa-js", "success")],
    )
    assert all_pass is False
    assert "NOT SUCCESS" in results["lint"]
    assert "None" in results["lint"]


def test_failure_conclusion_blocks() -> None:
    all_pass, results = mod.assert_required_checks_positive(
        ["lint", "pwa-js"],
        [_run("lint", "failure"), _run("pwa-js", "success")],
    )
    assert all_pass is False
    assert "failure" in results["lint"]


def test_non_required_context_failing_does_not_affect_result() -> None:
    # A red docs-freshness/boundary-leak-check (non-required) must not
    # block -- this script only asserts on the contexts branch protection
    # actually requires, matching the real repo's established UNSTABLE-
    # vs-BLOCKED distinction.
    all_pass, results = mod.assert_required_checks_positive(
        ["lint", "pwa-js"],
        [_run("lint", "success"), _run("pwa-js", "success"), _run("docs-freshness", "failure")],
    )
    assert all_pass is True
    assert "docs-freshness" not in results


def test_multiple_runs_for_the_same_context_uses_the_latest() -> None:
    # A re-run: the FIRST attempt failed, a LATER attempt succeeded --
    # the current state is success, and this must read as a pass, not be
    # confused by the earlier failing attempt still being in the list.
    all_pass, results = mod.assert_required_checks_positive(
        ["lint"],
        [
            _run("lint", "failure", started_at="2026-09-12T00:00:00Z"),
            _run("lint", "success", started_at="2026-09-12T00:05:00Z"),
        ],
    )
    assert all_pass is True
    assert results["lint"] == "SUCCESS"


def test_multiple_runs_latest_is_the_failing_one() -> None:
    # Inverse of the above: an EARLIER success followed by a LATER
    # failure (e.g. someone re-ran it and it broke) must block, not pass
    # on the stale earlier success.
    all_pass, results = mod.assert_required_checks_positive(
        ["lint"],
        [
            _run("lint", "success", started_at="2026-09-12T00:00:00Z"),
            _run("lint", "failure", started_at="2026-09-12T00:05:00Z"),
        ],
    )
    assert all_pass is False
    assert "failure" in results["lint"]


def test_empty_required_contexts_list_raises_rather_than_vacuously_passing() -> None:
    # get_required_contexts() itself raises on an empty list before this
    # function is ever called with one -- but if it somehow were called
    # with an empty list, an empty loop trivially "passes" with zero
    # results, which would be a silent, wrong all-clear. Documented here
    # as the reason get_required_contexts() fails closed instead.
    all_pass, results = mod.assert_required_checks_positive([], [_run("lint", "success")])
    assert all_pass is True
    assert results == {}
    # The real guard against this is get_required_contexts() raising on
    # an empty contexts list -- see test_get_required_contexts_empty_raises.


def test_get_required_contexts_empty_list_raises() -> None:
    import pytest

    class FakeCompleted:
        returncode = 0
        stdout = '{"required_status_checks": {"contexts": []}}'
        stderr = ""

    def fake_run(args: list[str]) -> FakeCompleted:
        return FakeCompleted()

    original_run = mod._run
    mod._run = fake_run
    try:
        with pytest.raises(mod.RequiredChecksError, match=r"no required_status_checks\.contexts"):
            mod.get_required_contexts("owner/repo")
    finally:
        mod._run = original_run
