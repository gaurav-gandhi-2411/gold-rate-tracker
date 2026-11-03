#!/usr/bin/env bash
# scripts/bot_pr_sweep.sh -- merge a stranded bot PR, or page about it. Every bot branch, every hour.
#
# Why (2026-11-01..03): weekly-backtest's sync gave up on bot PR #3424 after 10 minutes; its lint went green
# 3 minutes later, and nothing retries a weekly sync, so the PR sat open with green checks for 46 h and the
# week's backtest data never reached master. Nothing paged: the stuck-PR alert in check-price watched only
# bot/data-sync, and the stranded-PR merge only bot/tanishq-selfhosted-sync.
#
# What it does, for EVERY bot branch a workflow opens PRs from (scripts/bot_branches.py reads them from the
# workflow files, so a new bot workflow is covered without editing anything here):
#   * an open, non-draft PR into master is STRANDED when it is at least MIN_AGE_MIN old and no run of the
#     workflow(s) that produce its branch is queued or running on master (that run's own sync has ended);
#   * a stranded PR from this repository (never a fork) whose files, old names of renamed files included,
#     all match its branch's allowed-files pattern is merged through the ONE bot merge path,
#     scripts/bot_pr_merge.sh, pinned to the head read here: it needs every required context green on that
#     head and merges with --match-head-commit (a PR that is behind is updated first);
#   * a PR it could not merge (fork, files outside the allowlist, invalid allowlist, required checks not
#     green, merge refused, anything unreadable) that is at least ALERT_AGE_MIN old pages and makes the
#     run red; so does a PR whose producer has looked busy for BUSY_ALERT_MIN, or whose producer's runs
#     cannot be read (it is never merged then: a run might be about to push).
# DRY_RUN=1 merges nothing and says what it would merge; a stranded PR past ALERT_AGE_MIN still pages, so
# the alert is complete while the merge half is in its report-only period.
#
# Environment: GH_TOKEN (the merging PAT), GITHUB_REPOSITORY; optional NTFY_TOPIC, MIN_AGE_MIN (20),
# ALERT_AGE_MIN (60), BUSY_ALERT_MIN (360), MERGE_WAIT_MIN (3), DRY_RUN, NOW (epoch seconds; tests only).
# tests/test_bot_pr_sweep.py runs this file with a stub gh.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${HERE}/ci_retry.sh"

R="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
MIN_AGE_MIN="${MIN_AGE_MIN:-20}"
ALERT_AGE_MIN="${ALERT_AGE_MIN:-60}"
BUSY_ALERT_MIN="${BUSY_ALERT_MIN:-360}"
MERGE_WAIT_MIN="${MERGE_WAIT_MIN:-3}"
DRY_RUN="${DRY_RUN:-0}"
NOW="${NOW:-$(date -u +%s)}"
PY="${PYTHON:-python3}"
STUCK=0

page() {
  STUCK=1
  echo "PAGE: $1"
  echo "::error::$1"
  if [ -n "${NTFY_TOPIC:-}" ]; then
    curl -s -H "Title: Gold Tracker: bot PR stuck unmerged" -H "Priority: high" -H "Tags: warning" \
      -d "$1" "https://ntfy.sh/${NTFY_TOPIC}" > /dev/null || true
  fi
}

if ! LIST=$("$PY" "${HERE}/bot_branches.py"); then
  page "bot PR sweep could not read the bot branches from the workflow files; no bot PR was checked. https://github.com/${R}/actions"
  exit 1
fi
LIST=$(printf '%s\n' "$LIST" | tr -d '\r') # a CRLF from Python on Windows would end up inside the regex
[ "$DRY_RUN" = 1 ] && echo "DRY RUN: nothing will be merged."

while IFS=$'\t' read -r BRANCH WORKFLOWS ALLOW; do
  [ -n "$BRANCH" ] || continue
  if ! PRS=$(ci_retry gh pr list --repo "$R" --head "$BRANCH" --base master --state open \
      --json number,headRefOid,createdAt,isDraft,isCrossRepository \
      --jq '.[] | select(.isDraft | not) | "\(.number) \(.headRefOid) \(.createdAt) \(.isCrossRepository)"' \
      < /dev/null); then
    page "bot PR sweep could not list open ${BRANCH} PRs; that branch was not checked."
    continue
  fi
  if [ -z "$PRS" ]; then echo "${BRANCH}: no open PR."; continue; fi

  while read -r NUM SHA CREATED FORK; do
    [ -n "$NUM" ] || continue
    URL="https://github.com/${R}/pull/${NUM}"
    AGE=$(( (NOW - $(date -u -d "$CREATED" +%s)) / 60 ))
    if [ "$FORK" != false ]; then
      # a fork can name its branch bot/data-sync; only a branch in this repository is a bot's
      page "${BRANCH} PR #${NUM} comes from a fork (isCrossRepository=${FORK}); never merged by the sweep. ${URL}"
      continue
    fi
    if [ "$AGE" -lt "$MIN_AGE_MIN" ]; then
      echo "${BRANCH} #${NUM}: ${AGE} min old; its own run may still merge it."
      continue
    fi
    BUSY=""
    UNREADABLE=""
    for WF in ${WORKFLOWS//,/ }; do
      if ! N=$(ci_retry gh run list --repo "$R" --workflow "$WF" --branch master --limit 20 --json status \
          --jq '[.[] | select(.status != "completed")] | length' < /dev/null); then
        UNREADABLE="${UNREADABLE} ${WF}"
      elif [ "$N" != 0 ]; then
        BUSY="${BUSY} ${WF} (${N})"
      fi
    done
    if [ -n "$UNREADABLE" ]; then
      MSG="${BRANCH} PR #${NUM}: ${AGE} min old; the runs of${UNREADABLE} could not be read, so it was not merged"
      if [ "$AGE" -ge "$ALERT_AGE_MIN" ]; then page "${MSG}. ${URL}"; else echo "${MSG}."; fi
      continue
    fi
    if [ -n "$BUSY" ]; then
      MSG="${BRANCH} PR #${NUM}: ${AGE} min old, but a producing run is queued or running:${BUSY}"
      if [ "$AGE" -ge "$BUSY_ALERT_MIN" ]; then page "${MSG}; still open past ${BUSY_ALERT_MIN} min. ${URL}"
      else echo "${MSG}; leaving it to that run."; fi
      continue
    fi

    WHY=""
    if ! FILES=$(ci_retry gh api "repos/${R}/pulls/${NUM}/files?per_page=100" --paginate \
        --jq '.[] | .filename, (.previous_filename // empty)' < /dev/null); then
      WHY="its file list could not be read"
    elif [ -z "$FILES" ]; then
      WHY="it lists no changed files"
    else
      BAD=$(printf '%s\n' "$FILES" | grep -v -E "$ALLOW")
      RC=$?
      if [ "$RC" -gt 1 ]; then
        WHY="${BRANCH}'s allowed-files pattern is not a valid regex"
      elif [ -n "$BAD" ]; then
        WHY="it touches $(echo "$BAD" | tr '\n' ' ')(outside ${BRANCH}'s allowed files)"
      fi
    fi

    if [ -z "$WHY" ]; then
      if [ "$DRY_RUN" = 1 ]; then
        echo "DRY RUN: ${BRANCH} #${NUM} (${AGE} min, head ${SHA}) is stranded and would be merged."
        WHY="it is stranded and this run is a dry run"
      else
        echo "${BRANCH} #${NUM}: stranded ${AGE} min after its run ended; merging through bot_pr_merge.sh at ${SHA}."
        if WAIT_MINUTES="$MERGE_WAIT_MIN" bash "${HERE}/bot_pr_merge.sh" "$NUM" "$BRANCH" "$SHA" < /dev/null; then
          echo "MERGED stranded ${BRANCH} PR #${NUM}."
          continue
        fi
        STATE=$(ci_retry gh pr view "$NUM" --repo "$R" --json state --jq .state < /dev/null) || STATE=unknown
        if [ "$STATE" = MERGED ] || [ "$STATE" = CLOSED ]; then
          echo "${BRANCH} #${NUM}: ${STATE} by something else meanwhile; nothing to do."
          continue
        fi
        WHY="the merge path did not merge it (required checks not green, or the merge was refused; see the log)"
      fi
    fi
    if [ "$AGE" -ge "$ALERT_AGE_MIN" ]; then
      page "${BRANCH} PR #${NUM} has been open ${AGE} min after its run ended and was not merged: ${WHY}. ${URL}"
    else
      echo "${BRANCH} #${NUM}: not merged (${WHY}); pages at ${ALERT_AGE_MIN} min."
    fi
  done <<< "$PRS"
done <<< "$LIST"

exit "$STUCK"
