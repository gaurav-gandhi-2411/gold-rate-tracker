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
  RUNNER_CONFIRMED_OFFLINE_HOURS,
} from "../src/deadman.mjs";
import { runCheck, safeTokenMatch } from "../src/index.mjs";
import worker from "../src/index.mjs";
import {
  PR_TRIGGER_STALE_MINUTES,
  MERGED_SETTLE_MINUTES,
  MERGED_LOOKBACK_MINUTES,
  REQUIRED_CONTEXTS,
} from "../src/pr_trigger_health.mjs";

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

test("runCheck: stale payload (past ESCALATE_THRESHOLD_HOURS) fires an ESCALATE ntfy POST with the right topic/priority", async () => {
  const staleHours = ESCALATE_THRESHOLD_HOURS + 1;
  const staleIso = isoHoursAgo(staleHours);
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
  // 1: only the forecast-staleness alert. The daily heartbeat (G4a) no longer sends (2026-11-06).
  assert.equal(ntfyCalls.length, 1);
  const escalateCall = ntfyCalls.find((c) => c.opts.headers.Priority === "5");
  assert.ok(escalateCall, "expected one ntfy call with Priority 5 (ESCALATE)");
  assert.equal(escalateCall.url, "https://ntfy.sh/test-gold-topic");
  assert.match(escalateCall.opts.body, new RegExp(`${staleHours}\\.0h`));
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

// AC3 (audit 2026-09-10): the manual-trigger endpoint's response must be a
// LIVE echo of deadman.mjs's real exported constants, not a hardcoded
// literal that happens to match today -- otherwise a self-report that
// echoes a fixed string is exactly instance #14 of the defect class this
// whole audit exists to catch (a control emitting a plausible value
// instead of actually verifying anything). Two things are checked, not
// one: (1) the reported numbers equal the imported constants (below), and
// (2) those same reported numbers are the ones actually driving
// classification behavior in the SAME call, not a cosmetic second copy
// (next test) -- JS `export const` bindings can't be monkey-patched from
// a test importing the real module, so "would visibly differ if master's
// changed" is proven by tying the reported value to observed behavior
// instead of by mutating the constant.
test("runCheck: response echoes the real threshold constants from deadman.mjs, not a hardcoded copy", async () => {
  const fetchImpl = async (url) => {
    if (url.includes("forecast.json")) {
      return { ok: true, json: async () => ({ predicted_at: isoHoursAgo(0.5), scraped_at: isoHoursAgo(0.5) }) };
    }
    return { ok: true };
  };
  const env = { NTFY_TOPIC: "test-gold-topic", DEADMAN_STATE: fakeKv() };
  const result = await runCheck(env, fetchImpl, NOW);
  assert.deepEqual(result.thresholds, {
    warnHours: WARN_THRESHOLD_HOURS,
    escalateHours: ESCALATE_THRESHOLD_HOURS,
    tanishqWarnHours: TANISHQ_WARN_HOURS,
    tanishqEscalateHours: TANISHQ_ESCALATE_HOURS,
    runnerConfirmedOfflineHours: RUNNER_CONFIRMED_OFFLINE_HOURS,
    prTriggerStaleMinutes: PR_TRIGGER_STALE_MINUTES,
    mergedSettleMinutes: MERGED_SETTLE_MINUTES,
    mergedLookbackMinutes: MERGED_LOOKBACK_MINUTES,
    requiredContexts: REQUIRED_CONTEXTS,
  });
});

test("runCheck: the reported warnHours is the actual operative boundary, not just a cosmetic echo", async () => {
  // Age the payload to exactly the reported warnHours minus a hair (still
  // "ok"), then exactly at it (must flip to "warn") -- proving the number
  // in result.thresholds.warnHours is the same number classifyStaleness
  // actually used to decide result.level in that same call. If
  // `thresholds` were ever hardcoded independently of the real constant,
  // this pairing could silently drift while the deepEqual test above still
  // passed (both sides copy-pasted the same wrong literal).
  const belowFetch = async (url) => {
    if (url.includes("forecast.json")) {
      return {
        ok: true,
        json: async () => ({
          predicted_at: isoHoursAgo(WARN_THRESHOLD_HOURS - 0.01),
          scraped_at: isoHoursAgo(0.5),
        }),
      };
    }
    return { ok: true };
  };
  const belowResult = await runCheck(
    { NTFY_TOPIC: "test-gold-topic", DEADMAN_STATE: fakeKv() },
    belowFetch,
    NOW,
  );
  assert.equal(belowResult.level, "ok");
  assert.equal(belowResult.thresholds.warnHours, WARN_THRESHOLD_HOURS);

  const atFetch = async (url) => {
    if (url.includes("forecast.json")) {
      return {
        ok: true,
        json: async () => ({
          predicted_at: isoHoursAgo(WARN_THRESHOLD_HOURS),
          scraped_at: isoHoursAgo(0.5),
        }),
      };
    }
    return { ok: true };
  };
  const atResult = await runCheck({ NTFY_TOPIC: "test-gold-topic", DEADMAN_STATE: fakeKv() }, atFetch, NOW);
  assert.equal(atResult.level, "warn");
  assert.equal(atResult.thresholds.warnHours, WARN_THRESHOLD_HOURS);
});

test("runCheck: repeated ESCALATE runs inside the reminder window only alert once", async () => {
  const staleIso = isoHoursAgo(ESCALATE_THRESHOLD_HOURS + 1);
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
  // 1: the first run's ESCALATE only (no heartbeat since 2026-11-06).
  // The second run, inside the reminder window, adds nothing.
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
  // 1: the unverifiable alert (the site price cannot be confirmed); no heartbeat since 2026-11-06.
  assert.equal(ntfyCalls.length, 1);
});

test("runCheck: missing NTFY_TOPIC skips cleanly instead of throwing", async () => {
  const fetchImpl = async () => {
    throw new Error("fetch should not be called when NTFY_TOPIC is unset");
  };
  const result = await runCheck({ NTFY_TOPIC: "" }, fetchImpl, NOW);
  assert.match(result.skipped, /no delivery channel configured/);
});

test("runCheck: corrupt KV state does not crash, treated as first run", async () => {
  const fetchImpl = async (url) => {
    if (url.includes("forecast.json")) {
      return {
        ok: true,
        json: async () => ({
          predicted_at: isoHoursAgo(ESCALATE_THRESHOLD_HOURS + 1),
          scraped_at: isoHoursAgo(0.5),
        }),
      };
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

// --- R2c (audit 2026-09-04): health-file corroboration ---

test("classifyTanishqSilence: WARN-range gap + stale health file escalates immediately (corroborated)", () => {
  const scrapedAt = isoHoursAgo(TANISHQ_WARN_HOURS + 1); // in WARN range, well under ESCALATE
  const staleHealth = isoHoursAgo(RUNNER_CONFIRMED_OFFLINE_HOURS + 1);
  const r = classifyTanishqSilence(scrapedAt, NOW, staleHealth);
  assert.equal(r.level, "escalate");
  assert.match(r.reason, /corroborated/);
});

test("classifyTanishqSilence: WARN-range gap + FRESH health file stays warn, not escalate", () => {
  const scrapedAt = isoHoursAgo(TANISHQ_WARN_HOURS + 1);
  const freshHealth = isoHoursAgo(RUNNER_CONFIRMED_OFFLINE_HOURS - 1);
  const r = classifyTanishqSilence(scrapedAt, NOW, freshHealth);
  assert.equal(r.level, "warn");
  assert.equal(r.reason, "health-fresh");
});

test("classifyTanishqSilence: missing health signal does not escalate early -- falls back to plain thresholds", () => {
  const scrapedAt = isoHoursAgo(TANISHQ_WARN_HOURS + 1);
  const r = classifyTanishqSilence(scrapedAt, NOW, null);
  assert.equal(r.level, "warn"); // plain threshold result, unaffected by absent corroboration
  assert.equal(r.reason, null);
});

test("classifyTanishqSilence: unparseable health signal does not escalate early or crash", () => {
  const scrapedAt = isoHoursAgo(TANISHQ_WARN_HOURS + 1);
  const r = classifyTanishqSilence(scrapedAt, NOW, "not-a-date");
  assert.equal(r.level, "warn");
  assert.equal(r.reason, null);
});

test("classifyTanishqSilence: health corroboration is not consulted when scraped_at is still fresh (no wasted work)", () => {
  const r = classifyTanishqSilence(isoHoursAgo(1), NOW, isoHoursAgo(100)); // health stale, but scraped_at fresh
  assert.equal(r.level, "ok");
});

test("buildTanishqAlert: escalate corroborated by health file states so explicitly", () => {
  const a = buildTanishqAlert(
    "escalate",
    50,
    "corroborated: tanishq_selfhosted_health.json also stale (12.0h) -- nothing has run, not just failed",
  );
  assert.match(a.body, /corroborated/);
});

test("buildTanishqAlert: warn with confirmed-fresh health names the scrape-failure hypothesis", () => {
  const a = buildTanishqAlert("warn", 50, "health-fresh");
  assert.match(a.body, /still shows the job itself executing/);
});

test("buildTanishqAlert: warn with no health signal does not claim the job is fine", () => {
  const a = buildTanishqAlert("warn", 50, null);
  assert.doesNotMatch(a.body, /still shows the job itself executing/);
  assert.match(a.body, /[Cc]ould not confirm/);
});

test("runCheck: fetches the health file only once scraped_at is already in WARN range", async () => {
  let healthFetched = false;
  const fetchImpl = async (url) => {
    if (url.includes("forecast.json")) {
      return { ok: true, json: async () => ({ predicted_at: isoHoursAgo(0.2), scraped_at: isoHoursAgo(1) }) };
    }
    if (url.includes("tanishq_selfhosted_health.json")) {
      healthFetched = true;
      return { ok: true, json: async () => ({ last_updated_utc: isoHoursAgo(1) }) };
    }
    return { ok: true };
  };
  const env = { NTFY_TOPIC: "test-gold-topic", DEADMAN_STATE: fakeKv() };
  await runCheck(env, fetchImpl, NOW);
  assert.equal(healthFetched, false); // scraped_at fresh -- no reason to spend the request
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
  // 1: the one "unverifiable" alert, NOT a second near-identical "could not verify Tanishq" alert
  // for the same underlying fetch failure (and no heartbeat since 2026-11-06).
  assert.equal(ntfyCount, 1);
});

// OWNER DECISION 2026-11-06: ntfy carries gold-price information only. The Tanishq-silence,
// PR-trigger-health, merged-unchecked and heartbeat channels no longer send (or even fetch).
test("runCheck: the Tanishq, PR-health, merged-unchecked and heartbeat channels never send or fetch", async () => {
  const seen = [];
  const fetchImpl = async (url, opts) => {
    seen.push(url);
    if (url.includes("forecast.json")) {
      // fresh price forecast, but Tanishq silent for 30 h: the old Tanishq channel would have paged
      return { ok: true, json: async () => ({ predicted_at: isoHoursAgo(0.5), scraped_at: isoHoursAgo(30) }) };
    }
    throw new Error(`a removed channel fetched or sent: ${url}`);
  };
  const env = {
    NTFY_TOPIC: "test-gold-topic",
    GITHUB_PR_HEALTH_PAT: "pat", // would have enabled the PR-health and merged-unchecked scans
    DEADMAN_STATE: fakeKv(), // fresh KV: the heartbeat would have been due
  };
  const result = await runCheck(env, fetchImpl, NOW);
  assert.equal(result.level, "ok");
  assert.equal(result.tanishqSent, false);
  assert.equal(result.heartbeatSent, false);
  assert.equal(result.prTriggerHealthSent, false);
  assert.equal(result.mergedUncheckedSent, false);
  assert.equal(result.ntfy.attempted, 0);
  assert.equal(seen.length, 1); // forecast.json only
});

// AI2: integration tests for the PR-trigger-health channel, proving the
// actual wiring (index.mjs's fetchOpenPrTriggerHealth + the GITHUB_PR_
// HEALTH_PAT gate) works end-to-end, not just the pure functions in
// pr_trigger_health.test.mjs in isolation.

function githubApiFetch({ openPrs, checkRunsBySha, failOn }) {
  return async (url, opts) => {
    if (url.includes("forecast.json")) {
      return { ok: true, json: async () => ({ predicted_at: isoHoursAgo(0.2), scraped_at: isoHoursAgo(0.2) }) };
    }
    if (url.includes("/pulls?state=open")) {
      if (failOn === "pulls") return { ok: false, status: 403 };
      return { ok: true, json: async () => openPrs };
    }
    if (url.includes("/check-runs")) {
      const sha = url.match(/commits\/([a-z0-9]+)\/check-runs/)[1];
      if (failOn === "check-runs") return { ok: false, status: 500 };
      return { ok: true, json: async () => ({ check_runs: checkRunsBySha[sha] || [] }) };
    }
    if (url.includes("/commits/")) {
      const sha = url.match(/commits\/([a-z0-9]+)$/)[1];
      if (failOn === "commit") return { ok: false, status: 404 };
      return { ok: true, json: async () => ({ commit: { committer: { date: isoMinutesAgo(sha) } } }) };
    }
    // ntfy POST
    return { ok: true };
  };
}

function isoMinutesAgo(sha) {
  // test helper: encode the desired age (minutes) into the fake sha itself,
  // e.g. sha "age60" -> 60 minutes ago.
  const m = /^age(\d+)$/.exec(sha);
  const minutes = m ? Number(m[1]) : 0;
  return new Date(NOW - minutes * 60_000).toISOString();
}

test("runCheck: PR trigger-health channel does not page for a fresh PR with no check-run yet", async () => {
  const ntfyCalls = [];
  const fetchImpl = async (url, opts) => {
    const inner = githubApiFetch({
      openPrs: [{ number: 9999, head: { ref: "feat/new", sha: "age1" } }],
      checkRunsBySha: {},
    });
    const resp = await inner(url, opts);
    if (url.includes("ntfy.sh")) ntfyCalls.push(opts);
    return resp;
  };
  const env = {
    NTFY_TOPIC: "test-gold-topic",
    DEADMAN_STATE: fakeKv(),
    GITHUB_PR_HEALTH_PAT: "fake-token",
  };
  const result = await runCheck(env, fetchImpl, NOW);
  assert.equal(result.prTriggerHealthSent, false);
  assert.equal(ntfyCalls.some((c) => c.headers.Title.includes("required checks never started")), false);
});

test("runCheck: PR trigger-health channel is skipped (not paged) when GITHUB_PR_HEALTH_PAT is unset", async () => {
  let githubApiCalled = false;
  const fetchImpl = async (url, opts) => {
    if (url.includes("api.github.com")) githubApiCalled = true;
    return githubApiFetch({ openPrs: [], checkRunsBySha: {} })(url, opts);
  };
  const env = { NTFY_TOPIC: "test-gold-topic", DEADMAN_STATE: fakeKv() }; // no GITHUB_PR_HEALTH_PAT
  const result = await runCheck(env, fetchImpl, NOW);
  assert.equal(result.prTriggerHealthSent, false);
  assert.equal(githubApiCalled, false);
});

// 2026-09-21: bot/ is no longer excluded (see fetchOpenPrTriggerHealth's comment) -- this test used
// to assert that it was, which encoded the assumption that bot PRs always merge within minutes;
// bot/docs-refresh sat open with zero check-runs for 10 days (#1578) and was skipped by construction.
// ---------------------------------------------------------------------------
// S2 (audit continuation, 2026-09-23): the manual HTTP trigger used to run the
// full check for ANY unauthenticated request. These pin the fix -- a shared
// secret is required and the comparison never falls open on a missing secret.
// ---------------------------------------------------------------------------

test("safeTokenMatch: identical strings match", async () => {
  assert.equal(await safeTokenMatch("abc123", "abc123"), true);
});

test("safeTokenMatch: different strings of the same length do not match", async () => {
  assert.equal(await safeTokenMatch("abc123", "abc124"), false);
});

test("safeTokenMatch: different-length strings do not match", async () => {
  assert.equal(await safeTokenMatch("short", "a-much-longer-token"), false);
});

test("safeTokenMatch: empty supplied value never matches a real token", async () => {
  assert.equal(await safeTokenMatch("", "real-token"), false);
});

test("fetch(): no token supplied is rejected even when TRIGGER_TOKEN is configured", async () => {
  const req = new Request("https://example.workers.dev/");
  const resp = await worker.fetch(req, { TRIGGER_TOKEN: "secret-token" }, {});
  assert.equal(resp.status, 401);
});

test("fetch(): wrong token is rejected", async () => {
  const req = new Request("https://example.workers.dev/?token=nope");
  const resp = await worker.fetch(req, { TRIGGER_TOKEN: "secret-token" }, {});
  assert.equal(resp.status, 401);
});

test("fetch(): TRIGGER_TOKEN unconfigured denies every request -- never falls open on a missing secret", async () => {
  const req = new Request("https://example.workers.dev/?token=anything");
  const resp = await worker.fetch(req, {}, {});
  assert.equal(resp.status, 401);
});

test("fetch(): correct token via ?token= query param is accepted and runs the check", async () => {
  const realFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () =>
      new Response(
        JSON.stringify({ predicted_at: new Date().toISOString(), scraped_at: new Date().toISOString() }),
        { status: 200 },
      );
    const req = new Request("https://example.workers.dev/?token=secret-token");
    const resp = await worker.fetch(req, { TRIGGER_TOKEN: "secret-token", NTFY_TOPIC: "test-topic" }, {});
    assert.equal(resp.status, 200);
    const body = await resp.json();
    assert.ok("level" in body);
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("fetch(): correct token via X-Trigger-Token header is accepted", async () => {
  const realFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () =>
      new Response(
        JSON.stringify({ predicted_at: new Date().toISOString(), scraped_at: new Date().toISOString() }),
        { status: 200 },
      );
    const req = new Request("https://example.workers.dev/", { headers: { "X-Trigger-Token": "secret-token" } });
    const resp = await worker.fetch(req, { TRIGGER_TOKEN: "secret-token" }, {});
    assert.equal(resp.status, 200);
  } finally {
    globalThis.fetch = realFetch;
  }
});
