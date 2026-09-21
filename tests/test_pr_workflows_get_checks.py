"""A workflow that opens a PR must give that PR a way to get its required checks.

GitHub skips `pull_request` runs when the PR's head commit message carries a skip marker
(`[skip ci]` and its aliases). A workflow that commits with the marker, opens a PR, and waits
on native auto-merge therefore produces a PR whose required checks can never start:
docs-refresh.yml did exactly this, and PR #1578 sat open for 10 days (2026-09-11 -> 09-21)
while master's scheduled Lint failed 12 of 12 runs on the stale README it was meant to fix.
bot-pr-sync's callers also commit with the marker but dispatch lint.yml explicitly, which is
the other way out -- so the rule is: no skip marker on a `git commit`, OR an explicit dispatch.

The set of PR-creating files is discovered (any workflow/action containing `gh pr create`),
not listed, so a new workflow that copies the docs-refresh shape is covered without anyone
remembering to register it.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Every marker GitHub honours in a commit message (docs: "Skipping workflow runs").
_SKIP_MARKER = re.compile(
    r"\[(?:skip ci|ci skip|no ci|skip actions|actions skip)\]|skip-checks:\s*true", re.I
)
_DISPATCHES_LINT = re.compile(r"gh\s+workflow\s+run\s+lint\.yml")


def workflow_files(root: Path) -> list[Path]:
    files = sorted((root / ".github" / "workflows").glob("*.yml"))
    files += sorted((root / ".github" / "actions").glob("*/action.yml"))
    return files


def commit_lines_with_skip_marker(text: str) -> list[str]:
    """`git commit ...` lines whose message carries a skip marker (comments are ignored)."""
    out = []
    for line in text.splitlines():
        code = line.split(" #", 1)[0] if not line.lstrip().startswith("#") else ""
        if "git commit" in code and _SKIP_MARKER.search(code):
            out.append(line.strip())
    return out


def find_pr_workflows_without_checks(root: Path) -> tuple[list[str], list[str]]:
    """Return (violations, pr_creating_files). A violation opens PRs, commits with a skip
    marker, and never dispatches lint.yml."""
    violations, pr_files = [], []
    for path in workflow_files(root):
        text = path.read_text(encoding="utf-8")
        if not re.search(r"\bgh\s+pr\s+create\b", text):
            continue
        rel = path.relative_to(root).as_posix()
        pr_files.append(rel)
        if commit_lines_with_skip_marker(text) and not _DISPATCHES_LINT.search(text):
            violations.append(rel)
    return violations, pr_files


def test_real_repo_every_pr_opening_workflow_can_get_its_checks() -> None:
    violations, pr_files = find_pr_workflows_without_checks(REPO_ROOT)
    # Fail closed: finding no PR-creating file means the discovery stopped matching, not that
    # nothing opens PRs (bot-pr-sync's action and docs-refresh.yml both do today).
    assert len(pr_files) >= 2, f"discovery found too few PR-creating files: {pr_files}"
    assert violations == [], (
        f"{violations} open PRs from a commit carrying a skip marker without dispatching "
        "lint.yml -- the PR's required checks can never start"
    )


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return tmp_path


_DOCS_REFRESH_SHAPE = (
    "jobs:\n  refresh:\n    steps:\n      - run: |\n"
    '          git commit -m "chore: refresh injected doc metrics [skip ci]"\n'
    "          gh pr create --base master --head bot/docs-refresh\n"
    "          gh pr merge 1 --auto --squash\n"
)


def test_detects_the_docs_refresh_shape(tmp_path: Path) -> None:
    # The violation itself, constructed from the real workflow's shape.
    root = _repo(tmp_path, {".github/workflows/x.yml": _DOCS_REFRESH_SHAPE})
    violations, _ = find_pr_workflows_without_checks(root)
    assert violations == [".github/workflows/x.yml"]


def test_explicit_lint_dispatch_is_an_allowed_way_out(tmp_path: Path) -> None:
    body = _DOCS_REFRESH_SHAPE + "          gh workflow run lint.yml --ref bot/docs-refresh\n"
    root = _repo(tmp_path, {".github/actions/a/action.yml": body})
    assert find_pr_workflows_without_checks(root)[0] == []


def test_commit_without_a_skip_marker_is_fine(tmp_path: Path) -> None:
    body = _DOCS_REFRESH_SHAPE.replace(" [skip ci]", "")
    root = _repo(tmp_path, {".github/workflows/x.yml": body})
    assert find_pr_workflows_without_checks(root)[0] == []


def test_every_marker_alias_is_recognised(tmp_path: Path) -> None:
    markers = ("[ci skip]", "[no ci]", "[skip actions]", "[actions skip]", "skip-checks: true")
    for i, marker in enumerate(markers):
        body = _DOCS_REFRESH_SHAPE.replace("[skip ci]", marker)
        # Indexed dir: some markers contain characters that are invalid in a Windows path.
        root = _repo(tmp_path / f"case{i}", {".github/workflows/x.yml": body})
        assert find_pr_workflows_without_checks(root)[0] == [".github/workflows/x.yml"], marker


def test_a_marker_only_in_a_comment_is_not_a_violation(tmp_path: Path) -> None:
    body = _DOCS_REFRESH_SHAPE.replace(
        '          git commit -m "chore: refresh injected doc metrics [skip ci]"\n',
        '          # do not use [skip ci] here\n          git commit -m "chore: refresh"\n',
    )
    root = _repo(tmp_path, {".github/workflows/x.yml": body})
    assert find_pr_workflows_without_checks(root)[0] == []
