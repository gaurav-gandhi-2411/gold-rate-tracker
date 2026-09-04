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
  classifyTanishqSilence,
  decideAction,
  decideTanishqAction,
  shouldSendHeartbeat,
  buildHeartbeatAlert,
} from "./deadman.mjs";

const KV_STATE_KEY = "deadman:last_state";
const KV_TANISHQ_STATE_KEY = "deadman:tanishq_last_state"; // Q4: independent dedup state, own KV key
const KV_HEARTBEAT_KEY = "deadman:last_heartbeat_date_ist";
const FETCH_TIMEOUT_MS = 10_000;

// R2c: same public GitHub Pages origin as forecast.json -- no new
// dependency, no GitHub API/token. Verified reachable (HTTP 200) 2026-09-04.
const PUBLIC_HEALTH_URL =
  "https://gaurav-gandhi-2411.github.io/gold-rate-tracker/data/tanishq_selfhosted_health.json";

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
      return { predictedAtIso: null, scrapedAtIso: null, failure: `HTTP ${resp.status}` };
    }
    const body = await resp.json();
    // Q4: scraped_at read from the SAME response/fetch as predicted_at -- one
    // fetch serves both channels, so a genuine site-unreachable failure
    // (below) correctly propagates to both rather than needing a second
    // request against the same public URL.
    return { predictedAtIso: body.predicted_at, scrapedAtIso: body.scraped_at, failure: null };
  } catch (err) {
    return {
      predictedAtIso: null,
      scrapedAtIso: null,
      failure: String(err && err.message ? err.message : err),
    };
  } finally {
    clearTimeout(timeout);
  }
}

/**
 * R2c: fetch the health file's last_updated_utc for corroboration. Best-
 * effort -- returns null on ANY failure (unreachable, non-200, bad JSON,
 * missing field) rather than throwing, since this is a secondary signal:
 * classifyTanishqSilence already treats a null healthUpdatedAtIso as "no
 * corroboration available" and falls back to the plain scrapedAtIso-only
 * thresholds, never as a false "confirmed offline" or "confirmed fine".
 */
async function fetchHealthUpdatedAt(fetchImpl) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const resp = await fetchImpl(PUBLIC_HEALTH_URL, {
      signal: controller.signal,
      headers: { "User-Agent": "gold-rate-tracker-deadman-switch" },
      cf: { cacheTtl: 0, cacheEverything: false },
    });
    if (!resp.ok) return null;
    const body = await resp.json();
    return typeof body.last_updated_utc === "string" ? body.last_updated_utc : null;
  } catch {
    return null;
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

async function loadState(env, kvKey) {
  if (!env.DEADMAN_STATE) return null;
  const raw = await env.DEADMAN_STATE.get(kvKey);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null; // corrupt state -- treat as first run, do not crash
  }
}

export async function runCheck(env, fetchImpl, nowMs) {
  if (!env.NTFY_TOPIC) {
    return { skipped: "NTFY_TOPIC secret not set" };
  }

  const { predictedAtIso, scrapedAtIso, failure } = await fetchForecast(fetchImpl);
  const current = failure
    ? classifyFetchFailure(failure)
    : classifyStaleness(predictedAtIso, nowMs);

  const previousState = await loadState(env, KV_STATE_KEY);
  const { send, alert, nextState } = decideAction(current, previousState, nowMs);

  if (env.DEADMAN_STATE) {
    await env.DEADMAN_STATE.put(KV_STATE_KEY, JSON.stringify(nextState));
  }

  if (send && alert) {
    await postToNtfy(fetchImpl, env.NTFY_TOPIC, alert);
  }

  // Q4 (audit 2026-09-03): independent second channel -- forecast.json's
  // predicted_at can stay perfectly fresh (IBJA-calibrated fallback) for
  // weeks while Tanishq confirmation (scraped_at) goes silent; T12 cannot
  // detect this (fires only when the runner is online and jobs are
  // genuinely failing, not when it's offline) and nothing on the page
  // tells users Tanishq confirmation has stopped. Own KV state key so its
  // dedup/reminder timing is entirely independent of the channel above.
  //
  // Deliberately skipped entirely on a fetch failure (`failure` truthy):
  // the channel above already sends one "could not verify the site"
  // alert for that same root cause (one fetch serves both channels) --
  // firing a second, near-identical "could not verify Tanishq" alert on
  // top of it would be redundant noise for a single underlying problem,
  // not two independent findings.
  let tanishqCurrent = { level: "ok", ageHours: null, reason: null };
  let tanishqSend = false;
  if (!failure) {
    // R2c: only worth fetching once we're already at/past WARN territory --
    // no point spending a request confirming runner health when scraped_at
    // is fresh anyway. classifyTanishqSilence itself also gates on
    // ageHours >= TANISHQ_WARN_HOURS before using this value, so fetching
    // it earlier would just be wasted.
    const provisional = classifyTanishqSilence(scrapedAtIso, nowMs);
    const healthUpdatedAtIso =
      provisional.level === "ok" ? null : await fetchHealthUpdatedAt(fetchImpl);
    tanishqCurrent = healthUpdatedAtIso
      ? classifyTanishqSilence(scrapedAtIso, nowMs, healthUpdatedAtIso)
      : provisional;
    const previousTanishqState = await loadState(env, KV_TANISHQ_STATE_KEY);
    const {
      send: sendResult,
      alert: tanishqAlert,
      nextState: nextTanishqState,
    } = decideTanishqAction(tanishqCurrent, previousTanishqState, nowMs);
    tanishqSend = sendResult;

    if (env.DEADMAN_STATE) {
      await env.DEADMAN_STATE.put(KV_TANISHQ_STATE_KEY, JSON.stringify(nextTanishqState));
    }
    if (tanishqSend && tanishqAlert) {
      await postToNtfy(fetchImpl, env.NTFY_TOPIC, tanishqAlert);
    }
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
        buildHeartbeatAlert(current.level, current.ageHours, tanishqCurrent.level, tanishqCurrent.ageHours),
      );
      await env.DEADMAN_STATE.put(KV_HEARTBEAT_KEY, todayIst);
      heartbeatSent = true;
    }
  }

  return {
    level: current.level,
    ageHours: current.ageHours,
    sent: send,
    tanishqLevel: tanishqCurrent.level,
    tanishqAgeHours: tanishqCurrent.ageHours,
    tanishqSent: tanishqSend,
    heartbeatSent,
  };
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
