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
  5. no step posts a status with its output or errors discarded (`>/dev/null`, `|| true`).

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
    for step_match in re.finditer(r"statuses/[^\n]*(?:\n[^\n]*){0,6}", text):
        block = step_match.group(0)
        if ">/dev/null" in block or "|| true" in block:
            out.append(f"{context}: {wf.name} posts a status with output/errors discarded")
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
        print(f"ok: {len(REQUIRED)} required checks cannot be skipped by paths or [skip ci]")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
