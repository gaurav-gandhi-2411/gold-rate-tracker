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
#     this run's commit goes to a separate rescue branch, the existing branch is left untouched, GG is paged
#     (ntfy) and the step fails;
#   * if nothing is new relative to master (the reading is already there) it pushes nothing and exits 0.
#
# Environment: NTFY_TOPIC (optional, pages on rescue/failure), GITHUB_RUN_ID (names the rescue branch),
# GITHUB_REPOSITORY (link in the page). Run from the repository root with the run's commit as HEAD.

set -euo pipefail

BRANCH="${1:?usage: bot_branch_push.sh BRANCH}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${HERE}/ci_retry.sh"

MINE="$(git rev-parse HEAD)"
MSG="$(git log -1 --format=%B "${MINE}")"
RESCUE="${BRANCH}-rescue-${GITHUB_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"

page() {
  # public ntfy path, plain words only; best effort
  if [ -n "${NTFY_TOPIC:-}" ]; then
    curl -s \
      -H "Title: Gold Tracker: a Tanishq reading needed rescuing" \
      -H "Priority: high" \
      -H "Tags: warning,floppy_disk" \
      -d "$1 https://github.com/${GITHUB_REPOSITORY:-}/branches" \
      "https://ntfy.sh/${NTFY_TOPIC}" || true
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
