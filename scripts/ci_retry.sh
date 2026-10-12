#!/usr/bin/env bash
# scripts/ci_retry.sh -- bounded retry for the network calls on the bot-PR and data paths.
#
# Source it, then wrap a command:   VALUE=$(ci_retry gh api "repos/${GITHUB_REPOSITORY}/...")
#
# Why: on 2026-10-12 a single GitHub `HTTP 502` in one un-retried `gh api` call left a bot data PR
# without its required statuses for two hours (ADR/PR #2745 fixed the status post). This is the same
# fix for every other call on that path.
#
# Behaviour (tests/test_ci_retry.py runs this file with a stub command):
#   * success on any attempt: the command's stdout is printed, return 0;
#   * a TRANSIENT failure (HTTP 5xx, 429, a secondary rate limit, a timeout, a dropped connection,
#     a DNS or TLS blip) is retried after a growing pause, up to CI_RETRY_ATTEMPTS (default 5)
#     attempts in all, with no pause after the last one;
#   * any OTHER failure (a 4xx such as 404/422, a bad argument, a merge conflict) is NOT retried:
#     it returns at once with the command's own exit code, its stdout and its stderr;
#   * five transient failures in a row return the command's last exit code (never 0), so a
#     persistent outage still fails the step, only a blip is absorbed.
# Pause after attempt N is N * CI_RETRY_BASE seconds (default 10: 10, 20, 30, 40 s = 100 s at most).
# Only use it on commands that are safe to repeat (reads, status posts, a workflow dispatch, a push
# of the same commit); for a create or a merge, re-check the resulting state after a failure.

_CI_RETRY_TRANSIENT='HTTP 5[0-9][0-9]|HTTP 429|Server Error|Bad Gateway|Service Unavailable|Gateway Time-?out|rate limit|timed? ?out|Timeout|connection (reset|refused|closed)|(unexpected|early) EOF|TLS handshake|Could not resolve host|Temporary failure in name resolution|RPC failed|The requested URL returned error: 5[0-9][0-9]|network is unreachable|broken pipe'

ci_retry() {
  local attempts="${CI_RETRY_ATTEMPTS:-5}" base="${CI_RETRY_BASE:-10}"
  local attempt=1 rc out err
  err="$(mktemp)"
  while :; do
    out="$("$@" 2>"$err")" && rc=0 || rc=$?
    if [ "$rc" -eq 0 ]; then
      [ -n "$out" ] && printf '%s\n' "$out"
      rm -f "$err"
      return 0
    fi
    if ! grep -E -i -q "$_CI_RETRY_TRANSIENT" "$err" || [ "$attempt" -ge "$attempts" ]; then
      [ -n "$out" ] && printf '%s\n' "$out"
      cat "$err" >&2
      if [ "$attempt" -ge "$attempts" ]; then
        echo "ci_retry: gave up after ${attempt} attempts: $*" >&2
      fi
      rm -f "$err"
      return "$rc"
    fi
    echo "ci_retry: transient failure (attempt ${attempt}/${attempts}): $(head -n 1 "$err")" >&2
    sleep $((attempt * base))
    attempt=$((attempt + 1))
  done
}
