#!/usr/bin/env bash
# scripts/ops_log_fallback.sh -- the ops log in pure bash, for hosts without python.
#
# Source it, then:   ops_log_fallback SOURCE TITLE BODY
#
# Why: scripts/bot_branch_push.sh runs on the self-hosted Windows runner, which has no python, so its
# `python3 scripts/ops_log.py ... || true` silently dropped every record. Same three things as
# ops_log.py record(), with only bash, awk, sed, date, base64 and gh (its own --jq, no jq):
#   1. the `::warning title=ops-alert::...` annotation; 2. a section in $GITHUB_STEP_SUMMARY;
#   3. one JSON line appended to ops_log.jsonl on `ops-log` (5 tries on a sha conflict; the payload
#      goes on stdin; it never creates the branch, it warns). Never fails the caller. Needs GH_TOKEN.
# Environment: GITHUB_ACTIONS, GITHUB_REPOSITORY, GITHUB_RUN_ID, GITHUB_WORKFLOW, GITHUB_SERVER_URL,
# OPS_LOG_NO_FILE (skip step 3), OPS_LOG_RETRY_BASE (seconds, default 1; tests set 0).

_ops_one_line() { printf '%s' "$1" | tr '\r\n\t' '   ' | sed -e 's/  */ /g' -e 's/^ //' -e 's/ $//'; }

_ops_json_escape() { printf '%s' "$1" | tr -d '\000-\010\013\014\016-\037' | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }

ops_log_fallback() {
  local source title body msg line
  source="$(_ops_one_line "${1:-}")"
  title="$(_ops_one_line "${2:-}")"
  body="$(_ops_one_line "${3:-}")"
  msg="${source}: ${title} -- ${body}"
  msg="${msg:0:900}"
  msg="${msg//%/%25}"
  line="::warning title=ops-alert::${msg}"
  echo "${line}"
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    printf '### ops-alert: %s\n\nsource: `%s`\n\n%s\n\n' "${title}" "${source}" "${body}" \
      >> "${GITHUB_STEP_SUMMARY}" 2>/dev/null || echo "ops_log: could not write the job summary"
  fi
  _ops_file_append "${source}" "${title}" "${body}" || echo "ops_log: warning: ops-log file not written"
  return 0
}

_ops_file_append() {
  local source="$1" title="$2" body="$3"
  local repo="${GITHUB_REPOSITORY:-}"
  if [ -n "${OPS_LOG_NO_FILE:-}" ] || [ "${GITHUB_ACTIONS:-}" != "true" ] || [ -z "${repo}" ] \
    || [ "${GITHUB_EVENT_NAME:-}" = "pull_request" ]; then
    return 0
  fi
  local token="${OPS_LOG_TOKEN:-${GH_TOKEN:-${GITHUB_TOKEN:-}}}"
  if [ -z "${token}" ] || ! command -v gh >/dev/null 2>&1; then
    echo "ops_log: warning: no gh or no token, the ops-log file was not written"
    return 0
  fi
  # scoped to each gh call below: the caller's own GH_TOKEN (the bot PAT) must stay as it was
  local GH_TOKEN="${token}"
  export GH_TOKEN
  local path="repos/${repo}/contents/ops_log.jsonl" ts cutoff entry run_id run_url attempt sha old new b64 msg
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  cutoff="$(date -u -d '120 days ago' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "")"
  run_id="${GITHUB_RUN_ID:-}"
  run_url=""
  [ -n "${run_id}" ] && run_url="${GITHUB_SERVER_URL:-https://github.com}/${repo}/actions/runs/${run_id}"
  entry="{\"ts\": \"${ts}\", \"source\": \"$(_ops_json_escape "${source}")\", \"title\": \"$(_ops_json_escape "${title}")\", \"body\": \"$(_ops_json_escape "${body:0:500}")\", \"workflow\": \"$(_ops_json_escape "${GITHUB_WORKFLOW:-}")\", \"run_id\": \"${run_id}\", \"run_url\": \"${run_url}\"}"
  for attempt in 1 2 3 4 5; do
    if ! sha="$(gh api "${path}?ref=ops-log" --jq .sha 2>/dev/null)" || [ -z "${sha}" ]; then
      echo "ops_log: warning: ops-log branch or file not readable, the ops-log file was not written"
      return 0
    fi
    if ! old="$(gh api -H 'Accept: application/vnd.github.raw+json' "${path}?ref=ops-log" 2>/dev/null)"; then
      echo "ops_log: warning: could not read ops_log.jsonl, the ops-log file was not written"
      return 0
    fi
    # lines start {"ts": "YYYY-MM-DDTHH:MM:SSZ": a string compare on that stamp prunes old ones;
    # a line that does not have the shape is kept
    if [ -n "${cutoff}" ]; then
      old="$(printf '%s\n' "${old}" | awk -v c="${cutoff}" 'NF && !(substr($0,1,8)=="{\"ts\": \"" && substr($0,9,20) < c)')"
    fi
    new="$(printf '%s\n%s\n' "${old}" "${entry}" | sed '/^$/d')"
    b64="$(printf '%s\n' "${new}" | base64 | tr -d '\n')"
    # The payload goes in on stdin: the whole file is in it, and a Windows command line holds only
    # about 32K characters, so `-f content=...` would fail once the file passed ~24 KB. printf is a
    # bash builtin, so the large value never crosses an exec boundary as an argument either.
    msg="$(_ops_json_escape "ops-log: ${source}: ${title}")"
    if printf '{"message": "%s [skip ci]", "content": "%s", "branch": "ops-log", "sha": "%s"}' \
      "${msg:0:200}" "${b64}" "${sha}" | gh api -X PUT "${path}" --input - >/dev/null 2>&1; then
      return 0
    fi
    sleep "$(( (RANDOM % 3 + 1) * attempt * ${OPS_LOG_RETRY_BASE:-1} ))" 2>/dev/null || true
  done
  echo "ops_log: warning: gave up after 5 tries, the ops-log file was not written"
  return 0
}
