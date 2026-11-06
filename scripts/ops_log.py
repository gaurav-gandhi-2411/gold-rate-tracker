"""The ops log: where system-health alerts go instead of the phone (owner decision 2026-11-06).

ntfy carries gold-price information only. Everything about the system's own health (a stuck bot PR,
a red check, a parked branch, a visitor-count run) is recorded here instead:

    python scripts/ops_log.py --source ci-health --title "Check red" --body "what happened"

`record()` does three things and never raises:
  1. appends a markdown section to $GITHUB_STEP_SUMMARY when that file is set (the run page shows it);
  2. prints `::warning title=ops-alert::<source>: <title> -- <body on one line>`, a workflow
     annotation (the fallback record: scripts/read_ops_log.py --from-annotations walks them);
  3. inside GitHub Actions, appends ONE JSON line to `ops_log.jsonl` on the orphan branch `ops-log`
     through the contents API, so the weekly read is one API call. A failure here prints a one-line
     warning and nothing else. Lines older than 120 days are pruned on each write. Skipped on
     pull_request events, outside Actions, and when OPS_LOG_NO_FILE is set (tests).

The repo is PUBLIC and so is every job summary and annotation: never put a secret, a token, a topic
name or a visitor count in `body`.

Stdlib only: runs on the runner's own python3.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

ANNOTATION_TITLE = "ops-alert"
_MAX_ANNOTATION = 900  # GitHub truncates long annotation messages; keep the one-liner readable
OPS_BRANCH = "ops-log"
OPS_FILE = "ops_log.jsonl"
RETENTION_DAYS = 120
MAX_BODY = 500
MAX_TRIES = 5  # conflict retries; the whole append stays within a few seconds of backoff
_NOT_RETRYABLE = re.compile(r"HTTP (401|403)")

# (args, stdin text or None) -> (returncode, stdout, stderr)
GhFn = Callable[[list[str], str | None], tuple[int, str, str]]


class OpsLogWriteError(RuntimeError):
    """The ops-log file could not be read or written; callers print one line and carry on."""


def one_line(text: str) -> str:
    """Collapse all whitespace (and newlines) so the annotation stays on a single log line."""
    return re.sub(r"\s+", " ", text).strip()


def annotation_line(source: str, title: str, body: str) -> str:
    """The exact `::warning` line; `%`, CR and LF are escaped the way the runner expects."""
    msg = f"{one_line(source)}: {one_line(title)} -- {one_line(body)}"[:_MAX_ANNOTATION]
    msg = msg.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return f"::warning title={ANNOTATION_TITLE}::{msg}"


def summary_section(source: str, title: str, body: str) -> str:
    """The markdown block appended to the job summary."""
    return f"### ops-alert: {one_line(title)}\n\nsource: `{one_line(source)}`\n\n{body.strip()}\n\n"


def run_gh(args: list[str], stdin: str | None = None) -> tuple[int, str, str]:
    """Run `gh api ...` and return (returncode, stdout, stderr); a missing gh is rc 127."""
    env = dict(os.environ)
    if env.get("OPS_LOG_TOKEN"):  # the workflow token, even when a step's GH_TOKEN is a read PAT
        env["GH_TOKEN"] = env["OPS_LOG_TOKEN"]
    try:
        proc = subprocess.run(
            ["gh", "api", *args],
            env=env,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", f"could not run gh ({type(exc).__name__})"
    return proc.returncode, proc.stdout, proc.stderr


def entry_json(source: str, title: str, body: str, env: Mapping[str, str], now: datetime) -> str:
    """One ops-log line. Only fields that are already public in the annotation and the run URL."""
    repo = env.get("GITHUB_REPOSITORY", "")
    run_id = env.get("GITHUB_RUN_ID", "")
    server = env.get("GITHUB_SERVER_URL", "https://github.com")
    entry = {
        "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": one_line(source),
        "title": one_line(title),
        "body": one_line(body)[:MAX_BODY],
        "workflow": env.get("GITHUB_WORKFLOW", ""),
        "run_id": run_id,
        "run_url": f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else "",
    }
    return json.dumps(entry, ensure_ascii=False, separators=(", ", ": "))


def prune(lines: list[str], now: datetime, days: int = RETENTION_DAYS) -> list[str]:
    """Drop lines whose ts is older than `days`; a line that cannot be parsed is kept, not lost."""
    cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    kept = []
    for line in lines:
        try:
            ts = json.loads(line)["ts"]
        except (ValueError, KeyError, TypeError):
            kept.append(line)
            continue
        if not isinstance(ts, str) or ts >= cutoff:
            kept.append(line)
    return kept


def _is_missing(stderr: str) -> bool:
    return "HTTP 404" in stderr


def read_remote(repo: str, gh: GhFn = run_gh) -> tuple[str, str] | None:
    """Return (blob sha, file text) of ops_log.jsonl on the ops-log branch, None if not there yet.

    Any failure other than a 404 raises OpsLogWriteError (the caller must not guess).
    """
    path = f"repos/{repo}/contents/{OPS_FILE}?ref={OPS_BRANCH}"
    rc, out, err = gh([path], None)
    if rc != 0:
        if _is_missing(err):
            return None
        raise OpsLogWriteError(f"read failed: {err.strip()[:200]}")
    try:
        meta = json.loads(out)
        sha = str(meta["sha"])
        if meta.get("encoding") == "base64":
            text = base64.b64decode(meta.get("content", "")).decode("utf-8")
        else:  # over 1 MB the JSON form has no content; ask for the raw bytes instead
            rc, raw, err = gh(["-H", "Accept: application/vnd.github.raw+json", path], None)
            if rc != 0:
                raise OpsLogWriteError(f"raw read failed: {err.strip()[:200]}")
            text = raw
    except (ValueError, KeyError, TypeError) as exc:
        raise OpsLogWriteError("read returned an unreadable response") from exc
    return sha, text


def _ref_exists(repo: str, gh: GhFn) -> bool:
    rc, _, err = gh([f"repos/{repo}/git/ref/heads/{OPS_BRANCH}"], None)
    if rc == 0:
        return True
    if _is_missing(err):
        return False
    raise OpsLogWriteError(f"branch check failed: {err.strip()[:200]}")


def _post(repo: str, kind: str, payload: dict, gh: GhFn) -> dict:
    args = ["-X", "POST", f"repos/{repo}/git/{kind}", "--input", "-"]
    rc, out, err = gh(args, json.dumps(payload))
    if rc != 0:
        raise OpsLogWriteError(f"{kind} failed: {err.strip()[:200]}")
    try:
        return json.loads(out)
    except ValueError as exc:
        raise OpsLogWriteError(f"{kind} returned unreadable JSON") from exc


def _create_branch(repo: str, text: str, message: str, gh: GhFn) -> None:
    """Create the orphan ops-log branch whose first commit holds the file (no parents)."""
    tree = _post(
        repo,
        "trees",
        {"tree": [{"path": OPS_FILE, "mode": "100644", "type": "blob", "content": text}]},
        gh,
    )
    commit = _post(repo, "commits", {"message": message, "tree": tree["sha"], "parents": []}, gh)
    _post(repo, "refs", {"ref": f"refs/heads/{OPS_BRANCH}", "sha": commit["sha"]}, gh)


def append_entry(
    entry: str,
    repo: str,
    message: str,
    gh: GhFn = run_gh,
    sleep: Callable[[float], None] = time.sleep,
    now: datetime | None = None,
) -> None:
    """Append `entry` (one JSON line) to ops_log.jsonl on the ops-log branch; raises on failure.

    Read the current file and sha, prune, PUT with that sha; on a 409/422 sha conflict (another
    writer got in first) read again and retry, up to MAX_TRIES with jittered backoff.
    """
    when = datetime.now(UTC) if now is None else now
    last = "no attempt made"
    for attempt in range(1, MAX_TRIES + 1):
        current = read_remote(repo, gh)
        old = current[1].splitlines() if current else []
        text = "\n".join([*prune(old, when), entry]) + "\n"
        if current is None and not _ref_exists(repo, gh):
            try:
                _create_branch(repo, text, message, gh)
                return
            except OpsLogWriteError as exc:  # another writer may have created it: go round again
                last = str(exc)
        else:
            payload = {
                "message": message,
                "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
                "branch": OPS_BRANCH,
            }
            if current is not None:
                payload["sha"] = current[0]
            args = ["-X", "PUT", f"repos/{repo}/contents/{OPS_FILE}", "--input", "-"]
            rc, _, err = gh(args, json.dumps(payload))
            if rc == 0:
                return
            last = err.strip()[:200]
            if _NOT_RETRYABLE.search(err):
                break
        if attempt < MAX_TRIES:
            sleep(random.uniform(0.5, 1.5) * attempt)
    raise OpsLogWriteError(f"gave up after {MAX_TRIES} tries: {last}")


def _file_append(source: str, title: str, body: str, env: Mapping[str, str], gh: GhFn, out) -> None:
    """Append to the ops-log file when running in Actions with a token; never raises."""
    repo = env.get("GITHUB_REPOSITORY", "")
    token = env.get("OPS_LOG_TOKEN") or env.get("GH_TOKEN") or env.get("GITHUB_TOKEN")
    if (
        env.get("OPS_LOG_NO_FILE")
        or env.get("GITHUB_ACTIONS") != "true"
        or env.get("GITHUB_EVENT_NAME") == "pull_request"  # PR runs must not write repo content
        or not repo
    ):
        return
    if not token:
        print("ops_log: warning: no GH_TOKEN, the ops-log file was not written", file=out)
        return
    try:
        entry = entry_json(source, title, body, env, datetime.now(UTC))
        message = f"ops-log: {one_line(source)}: {one_line(title)}"[:200] + " [skip ci]"
        append_entry(entry, repo, message, gh)
    except Exception as exc:
        print(f"ops_log: warning: ops-log file not written ({exc})", file=out)


def record(
    source: str,
    title: str,
    body: str,
    environ: Mapping[str, str] | None = None,
    stream=None,
    gh: GhFn = run_gh,
) -> str:
    """Write one ops-log entry; return the annotation line. Best effort: never raises."""
    env = os.environ if environ is None else environ
    out = sys.stdout if stream is None else stream
    line = annotation_line(source, title, body)
    summary = env.get("GITHUB_STEP_SUMMARY", "")
    if summary:
        try:
            with Path(summary).open("a", encoding="utf-8") as fh:
                fh.write(summary_section(source, title, body))
        except OSError as exc:
            print(f"ops_log: could not write the job summary: {type(exc).__name__}", file=out)
    print(line, file=out)
    _file_append(source, title, body, env, gh, out)
    return line


def main(argv: list[str] | None = None) -> int:
    """CLI used by workflow steps. Always exits 0: logging must never fail a run."""
    ap = argparse.ArgumentParser(description="Record one ops-log entry (no push is sent).")
    ap.add_argument("--source", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--body", default="")
    ap.add_argument("--no-file", action="store_true", help="skip the ops-log file append")
    args = ap.parse_args(argv)
    env = {**os.environ, "OPS_LOG_NO_FILE": "1"} if args.no_file else None
    record(args.source, args.title, args.body, environ=env)
    return 0


if __name__ == "__main__":
    sys.exit(main())
