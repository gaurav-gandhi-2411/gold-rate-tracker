#!/usr/bin/env bash
# scripts/bot_branch_push.sh BRANCH -- push this run's data commit to a reusable bot branch WITHOUT
# destroying readings that are already waiting on that branch.
#
# Why: bot-pr-sync used to `git push --force` this run's commit over the branch. While the data PR could not
# merge (the 2026-10-14/15 token outage) every self-hosted scrape replaced the previous unmerged reading,
# and three Tanishq readings were lost for good. This script instead rebuilds the branch as
#     origin/master + this run's reading(s) + the reading(s) already on the branch that master lacks
# (scripts/carry_forward_readings.js does the data part), so a blocked sync accumulates readings and all of
# them land, once, in time order, when it recovers. Because it never rebases the run's commit onto master,
# a master that moved while the scrape ran cannot make the push fail on a conflict either.
#
# Safety:
#   * the push is `--force-with-lease` against the branch tip the rebuild started from; if someone pushed in
#     between, it starts again (3 tries);
#   * anything it cannot carry (an unparseable file, an unexpected changed file, no node) is NEVER dropped:
#     this run's commit goes to a separate rescue branch, the existing branch is left untouched, the ops log
#     (scripts/ops_log.py) records it and the step fails;
#   * if nothing is new relative to master (the reading is already there) it pushes nothing and exits 0.
#
# Environment: GITHUB_RUN_ID (names the rescue branch),
# GITHUB_REPOSITORY (link in the page). Run from the repository root with the run's commit as HEAD.

set -euo pipefail

BRANCH="${1:?usage: bot_branch_push.sh BRANCH}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${HERE}/ci_retry.sh"
source "${HERE}/ops_log_fallback.sh"

MINE="$(git rev-parse HEAD)"
MSG="$(git log -1 --format=%B "${MINE}")"
RESCUE="${BRANCH}-rescue-${GITHUB_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"

page() {
  # ops log (job summary + ops-alert annotation + ops-log file), NOT the phone: ntfy is gold-price
  # information only. The self-hosted Windows runner has no python, and the old
  # `python3 ... || true` dropped the record there without a trace: use the bash version then.
  local py="${OPS_LOG_PYTHON:-python3}"
  if "${py}" -c 'import sys' >/dev/null 2>&1; then
    "${py}" "${HERE}/ops_log.py" --source "bot_branch_push" \
      --title "a Tanishq reading needed rescuing" \
      --body "$1 https://github.com/${GITHUB_REPOSITORY:-}/branches" || true
  else
    ops_log_fallback "bot_branch_push" "a Tanishq reading needed rescuing" \
      "$1 https://github.com/${GITHUB_REPOSITORY:-}/branches" || true
  fi
}

rescue() {
  echo "::error::bot_branch_push: $1"
  echo "Keeping this run's commit ${MINE} on ${RESCUE} instead of overwriting ${BRANCH}."
  if ci_retry git push origin "${MINE}:refs/heads/${RESCUE}"; then
    page "A scrape reading could not be merged into ${BRANCH} automatically ($1). It is saved on branch ${RESCUE}; nothing was overwritten."
  else
    echo "::error::bot_branch_push: the rescue push failed too; the reading from commit ${MINE} is NOT kept anywhere but this runner."
    page "A scrape reading could NOT be saved to GitHub at all ($1). It exists only on the runner until its workspace is cleaned."
  fi
  exit 1
}

# the files this run's commit may legitimately touch
ALLOWED_RE='^data/(prices\.json|tanishq_scrape_outcomes\.jsonl|tanishq_selfhosted_health\.json)$'

if ! command -v node >/dev/null 2>&1; then
  rescue "node is not available to merge the readings"
fi

ci_retry git fetch origin master

# The runner's checkout is shallow (depth 1). With no history, `git merge-base master <queued branch>` finds
# nothing as soon as master has moved since the branch was built, and every second queued reading would end
# up on a rescue branch (found by the independent verifier of #2856). Take the full history once.
if [ "$(git rev-parse --is-shallow-repository)" = "true" ]; then
  ci_retry git fetch --unshallow origin
  ci_retry git fetch origin master
fi

for attempt in 1 2 3; do
  # An unreachable remote must stop the step here, not be read as "the branch does not exist".
  if ! LS="$(ci_retry git ls-remote --heads origin "refs/heads/${BRANCH}")"; then
    echo "::error::bot_branch_push: cannot list ${BRANCH} on the remote; refusing to push."
    page "A scrape reading was not pushed: GitHub could not be asked about ${BRANCH}."
    exit 1
  fi
  REMOTE_SHA="$(printf '%s' "${LS}" | cut -f1 | head -n 1)"

  SOURCES=(--source "${MINE}")
  if [ -n "${REMOTE_SHA}" ]; then
    ci_retry git fetch origin "+refs/heads/${BRANCH}:refs/remotes/origin/${BRANCH}"
    if [ "$(git rev-parse "refs/remotes/origin/${BRANCH}")" != "${REMOTE_SHA}" ]; then
      echo "branch ${BRANCH} moved while fetching; trying again (${attempt}/3)"
      continue
    fi
    SOURCES+=(--source "refs/remotes/origin/${BRANCH}")
  fi

  git checkout -q --detach origin/master
  if ! OUT="$(node "${HERE}/carry_forward_readings.js" --base origin/master "${SOURCES[@]}" 2>&1)"; then
    rescue "$(printf '%s' "${OUT}" | tr '\n' ' ')"
  fi
  echo "${OUT}"

  git add data/prices.json data/tanishq_scrape_outcomes.jsonl data/tanishq_selfhosted_health.json
  if git diff --cached --quiet; then
    echo "Nothing to push: every reading is already on master."
    exit 0
  fi
  BAD="$(git diff --cached --name-only | grep -v -E "${ALLOWED_RE}" || true)"
  if [ -n "${BAD}" ]; then
    rescue "the rebuilt commit touches unexpected files: $(echo "${BAD}" | tr '\n' ' ')"
  fi
  git commit -q -m "${MSG}"

  if [ -n "${REMOTE_SHA}" ]; then
    LEASE="refs/heads/${BRANCH}:${REMOTE_SHA}"
  else
    LEASE="refs/heads/${BRANCH}:" # the branch must still not exist
  fi
  if ci_retry git push --force-with-lease="${LEASE}" origin "HEAD:refs/heads/${BRANCH}"; then
    echo "Pushed ${BRANCH} at $(git rev-parse --short HEAD)."
    exit 0
  fi
  echo "push of ${BRANCH} was refused (the branch moved?); rebuilding (${attempt}/3)"
done

rescue "the branch kept moving; three rebuilds in a row were refused"
