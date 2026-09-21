import { test } from "node:test";
import assert from "node:assert/strict";
import {
  classifyPrTriggerHealth,
  decidePrTriggerHealthAction,
  buildPrTriggerHealthAlert,
  buildPrTriggerHealthFetchFailureAlert,
  classifyMergedUnchecked,
  decideMergedUncheckedAction,
  buildMergedUncheckedAlert,
  PR_TRIGGER_STALE_MINUTES,
  MERGED_SETTLE_MINUTES,
  MERGED_LOOKBACK_MINUTES,
} from "../src/pr_trigger_health.mjs";
import { runCheck } from "../src/index.mjs";

const MINUTE = 60_000;
const NOW = Date.parse("2026-09-11T14:00:00Z");

function isoMinutesAgo(minutes) {
  return new Date(NOW - minutes * MINUTE).toISOString();
}

function pr(overrides = {}) {
  return {
    number: 1234,
    branch: "feat/example",
    headSha: "abc123",
    headCommitIso: isoMinutesAgo(0),
    hasRequiredCheckRun: false,
    ...overrides,
  };
}

test("classifyPrTriggerHealth: a PR with a check-run is never stale, regardless of age", () => {
  const result = classifyPrTriggerHealth(
    [pr({ headCommitIso: isoMinutesAgo(999), hasRequiredCheckRun: true })],
    NOW,
  );
  assert.deepEqual(result, []);
});

test("classifyPrTriggerHealth: no check-run, just under the threshold, is not stale", () => {
  const result = classifyPrTriggerHealth(
    [pr({ headCommitIso: isoMinutesAgo(PR_TRIGGER_STALE_MINUTES - 0.01) })],
    NOW,
  );
  assert.deepEqual(result, []);
});

test("classifyPrTriggerHealth: no check-run, exactly at the threshold, is stale", () => {
  const result = classifyPrTriggerHealth([pr({ headCommitIso: isoMinutesAgo(PR_TRIGGER_STALE_MINUTES) })], NOW);
  assert.equal(result.length, 1);
  assert.equal(result[0].number, 1234);
});

test("classifyPrTriggerHealth: this is the exact reconstruction of #1539/#1569's own recorded state", () => {
  // Both PRs merged with zero check-runs, ever, against their head SHA
  // (verified live via commits/{sha}/check-runs and commits/{sha}/status
  // -- AI1, docs/SESSION_AUDIT_2026-08.md §8 instance #20). A PR sitting
  // open with hasRequiredCheckRun:false past the threshold is exactly
  // that state, reconstructed as a pressure test rather than merely
  // asserted to be covered.
  const result = classifyPrTriggerHealth(
    [
      pr({ number: 1539, headCommitIso: isoMinutesAgo(60), hasRequiredCheckRun: false }),
      pr({ number: 1569, headCommitIso: isoMinutesAgo(45), hasRequiredCheckRun: false }),
    ],
    NOW,
  );
  assert.deepEqual(
    result.map((p) => p.number),
    [1539, 1569],
  );
});

test("classifyPrTriggerHealth: a genuinely fresh PR (0min old, no check-run yet) is not stale -- the normal case", () => {
  // Every real PR passes through this state for the first few minutes
  // (median observed: 0.42min, max observed: 8.48min, n=88). The check
  // must not false-positive on this, or it would page on every single
  // PR opened.
  const result = classifyPrTriggerHealth([pr({ headCommitIso: isoMinutesAgo(2) })], NOW);
  assert.deepEqual(result, []);
});

test("classifyPrTriggerHealth: unparseable commit timestamp surfaces explicitly, not silently skipped", () => {
  const result = classifyPrTriggerHealth([pr({ headCommitIso: "not-a-date" })], NOW);
  assert.equal(result.length, 1);
  assert.equal(result[0].ageMinutes, null);
  assert.match(result[0].reason, /unparseable/);
});

test("buildPrTriggerHealthAlert: names the PR, branch, and age", () => {
  const alert = buildPrTriggerHealthAlert([{ number: 1539, branch: "fix/thing", ageMinutes: 45.2, reason: null }]);
  assert.match(alert.body, /#1539/);
  assert.match(alert.body, /fix\/thing/);
  assert.match(alert.body, /45min/);
  assert.equal(alert.priority, 4);
});

test("buildPrTriggerHealthAlert: multiple stale PRs are all named", () => {
  const alert = buildPrTriggerHealthAlert([
    { number: 1, branch: "a", ageMinutes: 30, reason: null },
    { number: 2, branch: "b", ageMinutes: 40, reason: null },
  ]);
  assert.match(alert.body, /#1/);
  assert.match(alert.body, /#2/);
  assert.match(alert.body, /2 open PR/);
});

test("buildPrTriggerHealthFetchFailureAlert: states the failure reason, fails closed not silent", () => {
  const alert = buildPrTriggerHealthFetchFailureAlert("list open PRs HTTP 403");
  assert.match(alert.body, /403/);
  assert.match(alert.body, /failing closed/);
});

test("decidePrTriggerHealthAction: first occurrence sends and records state", () => {
  const stale = [{ number: 1539, branch: "fix/x", ageMinutes: 45, reason: null }];
  const { send, alertPrs, nextState } = decidePrTriggerHealthAction(stale, null, NOW);
  assert.equal(send, true);
  assert.equal(alertPrs.length, 1);
  assert.equal(nextState["1539"], NOW);
});

test("decidePrTriggerHealthAction: same PR still stale 30 minutes later does NOT re-alert", () => {
  const stale = [{ number: 1539, branch: "fix/x", ageMinutes: 75, reason: null }];
  const previousState = { "1539": NOW - 30 * MINUTE };
  const { send, alertPrs } = decidePrTriggerHealthAction(stale, previousState, NOW);
  assert.equal(send, false);
  assert.deepEqual(alertPrs, []);
});

test("decidePrTriggerHealthAction: same PR still stale past the 6h reminder interval DOES re-alert", () => {
  const stale = [{ number: 1539, branch: "fix/x", ageMinutes: 400, reason: null }];
  const sixHoursAndAMinuteAgo = NOW - (6 * 60 + 1) * MINUTE;
  const previousState = { "1539": sixHoursAndAMinuteAgo };
  const { send, alertPrs, nextState } = decidePrTriggerHealthAction(stale, previousState, NOW);
  assert.equal(send, true);
  assert.equal(alertPrs.length, 1);
  assert.equal(nextState["1539"], NOW);
});

test("decidePrTriggerHealthAction: a PR that recovers (no longer in stalePrs) is silently dropped from state", () => {
  const previousState = { "1539": NOW - 60 * MINUTE };
  const { send, nextState } = decidePrTriggerHealthAction([], previousState, NOW);
  assert.equal(send, false);
  assert.deepEqual(nextState, {});
});

test("decidePrTriggerHealthAction: a NEW PR going stale alerts even while an old one is in its dedup window", () => {
  const stale = [
    { number: 1539, branch: "fix/x", ageMinutes: 45, reason: null }, // recently alerted
    { number: 9999, branch: "fix/y", ageMinutes: 30, reason: null }, // brand new
  ];
  const previousState = { "1539": NOW - 10 * MINUTE };
  const { send, alertPrs } = decidePrTriggerHealthAction(stale, previousState, NOW);
  assert.equal(send, true);
  assert.deepEqual(
    alertPrs.map((p) => p.number),
    [9999],
  );
});

// ---------------------------------------------------------------------------
// AL3a: merged-unchecked scan
// ---------------------------------------------------------------------------

function merged(overrides = {}) {
  return {
    number: 4321,
    branch: "feat/example",
    headSha: "def456",
    mergedAtIso: isoMinutesAgo(30),
    hasRequiredCheckRun: false,
    ...overrides,
  };
}

test("classifyMergedUnchecked: a merged PR with a check-run is never flagged", () => {
  assert.deepEqual(classifyMergedUnchecked([merged({ hasRequiredCheckRun: true })], NOW), []);
});

test("classifyMergedUnchecked: merged with no check-run and past the settle window is flagged", () => {
  const out = classifyMergedUnchecked([merged({ mergedAtIso: isoMinutesAgo(30) })], NOW);
  assert.equal(out.length, 1);
  assert.equal(out[0].number, 4321);
  assert.ok(Math.abs(out[0].ageMinutes - 30) < 0.01);
});

test("classifyMergedUnchecked: merged inside the settle window is not judged yet (check-runs may still appear)", () => {
  assert.deepEqual(
    classifyMergedUnchecked([merged({ mergedAtIso: isoMinutesAgo(MERGED_SETTLE_MINUTES - 0.1) })], NOW),
    [],
  );
  assert.equal(
    classifyMergedUnchecked([merged({ mergedAtIso: isoMinutesAgo(MERGED_SETTLE_MINUTES + 0.1) })], NOW).length,
    1,
  );
});

test("classifyMergedUnchecked: merged before the lookback window is left to earlier ticks", () => {
  assert.deepEqual(
    classifyMergedUnchecked([merged({ mergedAtIso: isoMinutesAgo(MERGED_LOOKBACK_MINUTES + 1) })], NOW),
    [],
  );
});

test("classifyMergedUnchecked: an unparseable merge time fails closed (flagged), not skipped", () => {
  const out = classifyMergedUnchecked([merged({ mergedAtIso: "not-a-date" })], NOW);
  assert.equal(out.length, 1);
  assert.match(out[0].reason, /unparseable merged_at/);
});

// The two incidents, from their REAL recorded state (fetched 2026-09-21 with `gh api`): head
// SHA at merge, merge time, 0 check-runs on the head SHA, commit status `pending`.
const REAL_1539 = {
  number: 1539,
  branch: "fix/auto-commit-workflows-refresh-injected-docs",
  headSha: "e88aca46d183a6079d0716f5f34ae006141d122f",
  headCommitIso: "2026-09-10T13:33:18Z",
  mergedAtIso: "2026-09-10T13:38:30Z",
  hasRequiredCheckRun: false,
};
const REAL_1569 = {
  number: 1569,
  branch: "feat/ci-health-check",
  headSha: "cc09753d2d0fba8c64d39c815cb22a7b77b06efc",
  headCommitIso: "2026-09-11T08:30:45Z",
  mergedAtIso: "2026-09-11T10:27:11Z",
  hasRequiredCheckRun: false,
};

// The Worker only runs at :00 and :30. First tick (after the merge) at which the scan flags it.
function firstFlaggingTick(pr) {
  const merge = Date.parse(pr.mergedAtIso);
  for (let t = Math.ceil(merge / (30 * MINUTE)) * 30 * MINUTE; t <= merge + 6 * 3_600_000; t += 30 * MINUTE) {
    if (classifyMergedUnchecked([pr], t).length) return t;
  }
  return null;
}

test("REAL #1539: the open-PR channel could never page it, the merged scan pages it within one tick", () => {
  // Contrast: while #1539 was open (head commit -> merge = 5.2 min) no 30-min tick falls inside.
  const openMs = Date.parse(REAL_1539.mergedAtIso) - Date.parse(REAL_1539.headCommitIso);
  assert.ok(openMs < PR_TRIGGER_STALE_MINUTES * MINUTE);
  const t = firstFlaggingTick(REAL_1539);
  assert.equal(new Date(t).toISOString(), "2026-09-10T14:00:00.000Z");
  assert.ok((t - Date.parse(REAL_1539.mergedAtIso)) / MINUTE < 30, "paged within 30 min of the merge");
});

test("REAL #1569: paged by the merged scan too (its 2.8-min-old merge at the 10:30 tick is inside the settle window)", () => {
  const t = firstFlaggingTick(REAL_1569);
  assert.equal(new Date(t).toISOString(), "2026-09-11T11:00:00.000Z");
});

test("decideMergedUncheckedAction: alerts once per PR, then never again; prunes old state", () => {
  const flagged = classifyMergedUnchecked([merged()], NOW);
  const first = decideMergedUncheckedAction(flagged, null, NOW);
  assert.equal(first.send, true);
  const second = decideMergedUncheckedAction(flagged, first.nextState, NOW + 30 * MINUTE);
  assert.equal(second.send, false, "the same merged PR must not re-page on later ticks");
  const old = { "1": NOW - 8 * 24 * 3_600_000, "2": NOW - 1 * 3_600_000 };
  assert.deepEqual(Object.keys(decideMergedUncheckedAction([], old, NOW).nextState), ["2"]);
});

test("buildMergedUncheckedAlert: names each PR and its branch", () => {
  const a = buildMergedUncheckedAlert([{ number: 1539, branch: "fix/x", ageMinutes: 21.5, reason: null }]);
  assert.match(a.body, /#1539 \(fix\/x, merged 22min ago\)/);
  assert.equal(a.priority, 4);
});

// --- runCheck end to end, mocked GitHub API + in-memory KV --------------------------------------

function fakeKv() {
  const store = new Map();
  return { async get(k) { return store.has(k) ? store.get(k) : null; }, async put(k, v) { store.set(k, v); } };
}

function mockWorldFetch(
  { nowMs, open = [], commitIsoBySha = {}, closed = [], checkRunsBySha = {}, closedStatus = 200 },
  ntfyCalls,
) {
  const fresh = new Date(nowMs - 30 * MINUTE).toISOString();
  return async (url, opts) => {
    if (url.includes("forecast.json")) return { ok: true, json: async () => ({ predicted_at: fresh, scraped_at: fresh }) };
    if (url.startsWith("https://ntfy.sh/")) { ntfyCalls.push({ url, opts }); return { ok: true }; }
    if (url.includes("/pulls?state=open")) return { ok: true, json: async () => open };
    if (url.includes("/pulls?state=closed")) return { ok: closedStatus === 200, status: closedStatus, json: async () => closed };
    const checks = url.match(/commits\/([0-9a-f]+)\/check-runs/);
    if (checks) return { ok: true, json: async () => ({ check_runs: checkRunsBySha[checks[1]] || [] }) };
    const commit = url.match(/commits\/([0-9a-f]+)$/);
    if (commit) return { ok: true, json: async () => ({ commit: { committer: { date: commitIsoBySha[commit[1]] } } }) };
    throw new Error(`unexpected fetch: ${url}`);
  };
}

const closedPr = (pr) => ({ number: pr.number, head: { ref: pr.branch, sha: pr.headSha }, merged_at: pr.mergedAtIso });
const alertsOf = (calls) => calls.filter((c) => c.opts.headers.Title.includes("merged with no required check"));

test("runCheck: pages for a REAL #1539-shaped merge (closed PR, 0 check-runs on its head SHA), once", async () => {
  const nowMs = Date.parse("2026-09-10T14:00:00Z"); // the first tick after #1539 merged
  const ntfyCalls = [];
  const env = { NTFY_TOPIC: "t", GITHUB_PR_HEALTH_PAT: "pat", DEADMAN_STATE: fakeKv() };
  const fetchImpl = mockWorldFetch({ nowMs, closed: [closedPr(REAL_1539)] }, ntfyCalls);

  const first = await runCheck(env, fetchImpl, nowMs);
  assert.equal(first.mergedUncheckedSent, true);
  assert.equal(first.mergedUncheckedCount, 1);
  const alerts = alertsOf(ntfyCalls);
  assert.equal(alerts.length, 1);
  assert.match(alerts[0].opts.body, /#1539 \(fix\/auto-commit-workflows-refresh-injected-docs/);

  const second = await runCheck(env, fetchImpl, nowMs + 30 * MINUTE);
  assert.equal(second.mergedUncheckedSent, false, "no repeat page on the next tick");
  assert.equal(alertsOf(ntfyCalls).length, 1);
});

test("runCheck: a merged PR that DID get a lint check-run (including a bot/ PR) does not page", async () => {
  const nowMs = Date.parse("2026-09-10T14:00:00Z");
  const ntfyCalls = [];
  const botPr = { ...REAL_1539, number: 1600, branch: "bot/docs-refresh", headSha: "aaaa1111" };
  const env = { NTFY_TOPIC: "t", GITHUB_PR_HEALTH_PAT: "pat", DEADMAN_STATE: fakeKv() };
  const fetchImpl = mockWorldFetch(
    { nowMs, closed: [closedPr(botPr)], checkRunsBySha: { aaaa1111: [{ name: "lint" }, { name: "docs-freshness" }] } },
    ntfyCalls,
  );
  const result = await runCheck(env, fetchImpl, nowMs);
  assert.equal(result.mergedUncheckedCount, 0);
  assert.equal(alertsOf(ntfyCalls).length, 0);
});

test("runCheck: a check-run that is not lint/pwa-js does not count as the required check", async () => {
  const nowMs = Date.parse("2026-09-10T14:00:00Z");
  const ntfyCalls = [];
  const env = { NTFY_TOPIC: "t", GITHUB_PR_HEALTH_PAT: "pat", DEADMAN_STATE: fakeKv() };
  const fetchImpl = mockWorldFetch(
    { nowMs, closed: [closedPr(REAL_1539)], checkRunsBySha: { [REAL_1539.headSha]: [{ name: "docs-freshness" }] } },
    ntfyCalls,
  );
  const result = await runCheck(env, fetchImpl, nowMs);
  assert.equal(result.mergedUncheckedCount, 1);
});

test("runCheck: GitHub API failure pages honestly (fail closed) instead of silently skipping the scan", async () => {
  const nowMs = Date.parse("2026-09-10T14:00:00Z");
  const ntfyCalls = [];
  const env = { NTFY_TOPIC: "t", GITHUB_PR_HEALTH_PAT: "pat", DEADMAN_STATE: fakeKv() };
  const fetchImpl = mockWorldFetch({ nowMs, closedStatus: 500 }, ntfyCalls);
  const result = await runCheck(env, fetchImpl, nowMs);
  assert.equal(result.mergedUncheckedSent, true);
  assert.ok(ntfyCalls.some((c) => c.opts.headers.Title.includes("merged-PR check scan could not verify")));
});

test("runCheck: no GITHUB_PR_HEALTH_PAT -> the scan is skipped (feature not deployed), not paged about", async () => {
  const nowMs = Date.parse("2026-09-10T14:00:00Z");
  const ntfyCalls = [];
  const env = { NTFY_TOPIC: "t", DEADMAN_STATE: fakeKv() };
  const result = await runCheck(env, mockWorldFetch({ nowMs, closed: [closedPr(REAL_1539)] }, ntfyCalls), nowMs);
  assert.equal(result.mergedUncheckedSent, false);
  assert.equal(alertsOf(ntfyCalls).length, 0);
});

// --- open-PR channel: bot/ PRs are no longer skipped (the #1578 shape) --------------------------

const openPr = (number, ref, sha) => ({ number, head: { ref, sha } });
const openAlerts = (calls) => calls.filter((c) => c.opts.headers.Title.includes("required checks never started"));

test("runCheck: an OPEN bot/ PR with no check-run for 45 min pages (the #1578 shape; it was skipped by construction)", async () => {
  const nowMs = Date.parse("2026-09-21T06:00:00Z");
  const ntfyCalls = [];
  const env = { NTFY_TOPIC: "t", GITHUB_PR_HEALTH_PAT: "pat", DEADMAN_STATE: fakeKv() };
  const fetchImpl = mockWorldFetch(
    {
      nowMs,
      open: [openPr(1578, "bot/docs-refresh", "89b62565")],
      commitIsoBySha: { "89b62565": new Date(nowMs - 45 * MINUTE).toISOString() },
    },
    ntfyCalls,
  );
  const result = await runCheck(env, fetchImpl, nowMs);
  assert.equal(result.prTriggerHealthStaleCount, 1);
  assert.equal(openAlerts(ntfyCalls).length, 1);
  assert.match(openAlerts(ntfyCalls)[0].opts.body, /#1578 \(bot\/docs-refresh/);
});

test("runCheck: an open bot/ PR that DOES have its lint check-run does not page; scratch/ PRs stay excluded", async () => {
  const nowMs = Date.parse("2026-09-21T06:00:00Z");
  const ntfyCalls = [];
  const env = { NTFY_TOPIC: "t", GITHUB_PR_HEALTH_PAT: "pat", DEADMAN_STATE: fakeKv() };
  const old = new Date(nowMs - 45 * MINUTE).toISOString();
  const fetchImpl = mockWorldFetch(
    {
      nowMs,
      open: [openPr(1700, "bot/gold-prices", "bbbb2222"), openPr(1701, "scratch/proof", "cccc3333")],
      commitIsoBySha: { bbbb2222: old, cccc3333: old },
      checkRunsBySha: { bbbb2222: [{ name: "lint" }] },
    },
    ntfyCalls,
  );
  const result = await runCheck(env, fetchImpl, nowMs);
  assert.equal(result.prTriggerHealthStaleCount, 0);
  assert.equal(openAlerts(ntfyCalls).length, 0);
});

test("runCheck: the response echoes the scan's window constants", async () => {
  const nowMs = Date.parse("2026-09-10T14:00:00Z");
  const result = await runCheck({ NTFY_TOPIC: "t", DEADMAN_STATE: fakeKv() }, mockWorldFetch({ nowMs }, []), nowMs);
  assert.equal(result.thresholds.mergedSettleMinutes, MERGED_SETTLE_MINUTES);
  assert.equal(result.thresholds.mergedLookbackMinutes, MERGED_LOOKBACK_MINUTES);
});
