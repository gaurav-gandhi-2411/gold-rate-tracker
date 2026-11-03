"""Every bot branch a workflow opens PRs from, read from the workflow files (never a hand list).

Used by scripts/bot_pr_sweep.sh, which merges a stranded bot PR or pages about it. A branch is found
in either of the two ways the repo opens bot PRs:
  * a step that uses ./.github/actions/bot-pr-sync: its ``with.branch``; the files such a PR may
    touch are the action's own allowlist (the ``BAD_FILES`` pattern in action.yml);
  * a step whose script calls scripts/bot_pr_merge.sh itself (docs-refresh): every ``bot/<name>``
    literal in that script; the files it may touch are exactly the paths its job ``git add``s.

Output, one line per branch: ``branch<TAB>workflow[,workflow...]<TAB>allowed-files regex``.
Exit 2 (and print why) if a branch's allowed files cannot be derived, or nothing is found: the sweep
then pages instead of guessing. tests/test_bot_pr_sweep.py fails if a ``bot/<name>`` named anywhere
in a workflow is missing from this list.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

BRANCH = re.compile(r"\bbot/[A-Za-z0-9._-]+")
ACTION_ALLOW = re.compile(r"BAD_FILES=.*?grep -v -E '([^']+)'")


def _action_allow(root: Path) -> str | None:
    text = (root / ".github" / "actions" / "bot-pr-sync" / "action.yml").read_text(encoding="utf-8")
    m = ACTION_ALLOW.search(text)
    return m.group(1) if m else None


def _git_add_paths(job: dict) -> list[str]:
    paths: list[str] = []
    for step in job.get("steps") or []:
        for line in str(step.get("run") or "").splitlines():
            line = line.strip()
            if line.startswith("git add "):
                paths += [p for p in line[len("git add ") :].split() if not p.startswith("-")]
    return paths


def branches(root: Path) -> dict[str, dict]:
    """{branch: {"workflows": [...], "allow": regex or None}}."""
    found: dict[str, dict] = {}
    sync_allow = _action_allow(root)

    def add(branch: str, wf: str, allow: str | None) -> None:
        entry = found.setdefault(branch, {"workflows": [], "allow": allow})
        if wf not in entry["workflows"]:
            entry["workflows"].append(wf)
        if entry["allow"] != allow:  # two producers with different allowlists: refuse to guess
            entry["allow"] = None

    for path in sorted((root / ".github" / "workflows").glob("*.yml")):
        wf = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for job in (wf.get("jobs") or {}).values():
            for step in job.get("steps") or []:
                if str(step.get("uses", "")).endswith("/bot-pr-sync"):
                    b = str((step.get("with") or {}).get("branch", ""))
                    if BRANCH.fullmatch(b):
                        add(b, path.name, sync_allow)
                elif "bot_pr_merge.sh" in str(step.get("run") or ""):
                    adds = _git_add_paths(job)
                    allow = (
                        "^(" + "|".join(re.escape(p) + "$" for p in adds) + ")" if adds else None
                    )
                    for b in sorted(set(BRANCH.findall(str(step["run"])))):
                        add(b, path.name, allow)
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = ap.parse_args(argv)
    try:
        found = branches(args.root)
    except (OSError, yaml.YAMLError) as exc:
        print(f"cannot read the workflows: {exc}", file=sys.stderr)
        return 2
    if not found:
        print("no bot branch found in any workflow", file=sys.stderr)
        return 2
    bad = sorted(b for b, e in found.items() if not e["allow"])
    if bad:
        print(f"no allowed-files list for: {', '.join(bad)}", file=sys.stderr)
        return 2
    for b, e in sorted(found.items()):
        print(f"{b}\t{','.join(e['workflows'])}\t{e['allow']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
