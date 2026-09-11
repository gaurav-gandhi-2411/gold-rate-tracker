// Pure logic for the PR-trigger-health check, kept free of any Workers-
// runtime API (fetch, KV bindings) or GitHub API shape -- callers pass
// already-normalized PR descriptors. index.mjs is the thin wiring layer
// around this file, matching deadman.mjs's own convention.

// AI2 (production audit continuation, 2026-09-11/12): AH2/AI1 (§8 instance
// #20, docs/SESSION_AUDIT_2026-08.md) found that a PR's own required checks
// can silently never start at all -- not fail, never even trigger -- and
// that branch protection's admin-bypass (ADR 028, accepted risk) means such
// a PR can merge anyway, with nothing having verified it. #1539 (the PR
// that caused a 7-hour production incident) and #1569 both did exactly
// this. `ci-health.yml` (#1569's own fix, PR #1569) does NOT cover this
// shape -- it watches whether master's already-triggered checks are green,
// never whether a given PR's checks started in the first place.
//
// This channel closes that gap. It deliberately does NOT live in GitHub
// Actions (a `.github/workflows/*.yml` file): the failure mode it watches
// for is GitHub Actions itself silently not triggering a `pull_request`
// event -- a workflow-based watcher shares exactly the same fate as the
// thing it watches (the "instance #1" shape this whole audit is named
// for: a control that can't see its own failure mode). The Cloudflare
// Worker this file lives in is the only scheduler in this system
// independent of GitHub Actions' own trigger machinery -- it already
// exists (worker-deadman), already runs on Cloudflare's own Cron Trigger
// every 30 minutes, and already pages via the same ntfy topic.
//
// N (how long a PR can sit with zero required check-runs before paging),
// derived from measurement, not chosen by feel: swept the last 30 days'
// non-bot PRs (n=92 as of 2026-09-11) for the delay between each PR's head
// commit's own timestamp (`commit.committer.date` -- NOT the PR's
// `created_at`, which is contaminated by PRs opened once and pushed to
// again much later, e.g. #1153 showed an 13,652-minute "delay" under a
// created_at-anchored measurement that was actually a 9-day-later re-push,
// not a slow trigger; caught and corrected before this constant was
// chosen) and the earliest `started_at` of that commit's `lint`/`pwa-js`
// check-runs, for the 88 PRs where triggering worked normally: min=0.12min
// max=8.48min median=0.42min p90=1.60min p95=2.20min p99=8.48min (p99 and
// max coincide -- the single slowest normal trigger observed, PR #1340).
// The same sweep found the 4 PRs already known from AH2 to have zero
// check-runs at all (#1569, #1539, #1435, #1419).
export const PR_TRIGGER_STALE_MINUTES = 30;
// Chosen to match the Worker's own existing 30-min cron resolution
// (wrangler.toml) exactly -- picking N smaller than the check interval
// buys no real detection-latency improvement (the Worker still only looks
// every 30 min), and N=30 already clears the measured max (8.48min) by
// >3.5x, so 0/88 normal-trigger PRs in this sample would have false-
// positived at this threshold. See the PR body for the cost/false-positive
// comparison against N=15 and N=60 before this value was picked.

/**
 * Classifies which open PRs (already fetched and normalized by the caller)
 * have gone stale: no `lint`/`pwa-js` check-run recorded against their head
 * commit, for at least PR_TRIGGER_STALE_MINUTES since that commit landed.
 *
 * @param {Array<{number: number, branch: string, headSha: string, headCommitIso: string, hasRequiredCheckRun: boolean}>} openPrs
 * @param {number} nowMs
 * @returns {Array<{number: number, branch: string, headSha: string, ageMinutes: number}>}
 */
export function classifyPrTriggerHealth(openPrs, nowMs) {
  const stale = [];
  for (const pr of openPrs) {
    if (pr.hasRequiredCheckRun) continue;
    const commitMs = Date.parse(pr.headCommitIso);
    // rule 98a: a commit timestamp that fails to parse is "cannot verify
    // age", not "assume fresh, skip silently" -- but this function only
    // ever receives PRs the caller already fetched successfully (the
    // fetch layer itself fails the whole batch closed on any API error,
    // see index.mjs); an unparseable date reaching here would mean the
    // GitHub API itself returned malformed data, worth surfacing rather
    // than swallowing.
    if (Number.isNaN(commitMs)) {
      stale.push({
        number: pr.number,
        branch: pr.branch,
        headSha: pr.headSha,
        ageMinutes: null,
        reason: `unparseable commit timestamp: ${pr.headCommitIso}`,
      });
      continue;
    }
    const ageMinutes = (nowMs - commitMs) / 60_000;
    if (ageMinutes >= PR_TRIGGER_STALE_MINUTES) {
      stale.push({ number: pr.number, branch: pr.branch, headSha: pr.headSha, ageMinutes, reason: null });
    }
  }
  return stale;
}

export function buildPrTriggerHealthAlert(stalePrs) {
  const list = stalePrs
    .map((pr) =>
      pr.reason
        ? `#${pr.number} (${pr.branch}, ${pr.reason})`
        : `#${pr.number} (${pr.branch}, ${pr.ageMinutes.toFixed(0)}min with zero check-runs)`,
    )
    .join("; ");
  return {
    title: "Gold Tracker: PR required checks never started",
    body:
      `${stalePrs.length} open PR(s) have gone >= ${PR_TRIGGER_STALE_MINUTES}min since their last push ` +
      `with no lint/pwa-js check-run recorded: ${list}. This is the shape that let #1539/#1569 merge ` +
      `with zero checks ever recorded against their head SHA (audit docs/SESSION_AUDIT_2026-08.md ` +
      `§8 instance #20) -- branch protection's admin-bypass (ADR 028) does not require a human ` +
      `to notice this before merging.`,
    priority: 4,
    tags: "warning",
  };
}

/**
 * Built when the fetch layer itself could not complete (any GitHub API
 * call in the batch failed) -- rule 98a: "could not verify" pages with an
 * honest message, it never silently skips this run's check the way the
 * pre-fix version of ci-health.yml's own guard did (PR #1583's own module
 * comment documents that exact failure shape).
 */
export function buildPrTriggerHealthFetchFailureAlert(failureReason) {
  return {
    title: "Gold Tracker: PR trigger-health check could not verify",
    body: `Could not fetch open-PR check status from the GitHub API -- failing closed rather than silently skipping this run. Reason: ${failureReason}`,
    priority: 3,
    tags: "warning",
  };
}

// Re-alert cadence for a PR that stays stuck across multiple 30-min ticks --
// reuses deadman.mjs's own REMINDER_INTERVAL_HOURS value (6h) rather than
// importing a shared constant across files for one number, matching this
// Worker's existing "one reminder cadence for sustained conditions"
// convention instead of inventing a second, differently-tuned one.
const PR_REMINDER_INTERVAL_HOURS = 6;

/**
 * Per-PR dedup: alert once when a PR first crosses PR_TRIGGER_STALE_MINUTES,
 * then at most once per PR_REMINDER_INTERVAL_HOURS while it stays stuck --
 * without this, a genuinely stuck PR (the #1539/#1569 shape, which can
 * persist for hours before anyone notices) would re-page every single
 * 30-min Worker tick for as long as it stays open, which is noise, not
 * signal. A PR that recovers (starts a check-run, or closes) simply stops
 * appearing in stalePrs on a later run -- nothing to clean up explicitly.
 *
 * @param {ReturnType<typeof classifyPrTriggerHealth>} stalePrs
 * @param {Record<string, number> | null} previousState PR number (string) -> last-alerted epoch ms
 * @param {number} nowMs
 */
export function decidePrTriggerHealthAction(stalePrs, previousState, nowMs) {
  const prior = previousState || {};
  const toAlert = [];
  const nextState = {};
  for (const pr of stalePrs) {
    const key = String(pr.number);
    const lastAlertedMs = prior[key];
    const dueForReminder =
      lastAlertedMs == null || nowMs - lastAlertedMs >= PR_REMINDER_INTERVAL_HOURS * 3_600_000;
    if (dueForReminder) {
      toAlert.push(pr);
      nextState[key] = nowMs;
    } else {
      nextState[key] = lastAlertedMs;
    }
  }
  // PRs no longer stale (recovered or closed) are simply absent from
  // nextState -- next time they go stale (if ever) it reads as a fresh
  // first occurrence, which is correct: a resolved-then-recurring problem
  // deserves its own alert, not silence because it happened once before.
  return { send: toAlert.length > 0, alertPrs: toAlert, nextState };
}
