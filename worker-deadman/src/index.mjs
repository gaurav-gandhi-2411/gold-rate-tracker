// Cloudflare Worker entry point. Thin wiring around deadman.mjs's pure
// functions: fetch the PUBLIC site (not any repo/GitHub API -- this must
// verify what users actually see), classify staleness, decide whether to
// alert based on KV-stored last-sent state, and post to the EXISTING ntfy
// topic if so. Zero dependency on GitHub Actions: this runs entirely on
// Cloudflare's own Cron Trigger scheduler.

import {
  PUBLIC_FORECAST_URL,
  classifyStaleness,
  classifyFetchFailure,
  decideAction,
  shouldSendHeartbeat,
  buildHeartbeatAlert,
} from "./deadman.mjs";

const KV_STATE_KEY = "deadman:last_state";
const KV_HEARTBEAT_KEY = "deadman:last_heartbeat_date_ist";
const FETCH_TIMEOUT_MS = 10_000;

async function fetchForecast(fetchImpl) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const resp = await fetchImpl(PUBLIC_FORECAST_URL, {
      signal: controller.signal,
      headers: { "User-Agent": "gold-rate-tracker-deadman-switch" },
      cf: { cacheTtl: 0, cacheEverything: false },
    });
    if (!resp.ok) {
      return { predictedAtIso: null, failure: `HTTP ${resp.status}` };
    }
    const body = await resp.json();
    return { predictedAtIso: body.predicted_at, failure: null };
  } catch (err) {
    return { predictedAtIso: null, failure: String(err && err.message ? err.message : err) };
  } finally {
    clearTimeout(timeout);
  }
}

async function postToNtfy(fetchImpl, topic, alert) {
  return fetchImpl(`https://ntfy.sh/${topic}`, {
    method: "POST",
    headers: {
      Title: alert.title,
      Priority: String(alert.priority),
      Tags: alert.tags,
    },
    body: alert.body,
  });
}

export async function runCheck(env, fetchImpl, nowMs) {
  if (!env.NTFY_TOPIC) {
    return { skipped: "NTFY_TOPIC secret not set" };
  }

  const { predictedAtIso, failure } = await fetchForecast(fetchImpl);
  const current = failure
    ? classifyFetchFailure(failure)
    : classifyStaleness(predictedAtIso, nowMs);

  let previousState = null;
  if (env.DEADMAN_STATE) {
    const raw = await env.DEADMAN_STATE.get(KV_STATE_KEY);
    if (raw) {
      try {
        previousState = JSON.parse(raw);
      } catch {
        previousState = null; // corrupt state -- treat as first run, do not crash
      }
    }
  }

  const { send, alert, nextState } = decideAction(current, previousState, nowMs);

  if (env.DEADMAN_STATE) {
    await env.DEADMAN_STATE.put(KV_STATE_KEY, JSON.stringify(nextState));
  }

  if (send && alert) {
    await postToNtfy(fetchImpl, env.NTFY_TOPIC, alert);
  }

  // G4a: independent of whatever staleness alert may have just fired --
  // the heartbeat's job is confirming the SWITCH ITSELF ran today, not
  // reporting site staleness (decideAction's job, above).
  let heartbeatSent = false;
  if (env.DEADMAN_STATE) {
    const lastHeartbeatDateIst = await env.DEADMAN_STATE.get(KV_HEARTBEAT_KEY);
    const { send: dueToday, todayIst } = shouldSendHeartbeat(lastHeartbeatDateIst, nowMs);
    if (dueToday) {
      await postToNtfy(
        fetchImpl,
        env.NTFY_TOPIC,
        buildHeartbeatAlert(current.level, current.ageHours),
      );
      await env.DEADMAN_STATE.put(KV_HEARTBEAT_KEY, todayIst);
      heartbeatSent = true;
    }
  }

  return { level: current.level, ageHours: current.ageHours, sent: send, heartbeatSent };
}

export default {
  async scheduled(_event, env, ctx) {
    ctx.waitUntil(runCheck(env, fetch, Date.now()));
  },
  // Manual HTTP trigger for verification (GET the Worker's own URL) --
  // see README.md's manual verification procedure. Not used by the cron
  // path itself.
  async fetch(_request, env, _ctx) {
    const result = await runCheck(env, fetch, Date.now());
    return new Response(JSON.stringify(result, null, 2), {
      headers: { "content-type": "application/json" },
    });
  },
};
