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
# Output: one line per open same-repository PR into master, newest first: "NUMBER HEAD_SHA CREATED_AT IS_DRAFT".
# With --first-number, only the first PR's number (empty if none). A PR is dropped unless isCrossRepository is
# literally false AND its head repository's owner and name equal GITHUB_REPOSITORY's (a missing field drops it).
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
NAME="${R#*/}"
lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

ROWS=$(ci_retry gh pr list --repo "$R" --head "$BRANCH" --base master --state open --limit 100 \
  --json number,headRefOid,createdAt,isDraft,isCrossRepository,headRepositoryOwner,headRepository \
  --jq '.[] | [.number, .headRefOid, .createdAt, .isDraft, .isCrossRepository, .headRepositoryOwner.login, .headRepository.name] | map(tostring) | join("\t")' \
  < /dev/null)

OUT=""
while IFS=$'\t' read -r NUM SHA CREATED DRAFT CROSS HOWNER HNAME; do
  [ -n "${NUM:-}" ] || continue
  if [ "${CROSS:-}" != false ] || [ "$(lower "${HOWNER:-}")" != "$(lower "$OWNER")" ] \
      || [ "${HNAME:-}" != "$NAME" ]; then
    echo "bot_pr_find: ignoring PR #${NUM} on ${BRANCH}: its head is ${HOWNER:-?}/${HNAME:-?} (cross-repository=${CROSS:-?}), not ${R}" >&2
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
