"""scripts/check_required_checks_skippable.py -- fail when a REQUIRED check's workflow can be skipped.

Lesson (2026-10-09, docs/SESSION_AUDIT_2026-08.md): scraper-dependency-guard was made a required check
(2026-10-08). The bot's data PRs are pushed with a PAT and a `[skip ci]` commit, which fires NO
`pull_request` workflow, so the required check never reported on them and every bot PR sat at "base branch
policy prohibits the merge" (about 6 h of stale data). The first fix posted a status with `>/dev/null`
and no error handling, so it failed silently (#2588). A required check that does not run on every PR
blocks exactly the PRs it skips; a status post that hides its own errors turns a fix into a silent failure.

What this checks, for each required context (default: the REQUIRED list below, which mirrors branch
protection; `--live` reads the real list with `gh api` and fails if the two differ):
  1. the workflow job that produces it is found (by job id or `name:`);
  2. its workflow's `pull_request` trigger has no `paths` / `paths-ignore` filter;
  3. the job has no job-level `if:` (a skipped job reads as "never ran");
  4. a PR whose commits carry `[skip ci]` can still get the check: the workflow has `workflow_dispatch`
     AND forwards a commit status (`statuses/` in some step), AND the bot-pr-sync action dispatches it
     (`gh workflow run <file>`);
  5. no step posts a status with its output or errors discarded (`>/dev/null`, `|| true`);
  6. a check whose workflow takes a `pr` dispatch input is dispatched WITH `-f pr=` (without it the run
     cannot say which PR to post its status on);
  7. (2026-10-15, bot_pr_workflow_problems) EVERY workflow that creates or updates a bot PR, discovered
     from the workflow files (a step that `uses` bot-pr-sync, runs `gh pr create`, or uses a
     create-pull-request action), satisfies every required context on the PR's current head: a bot-pr-sync
     caller through the action's dispatches (item 4); a workflow with its own PR code by dispatching each
     required check's workflow itself (with `-f pr=` where item 6 applies), unless its PR branch is fresh
     per run (name carries the run id, never force-pushed), where the first push's pull_request run fires
     by itself. Why: docs-refresh force-pushes one reusable branch; a later push's pull_request guard run
     ended `action_required` (waits for a click), so its PR sat BLOCKED for 2 h (#2810) and this control,
     which only looked at the check's own workflow and at bot-pr-sync, stayed green (#2607 -> #2821);
  8. (2026-10-17, same function) EVERY such workflow also merges through the one shared merge path,
     `scripts/bot_pr_merge.sh` (a bot-pr-sync caller through the action, a workflow with its own PR code by
     calling the script itself). Native auto-merge does not re-trigger on statuses forwarded from dispatched
     runs: docs-refresh PRs sat open 80 min, 4 h 53 min and 6 h 30 min with every required context green
     (#2824, #2832, #2845). The one exemption is a DRAFT PR (`gh pr create --draft`), which a human merges
     by design (encrypt-raw-data-migration). The script itself must pin the merge to the verified head
     (`--match-head-commit`), fail loudly (`::error::`), and wait for exactly the REQUIRED contexts.

Surface and blind spots (rule 85a): it reads workflow YAML, not GitHub's runtime behaviour; it cannot see a
repository ruleset or an org-level required workflow; it trusts that the dispatched run's forwarded status
carries the same context name. The live proof is a real bot PR showing the status (see the audit doc).

Exit 0 when every required check is safe, 1 otherwise (each problem printed).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
BOT_ACTION = ROOT / ".github" / "actions" / "bot-pr-sync" / "action.yml"

# Mirrors branch protection's required contexts (GG re-adds scraper-dependency-guard once proven).
REQUIRED = ["lint", "pwa-js", "scraper-dependency-guard"]


def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    # PyYAML reads the bare key `on` as boolean True.
    if True in data and "on" not in data:
        data["on"] = data.pop(True)
    return data


def find_job(context: str) -> tuple[Path, dict] | None:
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        jobs = _load(wf).get("jobs") or {}
        for job_id, job in jobs.items():
            if context in (job_id, (job or {}).get("name")):
                return wf, _load(wf)
    return None


def problems_for(
    context: str, bot_action_text: str, workflows_dir: Path | None = None
) -> list[str]:
    found = find_job(context)
    if not found:
        return [f"{context}: no workflow job produces this check name"]
    wf, data = found
    out: list[str] = []
    on = data.get("on") or {}
    if isinstance(on, str):
        on = {on: None}
    elif isinstance(on, list):
        on = {k: None for k in on}
    pr = on.get("pull_request", "absent")
    if pr == "absent":
        out.append(f"{context}: {wf.name} has no pull_request trigger")
    elif isinstance(pr, dict) and ("paths" in pr or "paths-ignore" in pr):
        out.append(
            f"{context}: {wf.name} pull_request trigger is path-filtered (skipped on other PRs)"
        )
    jobs = data.get("jobs") or {}
    for job_id, job in jobs.items():
        if context in (job_id, (job or {}).get("name")) and (job or {}).get("if") is not None:
            out.append(f"{context}: job {job_id} has a job-level `if:` (can be skipped)")
    text = wf.read_text(encoding="utf-8")
    if "workflow_dispatch" not in on:
        out.append(
            f"{context}: {wf.name} has no workflow_dispatch, so a [skip ci] bot PR cannot get it"
        )
    if "statuses/" not in text:
        out.append(f"{context}: {wf.name} never forwards a commit status (statuses/ API)")
    if not re.search(r"gh workflow run\s+" + re.escape(wf.name), bot_action_text):
        out.append(f"{context}: bot-pr-sync does not dispatch {wf.name}")
    elif _takes_pr_input(on) and not _dispatch_passes_pr(bot_action_text, wf.name):
        out.append(f"{context}: bot-pr-sync dispatches {wf.name} without `-f pr=`")
    for step_match in re.finditer(r"statuses/[^\n]*(?:\n[^\n]*){0,6}", text):
        block = step_match.group(0)
        if ">/dev/null" in block or "|| true" in block:
            out.append(f"{context}: {wf.name} posts a status with output/errors discarded")
    return out


def _takes_pr_input(on: dict) -> bool:
    wd = on.get("workflow_dispatch")
    return isinstance(wd, dict) and "pr" in (wd.get("inputs") or {})


def _dispatch_passes_pr(text: str, wf_name: str) -> bool:
    """Every `gh workflow run <wf>` line in the text carries `-f pr=`."""
    lines = [
        ln for ln in text.splitlines() if re.search(r"gh workflow run\s+" + re.escape(wf_name), ln)
    ]
    return bool(lines) and all(re.search(r"-f\s+pr=", ln) for ln in lines)


def _step_scripts(data: dict) -> tuple[str, list[str]]:
    """(all `run:` text with comment lines dropped, all `uses:` values) of a workflow's jobs."""
    runs: list[str] = []
    uses: list[str] = []
    for job in (data.get("jobs") or {}).values():
        for step in (job or {}).get("steps") or []:
            if not isinstance(step, dict):
                continue
            if str(step.get("if", "")).strip().lower() == "false":
                continue  # a step that never runs does not count
            if "run" in step:
                runs.append(
                    "\n".join(
                        ln for ln in str(step["run"]).splitlines() if not ln.strip().startswith("#")
                    )
                )
            if "uses" in step:
                uses.append(str(step["uses"]))
    return "\n".join(runs), uses


def bot_pr_workflows(workflows_dir: Path | None = None) -> dict[str, str]:
    """Workflows that create or update a bot PR, found from the files: name -> "action" | "own"."""
    found: dict[str, str] = {}
    for wf in sorted((workflows_dir or WORKFLOWS).glob("*.yml")):
        run, uses = _step_scripts(_load(wf))
        if any(u.endswith("actions/bot-pr-sync") for u in uses):
            found[wf.name] = "action"
        elif re.search(r"\bgh pr create\b", run) or any("create-pull-request" in u for u in uses):
            found[wf.name] = "own"
    return found


def _fresh_branch_per_run(run: str) -> bool:
    """The PR branch name carries the run id and nothing force-pushes it."""
    named = re.search(r"BRANCH=\"?[^\n]*(?:GITHUB_RUN_ID|github\.run_id)", run)
    forced = re.search(r"git push[^\n]*(?:--force\b|--force-with-lease|\s-f\b)", run)
    return bool(named) and not forced


MERGE_SCRIPT = "scripts/bot_pr_merge.sh"
# An executable call: `[VAR=value ...] bash <path>/bot_pr_merge.sh PR BRANCH "$(git rev-parse HEAD)"`, not in a
# comment, an echo or a heredoc, not followed by `|| true`, with the commit this run pushed as the third argument.
_MERGE_CALL = re.compile(
    r"^\s*(?:\w+=\S+\s+)*bash\s+\S*scripts/bot_pr_merge\.sh\"?\s+\S+\s+\S+\s+\"?\$\(git rev-parse HEAD\)\"?\s*$"
)


def _calls_merge_script(text: str) -> bool:
    return any(
        _MERGE_CALL.match(ln) and "|| true" not in ln
        for ln in text.splitlines()
        if not ln.strip().startswith("#")
    )


def _human_merged_draft(run: str) -> bool:
    return any(
        re.match(r"^\s*(?:ci_retry\s+)?gh\s+pr\s+create\b[^\n]*--draft\b", ln)
        for ln in run.splitlines()
        if not ln.strip().startswith("#")
    )


def merge_script_problems(required: list[str], script_text: str | None = None) -> list[str]:
    """Item 8: the shared merge script exists, pins the head, fails loudly, waits for REQUIRED."""
    if script_text is None:
        path = ROOT / MERGE_SCRIPT
        if not path.is_file():
            return [f"{MERGE_SCRIPT} does not exist"]
        script_text = path.read_text(encoding="utf-8")
    out: list[str] = []
    if "--match-head-commit" not in script_text:
        out.append(
            f"{MERGE_SCRIPT} does not pin the merge to the verified head (--match-head-commit)"
        )
    if "::error::" not in script_text:
        out.append(f"{MERGE_SCRIPT} never fails loudly (::error::)")
    m = re.search(r'REQUIRED_CONTEXTS="\$\{REQUIRED_CONTEXTS:-([^}]*)\}"', script_text)
    if not m:
        out.append(f"{MERGE_SCRIPT} has no default REQUIRED_CONTEXTS")
    elif set(m.group(1).split()) != set(required):
        out.append(
            f"{MERGE_SCRIPT} waits for {sorted(set(m.group(1).split()))} but branch protection "
            f"requires {sorted(required)}"
        )
    return out


def bot_pr_workflow_problems(
    required: list[str], bot_action_text: str, workflows_dir: Path | None = None
) -> list[str]:
    """Items 7 and 8: every bot-PR workflow satisfies every required context on its PR's head
    and merges through the shared merge path."""
    d = workflows_dir or WORKFLOWS
    wanted: dict[str, str] = {}  # required workflow file -> a context it produces
    for ctx in required:
        found = find_job(ctx)
        if found:
            wanted.setdefault(found[0].name, ctx)
    out: list[str] = []
    if not _calls_merge_script(bot_action_text):
        out.append(f"bot-pr-sync does not merge through {MERGE_SCRIPT}")
    for name, kind in bot_pr_workflows(d).items():
        if kind == "action":
            for fname, ctx in wanted.items():
                if not re.search(r"gh workflow run\s+" + re.escape(fname), bot_action_text):
                    out.append(f"{name}: calls bot-pr-sync, which never dispatches {fname} ({ctx})")
            continue
        run, _ = _step_scripts(_load(d / name))
        if not _calls_merge_script(run) and not _human_merged_draft(run):
            out.append(
                f"{name}: creates a bot PR but does not merge it through {MERGE_SCRIPT}; native "
                "auto-merge does not re-trigger on forwarded statuses and the PR can sit open for hours"
            )
        if _fresh_branch_per_run(run):
            continue
        for fname, ctx in wanted.items():
            line = next(
                (
                    ln
                    for ln in run.splitlines()
                    if re.search(r"gh workflow run\s+" + re.escape(fname), ln)
                ),
                None,
            )
            if line is None:
                out.append(
                    f"{name}: creates a bot PR but never dispatches {fname} (required check "
                    f"{ctx}); a force-pushed branch gets `action_required` and the PR sits BLOCKED"
                )
                continue
            wf_on = (_load(d / fname).get("on")) or {}
            if (
                isinstance(wf_on, dict)
                and _takes_pr_input(wf_on)
                and not re.search(r"-f\s+pr=", line)
            ):
                out.append(f"{name}: dispatches {fname} ({ctx}) without `-f pr=`")
    return out


def live_required() -> list[str]:
    res = subprocess.run(
        ["gh", "api", "repos/{owner}/{repo}/branches/master/protection/required_status_checks"],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return sorted(json.loads(res.stdout)["contexts"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--live", action="store_true", help="also compare REQUIRED with branch protection"
    )
    args = ap.parse_args()
    bot = BOT_ACTION.read_text(encoding="utf-8")
    bad: list[str] = []
    for ctx in REQUIRED:
        bad += problems_for(ctx, bot)
    bad += bot_pr_workflow_problems(REQUIRED, bot)
    bad += merge_script_problems(REQUIRED)
    if args.live:
        live = live_required()
        # A live subset is fine (GG may have un-required one temporarily); a live context we do not
        # cover is not.
        extra = [c for c in live if c not in REQUIRED]
        if extra:
            bad.append(f"branch protection requires {extra} which REQUIRED here does not cover")
    for line in bad:
        print("FAIL", line)
    if not bad:
        names = ", ".join(sorted(bot_pr_workflows()))
        print(f"ok: {len(REQUIRED)} required checks cannot be skipped by paths or [skip ci]")
        print(f"ok: every bot-PR workflow satisfies them: {names}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
