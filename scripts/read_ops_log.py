"""List the ops-log entries (scripts/ops_log.py annotations) from the last N days.

    python scripts/read_ops_log.py [--days 7] [--repo owner/name] [--workflow name-substring]

Walks this repo's workflow runs -> jobs -> check-run annotations through `gh api` and prints every
annotation whose title is exactly `ops-alert`: date (UTC), workflow, message.

Fails closed: if `gh` is missing or any API call fails, it prints what failed and exits 2. An empty
list is only ever printed after every run in the window was read, so "nothing" means nothing.
Cost: one runs call per 100 runs, one jobs call per run, one annotations call per job.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

DEFAULT_REPO = "gaurav-gandhi-2411/gold-rate-tracker"
ANNOTATION_TITLE = "ops-alert"

GhFn = Callable[[str], list[dict]]


class OpsLogError(RuntimeError):
    """A gh/API call failed; the listing cannot be trusted."""


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


def _items(pages: list[dict] | list[list], key: str | None) -> list[dict]:
    """Flatten paginated results: pages of {key: [...]} (or plain list pages when key is None)."""
    out: list[dict] = []
    for page in pages:
        chunk = page if key is None else page.get(key) if isinstance(page, dict) else None
        if chunk is None:
            raise OpsLogError(f"unexpected API response shape (missing {key!r})")
        out.extend(chunk)
    return out


def collect(
    repo: str, since: datetime, gh: GhFn = gh_api, workflow: str = ""
) -> list[tuple[str, str, str]]:
    """Return (date, workflow, message) for every ops-alert annotation on runs since `since`."""
    stamp = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    runs = _items(
        gh(f"repos/{repo}/actions/runs?per_page=100&created=%3E%3D{stamp}"), "workflow_runs"
    )
    found: list[tuple[str, str, str]] = []
    for run in runs:
        name = str(run.get("name", ""))
        if workflow and workflow.lower() not in name.lower():
            continue
        jobs = _items(gh(f"repos/{repo}/actions/runs/{run['id']}/jobs?per_page=100"), "jobs")
        for job in jobs:
            if job.get("conclusion") == "skipped":
                continue
            notes = _items(
                gh(f"repos/{repo}/check-runs/{job['id']}/annotations?per_page=100"), None
            )
            for note in notes:
                if note.get("title") == ANNOTATION_TITLE:
                    when = str(run.get("created_at", ""))[:16].replace("T", " ")
                    found.append((when, name, str(note.get("message", ""))))
    return sorted(found)


def main(argv: list[str] | None = None) -> int:
    """CLI entry. Exit 0 listing printed, 2 if the API could not be read."""
    ap = argparse.ArgumentParser(
        description="List ops-alert annotations from recent workflow runs."
    )
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--workflow", default="", help="only workflows whose name contains this")
    args = ap.parse_args(argv)
    since = datetime.now(UTC) - timedelta(days=args.days)
    try:
        rows = collect(args.repo, since, workflow=args.workflow)
    except OpsLogError as exc:
        print(f"read_ops_log: FAILED, the list below would be incomplete: {exc}", file=sys.stderr)
        return 2
    if not rows:
        print(f"ops log: no ops-alert entries in the last {args.days} days (all runs read)")
        return 0
    print(f"ops log: {len(rows)} entries in the last {args.days} days (UTC)")
    for when, name, message in rows:
        print(f"{when}  {name}  {message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
