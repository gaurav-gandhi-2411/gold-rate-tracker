import { test } from "node:test";
import assert from "node:assert/strict";
import {
  classifyStaleness,
  classifyFetchFailure,
  buildAlert,
  decideAction,
  WARN_THRESHOLD_HOURS,
  ESCALATE_THRESHOLD_HOURS,
} from "../src/deadman.mjs";
import { runCheck } from "../src/index.mjs";

const HOUR = 3_600_000;
const NOW = Date.parse("2026-08-28T04:00:00Z");

function isoHoursAgo(hours) {
  return new Date(NOW - hours * HOUR).toISOString();
}

test("classifyStaleness: fresh payload is ok", () => {
  const r = classifyStaleness(isoHoursAgo(1), NOW);
  assert.equal(r.level, "ok");
});

test("classifyStaleness: exactly at WARN threshold is warn", () => {
  const r = classifyStaleness(isoHoursAgo(WARN_THRESHOLD_HOURS), NOW);
  assert.equal(r.level, "warn");
});

test("classifyStaleness: just under WARN threshold is ok", () => {
  const r = classifyStaleness(isoHoursAgo(WARN_THRESHOLD_HOURS - 0.01), NOW);
  assert.equal(r.level, "ok");
});

test("classifyStaleness: exactly at ESCALATE threshold is escalate", () => {
  const r = classifyStaleness(isoHoursAgo(ESCALATE_THRESHOLD_HOURS), NOW);
  assert.equal(r.level, "escalate");
});

test("classifyStaleness: well past ESCALATE stays escalate", () => {
  const r = classifyStaleness(isoHoursAgo(48), NOW);
  assert.equal(r.level, "escalate");
  assert.ok(r.ageHours > 40);
});

test("classifyStaleness: missing predicted_at is unverifiable, not ok", () => {
  const r = classifyStaleness(undefined, NOW);
  assert.equal(r.level, "unverifiable");
});

test("classifyStaleness: unparseable predicted_at is unverifiable, not ok", () => {
  const r = classifyStaleness("not-a-date", NOW);
  assert.equal(r.level, "unverifiable");
});

test("classifyFetchFailure: always unverifiable, carries the reason", () => {
  const r = classifyFetchFailure("HTTP 503");
  assert.equal(r.level, "unverifiable");
  assert.equal(r.reason, "HTTP 503");
});

test("buildAlert: escalate is priority 5 with urgent tags", () => {
  const a = buildAlert("escalate", 11.3, null);
  assert.equal(a.priority, 5);
  assert.match(a.tags, /rotating_light/);
  assert.match(a.body, /11\.3h/);
});

test("buildAlert: warn is priority 4", () => {
  const a = buildAlert("warn", 6.2, null);
  assert.equal(a.priority, 4);
  assert.match(a.body, /6\.2h/);
});

test("buildAlert: unverifiable includes the reason", () => {
  const a = buildAlert("unverifiable", null, "HTTP 503");
  assert.match(a.body, /HTTP 503/);
});

test("buildAlert: ok returns null (nothing to send)", () => {
  assert.equal(buildAlert("ok", 1, null), null);
});

// --- decideAction: dedup / state-machine behavior ---

test("decideAction: first-ever run at ok sends nothing", () => {
  const { send } = decideAction({ level: "ok", ageHours: 1, reason: null }, null, NOW);
  assert.equal(send, false);
});

test("decideAction: first-ever run already stale sends immediately (no prior state to compare)", () => {
  const { send, alert, nextState } = decideAction(
    { level: "escalate", ageHours: 12, reason: null },
    null,
    NOW,
  );
  assert.equal(send, true);
  assert.equal(alert.priority, 5);
  assert.equal(nextState.level, "escalate");
  assert.equal(nextState.lastSentAtMs, NOW);
});

test("decideAction: entering warn from ok sends once", () => {
  const prev = { level: "ok", lastSentAtMs: NOW - 100 * HOUR };
  const { send, alert } = decideAction({ level: "warn", ageHours: 5.1, reason: null }, prev, NOW);
  assert.equal(send, true);
  assert.equal(alert.priority, 4);
});

test("decideAction: staying warn just after the first alert does NOT re-send", () => {
  const prev = { level: "warn", lastSentAtMs: NOW - 1 * HOUR };
  const { send } = decideAction({ level: "warn", ageHours: 6, reason: null }, prev, NOW);
  assert.equal(send, false);
});

test("decideAction: staying warn past the reminder interval re-sends", () => {
  const prev = { level: "warn", lastSentAtMs: NOW - 7 * HOUR };
  const { send, alert } = decideAction({ level: "warn", ageHours: 6, reason: null }, prev, NOW);
  assert.equal(send, true);
  assert.equal(alert.priority, 4);
});

test("decideAction: warn escalating to escalate sends immediately even inside the reminder window", () => {
  const prev = { level: "warn", lastSentAtMs: NOW - 0.5 * HOUR };
  const { send, alert, nextState } = decideAction(
    { level: "escalate", ageHours: 10.2, reason: null },
    prev,
    NOW,
  );
  assert.equal(send, true);
  assert.equal(alert.priority, 5);
  assert.equal(nextState.level, "escalate");
});

test("decideAction: recovering from escalate to ok sends one low-priority recovery notice", () => {
  const prev = { level: "escalate", lastSentAtMs: NOW - 2 * HOUR };
  const { send, alert, nextState } = decideAction({ level: "ok", ageHours: 0.2, reason: null }, prev, NOW);
  assert.equal(send, true);
  assert.equal(alert.priority, 3);
  assert.equal(nextState.level, "ok");
});

test("decideAction: staying ok after ok sends nothing", () => {
  const prev = { level: "ok", lastSentAtMs: NOW - 50 * HOUR };
  const { send } = decideAction({ level: "ok", ageHours: 0.1, reason: null }, prev, NOW);
  assert.equal(send, false);
});

// --- runCheck: end-to-end with a mocked fetch and in-memory KV ---

function fakeKv(initial) {
  const store = new Map(initial ? [[initial.key, initial.value]] : []);
  return {
    async get(key) {
      return store.has(key) ? store.get(key) : null;
    },
    async put(key, value) {
      store.set(key, value);
    },
    _store: store,
  };
}

test("runCheck: stale payload (11h old) fires an ESCALATE ntfy POST with the right topic/priority", async () => {
  const staleIso = isoHoursAgo(11);
  const ntfyCalls = [];
  const fetchImpl = async (url, opts) => {
    if (url.includes("forecast.json")) {
      return {
        ok: true,
        json: async () => ({ predicted_at: staleIso }),
      };
    }
    if (url.startsWith("https://ntfy.sh/")) {
      ntfyCalls.push({ url, opts });
      return { ok: true };
    }
    throw new Error(`unexpected fetch: ${url}`);
  };
  const env = { NTFY_TOPIC: "test-gold-topic", DEADMAN_STATE: fakeKv() };
  const result = await runCheck(env, fetchImpl, NOW);

  assert.equal(result.level, "escalate");
  assert.equal(result.sent, true);
  assert.equal(ntfyCalls.length, 1);
  assert.equal(ntfyCalls[0].url, "https://ntfy.sh/test-gold-topic");
  assert.equal(ntfyCalls[0].opts.headers.Priority, "5");
  assert.match(ntfyCalls[0].opts.body, /11\.0h/);
});

test("runCheck: fresh payload sends nothing", async () => {
  const fetchImpl = async (url) => {
    if (url.includes("forecast.json")) {
      return { ok: true, json: async () => ({ predicted_at: isoHoursAgo(0.5) }) };
    }
    throw new Error(`unexpected fetch: ${url}`);
  };
  const env = { NTFY_TOPIC: "test-gold-topic", DEADMAN_STATE: fakeKv() };
  const result = await runCheck(env, fetchImpl, NOW);
  assert.equal(result.level, "ok");
  assert.equal(result.sent, false);
});

test("runCheck: repeated ESCALATE runs inside the reminder window only alert once", async () => {
  const staleIso = isoHoursAgo(11);
  let ntfyCount = 0;
  const fetchImpl = async (url) => {
    if (url.includes("forecast.json")) {
      return { ok: true, json: async () => ({ predicted_at: staleIso }) };
    }
    ntfyCount += 1;
    return { ok: true };
  };
  const env = { NTFY_TOPIC: "test-gold-topic", DEADMAN_STATE: fakeKv() };
  await runCheck(env, fetchImpl, NOW);
  const second = await runCheck(env, fetchImpl, NOW + 20 * 60 * 1000); // 20 min later
  assert.equal(ntfyCount, 1);
  assert.equal(second.sent, false);
});

test("runCheck: HTTP failure on the public URL is unverifiable, still alerts (fail closed, not silently ok)", async () => {
  const ntfyCalls = [];
  const fetchImpl = async (url) => {
    if (url.includes("forecast.json")) {
      return { ok: false, status: 503, json: async () => { throw new Error("no body"); } };
    }
    ntfyCalls.push(url);
    return { ok: true };
  };
  const env = { NTFY_TOPIC: "test-gold-topic", DEADMAN_STATE: fakeKv() };
  const result = await runCheck(env, fetchImpl, NOW);
  assert.equal(result.level, "unverifiable");
  assert.equal(result.sent, true);
  assert.equal(ntfyCalls.length, 1);
});

test("runCheck: missing NTFY_TOPIC skips cleanly instead of throwing", async () => {
  const fetchImpl = async () => {
    throw new Error("fetch should not be called when NTFY_TOPIC is unset");
  };
  const result = await runCheck({ NTFY_TOPIC: "" }, fetchImpl, NOW);
  assert.equal(result.skipped, "NTFY_TOPIC secret not set");
});

test("runCheck: corrupt KV state does not crash, treated as first run", async () => {
  const fetchImpl = async (url) => {
    if (url.includes("forecast.json")) {
      return { ok: true, json: async () => ({ predicted_at: isoHoursAgo(11) }) };
    }
    return { ok: true };
  };
  const kv = fakeKv();
  await kv.put("deadman:last_state", "{not valid json");
  const env = { NTFY_TOPIC: "test-gold-topic", DEADMAN_STATE: kv };
  const result = await runCheck(env, fetchImpl, NOW);
  assert.equal(result.level, "escalate");
  assert.equal(result.sent, true);
});
