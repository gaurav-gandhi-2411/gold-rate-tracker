"""Tests for scripts/check_bot_pr_sync_allowlist.py (AF1, audit 2026-09-10)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_bot_pr_sync_allowlist.py"
_spec = importlib.util.spec_from_file_location("check_bot_pr_sync_allowlist", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["check_bot_pr_sync_allowlist"] = mod
_spec.loader.exec_module(mod)


ACTION_YML_TEMPLATE = """\
name: bot-pr-sync
runs:
  using: composite
  steps:
    - shell: bash
      run: |
        BAD_FILES=$(git diff --name-only origin/master...HEAD | grep -v -E '{regex}' || true)
        if [ -n "$BAD_FILES" ]; then
          exit 1
        fi
"""


def _write_action(tmp_path: Path, regex: str = r"^(data/|og\.png$|reports/lighthouse/)") -> Path:
    action_dir = tmp_path / ".github" / "actions" / "bot-pr-sync"
    action_dir.mkdir(parents=True)
    path = action_dir / "action.yml"
    path.write_text(ACTION_YML_TEMPLATE.format(regex=regex), encoding="utf-8")
    return path


def _write_workflow(tmp_path: Path, name: str, body: str) -> Path:
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    path = workflows_dir / name
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# extract_allowlist_regex
# ---------------------------------------------------------------------------


def test_extract_allowlist_regex_reads_from_action_yml(tmp_path: Path):
    action_path = _write_action(tmp_path)
    assert mod.extract_allowlist_regex(action_path) == r"^(data/|og\.png$|reports/lighthouse/)"


def test_extract_allowlist_regex_missing_file_fails_closed(tmp_path: Path):
    with pytest.raises(mod.AllowlistCheckError):
        mod.extract_allowlist_regex(tmp_path / "does_not_exist.yml")


def test_extract_allowlist_regex_missing_guard_line_fails_closed(tmp_path: Path):
    action_dir = tmp_path / ".github" / "actions" / "bot-pr-sync"
    action_dir.mkdir(parents=True)
    path = action_dir / "action.yml"
    path.write_text("name: bot-pr-sync\nruns:\n  using: composite\n  steps: []\n", encoding="utf-8")
    with pytest.raises(mod.AllowlistCheckError, match="could not find"):
        mod.extract_allowlist_regex(path)


# ---------------------------------------------------------------------------
# find_bot_pr_sync_callers -- dynamic discovery, not a hardcoded list
# ---------------------------------------------------------------------------


def test_find_callers_discovers_workflows_using_the_action(tmp_path: Path):
    _write_workflow(
        tmp_path,
        "check-price.yml",
        "jobs:\n  check:\n    steps:\n      - uses: ./.github/actions/bot-pr-sync\n",
    )
    _write_workflow(tmp_path, "lint.yml", "jobs:\n  lint:\n    steps:\n      - run: echo hi\n")
    callers = mod.find_bot_pr_sync_callers(tmp_path / ".github" / "workflows")
    assert [p.name for p in callers] == ["check-price.yml"]


def test_find_callers_returns_empty_when_none_use_it(tmp_path: Path):
    _write_workflow(tmp_path, "lint.yml", "jobs:\n  lint:\n    steps:\n      - run: echo hi\n")
    assert mod.find_bot_pr_sync_callers(tmp_path / ".github" / "workflows") == []


# ---------------------------------------------------------------------------
# extract_git_add_paths
# ---------------------------------------------------------------------------


def test_extract_git_add_paths_simple_line():
    text = "run: |\n  git add data/a.json data/b.json || true\n"
    assert mod.extract_git_add_paths(text) == [(2, "data/a.json"), (2, "data/b.json")]


def test_extract_git_add_paths_stops_at_double_ampersand():
    text = "run: |\n  git add data/a.json && echo done\n"
    assert mod.extract_git_add_paths(text) == [(2, "data/a.json")]


def test_extract_git_add_paths_stops_at_stderr_redirect():
    text = "run: |\n  git add data/a.json 2>/dev/null || true\n"
    assert mod.extract_git_add_paths(text) == [(2, "data/a.json")]


def test_extract_git_add_paths_handles_quoted_variable_token():
    text = 'run: |\n  git add "$HEALTH_FILE"\n'
    assert mod.extract_git_add_paths(text) == [(2, "$HEALTH_FILE")]


def test_extract_git_add_paths_multiple_lines_correct_line_numbers():
    text = "run: |\n  echo hi\n  git add data/a.json\n  echo bye\n  git add data/b.json\n"
    assert mod.extract_git_add_paths(text) == [(3, "data/a.json"), (5, "data/b.json")]


# ---------------------------------------------------------------------------
# resolve_variable
# ---------------------------------------------------------------------------


def test_resolve_variable_finds_literal_assignment():
    text = 'HEALTH_FILE="data/tanishq_selfhosted_health.json"\ngit add "$HEALTH_FILE"\n'
    assert mod.resolve_variable(text, "HEALTH_FILE") == "data/tanishq_selfhosted_health.json"


def test_resolve_variable_returns_none_when_not_assigned():
    assert mod.resolve_variable('git add "$MYSTERY_VAR"\n', "MYSTERY_VAR") is None


def test_resolve_variable_returns_none_when_assigned_to_a_further_expansion():
    """A `VAR="$(cmd)"` or `VAR="$OTHER"` assignment isn't a genuine literal
    -- must not be silently trusted as one."""
    text = 'HEALTH_FILE="$(echo data/x.json)"\n'
    assert mod.resolve_variable(text, "HEALTH_FILE") is None


def test_resolve_variable_returns_none_when_ambiguous():
    """Two different literal assignments to the same name in one file --
    can't confidently pick one, must fail closed rather than guess."""
    text = 'X="data/a.json"\nX="data/b.json"\n'
    assert mod.resolve_variable(text, "X") is None


# ---------------------------------------------------------------------------
# check_workflow -- the actual allowlist-compliance decision
# ---------------------------------------------------------------------------

ALLOWLIST = r"^(data/|og\.png$|reports/lighthouse/)"


def test_check_workflow_clean_data_paths_no_violations(tmp_path: Path):
    path = _write_workflow(
        tmp_path,
        "check-price.yml",
        "run: |\n  git add data/forecast.json data/cadence_metrics.json || true\n",
    )
    assert mod.check_workflow(path, ALLOWLIST) == []


def test_check_workflow_flags_readme_outside_allowlist(tmp_path: Path):
    """Reconstructs the actual #1539/#1353 mechanism: README.md added
    alongside legitimate data/ paths."""
    path = _write_workflow(
        tmp_path,
        "check-price.yml",
        "run: |\n  git add data/forecast.json README.md docs/DIRECTION_SIGNAL_STATUS.md || true\n",
    )
    violations = mod.check_workflow(path, ALLOWLIST)
    assert len(violations) == 2
    assert any("README.md" in v for v in violations)
    assert any("docs/DIRECTION_SIGNAL_STATUS.md" in v for v in violations)


def test_check_workflow_flags_glob_pattern_outside_allowlist(tmp_path: Path):
    """#1353's actual diff used `docs/*.md` / `docs/adr/*.md` glob patterns,
    not literal filenames -- must be caught the same way."""
    path = _write_workflow(
        tmp_path,
        "check-price.yml",
        "run: |\n  git add data/forecast.json README.md docs/*.md docs/adr/*.md || true\n",
    )
    violations = mod.check_workflow(path, ALLOWLIST)
    assert len(violations) == 3


def test_check_workflow_resolves_and_allows_a_compliant_variable(tmp_path: Path):
    path = _write_workflow(
        tmp_path,
        "scrape-tanishq-selfhosted.yml",
        'run: |\n  HEALTH_FILE="data/tanishq_selfhosted_health.json"\n  git add "$HEALTH_FILE"\n',
    )
    assert mod.check_workflow(path, ALLOWLIST) == []


def test_check_workflow_flags_unresolvable_variable(tmp_path: Path):
    path = _write_workflow(
        tmp_path,
        "check-price.yml",
        'run: |\n  git add "$SOME_DYNAMIC_VAR"\n',
    )
    violations = mod.check_workflow(path, ALLOWLIST)
    assert len(violations) == 1
    assert "could not be resolved" in violations[0]


def test_check_workflow_flags_blanket_add(tmp_path: Path):
    path = _write_workflow(tmp_path, "check-price.yml", "run: |\n  git add -A\n")
    violations = mod.check_workflow(path, ALLOWLIST)
    assert len(violations) == 1
    assert "blanket" in violations[0]


def test_check_workflow_flags_command_substitution_token(tmp_path: Path):
    path = _write_workflow(
        tmp_path, "check-price.yml", "run: |\n  git add $(find data -name '*.json')\n"
    )
    violations = mod.check_workflow(path, ALLOWLIST)
    assert len(violations) == 1
    assert "unresolved shell expansion" in violations[0]


# ---------------------------------------------------------------------------
# main() -- end-to-end
# ---------------------------------------------------------------------------


def test_main_exits_zero_on_clean_repo(tmp_path: Path, capsys):
    _write_action(tmp_path)
    _write_workflow(
        tmp_path,
        "check-price.yml",
        "jobs:\n  check:\n    steps:\n      - run: |\n          git add data/forecast.json || true\n"
        "      - uses: ./.github/actions/bot-pr-sync\n",
    )
    # main() parses sys.argv directly; invoke via subprocess-free call by patching argv.
    import sys as _sys

    old_argv = _sys.argv
    try:
        _sys.argv = ["check_bot_pr_sync_allowlist.py", "--repo-root", str(tmp_path)]
        exit_code = mod.main()
    finally:
        _sys.argv = old_argv
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "OK" in out


def test_main_exits_one_and_names_the_offending_path_on_a_reconstructed_1539(
    tmp_path: Path, capsys
):
    """AF1b: reconstructs #1539's actual mechanism (README.md added to
    check-price.yml's git-add list) and confirms the check fails, naming
    README.md specifically -- not just failing generically."""
    _write_action(tmp_path)
    _write_workflow(
        tmp_path,
        "check-price.yml",
        "jobs:\n  check:\n    steps:\n"
        "      - name: Commit\n        run: |\n"
        "          git add data/forecast.json README.md docs/DIRECTION_SIGNAL_STATUS.md || true\n"
        "      - uses: ./.github/actions/bot-pr-sync\n",
    )
    import sys as _sys

    old_argv = _sys.argv
    try:
        _sys.argv = ["check_bot_pr_sync_allowlist.py", "--repo-root", str(tmp_path)]
        exit_code = mod.main()
    finally:
        _sys.argv = old_argv
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "FAIL" in out
    assert "README.md" in out
    assert "docs/DIRECTION_SIGNAL_STATUS.md" in out


def test_main_fails_closed_when_no_callers_found(tmp_path: Path, capsys):
    _write_action(tmp_path)
    _write_workflow(tmp_path, "lint.yml", "jobs:\n  lint:\n    steps:\n      - run: echo hi\n")
    import sys as _sys

    old_argv = _sys.argv
    try:
        _sys.argv = ["check_bot_pr_sync_allowlist.py", "--repo-root", str(tmp_path)]
        exit_code = mod.main()
    finally:
        _sys.argv = old_argv
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "zero workflows" in out
