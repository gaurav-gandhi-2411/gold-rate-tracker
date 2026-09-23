"""scripts/prepare_direction_eval_publish.py — make published direction numbers
monotone: a run can never replace numbers that came from newer code or a later
run.

Incident (2026-09-23): two pushes to ml/direction landed minutes apart. Their
eval-direction runs are serialized, but each checks out its own commit. The
first (older, leaky evaluator) published; the second (fixed evaluator) then
failed its bot rebase on the same two generated files, so the site showed the
older computation for ~10 minutes, until a third run replaced it.

Run by eval-direction.yml after `python -m ml.direction.evaluate`, from the
run's own checkout (GITHUB_SHA). It:
  1. fetches origin/master;
  2. decides whether this run may publish (decide_publish below): not if
     master already has a newer evaluator commit (that push triggers its own
     run), and not if master's published numbers come from newer code or,
     for the same code, from a later run;
  3. if it may, moves the working tree to origin/master and writes this run's
     baseline JSON plus master's history log with this run's record appended,
     so the publish commit sits on top of master and the bot rebase cannot
     conflict.
Prints `publish=true|false` for $GITHUB_OUTPUT. Any git failure exits non-zero
(fail closed: nothing is published on an error).
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

BASELINE = Path("data/direction_baseline.json")
HISTORY = Path("data/direction_eval_history.jsonl")
# Paths whose change means "the evaluator's output would differ".
EVALUATOR_PATHS = ["ml/direction/", "ml/requirements-inference.lock"]


@dataclass(frozen=True)
class Decision:
    publish: bool
    reason: str


def decide_publish(
    *,
    run_sha: str,
    run_is_on_master: bool,
    newer_evaluator_commits: list[str],
    master_source_sha: str | None,
    master_source_is_descendant_of_run: bool,
    run_generated_at: str,
    master_generated_at: str | None,
) -> Decision:
    """Pure decision. ISO-8601 UTC timestamps compare correctly as strings."""
    if not run_is_on_master:
        return Decision(False, f"run commit {run_sha[:8]} is not on master")
    if newer_evaluator_commits:
        return Decision(
            False,
            f"master has {len(newer_evaluator_commits)} newer evaluator commit(s) "
            f"({newer_evaluator_commits[0][:8]}...): its own run will publish",
        )
    if master_source_sha and master_source_sha != run_sha and master_source_is_descendant_of_run:
        return Decision(False, f"master's numbers come from newer code {master_source_sha[:8]}")
    if (
        master_generated_at
        and master_source_sha in (None, run_sha)
        and master_generated_at >= run_generated_at
    ):
        return Decision(False, "master's numbers come from a later run of the same code")
    return Decision(True, "this run is the newest computation")


def _git(*args: str) -> str:
    out = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout


def main() -> int:
    run_sha = _git("rev-parse", "HEAD").strip()
    ours = json.loads(BASELINE.read_text(encoding="utf-8"))
    our_history_lines = HISTORY.read_text(encoding="utf-8").splitlines()
    our_record = our_history_lines[-1]
    if json.loads(our_record).get("generated_at_utc") != ours.get("generated_at_utc"):
        raise SystemExit("last history record is not this run's -- refusing to publish")

    _git("fetch", "origin", "master")
    newer = [
        c
        for c in _git(
            "log", "--format=%H", f"{run_sha}..origin/master", "--", *EVALUATOR_PATHS
        ).split()
        if c
    ]
    on_master_rc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", run_sha, "origin/master"],
        capture_output=True,
        check=False,
    ).returncode
    if on_master_rc not in (0, 1):
        raise SystemExit("git merge-base --is-ancestor failed -- refusing to publish")
    master_json = json.loads(_git("show", f"origin/master:{BASELINE.as_posix()}"))
    master_src = master_json.get("source_sha")
    descendant = False
    if master_src and master_src != run_sha:
        rc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", run_sha, master_src],
            capture_output=True,
            check=False,
        ).returncode
        if rc not in (0, 1):
            raise SystemExit("git merge-base --is-ancestor failed -- refusing to publish")
        descendant = rc == 0
    decision = decide_publish(
        run_sha=run_sha,
        run_is_on_master=on_master_rc == 0,
        newer_evaluator_commits=newer,
        master_source_sha=master_src,
        master_source_is_descendant_of_run=descendant,
        run_generated_at=str(ours.get("generated_at_utc")),
        master_generated_at=master_json.get("generated_at_utc"),
    )
    print(f"decision: publish={decision.publish} -- {decision.reason}", file=sys.stderr)
    if not decision.publish:
        print("publish=false")
        return 0

    ours_text = BASELINE.read_text(encoding="utf-8")
    master_history = _git("show", f"origin/master:{HISTORY.as_posix()}")
    # Our outputs are saved above; discard them in the tree and move to master.
    _git("checkout", "--quiet", "--force", "--detach", "origin/master")
    BASELINE.write_text(ours_text, encoding="utf-8")
    HISTORY.write_text(master_history.rstrip("\n") + "\n" + our_record + "\n", encoding="utf-8")
    print("publish=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
