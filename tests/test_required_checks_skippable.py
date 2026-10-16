"""Tests for scripts/check_required_checks_skippable.py (2026-10-09 lesson: a required check that a PR can
skip blocks that PR; a status post that discards its errors is a silent failure)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "rcs", ROOT / "scripts" / "check_required_checks_skippable.py"
)
rcs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rcs)

BOT_OK = "gh workflow run lint.yml --ref x\n"

GOOD = """name: X
on:
  pull_request:
  workflow_dispatch:
jobs:
  chk:
    runs-on: ubuntu-latest
    steps:
      - run: gh api "repos/r/statuses/$SHA" -f state=success -f context=chk
"""


def _use(tmp_path, monkeypatch, text, name="lint.yml"):
    (tmp_path / name).write_text(text, encoding="utf-8")
    monkeypatch.setattr(rcs, "WORKFLOWS", tmp_path)


def test_real_repo_required_checks_are_not_skippable():
    bot = rcs.BOT_ACTION.read_text(encoding="utf-8")
    for ctx in rcs.REQUIRED:
        assert rcs.problems_for(ctx, bot) == [], ctx


def test_a_good_workflow_passes(tmp_path, monkeypatch):
    _use(tmp_path, monkeypatch, GOOD)
    assert rcs.problems_for("chk", BOT_OK) == []


def test_path_filter_is_flagged(tmp_path, monkeypatch):
    _use(
        tmp_path,
        monkeypatch,
        GOOD.replace("  pull_request:\n", "  pull_request:\n    paths: ['a/**']\n"),
    )
    assert any("path-filtered" in p for p in rcs.problems_for("chk", BOT_OK))


def test_job_level_if_is_flagged(tmp_path, monkeypatch):
    _use(tmp_path, monkeypatch, GOOD.replace("  chk:\n", "  chk:\n    if: github.actor != 'bot'\n"))
    assert any("job-level" in p for p in rcs.problems_for("chk", BOT_OK))


def test_no_dispatch_or_no_forwarding_or_no_bot_dispatch_is_flagged(tmp_path, monkeypatch):
    _use(tmp_path, monkeypatch, GOOD.replace("  workflow_dispatch:\n", ""))
    assert any("workflow_dispatch" in p for p in rcs.problems_for("chk", BOT_OK))
    _use(tmp_path, monkeypatch, GOOD.replace("statuses/", "other/"))
    assert any("never forwards" in p for p in rcs.problems_for("chk", BOT_OK))
    _use(tmp_path, monkeypatch, GOOD)
    assert any("bot-pr-sync does not dispatch" in p for p in rcs.problems_for("chk", ""))


def test_status_post_that_hides_errors_is_flagged(tmp_path, monkeypatch):
    for tail in (" >/dev/null", " || true"):
        _use(tmp_path, monkeypatch, GOOD.rstrip("\n") + tail + "\n")
        assert any("discarded" in p for p in rcs.problems_for("chk", BOT_OK)), tail


def test_unknown_check_name_is_flagged(tmp_path, monkeypatch):
    _use(tmp_path, monkeypatch, GOOD)
    assert rcs.problems_for("nope", BOT_OK)


# --- item 7 (2026-10-15): every workflow that creates or updates a bot PR -------------------------------
FIXTURES = Path(__file__).resolve().parent / "fixtures"
GUARD = """name: scraper-dependency-guard
on:
  pull_request:
  workflow_dispatch:
    inputs:
      pr:
        required: false
jobs:
  scraper-dependency-guard:
    runs-on: ubuntu-latest
    steps:
      - run: gh api "repos/r/statuses/$SHA" -f state=success -f context=scraper-dependency-guard
"""
BOT_ACTION_OK = (
    "gh workflow run lint.yml --ref x\n"
    "gh workflow run scraper-dependency-guard.yml --ref x -f pr=1\n"
)


def _repo_copy(tmp_path, monkeypatch, extra: dict[str, str]):
    """A workflows dir with a lint.yml producing `lint`, the guard, and the given extra workflows."""
    (tmp_path / "lint.yml").write_text(
        GOOD.replace("chk:", "lint:").replace("context=chk", "context=lint"), "utf-8"
    )
    (tmp_path / "scraper-dependency-guard.yml").write_text(GUARD, "utf-8")
    for name, text in extra.items():
        (tmp_path / name).write_text(text, "utf-8")
    monkeypatch.setattr(rcs, "WORKFLOWS", tmp_path)


def _own_pr_workflow(
    dispatches: str,
    branch: str = "bot/x",
    push: str = "git push --force origin HEAD:refs/heads/$BRANCH",
) -> str:
    return f"""name: own
on: workflow_dispatch
jobs:
  j:
    runs-on: ubuntu-latest
    steps:
      - run: |
          BRANCH="{branch}"
          {push}
          gh pr create --base master --head "$BRANCH" --title t --body b
          {dispatches}
"""


REQ = ["lint", "scraper-dependency-guard"]
BOTH = "gh workflow run lint.yml --ref $BRANCH\n          gh workflow run scraper-dependency-guard.yml --ref $BRANCH -f pr=$PR"


def test_the_real_repo_every_bot_pr_workflow_satisfies_every_required_check():
    bot = rcs.BOT_ACTION.read_text(encoding="utf-8")
    assert rcs.bot_pr_workflow_problems(rcs.REQUIRED, bot) == []
    found = rcs.bot_pr_workflows()
    # enumerated from the files, not a hand list: the known members must be among them
    assert found["docs-refresh.yml"] == "own" and found["check-price.yml"] == "action"
    assert found["encrypt-raw-data-migration.yml"] == "own"
    assert found["scrape-tanishq-selfhosted.yml"] == "action"


def test_pre_2821_docs_refresh_FAILS_the_control():
    """The docs-refresh.yml of master before #2821 (lint dispatched, guard not): PR #2810 sat BLOCKED 2 h."""
    old = (FIXTURES / "docs-refresh.pre-2821.yml").read_text(encoding="utf-8")
    assert "scraper-dependency-guard" not in old and "gh workflow run lint.yml" in old
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        wfs = Path(d)
        for f in (ROOT / ".github" / "workflows").glob("*.yml"):
            (wfs / f.name).write_bytes(f.read_bytes())
        (wfs / "docs-refresh.yml").write_text(old, encoding="utf-8")
        bot = rcs.BOT_ACTION.read_text(encoding="utf-8")
        probs = rcs.bot_pr_workflow_problems(rcs.REQUIRED, bot, wfs)
    assert any("docs-refresh.yml" in p and "scraper-dependency-guard.yml" in p for p in probs), (
        probs
    )


def test_the_current_docs_refresh_passes_the_control():
    bot = rcs.BOT_ACTION.read_text(encoding="utf-8")
    assert not [p for p in rcs.bot_pr_workflow_problems(rcs.REQUIRED, bot) if "docs-refresh" in p]


def test_a_new_own_pr_workflow_that_dispatches_only_lint_is_caught(tmp_path, monkeypatch):
    _repo_copy(
        tmp_path,
        monkeypatch,
        {"newbot.yml": _own_pr_workflow("gh workflow run lint.yml --ref $BRANCH")},
    )
    probs = rcs.bot_pr_workflow_problems(REQ, BOT_ACTION_OK, tmp_path)
    assert (
        len(probs) == 1 and "newbot.yml" in probs[0] and "scraper-dependency-guard.yml" in probs[0]
    )


def test_a_new_own_pr_workflow_that_dispatches_both_with_pr_passes(tmp_path, monkeypatch):
    _repo_copy(tmp_path, monkeypatch, {"newbot.yml": _own_pr_workflow(BOTH)})
    assert rcs.bot_pr_workflow_problems(REQ, BOT_ACTION_OK, tmp_path) == []


def test_a_dispatch_of_the_guard_without_the_pr_number_is_caught(tmp_path, monkeypatch):
    bad = "gh workflow run lint.yml --ref $BRANCH\n          gh workflow run scraper-dependency-guard.yml --ref $BRANCH"
    _repo_copy(tmp_path, monkeypatch, {"newbot.yml": _own_pr_workflow(bad)})
    probs = rcs.bot_pr_workflow_problems(REQ, BOT_ACTION_OK, tmp_path)
    assert len(probs) == 1 and "without `-f pr=`" in probs[0]


def test_a_commented_out_dispatch_does_not_count(tmp_path, monkeypatch):
    text = _own_pr_workflow(
        "# gh workflow run scraper-dependency-guard.yml -f pr=$PR\n          gh workflow run lint.yml --ref $BRANCH"
    )
    _repo_copy(tmp_path, monkeypatch, {"newbot.yml": text})
    assert rcs.bot_pr_workflow_problems(REQ, BOT_ACTION_OK, tmp_path)


def test_a_fresh_branch_per_run_needs_no_dispatch(tmp_path, monkeypatch):
    text = _own_pr_workflow(
        "echo done",
        branch="chore/thing-${GITHUB_RUN_ID}",
        push="git push origin HEAD:refs/heads/$BRANCH",
    )
    _repo_copy(tmp_path, monkeypatch, {"fresh.yml": text})
    assert rcs.bot_pr_workflow_problems(REQ, BOT_ACTION_OK, tmp_path) == []


def test_a_run_id_branch_that_is_force_pushed_still_needs_dispatch(tmp_path, monkeypatch):
    text = _own_pr_workflow(
        "echo done", branch="chore/thing-${GITHUB_RUN_ID}"
    )  # default push is --force
    _repo_copy(tmp_path, monkeypatch, {"forced.yml": text})
    assert rcs.bot_pr_workflow_problems(REQ, BOT_ACTION_OK, tmp_path)


def test_a_bot_pr_sync_caller_is_judged_by_the_action(tmp_path, monkeypatch):
    caller = """name: c
on: workflow_dispatch
jobs:
  j:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/bot-pr-sync
        with:
          branch: bot/c
"""
    _repo_copy(tmp_path, monkeypatch, {"caller.yml": caller})
    assert rcs.bot_pr_workflow_problems(REQ, BOT_ACTION_OK, tmp_path) == []
    probs = rcs.bot_pr_workflow_problems(REQ, "gh workflow run lint.yml --ref x\n", tmp_path)
    assert any(
        "caller.yml" in p and "never dispatches scraper-dependency-guard.yml" in p for p in probs
    )


def test_workflows_that_only_mention_gh_pr_create_in_comments_are_not_bot_pr_workflows(
    tmp_path, monkeypatch
):
    text = "name: n\non: workflow_dispatch\njobs:\n  j:\n    runs-on: x\n    steps:\n      - run: |\n          # gh pr create is not run here\n          echo hi\n"
    _repo_copy(tmp_path, monkeypatch, {"mention.yml": text})
    assert "mention.yml" not in rcs.bot_pr_workflows(tmp_path)


def test_bot_pr_sync_dispatching_the_guard_without_pr_is_flagged(tmp_path, monkeypatch):
    _repo_copy(tmp_path, monkeypatch, {})
    probs = rcs.problems_for(
        "scraper-dependency-guard",
        "gh workflow run lint.yml\ngh workflow run scraper-dependency-guard.yml --ref x\n",
    )
    assert any("without `-f pr=`" in p for p in probs)
