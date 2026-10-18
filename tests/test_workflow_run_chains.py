"""Every workflow_run chain needs a dispatch path for the runs a bot starts.

2026-10-18: `workflow_run` does not fire for runs created with GITHUB_TOKEN. Check Gold Price runs dispatched by
github-actions[bot] (the data syncs) were followed by neither docs-refresh nor generate-og-image (0 of 85, counted
per actor with gh api), while every schedule, push and owner-dispatched run was. The fix is a dispatch from
scripts/bot_pr_merge.sh after the producing PR merges; this file fails a chain that lacks one, so a new
workflow_run workflow cannot repeat the bug. The chains are enumerated from the workflow files, not a hand list."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
SCRIPT = ROOT / "scripts" / "bot_pr_merge.sh"


def load_workflows(directory: Path) -> dict[str, dict]:
    out = {}
    for p in sorted(directory.glob("*.yml")):
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
        on = d.get("on", d.get(True)) or {}  # YAML 1.1 reads a bare `on` key as boolean True
        out[p.name] = {"name": d.get("name"), "on": on if isinstance(on, dict) else {}, "doc": d}
    return out


def chains(wfs: dict[str, dict]) -> list[tuple[str, list[str]]]:
    """(downstream file, upstream workflow names) for every workflow with an on.workflow_run trigger."""
    return [
        (f, w["on"]["workflow_run"]["workflows"])
        for f, w in wfs.items()
        if "workflow_run" in w["on"]
    ]


def chain_problems(wfs: dict[str, dict], merge_script: str) -> list[str]:
    by_name = {w["name"]: f for f, w in wfs.items()}
    problems = []
    for down, ups in chains(wfs):
        for up in ups:
            if up not in by_name:
                problems.append(f"{down}: upstream {up!r} is not a workflow in this repo")
                continue
            # an upstream that can be dispatched can be started by a bot, and a bot-made run fires no workflow_run
            if "workflow_dispatch" not in wfs[by_name[up]]["on"]:
                continue
            if "workflow_dispatch" not in wfs[down]["on"]:
                problems.append(
                    f"{down}: follows {up!r} (dispatchable) but cannot itself be dispatched"
                )
            if not re.search(rf"gh workflow run {re.escape(down)}\b", merge_script):
                problems.append(
                    f"{down}: follows {up!r} (dispatchable) but scripts/bot_pr_merge.sh never dispatches it"
                )
        for job, spec in (wfs[down]["doc"].get("jobs") or {}).items():
            cond = str(spec.get("if", ""))
            if (
                "workflow_run.conclusion" in cond
                and "github.event_name != 'workflow_run'" not in cond
            ):
                problems.append(
                    f"{down}: job {job} requires a workflow_run conclusion, so a dispatch would skip it"
                )
    return problems


def test_the_real_chains_are_enumerated_from_the_files():
    found = {f: ups for f, ups in chains(load_workflows(WORKFLOWS))}
    assert set(found) == {"docs-refresh.yml", "generate-og-image.yml"}, found
    assert "Check Gold Price" in found["generate-og-image.yml"]


def test_every_workflow_run_chain_has_a_dispatch_path_for_bot_started_upstream_runs():
    problems = chain_problems(load_workflows(WORKFLOWS), SCRIPT.read_text(encoding="utf-8"))
    assert problems == []


def test_the_check_fails_on_the_setup_before_the_fix():
    """The pre-#2933 state: bot_pr_merge.sh had no dispatch at all and generate-og-image could not be dispatched."""
    wfs = load_workflows(WORKFLOWS)
    wfs["generate-og-image.yml"]["on"] = {"workflow_run": {"workflows": ["Check Gold Price"]}}
    wfs["generate-og-image.yml"]["doc"] = {
        "jobs": {"generate": {"if": "${{ github.event.workflow_run.conclusion == 'success' }}"}}
    }
    problems = chain_problems(wfs, "gh pr merge 1 --squash\n")
    text = "\n".join(problems)
    assert (
        "docs-refresh.yml: follows 'Check Gold Price' (dispatchable) but scripts/bot_pr_merge.sh never"
        in text
    )
    assert (
        "generate-og-image.yml: follows 'Check Gold Price' (dispatchable) but cannot itself be dispatched"
        in text
    )
    assert (
        "generate-og-image.yml: follows 'Check Gold Price' (dispatchable) but scripts/bot_pr_merge.sh never"
        in text
    )
    assert "generate-og-image.yml: job generate requires a workflow_run conclusion" in text


def test_a_new_workflow_run_workflow_without_a_dispatch_path_is_caught():
    wfs = load_workflows(WORKFLOWS)
    wfs["new-follower.yml"] = {
        "name": "New follower",
        "on": {"workflow_run": {"workflows": ["Check Gold Price"]}, "workflow_dispatch": None},
        "doc": {"jobs": {}},
    }
    problems = chain_problems(wfs, SCRIPT.read_text(encoding="utf-8"))
    assert problems == [
        "new-follower.yml: follows 'Check Gold Price' (dispatchable) but scripts/bot_pr_merge.sh never dispatches it"
    ]


def test_an_unknown_upstream_name_is_a_problem_not_a_pass():
    wfs = load_workflows(WORKFLOWS)
    wfs["x.yml"] = {
        "name": "X",
        "on": {"workflow_run": {"workflows": ["No Such Workflow"]}},
        "doc": {},
    }
    assert any("not a workflow in this repo" in p for p in chain_problems(wfs, ""))


def test_an_upstream_that_cannot_be_dispatched_needs_no_dispatch_path():
    wfs = load_workflows(WORKFLOWS)
    wfs["up.yml"] = {"name": "Up", "on": {"schedule": [{"cron": "0 0 * * *"}]}, "doc": {}}
    wfs["down.yml"] = {
        "name": "Down",
        "on": {"workflow_run": {"workflows": ["Up"]}},
        "doc": {"jobs": {}},
    }
    assert not [p for p in chain_problems(wfs, "") if p.startswith("down.yml")]
