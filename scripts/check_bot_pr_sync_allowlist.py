"""scripts/check_bot_pr_sync_allowlist.py -- makes bot-pr-sync's data-only
allowlist mechanically enforced instead of memory-enforced (AF1, production
audit, 2026-09-10).

The incident, twice: `.github/actions/bot-pr-sync/action.yml` independently
verifies (BEFORE ever pushing or merging) that a caller workflow's diff is
confined to a hardcoded data-only allowlist -- so the guarantee "bot commits
only ever touch data/" can't be weakened by accident in a calling workflow
without the merge mechanism noticing. Twice, a caller workflow's own `git
add` list was widened to include README.md/docs/*.md (to fix a real,
separate problem -- docs-freshness never running on [skip ci] data commits)
without first reading that allowlist: PR #1353 (2026-09-04, reverted same
day as #1360) and PR #1539 (2026-09-10, reverted same day as #1549, after
blocking data sync for 7+ hours -- forecast.json's age climbed to 7.76h
against the dead-man's-switch's 10h WARN before anyone caught it). The
written rule ("read the allowlist before touching a path it guards") was
stated after the first incident and violated again at the second -- a rule
enforced only by a human remembering it is not enforced. This script is the
mechanical version: it fails a PR at review time, not a bot commit hours
later in production.

What it does: reads the allowlist regex directly from
bot-pr-sync/action.yml's own guard line (never a hand-copied duplicate --
that would just relocate the same staleness risk one file over), discovers
every workflow that actually invokes bot-pr-sync (by grepping for the
`uses:` line, not a hardcoded list -- bot-pr-sync/action.yml's own
descriptive comment claiming to enumerate its callers was found stale during
this same investigation, missing two of the eight real ones), and checks
every `git add <path...>` argument in each against that regex.

Known blind spots -- state plainly, not hidden (rule 85a):

- Understands simple `VAR="literal"` assignments in the same file when a
  `git add "$VAR"` token needs resolving (scrape-tanishq-selfhosted.yml does
  this). Anything else non-literal in a git-add argument position --
  command substitution, a variable set from a command's output, a loop
  building up a file list -- is NOT resolved; per rule 98b, an unresolvable
  token is a hard FAIL, never a silent skip.
- `git add -A`, `git add .`, `git add --all`, or any other blanket-add form
  is an automatic FAIL regardless of context -- there is no path to check,
  and no way to verify safety, so this fails closed rather than passing on
  "nothing to flag."
- Only checks workflows in THIS repo that literally invoke bot-pr-sync via
  `uses: ./.github/actions/bot-pr-sync` (or a path ending in
  `/bot-pr-sync`). A bot commit pushed through a differently-named or
  differently-invoked mechanism, or one bypassing bot-pr-sync entirely
  (e.g. a direct push with a different token), is invisible to this script
  -- it verifies one specific, named guarantee, not "no bot commit can ever
  touch a disallowed path by any means."
- Only inspects the `git add` argument list as written in the YAML at the
  commit being checked -- it cannot detect a script that computes which
  files to write at runtime in a way that could, on some future run, target
  a path outside what's currently listed. The allowlist and this checker
  both describe the DECLARED surface, not a runtime guarantee.
- Does not re-verify bot-pr-sync/action.yml's own allowlist regex is
  correctly written (e.g. a typo that accidentally allows everything) --
  it trusts that regex as the source of truth by design, since duplicating
  a validated copy of it here would reintroduce exactly the two-sources-
  drift risk this script exists to close. If that regex is ever wrong,
  fixing this script requires fixing the regex, not adding a second one.

Usage:
    python scripts/check_bot_pr_sync_allowlist.py [--repo-root PATH]
"""

from __future__ import annotations

import argparse
import re
import shlex
import sys
from pathlib import Path

ALLOWLIST_LINE_RE = re.compile(r"grep -v -E '([^']+)'")
BOT_PR_SYNC_USES_RE = re.compile(r"uses:\s*\S*bot-pr-sync\S*")
GIT_ADD_LINE_RE = re.compile(r"^(?P<indent>\s*)git add\s+(?P<rest>.+)$", re.MULTILINE)
# Cuts a git-add line's argument portion at the first shell operator/redirect
# that isn't part of the file-path list -- everything before this point is
# tokenized as candidate paths.
CUT_RE = re.compile(r"\s(\|\||&&|;)\s|\s\d*>")
BLANKET_ADD_TOKENS = {"-A", "--all", "."}
VAR_TOKEN_RE = re.compile(r'^"?\$(\w+)"?$')


class AllowlistCheckError(Exception):
    """Raised when the check itself cannot be completed -- callers must
    treat this as a failure, never as a pass (rule 98a: fail closed)."""


def extract_allowlist_regex(action_path: Path) -> str:
    """Reads the allowlist regex directly from bot-pr-sync/action.yml's own
    guard line -- never a hand-copied duplicate. Raises if the guard line
    itself can't be found (the action was restructured and this script's
    own understanding of it is now stale) -- an unreadable allowlist is not
    a passing allowlist."""
    if not action_path.exists():
        raise AllowlistCheckError(f"{action_path} does not exist")
    text = action_path.read_text(encoding="utf-8")
    match = ALLOWLIST_LINE_RE.search(text)
    if not match:
        raise AllowlistCheckError(
            f"could not find the `grep -v -E '...'` allowlist guard line in {action_path} -- "
            "the action was restructured; update ALLOWLIST_LINE_RE or this script's own logic"
        )
    return match.group(1)


def find_bot_pr_sync_callers(workflows_dir: Path) -> list[Path]:
    """Discovers every workflow that actually invokes bot-pr-sync by
    grepping for its `uses:` line -- not a hardcoded list. bot-pr-sync's own
    descriptive comment enumerating callers was found stale during this
    investigation (missing scrape-tanishq-selfhosted.yml and
    shadow-fusion.yml, 2 of the real 8) -- a hardcoded list here would only
    relocate that exact staleness risk."""
    callers = []
    for path in sorted(workflows_dir.glob("*.yml")):
        if BOT_PR_SYNC_USES_RE.search(path.read_text(encoding="utf-8")):
            callers.append(path)
    return callers


def resolve_variable(workflow_text: str, var_name: str) -> str | None:
    """Looks for a `VAR="literal"` assignment elsewhere in the same file.
    Returns the literal value only if found and genuinely constant (no `$`
    or backtick inside the quotes, i.e. not itself a further expansion).
    Returns None if unresolvable -- callers must treat None as a failure,
    never as an implicit pass."""
    pattern = re.compile(rf'^\s*{re.escape(var_name)}="([^"$`]*)"\s*$', re.MULTILINE)
    matches = {m.group(1) for m in pattern.finditer(workflow_text)}
    if len(matches) == 1:
        return next(iter(matches))
    return None  # not found, or assigned to more than one distinct literal


def extract_git_add_paths(workflow_text: str) -> list[tuple[int, str]]:
    """Every `git add <path...>` line's argument tokens, as (line_number,
    raw_token) pairs. Cuts each line's argument portion at the first shell
    operator/redirect so `|| true`, `2>/dev/null`, etc. are never mistaken
    for path tokens."""
    results: list[tuple[int, str]] = []
    for match in GIT_ADD_LINE_RE.finditer(workflow_text):
        line_no = workflow_text.count("\n", 0, match.start()) + 1
        rest = match.group("rest")
        cut = CUT_RE.search(rest)
        args_str = rest[: cut.start()] if cut else rest
        # Command substitution ($(...) or backticks) must be treated as ONE
        # unresolved expression, never shlex-split -- shlex has no concept
        # of $(...) as an atomic unit, so splitting first would fragment it
        # into several space-separated pieces (e.g. `$(find data -name
        # '*.json')` -> `$(find`, `data`, `-name`, `*.json)`), each then
        # individually mis-evaluated as if it were its own literal path.
        if "$(" in args_str or "`" in args_str:
            results.append((line_no, args_str.strip()))
            continue
        try:
            tokens = shlex.split(args_str)
        except ValueError as exc:
            raise AllowlistCheckError(
                f"line {line_no}: could not tokenize `git add {args_str}` ({exc}) -- "
                "failing closed rather than guessing"
            ) from exc
        for token in tokens:
            results.append((line_no, token))
    return results


def check_workflow(workflow_path: Path, allowlist_regex: str) -> list[str]:
    """Returns a list of human-readable violation strings for one workflow
    file; empty list means everything in it is allowlist-compliant."""
    text = workflow_path.read_text(encoding="utf-8")
    violations: list[str] = []
    for line_no, token in extract_git_add_paths(text):
        if token in BLANKET_ADD_TOKENS:
            violations.append(
                f"{workflow_path}:{line_no}: blanket `git add {token}` -- no specific path to "
                "verify, cannot be confirmed allowlist-safe, failing closed"
            )
            continue

        var_match = VAR_TOKEN_RE.match(token)
        if var_match:
            resolved = resolve_variable(text, var_match.group(1))
            if resolved is None:
                violations.append(
                    f"{workflow_path}:{line_no}: `{token}` is not a literal path and could not be "
                    'resolved to one via a same-file `VAR="literal"` assignment -- failing closed '
                    "rather than guessing its value (rule 98b)"
                )
                continue
            path_to_check, display = resolved, f"{token} (resolves to {resolved!r})"
        elif "$" in token or "`" in token:
            violations.append(
                f"{workflow_path}:{line_no}: `{token}` contains an unresolved shell expansion -- "
                "failing closed rather than guessing its value (rule 98b)"
            )
            continue
        else:
            path_to_check, display = token, token

        if not re.match(allowlist_regex, path_to_check):
            violations.append(
                f"{workflow_path}:{line_no}: `{display}` is not covered by bot-pr-sync's "
                f"allowlist (`{allowlist_regex}`)"
            )
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).parent.parent)
    args = parser.parse_args()

    action_path = args.repo_root / ".github" / "actions" / "bot-pr-sync" / "action.yml"
    workflows_dir = args.repo_root / ".github" / "workflows"

    try:
        allowlist_regex = extract_allowlist_regex(action_path)
        callers = find_bot_pr_sync_callers(workflows_dir)
        if not callers:
            raise AllowlistCheckError(
                "found zero workflows invoking bot-pr-sync -- either the repo genuinely has none "
                "(unlikely) or this script's discovery regex no longer matches how it's invoked; "
                "failing closed rather than reporting a vacuous pass"
            )
        all_violations: list[str] = []
        for workflow_path in callers:
            all_violations.extend(check_workflow(workflow_path, allowlist_regex))
    except AllowlistCheckError as exc:
        print(f"FAIL (could not complete check -- failing closed, not skipping): {exc}")
        return 1

    if all_violations:
        print(
            f"FAIL: {len(all_violations)} path(s) across {len(callers)} bot-pr-sync-calling "
            f"workflow(s) are not covered by the allowlist (`{allowlist_regex}`):"
        )
        for v in all_violations:
            print(f"  - {v}")
        return 1

    print(
        f"OK: all git-add paths in {len(callers)} bot-pr-sync-calling workflow(s) "
        f"({', '.join(p.name for p in callers)}) are covered by the allowlist "
        f"(`{allowlist_regex}`)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
