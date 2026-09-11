// Cloudflare Worker entry point. Thin wiring around deadman.mjs's pure
// functions: fetch the PUBLIC site (not any repo/GitHub API -- this must
// verify what users actually see), classify staleness, decide whether to
// alert based on KV-stored last-sent state, and post to the EXISTING ntfy
// topic if so. Mostly zero dependency on GitHub Actions: the staleness/
// Tanishq-silence/heartbeat channels run entirely on Cloudflare's own Cron
// Trigger scheduler with no GitHub API calls at all.
//
// AI2 (production audit continuation, 2026-09-11/12): the PR-trigger-health
// channel below (fetchOpenPrTriggerHealth / pr_trigger_health.mjs) is a
// deliberate, explicit departure from that "zero GitHub API dependency"
// principle -- it has to call GitHub's REST API (open PRs, their head
// commits, their check-runs) since that is the only place this information
// exists. Kept honest here rather than silently blurred: this one channel
// needs a GitHub PAT (env.GITHUB_PR_HEALTH_PAT, a NEW secret, narrowly
// scoped to Pull requests: Read + Contents: Read + Checks: Read -- Contents
// is what the commits/{sha} endpoint needs, easy to miss since this channel
// never reads file contents -- see README.md for the provisioning step) and
// unauthenticated calls to api.github.com are not a
// safe substitute (60 req/hour, shared across Cloudflare's entire outbound
// IP range with every other Worker on the platform, not just this one).
// Every OTHER channel in this file remains exactly as zero-GitHub-API-
// dependency as before; this is scoped to the one channel that structurally
// cannot avoid it.

import {
  PUBLIC_FORECAST_URL,
  classifyStaleness,
  classifyFetchFailure,
  classifyTanishqSilence,
  decideAction,
  decideTanishqAction,
  shouldSendHeartbeat,
  buildHeartbeatAlert,
  WARN_THRESHOLD_HOURS,
  ESCALATE_THRESHOLD_HOURS,
  TANISHQ_WARN_HOURS,
  TANISHQ_ESCALATE_HOURS,
  RUNNER_CONFIRMED_OFFLINE_HOURS,
} from "./deadman.mjs";
import {
  classifyPrTriggerHealth,
  decidePrTriggerHealthAction,
  buildPrTriggerHealthAlert,
  buildPrTriggerHealthFetchFailureAlert,
  PR_TRIGGER_STALE_MINUTES,
} from "./pr_trigger_health.mjs";

const KV_STATE_KEY = "deadman:last_state";
const KV_TANISHQ_STATE_KEY = "deadman:tanishq_last_state"; // Q4: independent dedup state, own KV key
const KV_HEARTBEAT_KEY = "deadman:last_heartbeat_date_ist";
const KV_PR_TRIGGER_HEALTH_KEY = "deadman:pr_trigger_health_state"; // AI2: own key, own dedup state
const FETCH_TIMEOUT_MS = 10_000;
const GITHUB_REPO = "gaurav-gandhi-2411/gold-rate-tracker";

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

/**
 * AI2: fetches every open, non-bot/-scratch PR's head commit timestamp and
 * whether it has a lint/pwa-js check-run yet. Fails the WHOLE batch closed
 * on any single API error (rule 98a) -- a partial result (some PRs checked,
 * one silently missing because its own call failed) is worse than an
 * explicit "could not verify this run" alert, since a silently-dropped PR
 * is exactly the failure mode this channel exists to catch.
 *
 * bot/-prefixed branches excluded: those PRs auto-merge via bot-pr-sync's
 * own polling mechanism within minutes, not the shape this channel watches
 * for. scratch/-prefixed excluded: this repo's own established convention
 * (AG2b, check_pr_boundary_leak.py) for deliberately disposable proof PRs,
 * reused identically here.
 */
async function fetchOpenPrTriggerHealth(fetchImpl, token) {
  const headers = {
    "User-Agent": "gold-rate-tracker-deadman-switch",
    Authorization: `Bearer ${token}`,
    Accept: "application/vnd.github+json",
  };
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const listResp = await fetchImpl(
      `https://api.github.com/repos/${GITHUB_REPO}/pulls?state=open&per_page=50`,
      { headers, signal: controller.signal },
    );
    if (!listResp.ok) return { openPrs: null, failure: `list open PRs HTTP ${listResp.status}` };
    const allPrs = await listResp.json();
    const candidates = allPrs.filter(
      (p) => !p.head.ref.startsWith("bot/") && !p.head.ref.startsWith("scratch/"),
    );

    const openPrs = [];
    for (const p of candidates) {
      const sha = p.head.sha;
      const commitResp = await fetchImpl(`https://api.github.com/repos/${GITHUB_REPO}/commits/${sha}`, {
        headers,
        signal: controller.signal,
      });
      if (!commitResp.ok) return { openPrs: null, failure: `commit ${sha} HTTP ${commitResp.status}` };
      const commit = await commitResp.json();

      const checksResp = await fetchImpl(
        `https://api.github.com/repos/${GITHUB_REPO}/commits/${sha}/check-runs`,
        { headers, signal: controller.signal },
      );
      if (!checksResp.ok) return { openPrs: null, failure: `check-runs ${sha} HTTP ${checksResp.status}` };
      const checkData = await checksResp.json();
      const hasRequiredCheckRun = (checkData.check_runs || []).some(
        (r) => r.name === "lint" || r.name === "pwa-js",
      );

      openPrs.push({
        number: p.number,
        branch: p.head.ref,
        headSha: sha,
        headCommitIso: commit.commit.committer.date,
        hasRequiredCheckRun,
      });
    }
    return { openPrs, failure: null };
  } catch (err) {
    return { openPrs: null, failure: String(err && err.message ? err.message : err) };
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

  // AI2: PR-trigger-health channel -- entirely independent of the price-
  // staleness/Tanishq channels above (different data source, different
  // failure mode, own KV key). Skipped gracefully, not paged about, when
  // GITHUB_PR_HEALTH_PAT isn't configured -- matches this file's existing
  // NTFY_TOPIC/DEADMAN_STATE-optional pattern: a missing secret means the
  // feature isn't deployed yet, not a live failure to alert on. Once the
  // secret DOES exist, a fetch that fails pages honestly (rule 98a) rather
  // than silently skipping, matching every other channel in this file.
  let prTriggerHealthSent = false;
  let prTriggerHealthStaleCount = 0;
  if (env.GITHUB_PR_HEALTH_PAT) {
    const { openPrs, failure: prFetchFailure } = await fetchOpenPrTriggerHealth(
      fetchImpl,
      env.GITHUB_PR_HEALTH_PAT,
    );
    if (prFetchFailure) {
      await postToNtfy(fetchImpl, env.NTFY_TOPIC, buildPrTriggerHealthFetchFailureAlert(prFetchFailure));
      prTriggerHealthSent = true;
    } else {
      const stalePrs = classifyPrTriggerHealth(openPrs, nowMs);
      prTriggerHealthStaleCount = stalePrs.length;
      const previousPrState = await loadState(env, KV_PR_TRIGGER_HEALTH_KEY);
      const { send: prSend, alertPrs, nextState: nextPrState } = decidePrTriggerHealthAction(
        stalePrs,
        previousPrState,
        nowMs,
      );
      if (env.DEADMAN_STATE) {
        await env.DEADMAN_STATE.put(KV_PR_TRIGGER_HEALTH_KEY, JSON.stringify(nextPrState));
      }
      if (prSend && alertPrs.length > 0) {
        await postToNtfy(fetchImpl, env.NTFY_TOPIC, buildPrTriggerHealthAlert(alertPrs));
        prTriggerHealthSent = true;
      }
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
    prTriggerHealthSent,
    prTriggerHealthStaleCount,
    // AC3 (audit 2026-09-10): echoes the constants this exact deployment is
    // actually running with, not a hardcoded copy of master's current
    // values -- imported directly from deadman.mjs, so this only matches
    // master by construction if the deployed bundle is actually built from
    // master. A stale deploy running an older deadman.mjs would report that
    // older file's real values here, not master's -- that divergence is the
    // whole point (see worker-deadman/README.md's deployed-vs-master proof
    // step). No threshold VALUE changes; purely additive to the response.
    thresholds: {
      warnHours: WARN_THRESHOLD_HOURS,
      escalateHours: ESCALATE_THRESHOLD_HOURS,
      tanishqWarnHours: TANISHQ_WARN_HOURS,
      tanishqEscalateHours: TANISHQ_ESCALATE_HOURS,
      runnerConfirmedOfflineHours: RUNNER_CONFIRMED_OFFLINE_HOURS,
      prTriggerStaleMinutes: PR_TRIGGER_STALE_MINUTES,
    },
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
