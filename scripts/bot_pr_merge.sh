#!/usr/bin/env bash
# scripts/bot_pr_merge.sh PR_NUMBER BRANCH PUSHED_SHA -- the ONE merge path for every bot PR.
#
# Why: GitHub's native auto-merge does not re-evaluate a PR when the required contexts arrive as commit
# statuses forwarded from a dispatched run (the way bot PRs pushed with a PAT get lint, pwa-js and
# scraper-dependency-guard). docs-refresh PRs #2824, #2832 and #2845 sat open 80 min, 4 h 53 min and 6 h 30 min
# with all three contexts `success` (2026-10-15/16); #2737 on 2026-10-12 is probably the same. bot-pr-sync had
# its own polling merge for that reason (bot-pr-sync/action.yml header); docs-refresh got a copy in #2858. This
# is that logic in one place, used by bot-pr-sync (so by every bot-pr-sync caller) and by docs-refresh.
# scripts/check_required_checks_skippable.py fails a workflow that opens a bot PR without it.
#
# Behaviour (tests/test_bot_pr_merge.py runs this file with stub gh):
#   * polls the PR every POLL_SECONDS (default 10) for WAIT_MINUTES (default 15);
#   * trusts only the commit this run pushed (PUSHED_SHA): statuses/check-runs of any other head are ignored,
#     and the merge itself passes `--match-head-commit`, so a force-push that lands mid-poll cannot get an
#     unverified head merged;
#   * merges when EVERY context in REQUIRED_CONTEXTS (default: lint pwa-js scraper-dependency-guard) is
#     `success` on that head, as a commit status or a check run; a merge that fails is retried on the next poll;
#   * if master moves and the PR falls BEHIND it updates the branch, re-dispatches lint.yml and
#     scraper-dependency-guard.yml (with the PR number) and pins to the new head;
#   * if the merge is refused while every context is green, it re-runs any CANCELLED run of a required
#     check once per head (a cancelled check run blocks the merge even next to a green status);
#   * exit 0 once the PR is MERGED; exit 1, loudly (::error::), if it is CLOSED unmerged or still open after
#     the wait, saying which contexts were missing.
# Environment: GH_TOKEN (the merging PAT), GITHUB_REPOSITORY; optional WAIT_MINUTES, POLL_SECONDS,
# REQUIRED_CONTEXTS, CI_RETRY_*.

set -euo pipefail

PR_NUM="${1:?usage: bot_pr_merge.sh PR_NUMBER BRANCH PUSHED_SHA}"
BRANCH="${2:?usage: bot_pr_merge.sh PR_NUMBER BRANCH PUSHED_SHA}"
EXPECTED="${3:-}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${HERE}/ci_retry.sh"

if [ -z "${EXPECTED}" ]; then
  echo "::error::bot_pr_merge: no pushed commit SHA given; refusing to merge a head that was not verified."
  exit 2
fi

WAIT_MINUTES="${WAIT_MINUTES:-15}"
POLL_SECONDS="${POLL_SECONDS:-10}"
REQUIRED_CONTEXTS="${REQUIRED_CONTEXTS:-lint pwa-js scraper-dependency-guard}"
DEADLINE=$(( $(date +%s) + ${WAIT_SECONDS:-$(( WAIT_MINUTES * 60 ))} )) # WAIT_SECONDS: tests only

echo "Waiting up to ${WAIT_MINUTES} minutes for PR #${PR_NUM} (${BRANCH}) at ${EXPECTED} to pass: ${REQUIRED_CONTEXTS}"

# Contexts that are `success` on a commit, as a commit status or as a check run (one name per line).
green_contexts() {
  {
    ci_retry gh api "repos/${GITHUB_REPOSITORY}/commits/$1/status?per_page=100" \
      --jq '.statuses[] | select(.state == "success") | .context' 2>/dev/null || true
    ci_retry gh api "repos/${GITHUB_REPOSITORY}/commits/$1/check-runs?per_page=100" \
      --jq '.check_runs[] | select(.conclusion == "success") | .name' 2>/dev/null || true
  } | sort -u
}

# After a merge: if the PR changed a file that an injected <!--METRIC:...--> marker reads, dispatch docs-refresh
# on master now. Why (2026-10-18): docs-refresh's workflow_run trigger does not fire for Check Gold Price runs that
# github-actions[bot] dispatched (0 of 85 followed, against 21 of 21 dispatched by the account owner; the data
# syncs arrive that way), so the README stayed stale for 2 h. `workflow_dispatch` is the one event a token-made
# run may create. Best effort: a failed dispatch only warns (the hourly cron is the fallback), and an unreadable
# file list dispatches anyway, because docs-refresh exits at once when nothing drifted. Loop guard: the
# docs-refresh PR's own merge never dispatches another.
dispatch_docs_refresh() {
  if [ "${BRANCH}" = "${DOCS_REFRESH_BRANCH:-bot/docs-refresh}" ]; then
    echo "docs-refresh PR #${PR_NUM}: not dispatching another docs-refresh."
    return 0
  fi
  local root files sources hit="" f NL
  NL=$'\n'
  root="$(cd "${HERE}/.." && pwd)"
  sources="$(cd "${root}" && grep -rhoE '<!--METRIC:[^#<>]+' README.md docs 2>/dev/null | sed 's/^<!--METRIC://' | sort -u || true)"
  files="$(ci_retry gh pr view "${PR_NUM}" --repo "${GITHUB_REPOSITORY}" --json files --jq '.files[].path' 2>/dev/null || true)"
  if [ -z "${files}" ] || [ -z "${sources}" ]; then
    hit="unknown"
  else
    for f in ${files}; do
      case "${NL}${sources}${NL}" in
        *"${NL}${f}${NL}"*) hit="${hit} ${f}" ;;
      esac
    done
  fi
  if [ -z "${hit}" ]; then
    echo "PR #${PR_NUM} changed no injected metric source: not dispatching docs-refresh."
    return 0
  fi
  echo "PR #${PR_NUM} changed an injected metric source (${hit# }): dispatching docs-refresh.yml on master."
  ci_retry gh workflow run docs-refresh.yml --repo "${GITHUB_REPOSITORY}" --ref master \
    || echo "::warning::could not dispatch docs-refresh.yml; the hourly schedule will pick the drift up."
  return 0
}

LAST_MISSING=""
LAST_HEAD=""
UPDATED=0
RERAN_HEAD=""
i=0
while :; do # at least one poll, even with WAIT_MINUTES=0
  i=$(( i + 1 ))
  PR_STATE="$(ci_retry gh pr view "${PR_NUM}" --repo "${GITHUB_REPOSITORY}" --json state --jq .state 2>/dev/null || echo UNKNOWN)"
  if [ "${PR_STATE}" = "MERGED" ]; then
    echo "Merged."
    dispatch_docs_refresh
    exit 0
  fi
  if [ "${PR_STATE}" = "CLOSED" ]; then
    echo "::error::PR #${PR_NUM} was closed without merging"
    exit 1
  fi

  # master can advance while we wait; a PR that falls BEHIND gets a new head with no checks.
  if [ $(( i % 2 )) -eq 0 ]; then
    MERGE_STATE="$(ci_retry gh pr view "${PR_NUM}" --repo "${GITHUB_REPOSITORY}" --json mergeStateStatus --jq .mergeStateStatus 2>/dev/null || echo UNKNOWN)"
    if [ "${MERGE_STATE}" = "BEHIND" ]; then
      echo "Branch fell behind master -- resyncing via gh pr update-branch"
      gh pr update-branch "${PR_NUM}" --repo "${GITHUB_REPOSITORY}" || true
      UPDATED=1
      sleep 5
      NEW_HEAD="$(ci_retry gh pr view "${PR_NUM}" --repo "${GITHUB_REPOSITORY}" --json headRefOid --jq .headRefOid 2>/dev/null || echo "")"
      if [ -n "${NEW_HEAD}" ]; then
        EXPECTED="${NEW_HEAD}" # we made this head ourselves
      fi
      gh workflow run lint.yml --repo "${GITHUB_REPOSITORY}" --ref "${BRANCH}" || true
      gh workflow run scraper-dependency-guard.yml --repo "${GITHUB_REPOSITORY}" --ref "${BRANCH}" -f pr="${PR_NUM}" || true
    fi
  fi

  HEAD_SHA="$(ci_retry gh pr view "${PR_NUM}" --repo "${GITHUB_REPOSITORY}" --json headRefOid --jq .headRefOid 2>/dev/null || echo "")"
  # `gh pr update-branch` is asynchronous: the new head can appear after our 5 s pause. Adopt a different head
  # only if we asked for the update AND it is a direct child of the commit we verified (independent review of
  # #2862); a head from anywhere else stays untrusted.
  if [ "${UPDATED}" = 1 ] && [ -n "${HEAD_SHA}" ] && [ "${HEAD_SHA}" != "${EXPECTED}" ]; then
    PARENTS="$(ci_retry gh api "repos/${GITHUB_REPOSITORY}/commits/${HEAD_SHA}" --jq '.parents[].sha' 2>/dev/null || echo "")"
    case "
${PARENTS}
" in
      *"
${EXPECTED}
"*) EXPECTED="${HEAD_SHA}" ;;
    esac
  fi
  LAST_HEAD="${HEAD_SHA}"
  if [ -n "${HEAD_SHA}" ] && [ "${HEAD_SHA}" = "${EXPECTED}" ]; then
    GREEN="$(green_contexts "${HEAD_SHA}")"
    MISSING=""
    # whole-line match without a pipe (grep -q in a pipeline can die of SIGPIPE under pipefail)
    NL=$'\n'
    HAY="${NL}${GREEN}${NL}"
    for ctx in ${REQUIRED_CONTEXTS}; do
      case "${HAY}" in
        *"${NL}${ctx}${NL}"*) ;;
        *) MISSING="${MISSING} ${ctx}" ;;
      esac
    done
    LAST_MISSING="${MISSING}"
    if [ -z "${MISSING}" ]; then
      echo "Required contexts green on ${HEAD_SHA} -- merging PR #${PR_NUM}"
      if gh pr merge "${PR_NUM}" --repo "${GITHUB_REPOSITORY}" --squash --match-head-commit "${HEAD_SHA}"; then
        echo "Merged."
        dispatch_docs_refresh
        exit 0
      fi
      echo "Merge attempt failed (see above); will retry on the next poll."
      # All required contexts are green yet the merge is refused: a CANCELLED run of a required check (e.g. the
      # pull_request run of scraper-dependency-guard, cancelled by the dispatched one) keeps "the base branch
      # policy" from being satisfied (PR #2864, 2026-10-16). Re-run such runs once per head.
      if [ "${RERAN_HEAD}" != "${HEAD_SHA}" ]; then
        RERAN_HEAD="${HEAD_SHA}"
        NAMES_JSON="["
        for ctx in ${REQUIRED_CONTEXTS}; do NAMES_JSON="${NAMES_JSON}\"${ctx}\","; done
        NAMES_JSON="${NAMES_JSON%,}]"
        CANCELLED_URLS="$(ci_retry gh api "repos/${GITHUB_REPOSITORY}/commits/${HEAD_SHA}/check-runs?per_page=100" \
          --jq ".check_runs[] | select(.conclusion == \"cancelled\" or .conclusion == \"timed_out\") | select(.name as \$n | ${NAMES_JSON} | index(\$n)) | .html_url" 2>/dev/null || true)"
        for url in ${CANCELLED_URLS}; do
          RUN_ID="${url#*/actions/runs/}"
          RUN_ID="${RUN_ID%%/*}"
          case "${RUN_ID}" in
            ''|*[!0-9]*) continue ;;
          esac
          echo "Re-running cancelled required-check run ${RUN_ID}"
          gh run rerun "${RUN_ID}" --repo "${GITHUB_REPOSITORY}" || true
        done
      fi
    fi
  fi

  if [ "$(date +%s)" -ge "${DEADLINE}" ]; then
    break
  fi
  sleep "${POLL_SECONDS}"
done

if [ -n "${LAST_HEAD}" ] && [ "${LAST_HEAD}" != "${EXPECTED}" ]; then
  echo "::error::PR #${PR_NUM} (${BRANCH}) head is ${LAST_HEAD}, not the commit this run verified (${EXPECTED}); not merged after ${WAIT_MINUTES} minutes"
else
  echo "::error::PR #${PR_NUM} (${BRANCH}) was not merged after ${WAIT_MINUTES} minutes; contexts not green on ${EXPECTED}:${LAST_MISSING:- (none missing; the merge itself kept failing)}"
fi
exit 1
