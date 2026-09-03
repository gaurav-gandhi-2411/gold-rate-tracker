import { test } from "node:test";
import assert from "node:assert/strict";
import {
  classifyStaleness,
  classifyFetchFailure,
  classifyTanishqSilence,
  buildAlert,
  buildTanishqAlert,
  decideAction,
  decideTanishqAction,
  istDateString,
  shouldSendHeartbeat,
  buildHeartbeatAlert,
  WARN_THRESHOLD_HOURS,
  ESCALATE_THRESHOLD_HOURS,
  TANISHQ_WARN_HOURS,
  TANISHQ_ESCALATE_HOURS,
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
        json: async () => ({ predicted_at: staleIso, scraped_at: isoHoursAgo(0.5) }),
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
  // 2, not 1: the ESCALATE alert AND the once-per-day heartbeat (G4a) --
  // this is also the first run of the IST day against a fresh KV.
  assert.equal(ntfyCalls.length, 2);
  const escalateCall = ntfyCalls.find((c) => c.opts.headers.Priority === "5");
  assert.ok(escalateCall, "expected one ntfy call with Priority 5 (ESCALATE)");
  assert.equal(escalateCall.url, "https://ntfy.sh/test-gold-topic");
  assert.match(escalateCall.opts.body, /11\.0h/);
});

test("runCheck: fresh payload sends no staleness alert (heartbeat is separate -- see G4a tests)", async () => {
  const fetchImpl = async (url) => {
    if (url.includes("forecast.json")) {
      return { ok: true, json: async () => ({ predicted_at: isoHoursAgo(0.5), scraped_at: isoHoursAgo(0.5) }) };
    }
    return { ok: true }; // heartbeat still fires on the first run of the day (G4a)
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
      return { ok: true, json: async () => ({ predicted_at: staleIso, scraped_at: isoHoursAgo(0.5) }) };
    }
    ntfyCount += 1;
    return { ok: true };
  };
  const env = { NTFY_TOPIC: "test-gold-topic", DEADMAN_STATE: fakeKv() };
  await runCheck(env, fetchImpl, NOW);
  const second = await runCheck(env, fetchImpl, NOW + 20 * 60 * 1000); // 20 min later
  // 2, not 1: the first run's ESCALATE + its once-per-day heartbeat (G4a).
  // The second run (same IST day, inside the reminder window) adds neither.
  assert.equal(ntfyCount, 2);
  assert.equal(second.sent, false);
  assert.equal(second.heartbeatSent, false);
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
  // 2, not 1: the unverifiable alert AND the once-per-day heartbeat (G4a) --
  // a fresh KV means this is also the first run of the IST day.
  assert.equal(ntfyCalls.length, 2);
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
      return { ok: true, json: async () => ({ predicted_at: isoHoursAgo(11), scraped_at: isoHoursAgo(0.5) }) };
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

// --- G4a: daily heartbeat ---

test("istDateString: converts UTC to the IST calendar date", () => {
  // 2026-08-28T04:00:00Z + 5:30 = 2026-08-28T09:30 IST -- same calendar day.
  assert.equal(istDateString(NOW), "2026-08-28");
  // 2026-08-27T19:00:00Z + 5:30 = 2026-08-28T00:30 IST -- crosses into the next day.
  assert.equal(istDateString(Date.parse("2026-08-27T19:00:00Z")), "2026-08-28");
});

test("shouldSendHeartbeat: first-ever run (no prior heartbeat) sends", () => {
  const { send, todayIst } = shouldSendHeartbeat(null, NOW);
  assert.equal(send, true);
  assert.equal(todayIst, "2026-08-28");
});

test("shouldSendHeartbeat: same IST day as last heartbeat does not re-send", () => {
  const { send } = shouldSendHeartbeat("2026-08-28", NOW);
  assert.equal(send, false);
});

test("shouldSendHeartbeat: a new IST day sends again", () => {
  const { send } = shouldSendHeartbeat("2026-08-27", NOW);
  assert.equal(send, true);
});

test("buildHeartbeatAlert: lowest priority, states current level and age", () => {
  const alert = buildHeartbeatAlert("ok", 1.2);
  assert.equal(alert.priority, 1);
  assert.match(alert.body, /1\.2h/);
  assert.match(alert.body, /ok/);
});

test("runCheck: sends a heartbeat on first run of the day, independent of staleness state", async () => {
  const ntfyCalls = [];
  const fetchImpl = async (url) => {
    if (url.includes("forecast.json")) {
      return { ok: true, json: async () => ({ predicted_at: isoHoursAgo(0.5), scraped_at: isoHoursAgo(0.5) }) }; // fresh -- no staleness alert
    }
    ntfyCalls.push(url);
    return { ok: true };
  };
  const env = { NTFY_TOPIC: "test-gold-topic", DEADMAN_STATE: fakeKv() };
  const result = await runCheck(env, fetchImpl, NOW);

  assert.equal(result.sent, false); // no staleness alert -- forecast is fresh
  assert.equal(result.heartbeatSent, true); // but the heartbeat still fires
  assert.equal(ntfyCalls.length, 1);
});

test("runCheck: does not re-send the heartbeat twice on the same IST day", async () => {
  let ntfyCount = 0;
  const fetchImpl = async (url) => {
    if (url.includes("forecast.json")) {
      return { ok: true, json: async () => ({ predicted_at: isoHoursAgo(0.5), scraped_at: isoHoursAgo(0.5) }) };
    }
    ntfyCount += 1;
    return { ok: true };
  };
  const env = { NTFY_TOPIC: "test-gold-topic", DEADMAN_STATE: fakeKv() };
  await runCheck(env, fetchImpl, NOW);
  const second = await runCheck(env, fetchImpl, NOW + 20 * 60 * 1000); // 20 min later, same IST day
  assert.equal(ntfyCount, 1);
  assert.equal(second.heartbeatSent, false);
});

test("runCheck: heartbeat and a real ESCALATE alert can both fire in the same run", async () => {
  let ntfyCount = 0;
  const priorities = [];
  const fetchImpl = async (url, opts) => {
    if (url.includes("forecast.json")) {
      return { ok: true, json: async () => ({ predicted_at: isoHoursAgo(11), scraped_at: isoHoursAgo(0.5) }) };
    }
    ntfyCount += 1;
    priorities.push(opts.headers.Priority);
    return { ok: true };
  };
  const env = { NTFY_TOPIC: "test-gold-topic", DEADMAN_STATE: fakeKv() };
  const result = await runCheck(env, fetchImpl, NOW);

  assert.equal(result.sent, true); // ESCALATE
  assert.equal(result.heartbeatSent, true); // AND the heartbeat
  assert.equal(ntfyCount, 2);
  assert.ok(priorities.includes("5")); // ESCALATE
  assert.ok(priorities.includes("1")); // heartbeat
});

// --- Q4 (audit 2026-09-03): Tanishq-silence channel ---

test("classifyTanishqSilence: fresh scraped_at is ok", () => {
  const r = classifyTanishqSilence(isoHoursAgo(1), NOW);
  assert.equal(r.level, "ok");
});

test("classifyTanishqSilence: exactly at WARN threshold is warn", () => {
  const r = classifyTanishqSilence(isoHoursAgo(TANISHQ_WARN_HOURS), NOW);
  assert.equal(r.level, "warn");
});

test("classifyTanishqSilence: just under WARN threshold is ok", () => {
  const r = classifyTanishqSilence(isoHoursAgo(TANISHQ_WARN_HOURS - 0.01), NOW);
  assert.equal(r.level, "ok");
});

test("classifyTanishqSilence: exactly at ESCALATE threshold is escalate", () => {
  const r = classifyTanishqSilence(isoHoursAgo(TANISHQ_ESCALATE_HOURS), NOW);
  assert.equal(r.level, "escalate");
});

test("classifyTanishqSilence: missing scraped_at is unverifiable, not ok", () => {
  const r = classifyTanishqSilence(undefined, NOW);
  assert.equal(r.level, "unverifiable");
});

test("classifyTanishqSilence: unparseable scraped_at is unverifiable, not ok", () => {
  const r = classifyTanishqSilence("not-a-date", NOW);
  assert.equal(r.level, "unverifiable");
});

test("buildTanishqAlert: escalate names the runner as the likely cause", () => {
  const a = buildTanishqAlert("escalate", 80, null);
  assert.equal(a.priority, 5);
  assert.match(a.body, /80\.0h/);
  assert.match(a.body, /self-hosted runner/);
});

test("buildTanishqAlert: warn is lower priority than the forecast-staleness WARN (4)", () => {
  const a = buildTanishqAlert("warn", 30, null);
  assert.equal(a.priority, 3);
});

test("buildTanishqAlert: ok returns null", () => {
  assert.equal(buildTanishqAlert("ok", 1, null), null);
});

test("decideTanishqAction: first-ever run already past ESCALATE sends immediately", () => {
  const { send, alert, nextState } = decideTanishqAction(
    { level: "escalate", ageHours: 80, reason: null },
    null,
    NOW,
  );
  assert.equal(send, true);
  assert.equal(alert.priority, 5);
  assert.equal(nextState.level, "escalate");
});

test("decideTanishqAction: recovering to ok sends a distinct (non-forecast) recovery notice", () => {
  const prev = { level: "escalate", lastSentAtMs: NOW - 2 * HOUR };
  const { send, alert } = decideTanishqAction({ level: "ok", ageHours: 0.5, reason: null }, prev, NOW);
  assert.equal(send, true);
  assert.match(alert.title, /Tanishq confirmation resumed/);
});

test("runCheck: scraped_at far in the past fires a Tanishq-silence alert, independent of a fresh predicted_at", () => {
  const ntfyBodies = [];
  const fetchImpl = async (url, opts) => {
    if (url.includes("forecast.json")) {
      // predicted_at fresh (IBJA-calibrated fallback keeping it alive) --
      // exactly the scenario Q4 exists for: forecast looks fine, Tanishq
      // confirmation has actually been silent for days.
      return {
        ok: true,
        json: async () => ({ predicted_at: isoHoursAgo(0.2), scraped_at: isoHoursAgo(100) }),
      };
    }
    ntfyBodies.push(opts.body);
    return { ok: true };
  };
  const env = { NTFY_TOPIC: "test-gold-topic", DEADMAN_STATE: fakeKv() };
  return runCheck(env, fetchImpl, NOW).then((result) => {
    assert.equal(result.level, "ok"); // forecast-staleness channel: fine
    assert.equal(result.sent, false);
    assert.equal(result.tanishqLevel, "escalate"); // Tanishq channel: not fine
    assert.equal(result.tanishqSent, true);
    assert.ok(ntfyBodies.some((b) => b.includes("Tanishq")));
  });
});

test("runCheck: a single fetch failure does not double-alert (one root cause, one alert)", async () => {
  let ntfyCount = 0;
  const fetchImpl = async (url) => {
    if (url.includes("forecast.json")) {
      return {
        ok: false,
        status: 503,
        json: async () => {
          throw new Error("no body");
        },
      };
    }
    ntfyCount += 1;
    return { ok: true };
  };
  const env = { NTFY_TOPIC: "test-gold-topic", DEADMAN_STATE: fakeKv() };
  const result = await runCheck(env, fetchImpl, NOW);
  assert.equal(result.level, "unverifiable");
  assert.equal(result.tanishqSent, false); // Tanishq channel skipped, not a second "unverifiable" alert
  // 2, not 3: the one "unverifiable" alert AND the once-per-day heartbeat --
  // NOT a second near-identical "could not verify Tanishq" alert for the
  // same underlying fetch failure.
  assert.equal(ntfyCount, 2);
});

test("runCheck: heartbeat body states the Tanishq channel too", async () => {
  const ntfyCalls = [];
  const fetchImpl = async (url, opts) => {
    if (url.includes("forecast.json")) {
      return {
        ok: true,
        json: async () => ({ predicted_at: isoHoursAgo(0.2), scraped_at: isoHoursAgo(2) }),
      };
    }
    ntfyCalls.push(opts);
    return { ok: true };
  };
  const env = { NTFY_TOPIC: "test-gold-topic", DEADMAN_STATE: fakeKv() };
  await runCheck(env, fetchImpl, NOW);
  const heartbeat = ntfyCalls.find((c) => c.headers.Priority === "1");
  assert.ok(heartbeat);
  assert.match(heartbeat.body, /Tanishq confirmation/);
});
