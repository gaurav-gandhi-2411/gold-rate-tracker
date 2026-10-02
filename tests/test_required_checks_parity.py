"""The Worker's merged-unchecked predicate must agree with the Python one (AN8, 2026-09-21).

scripts/check_required_checks_positive.py is the specification: it is what gates every self-merge.
worker-deadman/src/pr_trigger_health.mjs re-implements it in JS because the Worker cannot import
Python. Two copies of one rule drift, so both are pinned to the same recorded cases in
worker-deadman/test/fixtures/required_checks_cases.json -- this file replays them through the Python
spec, and worker-deadman/test/pr_trigger_health.test.mjs replays them through the JS copy.

Includes the three real incidents (#1539 and #1569: no check-runs at all; #1541: lint FAILED) and the
real bot PR that must never page.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "check_required_checks_positive", _ROOT / "scripts" / "check_required_checks_positive.py"
)
mod = importlib.util.module_from_spec(_spec)
sys.modules["check_required_checks_positive"] = mod
_spec.loader.exec_module(mod)

_FIXTURES = json.loads(
    (_ROOT / "worker-deadman" / "test" / "fixtures" / "required_checks_cases.json").read_text(
        encoding="utf-8"
    )
)


def _category(verdict: str) -> str:
    if verdict == "SUCCESS":
        return "SUCCESS"
    return "ABSENT" if verdict.startswith("ABSENT") else "NOT SUCCESS"


def test_fixtures_are_not_empty_and_cover_the_real_incidents() -> None:
    # Fail closed: an emptied fixture file would make the parametrized test below vacuously pass.
    names = [c["name"] for c in _FIXTURES["cases"]]
    assert len(names) >= 10
    for needle in ("REAL #1539", "REAL #1569", "REAL #1541", "REAL bot PR"):
        assert any(n.startswith(needle) for n in names), needle


def test_worker_required_contexts_match_the_fixtures() -> None:
    # The Worker's constant and the fixtures' list must be the same, and the Worker cannot read
    # branch protection, so this is the only place a stale constant can be caught in CI.
    src = (_ROOT / "worker-deadman" / "src" / "pr_trigger_health.mjs").read_text(encoding="utf-8")
    match = re.search(r"export const REQUIRED_CONTEXTS = (\[[^\]]*\]);", src)
    assert match, "REQUIRED_CONTEXTS constant not found in pr_trigger_health.mjs"
    assert json.loads(match.group(1)) == _FIXTURES["required_contexts"]


@pytest.mark.parametrize("case", _FIXTURES["cases"], ids=[c["name"] for c in _FIXTURES["cases"]])
def test_python_spec_reproduces_every_recorded_verdict(case: dict) -> None:
    ok, results = mod.assert_required_checks_positive(
        _FIXTURES["required_contexts"], case["check_runs"]
    )
    assert ok is case["expected"]["all_pass"]
    assert {k: _category(v) for k, v in results.items()} == case["expected"]["statuses"]


def test_the_three_real_incidents_block_and_the_bot_pr_passes() -> None:
    by_prefix = {c["name"].split(" - ")[0]: c for c in _FIXTURES["cases"]}
    for key in ("REAL #1539", "REAL #1569", "REAL #1541"):
        ok, _ = mod.assert_required_checks_positive(
            _FIXTURES["required_contexts"], by_prefix[key]["check_runs"]
        )
        assert ok is False, key
    bot = next(c for c in _FIXTURES["cases"] if c["name"].startswith("REAL bot PR"))
    ok, _ = mod.assert_required_checks_positive(_FIXTURES["required_contexts"], bot["check_runs"])
    assert ok is True
