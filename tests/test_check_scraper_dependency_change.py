"""Tests for scripts/check_scraper_dependency_change.py (item 5a).

No network: the script's single gh entry point (_gh_text) is replaced by a router over canned API
responses. The base fixture models PR #1846 (Playwright 1.62.1 -> 1.63.0, touched
scraper/package.json + scraper/package-lock.json, Dependabot body, no proof run), the PR that broke
the self-hosted scraper for ~11 hours on 2026-10-02.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_scraper_dependency_change.py"
_spec = importlib.util.spec_from_file_location("check_scraper_dependency_change", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["check_scraper_dependency_change"] = mod
_spec.loader.exec_module(mod)

REPO = "gaurav-gandhi-2411/gold-rate-tracker"
PR = 1846
HEAD_SHA = "19aa6ed06fdd8fbe18da6345f9a8b85d5d218f0f"
HEAD_REF = "dependabot/npm_and_yarn/scraper/npm-minor-patch-abc"
RUN_ID = 40000000001
JOB_ID = 110000000001

# Dependabot-style body, as on #1846: release notes, no run cited.
BODY_1846 = (
    "Bumps the npm-minor-patch group with 2 updates in the /scraper directory: "
    "lighthouse and playwright.\n\nUpdates `playwright` from 1.62.1 to 1.63.0\n"
    "See https://github.com/microsoft/playwright/releases and PR #1846."
)


def _good_log(run_id: int = RUN_ID, sha: str = HEAD_SHA) -> str:
    return (
        "2026-10-05T10:00:00Z ##[group]Run set -e\n"
        'echo "DEPENDENCY_PROOF scratch_browsers_path=$PLAYWRIGHT_BROWSERS_PATH '
        'browsers_installed=$N playwright=$PW head_sha=$GITHUB_SHA scrape=success"\n'
        "2026-10-05T10:00:01Z DEPENDENCY_PROOF scratch_browsers_path="
        f"C:\\actions-runner\\_work\\_temp\\pw-proof-{run_id} browsers_installed=2 "
        f"playwright=1.63.0 head_sha={sha} scrape=success\n"
    )


def _good_job() -> dict[str, object]:
    names = [
        "Set up job",
        "Checkout repository",
        mod.STEP_SCRATCH,
        "Set up Node.js",
        mod.STEP_INSTALL_DEPS,
        mod.STEP_INSTALL_BROWSERS,
        "Scrape Tanishq and update prices.json",
        mod.STEP_PROOF,
    ]
    return {
        "id": JOB_ID,
        "labels": ["self-hosted", "tanishq-scraper"],
        "runner_name": "gg-home-tanishq",
        "conclusion": "success",
        "steps": [{"name": n, "conclusion": "success"} for n in names],
    }


def _good_run() -> dict[str, object]:
    return {
        "id": RUN_ID,
        "path": mod.WORKFLOW_PATH,
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "success",
        "head_sha": HEAD_SHA,
        "head_branch": HEAD_REF,
        "head_repository": {"full_name": REPO},
    }


class World:
    """Canned gh responses; override fields per test. A value that is an Exception is raised."""

    def __init__(self) -> None:
        self.pr: object = {
            "body": BODY_1846,
            "head": {"sha": HEAD_SHA, "ref": HEAD_REF, "repo": {"full_name": REPO}},
        }
        self.files: object = [
            [{"filename": "scraper/package-lock.json"}, {"filename": "scraper/package.json"}]
        ]
        self.runs: dict[int, object] = {RUN_ID: _good_run()}
        self.jobs: dict[int, object] = {RUN_ID: {"jobs": [_good_job()]}}
        self.logs: dict[int, object] = {JOB_ID: _good_log()}
        self.compare: object = {"status": "ahead", "files": []}

    def route(self, args: list[str]) -> str:
        call = " ".join(args)

        def out(v: object) -> str:
            if isinstance(v, Exception):
                raise v
            return v if isinstance(v, str) else json.dumps(v)

        if f"repos/{REPO}/pulls/{PR}/files" in call:
            if isinstance(self.files, list):  # paginated: one JSON document per page
                return "".join(json.dumps(p) for p in self.files)
            return out(self.files)
        if f"repos/{REPO}/pulls/{PR}" in call:
            return out(self.pr)
        if "/compare/" in call:
            return out(self.compare)
        if "/logs" in call:
            return out(self.logs[int(call.split("/jobs/")[1].split("/")[0])])
        if "/jobs?" in call:
            return out(self.jobs[int(call.split("/runs/")[1].split("/")[0])])
        if "/actions/runs/" in call:
            return out(self.runs[int(call.split("/runs/")[1].split("/")[0])])
        raise AssertionError(f"unexpected gh call: {call}")


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    w = World()
    monkeypatch.setattr(mod, "_gh_text", w.route)
    return w


def _cite(world: World, text: str) -> None:
    assert isinstance(world.pr, dict)
    world.pr["body"] = BODY_1846 + "\n" + text


def _url(run_id: int = RUN_ID) -> str:
    return f"https://github.com/{REPO}/actions/runs/{run_id}"


def _result(world: World) -> tuple[bool, str]:
    ok, lines = mod.evaluate(PR, REPO)
    return ok, "\n".join(lines)


# --- the #1846 shape ------------------------------------------------------------------------------


def test_1846_shape_no_run_cited_fails(world: World) -> None:
    ok, text = _result(world)
    assert not ok
    assert "cites no GitHub Actions run" in text
    assert "dependency_proof=true" in text


def test_main_exit_code_1_for_1846_shape(world: World, capsys: pytest.CaptureFixture[str]) -> None:
    sys.argv = ["x", "--pr", str(PR), "--repo", REPO]
    assert mod.main() == 1
    assert "RESULT: FAIL" in capsys.readouterr().out


# --- cited run is not valid proof -----------------------------------------------------------------


def test_cited_run_failed_conclusion_fails(world: World) -> None:
    _cite(world, f"Proof: {_url()}")
    world.runs[RUN_ID] = {**_good_run(), "conclusion": "failure"}  # type: ignore[dict-item]
    ok, text = _result(world)
    assert not ok and "conclusion='failure'" in text


def test_cited_run_other_workflow_fails(world: World) -> None:
    _cite(world, f"Proof: {_url()}")
    world.runs[RUN_ID] = {**_good_run(), "path": ".github/workflows/lint.yml"}  # type: ignore[dict-item]
    ok, text = _result(world)
    assert not ok and "workflow is '.github/workflows/lint.yml'" in text


def test_cited_run_not_self_hosted_fails(world: World) -> None:
    _cite(world, f"Proof: {_url()}")
    job = _good_job()
    job["labels"] = ["ubuntu-latest"]
    world.jobs[RUN_ID] = {"jobs": [job]}
    ok, text = _result(world)
    assert not ok and "self-hosted" in text


def test_install_step_failed_fails(world: World) -> None:
    _cite(world, f"Proof: {_url()}")
    job = _good_job()
    for s in job["steps"]:  # type: ignore[attr-defined]
        if s["name"] == mod.STEP_INSTALL_BROWSERS:
            s["conclusion"] = "failure"
    world.jobs[RUN_ID] = {"jobs": [job]}
    ok, text = _result(world)
    assert not ok and "Install Playwright browsers" in text


def test_scratch_step_missing_means_production_cache_fails(world: World) -> None:
    _cite(world, f"Proof: {_url()}")
    job = _good_job()
    job["steps"] = [s for s in job["steps"] if s["name"] != mod.STEP_SCRATCH]  # type: ignore[attr-defined]
    world.jobs[RUN_ID] = {"jobs": [job]}
    ok, text = _result(world)
    assert not ok and "scratch browser cache" in text


def test_log_without_marker_fails(world: World) -> None:
    _cite(world, f"Proof: {_url()}")
    world.logs[JOB_ID] = "ordinary production run log\n"
    ok, text = _result(world)
    assert not ok and "no DEPENDENCY_PROOF marker" in text


def test_log_with_default_cache_path_fails(world: World) -> None:
    _cite(world, f"Proof: {_url()}")
    world.logs[JOB_ID] = _good_log().replace(f"pw-proof-{RUN_ID}", "ms-playwright")
    ok, text = _result(world)
    assert not ok and "not the scratch" in text


def test_echoed_script_text_alone_is_not_a_marker(world: World) -> None:
    _cite(world, f"Proof: {_url()}")
    world.logs[JOB_ID] = _good_log().split("\n")[1] + "\n"  # only the echoed `run:` text
    ok, text = _result(world)
    assert not ok and "no DEPENDENCY_PROOF marker" in text


def test_workflow_run_event_push_fails(world: World) -> None:
    _cite(world, f"Proof: {_url()}")
    world.runs[RUN_ID] = {**_good_run(), "event": "push"}  # type: ignore[dict-item]
    ok, text = _result(world)
    assert not ok and "workflow_dispatch" in text


def test_run_on_other_branch_fails(world: World) -> None:
    _cite(world, f"Proof: {_url()}")
    world.runs[RUN_ID] = {**_good_run(), "head_branch": "master"}  # type: ignore[dict-item]
    ok, text = _result(world)
    assert not ok and "expected the PR branch" in text


def test_run_from_a_fork_fails(world: World) -> None:
    _cite(world, f"Proof: {_url()}")
    world.runs[RUN_ID] = {**_good_run(), "head_repository": {"full_name": "evil/fork"}}  # type: ignore[dict-item]
    ok, text = _result(world)
    assert not ok and "head repository" in text


def test_run_url_of_another_repo_is_ignored(world: World) -> None:
    _cite(world, "Proof: https://github.com/someone/else/actions/runs/40000000001")
    ok, text = _result(world)
    assert not ok and "cites no GitHub Actions run" in text


# --- head relation --------------------------------------------------------------------------------


def test_run_at_older_commit_with_scraper_change_since_fails(world: World) -> None:
    _cite(world, f"Proof: {_url()}")
    old = "a" * 40
    world.runs[RUN_ID] = {**_good_run(), "head_sha": old}  # type: ignore[dict-item]
    world.logs[JOB_ID] = _good_log(sha=old)
    world.compare = {"status": "ahead", "files": [{"filename": "scraper/package-lock.json"}]}
    ok, text = _result(world)
    assert not ok and "does not cover the final dependency state" in text


def test_run_at_older_commit_with_unrelated_change_since_passes(world: World) -> None:
    _cite(world, f"Proof: {_url()}")
    old = "a" * 40
    world.runs[RUN_ID] = {**_good_run(), "head_sha": old}  # type: ignore[dict-item]
    world.logs[JOB_ID] = _good_log(sha=old)
    world.compare = {"status": "ahead", "files": [{"filename": "docs/RUNBOOK.md"}]}
    ok, _ = _result(world)
    assert ok


def test_run_commit_not_ancestor_fails(world: World) -> None:
    _cite(world, f"Proof: {_url()}")
    old = "a" * 40
    world.runs[RUN_ID] = {**_good_run(), "head_sha": old}  # type: ignore[dict-item]
    world.logs[JOB_ID] = _good_log(sha=old)
    world.compare = {"status": "diverged", "files": []}
    ok, text = _result(world)
    assert not ok and "not an ancestor" in text


# --- fail closed ----------------------------------------------------------------------------------


def test_pr_fetch_failure_fails_closed(world: World) -> None:
    world.pr = mod.GuardError("gh api failed (exit 1): HTTP 502")
    with pytest.raises(mod.GuardError):
        mod.evaluate(PR, REPO)
    sys.argv = ["x", "--pr", str(PR), "--repo", REPO]
    assert mod.main() == 1


def test_pr_body_missing_field_fails_closed(world: World) -> None:
    world.pr = {"head": {"sha": HEAD_SHA, "ref": HEAD_REF}}  # no 'body' key at all
    with pytest.raises(mod.GuardError):
        mod.evaluate(PR, REPO)


def test_unparseable_pr_json_fails_closed(world: World) -> None:
    world.pr = "<html>rate limited</html>"
    with pytest.raises(mod.GuardError):
        mod.evaluate(PR, REPO)


def test_files_fetch_failure_fails_closed(world: World) -> None:
    world.files = mod.GuardError("gh api failed")
    sys.argv = ["x", "--pr", str(PR), "--repo", REPO]
    assert mod.main() == 1


def test_run_fetch_failure_fails_closed(world: World) -> None:
    _cite(world, f"Proof: {_url()}")
    world.runs[RUN_ID] = mod.GuardError("HTTP 404")
    ok, text = _result(world)
    assert not ok and "failing closed" in text


def test_log_fetch_failure_fails_closed(world: World) -> None:
    _cite(world, f"Proof: {_url()}")
    world.logs[JOB_ID] = mod.GuardError("HTTP 410 logs expired")
    ok, text = _result(world)
    assert not ok and "failing closed" in text


# --- passes ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cite",
    [
        f"Proof run: {_url()}",
        f"Proof run: {_url()}/job/{JOB_ID}",
        f"proof run id: {RUN_ID}",
        f"Run #{RUN_ID} on the laptop",
    ],
)
def test_valid_proof_passes(world: World, cite: str) -> None:
    _cite(world, cite)
    ok, text = _result(world)
    assert ok, text
    assert "VALID proof" in text


def test_one_bad_and_one_good_run_passes(world: World) -> None:
    _cite(world, f"old failed {_url(40000000099)} ; good {_url()}")
    world.runs[40000000099] = {**_good_run(), "conclusion": "failure"}  # type: ignore[dict-item]
    world.jobs[40000000099] = {"jobs": [_good_job()]}
    world.logs[JOB_ID] = _good_log()
    ok, text = _result(world)
    assert ok
    assert "run 40000000099: NOT valid proof" in text


def test_pr_not_touching_scraper_package_files_passes_without_proof(world: World) -> None:
    world.files = [[{"filename": "scraper/scrape.js"}, {"filename": "docs/RUNBOOK.md"}]]
    world.pr = mod.GuardError("must not be fetched when no guarded file changed")
    ok, text = _result(world)
    assert ok and "no proof required" in text


@pytest.mark.parametrize(
    ("path", "guarded"),
    [
        ("scraper/package.json", True),
        ("scraper/package-lock.json", True),
        ("scraper/npm-shrinkwrap.json", True),
        ("scraper/sub/package.json", True),
        ("scraper/package.json.bak", False),
        ("package.json", False),
        ("ml/package.json", False),
        ("scraper/scrape.js", False),
    ],
)
def test_guarded_path_matching(path: str, guarded: bool) -> None:
    assert bool(mod.GUARDED_RE.match(path)) is guarded


def test_renamed_away_package_json_still_counts(world: World) -> None:
    world.files = [[{"filename": "tmp/p.json", "previous_filename": "scraper/package.json"}]]
    ok, text = _result(world)
    assert not ok and "scraper/package.json" in text


def test_paginated_files_across_pages(world: World) -> None:
    world.files = [
        [{"filename": f"docs/f{i}.md"} for i in range(100)],
        [{"filename": "scraper/package.json"}],
    ]
    ok, _ = _result(world)
    assert not ok  # the guarded file on page 2 is seen
