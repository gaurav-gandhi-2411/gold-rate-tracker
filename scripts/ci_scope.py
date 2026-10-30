"""Decide whether a lint run may take the fast data-only path: `full` or `data-only`.

    python3 scripts/ci_scope.py --changes changes.txt   # `git diff --name-status BASE HEAD`

On 2026-10-29 an unpinned pandas-stubs release turned mypy red, and because the required `lint` job
ran every code check on bot data PRs too, no data reached the site for ~19.5 h. A PR that changes
only data cannot break the code, so its `lint` run may validate the data instead (schema, ranges,
timestamps, encryption pairs: scripts/validate_data.py) and report the same required context.

`data-only` needs EVERY change to be:
- added or modified (any delete, rename or copy -> full);
- under the path allowlist bot-pr-sync itself enforces before pushing a bot commit, read live from
  .github/actions/bot-pr-sync/action.yml (never a copy here);
- a data file by extension: .json, .jsonl, .enc, .png. Code, workflows, config, tests, docs (.md),
  parquet (validated only by the full suite) and anything unrecognised -> full.
Anything that cannot be decided (no changes, unreadable input, allowlist not found) -> full. This
script only ever widens to `full`; it can never turn a code change into a data-only run.

Prints `mode=<full|data-only>` (for $GITHUB_OUTPUT) and one reason line. Exit 0 always.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ACTION = ROOT / ".github/actions/bot-pr-sync/action.yml"
DATA_SUFFIXES = {".json", ".jsonl", ".enc", ".png"}
_GUARD = re.compile(r"""grep -v -E '([^']+)'""")


def allowlist_regex(action: Path = ACTION) -> re.Pattern[str] | None:
    """The regex on bot-pr-sync's BAD_FILES guard line, or None if it cannot be found."""
    try:
        text = action.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if "BAD_FILES=" in line:
            m = _GUARD.search(line)
            if m:
                return re.compile(m.group(1))
    return None


def decide(changes: str, allow: re.Pattern[str] | None) -> tuple[str, str]:
    if allow is None:
        return "full", "bot-pr-sync allowlist not found"
    rows = [r.split("\t") for r in changes.splitlines() if r.strip()]
    if not rows:
        return "full", "no changed files"
    for row in rows:
        status, paths = row[0], row[1:]
        if status not in ("A", "M") or len(paths) != 1:
            return "full", f"{status} {' '.join(paths)}: only added/modified files can be data-only"
        path = paths[0]
        if not allow.match(path):
            return "full", f"{path}: outside the data allowlist"
        if Path(path).suffix.lower() not in DATA_SUFFIXES:
            return "full", f"{path}: not a data file type"
    return "data-only", f"{len(rows)} changed file(s), all data"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--changes", type=Path, required=True)
    # lint.yml runs MASTER's copy of this script against master's action.yml, never the PR's own:
    # a PR that edited either could otherwise declare itself data-only.
    ap.add_argument("--action", type=Path, default=ACTION, help="bot-pr-sync action.yml to read")
    args = ap.parse_args(argv)
    try:
        changes = args.changes.read_text(encoding="utf-8")
    except OSError:
        mode, reason = "full", "change list unreadable"
    else:
        mode, reason = decide(changes, allowlist_regex(args.action))
    print(f"mode={mode}")
    print(f"reason: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
