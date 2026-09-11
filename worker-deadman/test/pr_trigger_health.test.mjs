import { test } from "node:test";
import assert from "node:assert/strict";
import {
  classifyPrTriggerHealth,
  decidePrTriggerHealthAction,
  buildPrTriggerHealthAlert,
  buildPrTriggerHealthFetchFailureAlert,
  PR_TRIGGER_STALE_MINUTES,
} from "../src/pr_trigger_health.mjs";

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
