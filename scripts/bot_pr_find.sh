#!/usr/bin/env bash
# scripts/bot_pr_find.sh BRANCH [--first-number] -- the ONE lookup of open bot PRs by branch name.
#
# Why (2026-11-03): `gh pr list --head bot/data-sync` matches that branch name in ANY repository, and this repo
# is public with forking allowed. A fork PR named bot/data-sync could be picked as "the" bot PR: the sync would
# wait on it instead of opening its own (the data stops), and the stranded-PR merges, which take the SHA from
# the PR they found, could merge the fork's commit. Every lookup therefore goes through here, and only a PR
# whose head repository IS this repository is returned. tests/test_bot_pr_same_repo.py fails if any workflow,
# action or script looks up a PR by branch any other way.
#
# Output: one line per open same-repository PR into master: "NUMBER HEAD_SHA CREATED_AT IS_DRAFT".
# With --first-number, only the first PR's number (empty if none). GitHub filters on head=OWNER:BRANCH, and a PR
# is then dropped unless its head repository's full name equals GITHUB_REPOSITORY (a missing field drops it).
# Exit non-zero, printing nothing, if the list cannot be read: callers must not read that as "no PR".
# Pure bash plus gh's built-in --jq (only to format the fields): the self-hosted Windows runner has no
# python3 or jq. Environment: GH_TOKEN, GITHUB_REPOSITORY.

set -euo pipefail

BRANCH="${1:?usage: bot_pr_find.sh BRANCH [--first-number]}"
MODE="${2:-}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${HERE}/ci_retry.sh"

R="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
OWNER="${R%%/*}"
lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

# The REST filter head=OWNER:BRANCH is applied by GitHub itself, so a fork's PRs never enter the result:
# 100 fork PRs named bot/data-sync cannot push the real one out of a page (independent review, 2026-11-03).
# --paginate reads every page anyway. The check below repeats the filter on the head repository's full name.
ROWS=$(ci_retry gh api "repos/${R}/pulls?head=${OWNER}:${BRANCH}&base=master&state=open&per_page=100" \
  --paginate \
  --jq '.[] | [.number, .head.sha, .created_at, .draft, .head.repo.full_name] | map(tostring) | join("\t")' \
  < /dev/null)

OUT=""
while IFS=$'\t' read -r NUM SHA CREATED DRAFT HEAD_REPO; do
  [ -n "${NUM:-}" ] || continue
  # a deleted fork reads as "null"; any field missing leaves HEAD_REPO empty: either way, not this repository
  if [ "$(lower "${HEAD_REPO:-}")" != "$(lower "$R")" ]; then
    echo "bot_pr_find: ignoring PR #${NUM} on ${BRANCH}: its head is ${HEAD_REPO:-?}, not ${R}" >&2
    continue
  fi
  OUT="${OUT}${NUM} ${SHA} ${CREATED} ${DRAFT}"$'\n'
done <<< "$ROWS"

if [ "$MODE" = --first-number ]; then
  FIRST="${OUT%%$'\n'*}"
  printf '%s\n' "${FIRST%% *}"
else
  printf '%s' "$OUT"
fi
