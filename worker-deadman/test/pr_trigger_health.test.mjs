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
  REQUIRED_CONTEXTS,
  evaluateRequiredChecks,
} from "../src/pr_trigger_health.mjs";
import { readFileSync } from "node:fs";
import worker, { runCheck } from "../src/index.mjs";
import { ESCALATE_THRESHOLD_HOURS } from "../src/deadman.mjs";
import { createHash } from "node:crypto";

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
    requiredOk: false,
    ...overrides,
  };
}

test("classifyMergedUnchecked: a merged PR whose required checks all passed is never flagged", () => {
  assert.deepEqual(classifyMergedUnchecked([merged({ requiredOk: true })], NOW), []);
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
  requiredOk: false,
};
const REAL_1569 = {
  number: 1569,
  branch: "feat/ci-health-check",
  headSha: "cc09753d2d0fba8c64d39c815cb22a7b77b06efc",
  headCommitIso: "2026-09-11T08:30:45Z",
  mergedAtIso: "2026-09-11T10:27:11Z",
  requiredOk: false,
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
  { nowMs, open = [], commitIsoBySha = {}, closed = [], checkRunsBySha = {}, closedStatus = 200, ntfy = null, ageHours = 0.5 },
  ntfyCalls,
) {
  const fresh = new Date(nowMs - ageHours * 60 * MINUTE).toISOString();
  return async (url, opts) => {
    if (url.includes("forecast.json")) return { ok: true, json: async () => ({ predicted_at: fresh, scraped_at: fresh }) };
    if (url.startsWith("https://ntfy.sh/")) {
      ntfyCalls.push({ url, opts });
      // `ntfy` lets a test decide what ntfy answers (or throw for a network failure); default is a bare 200.
      return ntfy ? ntfy(ntfyCalls.length) : { ok: true };
    }
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
const alertsOf = (calls) => calls.filter((c) => c.opts.headers.Title.includes("merged with a required check"));

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

test("runCheck: a merged PR whose required checks all SUCCEEDED (including a bot/ PR) does not page", async () => {
  const nowMs = Date.parse("2026-09-10T14:00:00Z");
  const ntfyCalls = [];
  const botPr = { ...REAL_1539, number: 1600, branch: "bot/docs-refresh", headSha: "aaaa1111" };
  const env = { NTFY_TOPIC: "t", GITHUB_PR_HEALTH_PAT: "pat", DEADMAN_STATE: fakeKv() };
  const fetchImpl = mockWorldFetch(
    {
      nowMs,
      closed: [closedPr(botPr)],
      checkRunsBySha: {
        aaaa1111: [
          { name: "lint", conclusion: "success", started_at: "2026-09-10T13:34:00Z" },
          { name: "pwa-js", conclusion: "success", started_at: "2026-09-10T13:34:01Z" },
          { name: "docs-freshness", conclusion: "failure", started_at: "2026-09-10T13:34:02Z" },
        ],
      },
    },
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

// ---------------------------------------------------------------------------
// AN3: "sent: true" must mean ntfy accepted the message
// ---------------------------------------------------------------------------
// 2026-09-21: prTriggerHealthSent:true and the daily heartbeat marked sent in KV, while nothing reached
// the phone. postToNtfy's result was never read, and each channel persisted its dedup state BEFORE
// sending, so a failed send was also never retried.

const TICK = 30 * MINUTE;
const httpFail = (status) => () => ({ ok: false, status });
const netFail = () => () => { throw new Error("connect ECONNRESET ntfy.sh"); };
const okWithId = (id) => () => ({ ok: true, status: 200, json: async () => ({ id }) });
const nonHeartbeat = (calls) => calls.filter((c) => !c.opts.headers.Title.includes("heartbeat"));

test("delivery: a non-2xx from ntfy is NOT reported as sent, and the merged alert is retried next tick", async () => {
  const nowMs = Date.parse("2026-09-10T14:00:00Z");
  const kv = fakeKv();
  const env = { NTFY_TOPIC: "t", GITHUB_PR_HEALTH_PAT: "pat", DEADMAN_STATE: kv };
  const calls = [];
  const failing = mockWorldFetch({ nowMs, closed: [closedPr(REAL_1539)], ntfy: httpFail(429) }, calls);

  const first = await runCheck(env, failing, nowMs);
  assert.equal(first.mergedUncheckedCount, 1);
  assert.equal(first.mergedUncheckedSent, false, "a 429 must not read as sent");
  assert.ok(first.ntfy.failed >= 1);
  assert.equal(first.ntfy.failures[0].status, 429);
  assert.equal(await kv.get("deadman:merged_unchecked_state"), null, "an undelivered alert must not be recorded as alerted");

  // Next tick, ntfy healthy: the SAME alert must now go out (before this fix it was suppressed forever).
  const healthyCalls = [];
  const healthy = mockWorldFetch({ nowMs: nowMs + TICK, closed: [closedPr(REAL_1539)], ntfy: okWithId("msg1") }, healthyCalls);
  const second = await runCheck(env, healthy, nowMs + TICK);
  assert.equal(second.mergedUncheckedSent, true);
  assert.equal(alertsOf(healthyCalls).length, 1);
});

test("delivery: a network error is a failed delivery, not an exception and not 'sent'", async () => {
  const nowMs = Date.parse("2026-09-21T06:00:00Z");
  const calls = [];
  const env = { NTFY_TOPIC: "t", GITHUB_PR_HEALTH_PAT: "pat", DEADMAN_STATE: fakeKv() };
  const fetchImpl = mockWorldFetch(
    {
      nowMs,
      open: [openPr(1791, "bot/docs-refresh", "32e62faa")],
      commitIsoBySha: { "32e62faa": new Date(nowMs - 120 * MINUTE).toISOString() },
      ntfy: netFail(),
    },
    calls,
  );
  const result = await runCheck(env, fetchImpl, nowMs);
  assert.equal(result.prTriggerHealthStaleCount, 1);
  assert.equal(result.prTriggerHealthSent, false);
  assert.match(result.ntfy.failures[0].error, /ECONNRESET/);
});

test("delivery: a failed PR-health page is retried next tick, not suppressed for the 6h reminder window", async () => {
  const nowMs = Date.parse("2026-09-21T06:00:00Z");
  const kv = fakeKv();
  const env = { NTFY_TOPIC: "t", GITHUB_PR_HEALTH_PAT: "pat", DEADMAN_STATE: kv };
  const world = (ntfy, at) => mockWorldFetch(
    {
      nowMs: at,
      open: [openPr(1791, "bot/docs-refresh", "32e62faa")],
      commitIsoBySha: { "32e62faa": new Date(nowMs - 120 * MINUTE).toISOString() },
      ntfy,
    },
    [],
  );
  const first = await runCheck(env, world(httpFail(503), nowMs), nowMs);
  assert.equal(first.prTriggerHealthSent, false);
  const second = await runCheck(env, world(okWithId("m2"), nowMs + TICK), nowMs + TICK);
  assert.equal(second.prTriggerHealthSent, true, "must retry on the next tick");
});

test("delivery: the daily heartbeat is only marked done when it was actually delivered", async () => {
  const nowMs = Date.parse("2026-09-21T00:10:00Z");
  const kv = fakeKv();
  const env = { NTFY_TOPIC: "t", DEADMAN_STATE: kv };
  const first = await runCheck(env, mockWorldFetch({ nowMs, ntfy: httpFail(429) }, []), nowMs);
  assert.equal(first.heartbeatSent, false);
  assert.equal(await kv.get("deadman:last_heartbeat_date_ist"), null, "KV must not say the heartbeat was sent");
  const second = await runCheck(env, mockWorldFetch({ nowMs: nowMs + TICK, ntfy: okWithId("hb1") }, []), nowMs + TICK);
  assert.equal(second.heartbeatSent, true);
  assert.notEqual(await kv.get("deadman:last_heartbeat_date_ist"), null);
});

test("delivery: a failed staleness ESCALATE is retried within the reminder window (it used to be suppressed)", async () => {
  const nowMs = Date.parse("2026-09-21T06:00:00Z");
  const env = { NTFY_TOPIC: "t", DEADMAN_STATE: fakeKv() };
  const stale = (ntfy, at) => mockWorldFetch({ nowMs: at, ageHours: ESCALATE_THRESHOLD_HOURS + 1, ntfy }, []);
  const first = await runCheck(env, stale(httpFail(500), nowMs), nowMs);
  assert.equal(first.level, "escalate");
  assert.equal(first.sent, false);
  const second = await runCheck(env, stale(okWithId("e1"), nowMs + 20 * MINUTE), nowMs + 20 * MINUTE);
  assert.equal(second.sent, true, "the undelivered ESCALATE must go out on the next tick");
});

test("delivery: ntfy's message id and a topic fingerprint are reported, never the topic itself", async () => {
  const nowMs = Date.parse("2026-09-21T00:10:00Z");
  const topic = "some-secret-topic-name";
  const env = { NTFY_TOPIC: topic, DEADMAN_STATE: fakeKv() };
  const result = await runCheck(env, mockWorldFetch({ nowMs, ntfy: okWithId("abc123") }, []), nowMs);
  assert.equal(result.ntfy.lastDelivery.id, "abc123");
  assert.equal(result.ntfy.lastDelivery.ok, true);
  assert.equal(result.ntfy.topicFingerprint, createHash("sha256").update(topic).digest("hex").slice(0, 8));
  assert.ok(!JSON.stringify(result).includes(topic), "the topic must not appear anywhere in the response");
});

test("delivery: with nothing to send the response still echoes the last persisted delivery", async () => {
  const kv = fakeKv();
  const env = { NTFY_TOPIC: "t", DEADMAN_STATE: kv };
  const t0 = Date.parse("2026-09-21T00:10:00Z");
  await runCheck(env, mockWorldFetch({ nowMs: t0, ntfy: okWithId("hb9") }, []), t0); // heartbeat -> persisted
  const later = await runCheck(env, mockWorldFetch({ nowMs: t0 + TICK }, []), t0 + TICK); // nothing to send
  assert.equal(later.ntfy.attempted, 0);
  assert.equal(later.ntfy.lastDelivery.id, "hb9");
});

test("scheduled(): a failed delivery fails the invocation (recorded as an exception), a healthy run does not", async () => {
  const nowMs = Date.now();
  const realFetch = globalThis.fetch;
  try {
    for (const [label, ntfy, expectReject] of [["429", httpFail(429), true], ["ok", okWithId("s1"), false]]) {
      globalThis.fetch = mockWorldFetch({ nowMs, ntfy }, []);
      let captured;
      await worker.scheduled({}, { NTFY_TOPIC: "t", DEADMAN_STATE: fakeKv() }, { waitUntil: (p) => { captured = p; } });
      if (expectReject) await assert.rejects(captured, /ntfy delivery failed/, label);
      else await captured;
    }
  } finally {
    globalThis.fetch = realFetch;
  }
});

// ---------------------------------------------------------------------------
// AN8: the merged-unchecked predicate is "every required context SUCCESS", not "any check-run exists"
// ---------------------------------------------------------------------------
// The first version (#1789) flagged only PRs with ZERO check-runs, which caught #1539 and #1569 but
// missed #1541 -- merged over a FAILING lint. The predicate is now the one
// scripts/check_required_checks_positive.py applies before a self-merge, pinned to it by shared fixtures.

const FIXTURES = JSON.parse(readFileSync(new URL("./fixtures/required_checks_cases.json", import.meta.url), "utf8"));
const category = (v) => (v === "SUCCESS" ? "SUCCESS" : v.startsWith("ABSENT") ? "ABSENT" : "NOT SUCCESS");
const fixtureNamed = (prefix) => FIXTURES.cases.find((c) => c.name.startsWith(prefix));

test("AN8 parity: evaluateRequiredChecks agrees with the Python spec on every shared fixture case", () => {
  assert.deepEqual(FIXTURES.required_contexts, REQUIRED_CONTEXTS, "the Worker's required contexts drifted from the fixtures'");
  assert.ok(FIXTURES.cases.length >= 10, "fixtures went missing");
  for (const c of FIXTURES.cases) {
    const { allPass, results } = evaluateRequiredChecks(REQUIRED_CONTEXTS, c.check_runs);
    assert.equal(allPass, c.expected.all_pass, c.name);
    const statuses = Object.fromEntries(Object.entries(results).map(([k, v]) => [k, category(v)]));
    assert.deepEqual(statuses, c.expected.statuses, c.name);
  }
});

// The three real incidents' recorded GitHub state (fixtures), with their real merge times.
const REAL = [
  { fx: "REAL #1539", number: 1539, branch: "fix/auto-commit-workflows-refresh-injected-docs", merged: "2026-09-10T13:38:30Z" },
  { fx: "REAL #1569", number: 1569, branch: "feat/ci-health-check", merged: "2026-09-11T10:27:11Z" },
  { fx: "REAL #1541", number: 1541, branch: "fix/check-price-cron-hour-shift-v2", merged: "2026-09-11T02:47:41Z" },
];

test("AN8: all THREE real incident SHAs page through runCheck, including #1541 (lint FAILED, not merely absent)", async () => {
  for (const r of REAL) {
    const fx = fixtureNamed(r.fx);
    const sha = fx.source.head_sha;
    // First :00/:30 tick at least the settle window after the merge.
    const mergeMs = Date.parse(r.merged);
    const nowMs = Math.ceil((mergeMs + MERGED_SETTLE_MINUTES * MINUTE) / TICK) * TICK;
    const calls = [];
    const env = { NTFY_TOPIC: "t", GITHUB_PR_HEALTH_PAT: "pat", DEADMAN_STATE: fakeKv() };
    const world = mockWorldFetch(
      { nowMs, closed: [{ number: r.number, head: { ref: r.branch, sha }, merged_at: r.merged }], checkRunsBySha: { [sha]: fx.check_runs } },
      calls,
    );
    const result = await runCheck(env, world, nowMs);
    assert.equal(result.mergedUncheckedCount, 1, r.fx);
    assert.equal(result.mergedUncheckedSent, true, r.fx);
    assert.equal(alertsOf(calls).length, 1, r.fx);
  }
});

test("AN8: #1541's alert says WHAT was wrong (lint NOT SUCCESS), not just that something was", async () => {
  const fx = fixtureNamed("REAL #1541");
  const sha = fx.source.head_sha;
  const nowMs = Date.parse("2026-09-11T03:00:00Z");
  const calls = [];
  const world = mockWorldFetch(
    {
      nowMs,
      closed: [{ number: 1541, head: { ref: "fix/check-price-cron-hour-shift-v2", sha }, merged_at: "2026-09-11T02:47:41Z" }],
      checkRunsBySha: { [sha]: fx.check_runs },
    },
    calls,
  );
  await runCheck({ NTFY_TOPIC: "t", GITHUB_PR_HEALTH_PAT: "pat", DEADMAN_STATE: fakeKv() }, world, nowMs);
  const body = alertsOf(calls)[0].opts.body;
  assert.match(body, /#1541/);
  assert.match(body, /lint: NOT SUCCESS \(conclusion="failure"\)/);
  assert.ok(!/pwa-js:/.test(body), "pwa-js passed and must not be listed as a problem");
});

test("AN8: the real all-green bot PR does not page", async () => {
  const fx = FIXTURES.cases.find((c) => c.name.startsWith("REAL bot PR"));
  const sha = fx.source.head_sha;
  const nowMs = Date.parse("2026-09-21T09:00:00Z");
  const calls = [];
  const world = mockWorldFetch(
    {
      nowMs,
      closed: [{ number: fx.source.pr, head: { ref: "bot/data-sync", sha }, merged_at: "2026-09-21T08:40:00Z" }],
      checkRunsBySha: { [sha]: fx.check_runs },
    },
    calls,
  );
  const result = await runCheck({ NTFY_TOPIC: "t", GITHUB_PR_HEALTH_PAT: "pat", DEADMAN_STATE: fakeKv() }, world, nowMs);
  assert.equal(result.mergedUncheckedCount, 0);
  assert.equal(alertsOf(calls).length, 0);
});

test("AN8: a merged PR whose required check was still running (conclusion null) 10+ min after merge is flagged", () => {
  const { allPass, results } = evaluateRequiredChecks(REQUIRED_CONTEXTS, [
    { name: "lint", conclusion: null, started_at: "2026-09-21T08:00:00Z" },
    { name: "pwa-js", conclusion: "success", started_at: "2026-09-21T08:00:01Z" },
  ]);
  assert.equal(allPass, false);
  assert.match(results.lint, /NOT SUCCESS/);
  const flagged = classifyMergedUnchecked([merged({ requiredOk: allPass, problems: ["lint: " + results.lint] })], NOW);
  assert.equal(flagged.length, 1);
  assert.match(flagged[0].problems[0], /lint/);
});

// ---------------------------------------------------------------------------
// AQ1c (2026-09-21): the merged scan must not abort on a busy merge day
// ---------------------------------------------------------------------------
// It used one 10s AbortController for the whole scan and read check-runs sequentially. Run against the
// real repo on a day with ~36 merges in 6h: 30 of 31 calls succeeded and the 31st was aborted, so it
// paged "could not verify" on every tick. These tests give each check-runs call real latency and honour
// the abort signal, which is what makes the shared-timeout bug observable.

const GREEN = (sha) => [
  { name: "lint", conclusion: "success", started_at: "2026-09-21T10:00:00Z", head_sha: sha },
  { name: "pwa-js", conclusion: "success", started_at: "2026-09-21T10:00:01Z", head_sha: sha },
];

function latencyWorld({ nowMs, closed, delayMs, statusBySha = {}, checkRunsBySha = {} }, ntfyCalls, stats) {
  const base = mockWorldFetch({ nowMs, closed, checkRunsBySha }, ntfyCalls);
  return async (url, opts = {}) => {
    const m = url.match(/commits\/([0-9a-f]+)\/check-runs/);
    if (!m) return base(url, opts);
    stats.checkRunCalls.push(m[1]);
    stats.inFlight++;
    stats.maxInFlight = Math.max(stats.maxInFlight, stats.inFlight);
    try {
      await new Promise((resolve, reject) => {
        const t = setTimeout(resolve, delayMs);
        opts.signal?.addEventListener("abort", () => { clearTimeout(t); reject(new Error("This operation was aborted")); });
      });
    } finally {
      stats.inFlight--;
    }
    const status = statusBySha[m[1]] || 200;
    return { ok: status === 200, status, json: async () => ({ check_runs: checkRunsBySha[m[1]] ?? GREEN(m[1]) }) };
  };
}

const manyMerged = (nowMs, n) =>
  Array.from({ length: n }, (_, i) =>
    closedPr({
      number: 2000 + i,
      branch: i % 2 ? "bot/data-sync" : `fix/thing-${i}`,
      headSha: (0xabc000 + i).toString(16),
      mergedAtIso: new Date(nowMs - (20 + i) * 60_000).toISOString(),
    }),
  );

test("AQ1c: 30 merged PRs with per-call latency verify fine (the old shared-timeout scan aborted)", async () => {
  const nowMs = Date.parse("2026-09-21T12:00:00Z");
  const calls = [];
  const stats = { checkRunCalls: [], inFlight: 0, maxInFlight: 0 };
  const world = latencyWorld({ nowMs, closed: manyMerged(nowMs, 30), delayMs: 350 }, calls, stats);
  const started = Date.now();
  const result = await runCheck({ NTFY_TOPIC: "t", GITHUB_PR_HEALTH_PAT: "pat", DEADMAN_STATE: fakeKv() }, world, nowMs);
  const elapsed = Date.now() - started;
  assert.equal(stats.checkRunCalls.length, 30, "every merged PR is verified");
  assert.equal(result.mergedUncheckedCount, 0);
  assert.equal(calls.filter((c) => c.opts.headers.Title.includes("could not verify")).length, 0, "no false 'could not verify'");
  // Sequential would take 30 * 350ms = 10.5s, past the 10s budget that used to abort the scan.
  assert.ok(elapsed < 6000, `scan took ${elapsed}ms; it should be concurrent (~2s), not sequential (~10.5s)`);
});

test("AQ1c: concurrency is bounded (not sequential, not unbounded)", async () => {
  const nowMs = Date.parse("2026-09-21T12:00:00Z");
  const stats = { checkRunCalls: [], inFlight: 0, maxInFlight: 0 };
  const world = latencyWorld({ nowMs, closed: manyMerged(nowMs, 30), delayMs: 60 }, [], stats);
  await runCheck({ NTFY_TOPIC: "t", GITHUB_PR_HEALTH_PAT: "pat", DEADMAN_STATE: fakeKv() }, world, nowMs);
  assert.ok(stats.maxInFlight >= 2, `expected concurrent reads, saw max ${stats.maxInFlight}`);
  assert.ok(stats.maxInFlight <= 6, `expected at most 6 in flight, saw ${stats.maxInFlight}`);
});

test("AQ1c: a merged PR seen passing is not re-read on later ticks (steady state costs ~0 subrequests)", async () => {
  const nowMs = Date.parse("2026-09-21T12:00:00Z");
  const env = { NTFY_TOPIC: "t", GITHUB_PR_HEALTH_PAT: "pat", DEADMAN_STATE: fakeKv() };
  const closed = manyMerged(nowMs, 12);
  const first = { checkRunCalls: [], inFlight: 0, maxInFlight: 0 };
  await runCheck(env, latencyWorld({ nowMs, closed, delayMs: 5 }, [], first), nowMs);
  assert.equal(first.checkRunCalls.length, 12);
  const second = { checkRunCalls: [], inFlight: 0, maxInFlight: 0 };
  await runCheck(env, latencyWorld({ nowMs: nowMs + 30 * MINUTE, closed, delayMs: 5 }, [], second), nowMs + 30 * MINUTE);
  assert.equal(second.checkRunCalls.length, 0, "already-verified PRs must be served from the cache");
});

test("AQ1c: a PR that did NOT pass is re-read every tick and still pages (the cache never hides a failure)", async () => {
  const nowMs = Date.parse("2026-09-21T12:00:00Z");
  const env = { NTFY_TOPIC: "t", GITHUB_PR_HEALTH_PAT: "pat", DEADMAN_STATE: fakeKv() };
  const closed = manyMerged(nowMs, 5);
  const badSha = closed[2].head.sha;
  const calls = [];
  const s1 = { checkRunCalls: [], inFlight: 0, maxInFlight: 0 };
  const r1 = await runCheck(env, latencyWorld({ nowMs, closed, delayMs: 5, checkRunsBySha: { [badSha]: [] } }, calls, s1), nowMs);
  assert.equal(r1.mergedUncheckedCount, 1);
  const s2 = { checkRunCalls: [], inFlight: 0, maxInFlight: 0 };
  await runCheck(env, latencyWorld({ nowMs: nowMs + 30 * MINUTE, closed, delayMs: 5, checkRunsBySha: { [badSha]: [] } }, calls, s2), nowMs + 30 * MINUTE);
  assert.deepEqual(s2.checkRunCalls, [badSha], "only the failing PR is re-read");
});

test("AQ1c: a real GitHub error on one PR still fails closed (pages 'could not verify'), it is not swallowed", async () => {
  const nowMs = Date.parse("2026-09-21T12:00:00Z");
  const calls = [];
  const closed = manyMerged(nowMs, 8);
  const stats = { checkRunCalls: [], inFlight: 0, maxInFlight: 0 };
  const world = latencyWorld({ nowMs, closed, delayMs: 5, statusBySha: { [closed[4].head.sha]: 500 } }, calls, stats);
  await runCheck({ NTFY_TOPIC: "t", GITHUB_PR_HEALTH_PAT: "pat", DEADMAN_STATE: fakeKv() }, world, nowMs);
  const alert = calls.find((c) => c.opts.headers.Title.includes("could not verify"));
  assert.ok(alert, "an unverifiable scan must page");
  assert.match(alert.opts.body, /HTTP 500/);
});

// ---------------------------------------------------------------------------
// AQ1b (2026-09-21): Telegram as a delivery channel, ntfy as the fallback
// ---------------------------------------------------------------------------
// ntfy.sh answered every Worker attempt since the #1797 deploy with 522 or 429 (4 of 4). Telegram is
// tried first when configured; one message reaches the phone, not one per channel.

const TG_TOKEN = "123456:SECRET-bot-token-value";
const TG_CHAT = "987654";
const STALE_WORLD = (nowMs, calls) => mockWorldFetch({ nowMs, ageHours: 11 }, calls); // WARN staleness -> one alert

function withTelegram(base, tg) {
  return async (url, opts) => (String(url).startsWith("https://api.telegram.org/") ? tg(url, opts) : base(url, opts));
}
const tgOk = (id = 4242) => async () => ({ ok: true, status: 200, json: async () => ({ ok: true, result: { message_id: id } }) });
const captureErrors = () => {
  const lines = [];
  const orig = console.error;
  console.error = (...a) => lines.push(a.join(" "));
  return { lines, restore: () => (console.error = orig) };
};
// The first run of each IST day also sends the daily heartbeat; pre-seed today's date so each test
// sees only the one alert it is about.
const kvHeartbeatDone = () => {
  const kv = fakeKv();
  kv.put("deadman:last_heartbeat_date_ist", "2026-09-21");
  return kv;
};
const TG_ENV = () => ({ NTFY_TOPIC: "t", TELEGRAM_BOT_TOKEN: TG_TOKEN, TELEGRAM_CHAT_ID: TG_CHAT, DEADMAN_STATE: kvHeartbeatDone() });

test("AQ1b: Telegram succeeds -> delivered via telegram, ntfy is NOT also posted, message id recorded", async () => {
  const nowMs = Date.parse("2026-09-21T12:00:00Z");
  const ntfyCalls = [];
  const tgCalls = [];
  const world = withTelegram(STALE_WORLD(nowMs, ntfyCalls), async (u, o) => (tgCalls.push({ u, o }), tgOk(4242)()));
  const r = await runCheck(TG_ENV(), world, nowMs);
  assert.equal(r.sent, true);
  assert.equal(ntfyCalls.length, 0, "one message reaches the phone, not one per channel");
  assert.equal(tgCalls.length, 1);
  assert.match(tgCalls[0].u, /^https:\/\/api\.telegram\.org\/bot.+\/sendMessage$/);
  assert.equal(JSON.parse(tgCalls[0].o.body).chat_id, TG_CHAT);
  assert.deepEqual(r.ntfy.channels.deliveredVia, ["telegram"]);
  assert.equal(r.ntfy.lastDelivery.id, 4242);
  assert.equal(r.ntfy.lastDelivery.channel, "telegram");
});

test("AQ1b: Telegram fails -> falls back to ntfy, and the fallback is visible (not a clean run)", async () => {
  const nowMs = Date.parse("2026-09-21T12:00:00Z");
  const ntfyCalls = [];
  const world = withTelegram(STALE_WORLD(nowMs, ntfyCalls), async () => ({ ok: false, status: 502, json: async () => ({}) }));
  const cap = captureErrors();
  let r;
  try {
    r = await runCheck(TG_ENV(), world, nowMs);
  } finally {
    cap.restore();
  }
  assert.equal(r.sent, true);
  assert.equal(ntfyCalls.length, 1);
  assert.deepEqual(r.ntfy.channels.deliveredVia, ["ntfy"]);
  assert.ok(cap.lines.some((l) => /used ntfy after telegram failed/.test(l)), cap.lines.join("|"));
});

test("AQ1b: both channels fail -> not sent, and BOTH failures are reported", async () => {
  const nowMs = Date.parse("2026-09-21T12:00:00Z");
  const ntfyCalls = [];
  const world = withTelegram(
    mockWorldFetch({ nowMs, ageHours: 11, ntfy: () => ({ ok: false, status: 522 }) }, ntfyCalls),
    async () => ({ ok: false, status: 401, json: async () => ({ ok: false, description: "Unauthorized" }) }),
  );
  const cap = captureErrors();
  let r;
  try {
    r = await runCheck(TG_ENV(), world, nowMs);
  } finally {
    cap.restore();
  }
  assert.equal(r.sent, false);
  assert.equal(r.ntfy.failed, 1);
  assert.match(r.ntfy.failures[0].error, /telegram: HTTP 401: Unauthorized/);
  assert.match(r.ntfy.failures[0].error, /ntfy: HTTP 522/);
});

test("AQ1b: HTTP 200 with ok:false in the body is a FAILURE (Telegram reports some errors that way)", async () => {
  const nowMs = Date.parse("2026-09-21T12:00:00Z");
  const world = withTelegram(STALE_WORLD(nowMs, []), async () => ({ ok: true, status: 200, json: async () => ({ ok: false, description: "chat not found" }) }));
  const cap = captureErrors();
  let r;
  try {
    r = await runCheck({ ...TG_ENV(), NTFY_TOPIC: undefined }, world, nowMs);
  } finally {
    cap.restore();
  }
  assert.equal(r.sent, false);
  assert.match(r.ntfy.failures[0].error, /chat not found/);
});

test("AQ1b: the bot token never appears in the response, KV or logs, even if a fetch error echoes the URL", async () => {
  const nowMs = Date.parse("2026-09-21T12:00:00Z");
  const env = { ...TG_ENV(), NTFY_TOPIC: undefined };
  const world = withTelegram(STALE_WORLD(nowMs, []), async (u) => {
    throw new Error(`fetch failed for ${u}`);
  });
  const cap = captureErrors();
  let r;
  try {
    r = await runCheck(env, world, nowMs);
  } finally {
    cap.restore();
  }
  const everything = JSON.stringify(r) + cap.lines.join("\n") + (await env.DEADMAN_STATE.get("deadman:last_ntfy_delivery"));
  assert.ok(!everything.includes("SECRET-bot-token-value"), "token leaked");
  assert.ok(!everything.includes(TG_TOKEN), "token leaked");
  assert.match(r.ntfy.failures[0].error, /\[redacted\]/);
});

test("AQ1b: a Worker configured with ONLY Telegram works (no NTFY_TOPIC needed), fingerprint is null", async () => {
  const nowMs = Date.parse("2026-09-21T12:00:00Z");
  const env = { ...TG_ENV(), NTFY_TOPIC: undefined };
  const r = await runCheck(env, withTelegram(STALE_WORLD(nowMs, []), tgOk(7)), nowMs);
  assert.equal(r.sent, true);
  assert.equal(r.ntfy.topicFingerprint, null);
  assert.equal(r.ntfy.channels.telegramConfigured, true);
  assert.equal(r.ntfy.channels.ntfyConfigured, false);
});

test("AQ1b: ntfy-only deployments behave exactly as before (no Telegram calls)", async () => {
  const nowMs = Date.parse("2026-09-21T12:00:00Z");
  const ntfyCalls = [];
  let tg = 0;
  const world = withTelegram(STALE_WORLD(nowMs, ntfyCalls), async () => (tg++, tgOk()()));
  const r = await runCheck({ NTFY_TOPIC: "t", DEADMAN_STATE: kvHeartbeatDone() }, world, nowMs);
  assert.equal(tg, 0);
  assert.equal(ntfyCalls.length, 1);
  assert.equal(r.sent, true);
  assert.equal(r.ntfy.channels.telegramConfigured, false);
});
