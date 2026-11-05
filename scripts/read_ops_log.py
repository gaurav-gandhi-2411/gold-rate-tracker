"""List the ops-log entries (scripts/ops_log.py annotations) from the last N days.

    python scripts/read_ops_log.py [--days 7] [--repo owner/name] [--workflow file-substring]

Only workflows that can emit an ops-alert are read. They are derived from the workflow files: any
.github/workflows/*.yml|*.yaml that mentions ops_log.py, the bot-pr-sync composite action,
bot_branch_push.sh or ml.notifications (the last two reach ops_log.py indirectly).

For each of them: /actions/workflows/<file>/runs?created=>=<since> -> jobs -> check-run annotations,
printing every annotation whose title is exactly `ops-alert`: date (UTC), workflow, message.

Why per workflow: the repo-wide runs list with created= is capped by GitHub at 1000 results (7 days
held about 2500), so a repo-wide walk silently returns only the newest 1000. A single workflow stays
far under the cap, and the reader still checks each workflow's reported total_count against the rows
it read: any mismatch (or a total at the 1000 cap) fails closed, naming the workflow.

Fails closed: if `gh` is missing, any API call fails, or a count does not match, it says what and
exits 2. An empty list is printed only when every listed workflow was read in full.
Cost: per workflow one runs call per 100 runs; then one jobs call per run and one annotations call
per non-skipped job. check-price (every ~3 h plus pushes) is the bulk; expect a few hundred calls
for 7 days and a couple of minutes.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

DEFAULT_REPO = "gaurav-gandhi-2411/gold-rate-tracker"
ANNOTATION_TITLE = "ops-alert"
RUNS_API_CAP = 1000  # GitHub returns at most this many results for a filtered runs query
ROOT = Path(__file__).resolve().parent.parent
# A workflow that mentions any of these can print an ops-alert annotation.
EMITTER_MARKERS = ("ops_log.py", "bot-pr-sync", "bot_branch_push", "ml.notifications")

GhFn = Callable[[str], list[dict]]


class OpsLogError(RuntimeError):
    """A gh/API call failed or a count did not match; the listing cannot be trusted."""


def gh_api(path: str) -> list[dict]:
    """GET `path` with `gh api --paginate` and return one dict per page (decoded JSON)."""
    try:
        proc = subprocess.run(
            ["gh", "api", "--paginate", "--slurp", path],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OpsLogError(f"could not run gh ({type(exc).__name__}) for {path}") from exc
    if proc.returncode != 0:
        raise OpsLogError(
            f"gh api {path} failed (exit {proc.returncode}): {proc.stderr.strip()[:300]}"
        )
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise OpsLogError(f"gh api {path} returned unreadable JSON") from exc
    return data if isinstance(data, list) else [data]


def _items(pages: list, key: str | None) -> list[dict]:
    """Flatten paginated results: pages of {key: [...]} (or plain list pages when key is None)."""
    out: list[dict] = []
    for page in pages:
        chunk = page if key is None else page.get(key) if isinstance(page, dict) else None
        if chunk is None:
            raise OpsLogError(f"unexpected API response shape (missing {key!r})")
        out.extend(chunk)
    return out


def emitting_workflows(root: Path = ROOT) -> list[str]:
    """File names of the workflows that can emit an ops-alert, derived from the workflow files."""
    found = []
    for p in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        if p.suffix not in (".yml", ".yaml"):
            continue
        text = p.read_text(encoding="utf-8")
        if any(m in text for m in EMITTER_MARKERS):
            found.append(p.name)
    if not found:
        raise OpsLogError("no workflow mentions ops_log.py: the emitter scan found nothing")
    return found


def _total(pages: list, what: str) -> int:
    total = pages[0].get("total_count") if pages and isinstance(pages[0], dict) else None
    if not isinstance(total, int):
        raise OpsLogError(f"{what}: the API gave no total_count, cannot confirm completeness")
    return total


def collect(
    repo: str,
    since: datetime,
    workflows: list[str],
    gh: GhFn = gh_api,
) -> list[tuple[str, str, str]]:
    """Return (date, workflow, message) for every ops-alert annotation on runs since `since`."""
    stamp = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    found: list[tuple[str, str, str]] = []
    for wf in workflows:
        pages = gh(f"repos/{repo}/actions/workflows/{wf}/runs?per_page=100&created=%3E%3D{stamp}")
        runs = _items(pages, "workflow_runs")
        total = _total(pages, wf)
        if total >= RUNS_API_CAP or total != len(runs):
            raise OpsLogError(
                f"{wf}: API reports {total} runs, read {len(runs)} (cap {RUNS_API_CAP}); "
                "narrow --days"
            )
        for run in runs:
            jpages = gh(f"repos/{repo}/actions/runs/{run['id']}/jobs?per_page=100")
            jobs = _items(jpages, "jobs")
            if _total(jpages, f"{wf} run {run['id']} jobs") != len(jobs):
                raise OpsLogError(f"{wf} run {run['id']}: job list incomplete")
            for job in jobs:
                if job.get("conclusion") == "skipped":
                    continue
                notes = _items(
                    gh(f"repos/{repo}/check-runs/{job['id']}/annotations?per_page=100"), None
                )
                for note in notes:
                    if note.get("title") == ANNOTATION_TITLE:
                        when = str(run.get("created_at", ""))[:16].replace("T", " ")
                        found.append((when, wf, str(note.get("message", ""))))
    return sorted(found)


def main(argv: list[str] | None = None) -> int:
    """CLI entry. Exit 0 listing printed, 2 if the API could not be read in full."""
    ap = argparse.ArgumentParser(
        description="List ops-alert annotations from recent workflow runs."
    )
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--workflow", default="", help="only workflow files containing this text")
    args = ap.parse_args(argv)
    since = datetime.now(UTC) - timedelta(days=args.days)
    try:
        workflows = [w for w in emitting_workflows() if re.search(re.escape(args.workflow), w)]
        if not workflows:
            raise OpsLogError(f"no emitting workflow matches {args.workflow!r}")
        rows = collect(args.repo, since, workflows)
    except OpsLogError as exc:
        print(f"read_ops_log: FAILED, a list would be incomplete: {exc}", file=sys.stderr)
        return 2
    scope = f"{len(workflows)} emitting workflows, every run in the window read"
    if not rows:
        print(f"ops log: no ops-alert entries in the last {args.days} days ({scope})")
        return 0
    print(f"ops log: {len(rows)} entries in the last {args.days} days (UTC; {scope})")
    for when, name, message in rows:
        print(f"{when}  {name}  {message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
