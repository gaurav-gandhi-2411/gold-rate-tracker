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
  classifyMergedUnchecked,
  evaluateRequiredChecks,
  REQUIRED_CONTEXTS,
  decideMergedUncheckedAction,
  buildMergedUncheckedAlert,
  buildMergedUncheckedFetchFailureAlert,
  PR_TRIGGER_STALE_MINUTES,
  MERGED_SETTLE_MINUTES,
  MERGED_LOOKBACK_MINUTES,
} from "./pr_trigger_health.mjs";

const KV_STATE_KEY = "deadman:last_state";
const KV_TANISHQ_STATE_KEY = "deadman:tanishq_last_state"; // Q4: independent dedup state, own KV key
const KV_HEARTBEAT_KEY = "deadman:last_heartbeat_date_ist";
const KV_PR_TRIGGER_HEALTH_KEY = "deadman:pr_trigger_health_state"; // AI2: own key, own dedup state
const KV_MERGED_UNCHECKED_KEY = "deadman:merged_unchecked_state"; // AL3a: PR number -> alerted-at
// AQ1c: PR number -> head SHA of merged PRs already seen with every required context SUCCESS, so a
// merged PR is read once rather than every tick. Pruned to the lookback window on every write.
const KV_MERGED_VERIFIED_KEY = "deadman:merged_verified";
const KV_LAST_DELIVERY_KEY = "deadman:last_ntfy_delivery"; // AN3: last delivery attempt, echoed by ?trigger=1
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
 * bot/-prefixed branches are NO LONGER excluded (2026-09-21). They used to be, on the
 * assumption that bot PRs "auto-merge via bot-pr-sync's own polling within minutes" -- but
 * `bot/docs-refresh` does not use bot-pr-sync (native auto-merge), and it was the one PR
 * that sat open with zero check-runs for 10 days (#1578) while this channel skipped it by
 * construction. Measured over 100 merged bot PRs: head commit -> first lint/pwa-js check-run
 * start median 0.17 min, max 0.35 min, and -> merged max 4.5 min, so an open bot PR with no
 * check-run for PR_TRIGGER_STALE_MINUTES (30) is a genuine anomaly, not noise.
 * scratch/-prefixed excluded: this repo's own established convention (AG2b,
 * check_pr_boundary_leak.py) for deliberately disposable proof PRs, reused identically here.
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
    const candidates = allPrs.filter((p) => !p.head.ref.startsWith("scratch/"));

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

// Concurrent GitHub reads per scan. Bounded so a busy merge day cannot fan out without limit.
const MERGED_SCAN_CONCURRENCY = 6;

/** One GET with ITS OWN timeout (a shared one is what broke the scan, see fetchRecentlyMergedPrs). */
async function getJson(fetchImpl, url, headers) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const resp = await fetchImpl(url, { headers, signal: controller.signal });
    if (!resp.ok) return { ok: false, status: resp.status, data: null };
    return { ok: true, status: resp.status, data: await resp.json() };
  } finally {
    clearTimeout(timeout);
  }
}

/** Run `fn` over `items` with at most `limit` in flight; results keep input order. */
async function mapLimit(items, limit, fn) {
  const out = new Array(items.length);
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const i = next++;
      out[i] = await fn(items[i]);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return out;
}

/**
 * AL3a: PRs merged inside the lookback window, each with whether every required context is SUCCESS
 * on its head SHA. Same fail-closed batching as the open-PR fetch above: one failed API call fails
 * the whole batch, never a silently shorter list. Bot PRs are NOT excluded here -- 355/355 merged
 * bot PRs had check-runs, so a bot PR merged without one is exactly the anomaly this scan exists
 * to surface.
 *
 * AQ1c (2026-09-21): this used ONE AbortController (10s) for the whole scan and awaited one
 * check-runs call per merged PR in sequence. On a normal day that is a handful of calls; on a day
 * with ~36 merges in 6h it exhausted the budget mid-scan ("This operation was aborted"), so the
 * scan paged "could not verify" on every tick. Reproduced by running this function against the
 * real repo: 30 of 31 calls succeeded, the 31st was aborted. Now: a timeout per request, bounded
 * concurrency, and `verified` (PR number -> head SHA of PRs already seen passing) so each merged PR
 * is read once instead of every 30 minutes, which also keeps the per-invocation subrequest count
 * near zero in steady state. Returns `verifiedNext` (only PRs still inside the lookback).
 */
async function fetchRecentlyMergedPrs(fetchImpl, token, nowMs, verified = {}) {
  const headers = {
    "User-Agent": "gold-rate-tracker-deadman-switch",
    Authorization: `Bearer ${token}`,
    Accept: "application/vnd.github+json",
  };
  try {
    // sort=updated desc: anything merged in the last few hours is among the first page.
    const list = await getJson(
      fetchImpl,
      `https://api.github.com/repos/${GITHUB_REPO}/pulls?state=closed&sort=updated&direction=desc&per_page=50`,
      headers,
    );
    if (!list.ok) return { mergedPrs: null, failure: `list closed PRs HTTP ${list.status}` };
    const cutoffMs = nowMs - MERGED_LOOKBACK_MINUTES * 60_000;
    const recent = list.data.filter((p) => p.merged_at && Date.parse(p.merged_at) >= cutoffMs);

    const results = await mapLimit(recent, MERGED_SCAN_CONCURRENCY, async (p) => {
      const sha = p.head.sha;
      const base = { number: p.number, branch: p.head.ref, headSha: sha, mergedAtIso: p.merged_at };
      if (verified[String(p.number)] === sha) return { pr: { ...base, requiredOk: true, problems: [] } };
      try {
        const checks = await getJson(
          fetchImpl,
          `https://api.github.com/repos/${GITHUB_REPO}/commits/${sha}/check-runs?per_page=100`,
          headers,
        );
        if (!checks.ok) return { failure: `check-runs ${sha} HTTP ${checks.status}` };
        // AN8: the same predicate scripts/check_required_checks_positive.py applies before a self-merge --
        // every required context SUCCESS, not merely present (#1541 merged over a failing lint).
        const { allPass, results: perContext } = evaluateRequiredChecks(
          REQUIRED_CONTEXTS,
          checks.data.check_runs || [],
        );
        return {
          pr: {
            ...base,
            requiredOk: allPass,
            problems: Object.entries(perContext)
              .filter(([, v]) => v !== "SUCCESS")
              .map(([context, v]) => `${context}: ${v}`),
          },
        };
      } catch (err) {
        return { failure: `check-runs ${sha}: ${String(err && err.message ? err.message : err)}` };
      }
    });
    const failed = results.find((r) => r.failure);
    if (failed) return { mergedPrs: null, failure: failed.failure };
    const mergedPrs = results.map((r) => r.pr);
    const verifiedNext = {};
    for (const pr of mergedPrs) if (pr.requiredOk) verifiedNext[String(pr.number)] = pr.headSha;
    return { mergedPrs, failure: null, verifiedNext };
  } catch (err) {
    return { mergedPrs: null, failure: String(err && err.message ? err.message : err) };
  }
}

/**
 * One delivery attempt. Returns {ok, status, id, error} -- ok is true ONLY for a 2xx response.
 * Every caller used to `await postToNtfy(...)` and then set its `...Sent` flag (and persist its
 * dedup state) whether or not ntfy accepted the message, so "sent: true" meant only "the request
 * did not throw" -- a 4xx/5xx, a rate-limit or a wrong topic all looked like success (2026-09-21:
 * prTriggerHealthSent:true, nothing on the phone). `id` is ntfy's message id, so a sender can
 * verify receipt server-side with GET ntfy.sh/<topic>/json?poll=1&since=<id>.
 */
async function postToNtfy(fetchImpl, topic, alert) {
  try {
    const resp = await fetchImpl(`https://ntfy.sh/${topic}`, {
      method: "POST",
      headers: {
        Title: alert.title,
        Priority: String(alert.priority),
        Tags: alert.tags,
      },
      body: alert.body,
    });
    const ok = Boolean(resp && resp.ok);
    let id = null;
    if (ok && typeof resp.json === "function") {
      try {
        id = (await resp.json()).id ?? null;
      } catch {
        // body is informational; a 2xx without JSON is still a delivery
      }
    }
    const status = resp && resp.status !== undefined ? resp.status : null;
    return { ok, status, id, error: ok ? null : `HTTP ${status ?? "no response"}` };
  } catch (err) {
    return { ok: false, status: null, id: null, error: String(err && err.message ? err.message : err) };
  }
}

/**
 * Telegram Bot API sendMessage (AQ1b). Free, reachable from Cloudflare Workers, independent of GitHub
 * Actions, and it answers synchronously with `result.message_id`, which is evidence Telegram accepted
 * the message. ok requires BOTH an HTTP 2xx and `ok: true` in the body.
 *
 * The bot token is part of the request URL, and a fetch failure message can echo that URL, so every
 * error string is scrubbed of the token before it can reach a response, a KV record or a log line.
 */
async function postToTelegram(fetchImpl, token, chatId, alert) {
  const scrub = (s) => String(s).split(token).join("[redacted]");
  try {
    const resp = await fetchImpl(`https://api.telegram.org/bot${token}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: chatId, text: `${alert.title}\n${alert.body}`, disable_web_page_preview: true }),
    });
    const status = resp && resp.status !== undefined ? resp.status : null;
    let data = null;
    if (resp && typeof resp.json === "function") {
      try {
        data = await resp.json();
      } catch {
        // an unparseable body is judged by the status alone below
      }
    }
    const ok = Boolean(resp && resp.ok) && Boolean(data && data.ok === true);
    const id = ok && data.result ? data.result.message_id ?? null : null;
    const why = data && data.description ? `: ${scrub(data.description)}` : "";
    return { ok, status, id, error: ok ? null : `HTTP ${status ?? "no response"}${why}` };
  } catch (err) {
    return { ok: false, status: null, id: null, error: scrub(err && err.message ? err.message : err) };
  }
}

/**
 * Deliver one alert over the configured channels: Telegram first (when TELEGRAM_BOT_TOKEN and
 * TELEGRAM_CHAT_ID are set), then ntfy as the fallback. One message reaches the phone, not one per
 * channel. ok means SOME channel confirmed; `attempts` records every channel tried, so a fallback
 * that only worked because the first path was down is visible instead of hidden.
 */
async function postAlert(fetchImpl, env, alert) {
  const attempts = [];
  if (env.TELEGRAM_BOT_TOKEN && env.TELEGRAM_CHAT_ID) {
    const r = await postToTelegram(fetchImpl, env.TELEGRAM_BOT_TOKEN, env.TELEGRAM_CHAT_ID, alert);
    attempts.push({ channel: "telegram", ...r });
    if (r.ok) return { ...r, channel: "telegram", attempts };
  }
  if (env.NTFY_TOPIC) {
    const r = await postToNtfy(fetchImpl, env.NTFY_TOPIC, alert);
    attempts.push({ channel: "ntfy", ...r });
    if (r.ok) return { ...r, channel: "ntfy", attempts };
  }
  const last = attempts[attempts.length - 1];
  return {
    ok: false,
    status: last ? last.status : null,
    id: null,
    channel: last ? last.channel : null,
    error: last ? attempts.map((a) => `${a.channel}: ${a.error}`).join("; ") : "no delivery channel configured",
    attempts,
  };
}

// First 4 bytes of sha256(topic) as hex: lets GG compare the Worker's NTFY_TOPIC secret with the
// topic his phone/Actions use WITHOUT the topic itself ever appearing in a response or log.
async function topicFingerprint(topic) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(topic));
  return [...new Uint8Array(buf).slice(0, 4)].map((b) => b.toString(16).padStart(2, "0")).join("");
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
  if (!env.NTFY_TOPIC && !(env.TELEGRAM_BOT_TOKEN && env.TELEGRAM_CHAT_ID)) {
    return { skipped: "no delivery channel configured (NTFY_TOPIC, or TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)" };
  }

  const { predictedAtIso, scrapedAtIso, failure } = await fetchForecast(fetchImpl);
  const current = failure
    ? classifyFetchFailure(failure)
    : classifyStaleness(predictedAtIso, nowMs);

  // Every send goes through deliver(): it records the attempt and returns true only for a 2xx.
  // Dedup state below advances ONLY when the alert actually landed -- previously it was persisted
  // BEFORE sending, so a failed send also suppressed the retry for the whole reminder interval.
  const deliveries = [];
  const deliver = async (toSend) => {
    const r = await postAlert(fetchImpl, env, toSend);
    deliveries.push({ title: toSend.title, ok: r.ok, status: r.status, id: r.id, error: r.error, channel: r.channel });
    if (!r.ok) console.error(`delivery FAILED for "${toSend.title}": ${r.error}`);
    else if (r.attempts.length > 1) {
      // A fallback that worked only because the first path failed must not look like a clean run.
      console.error(`delivery for "${toSend.title}" used ${r.channel} after ${r.attempts[0].channel} failed: ${r.attempts[0].error}`);
    }
    return r.ok;
  };

  const previousState = await loadState(env, KV_STATE_KEY);
  const { send, alert, nextState } = decideAction(current, previousState, nowMs);

  const mainDelivered = send && alert ? await deliver(alert) : true;
  if (env.DEADMAN_STATE && mainDelivered) {
    await env.DEADMAN_STATE.put(KV_STATE_KEY, JSON.stringify(nextState));
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
    const tanishqDelivered = sendResult && tanishqAlert ? await deliver(tanishqAlert) : true;
    tanishqSend = sendResult && tanishqDelivered;

    if (env.DEADMAN_STATE && tanishqDelivered) {
      await env.DEADMAN_STATE.put(KV_TANISHQ_STATE_KEY, JSON.stringify(nextTanishqState));
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
      prTriggerHealthSent = await deliver(buildPrTriggerHealthFetchFailureAlert(prFetchFailure));
    } else {
      const stalePrs = classifyPrTriggerHealth(openPrs, nowMs);
      prTriggerHealthStaleCount = stalePrs.length;
      const previousPrState = await loadState(env, KV_PR_TRIGGER_HEALTH_KEY);
      const { send: prSend, alertPrs, nextState: nextPrState } = decidePrTriggerHealthAction(
        stalePrs,
        previousPrState,
        nowMs,
      );
      const prDelivered = prSend && alertPrs.length > 0 ? await deliver(buildPrTriggerHealthAlert(alertPrs)) : true;
      prTriggerHealthSent = prSend && alertPrs.length > 0 && prDelivered;
      if (env.DEADMAN_STATE && prDelivered) {
        await env.DEADMAN_STATE.put(KV_PR_TRIGGER_HEALTH_KEY, JSON.stringify(nextPrState));
      }
    }
  }

  // AL3a: merged-unchecked scan -- the open-PR channel above cannot see a PR that merges
  // before its head commit is PR_TRIGGER_STALE_MINUTES old (#1539 merged 5.2 min after its
  // last push). Same secret (Pull requests/Contents/Checks read), own KV key, own dedup.
  let mergedUncheckedSent = false;
  let mergedUncheckedCount = 0;
  if (env.GITHUB_PR_HEALTH_PAT) {
    const verified = (await loadState(env, KV_MERGED_VERIFIED_KEY)) || {};
    const { mergedPrs, failure: mergedFetchFailure, verifiedNext } = await fetchRecentlyMergedPrs(
      fetchImpl,
      env.GITHUB_PR_HEALTH_PAT,
      nowMs,
      verified,
    );
    if (mergedFetchFailure) {
      mergedUncheckedSent = await deliver(buildMergedUncheckedFetchFailureAlert(mergedFetchFailure));
    } else {
      // Verification is independent of whether an alert is delivered: a PR seen passing stays passed.
      if (env.DEADMAN_STATE) {
        await env.DEADMAN_STATE.put(KV_MERGED_VERIFIED_KEY, JSON.stringify(verifiedNext));
      }
      const flagged = classifyMergedUnchecked(mergedPrs, nowMs);
      mergedUncheckedCount = flagged.length;
      const previousMergedState = await loadState(env, KV_MERGED_UNCHECKED_KEY);
      const { send: mergedSend, alertPrs: mergedAlertPrs, nextState: nextMergedState } =
        decideMergedUncheckedAction(flagged, previousMergedState, nowMs);
      const mergedDelivered = mergedSend ? await deliver(buildMergedUncheckedAlert(mergedAlertPrs)) : true;
      mergedUncheckedSent = mergedSend && mergedDelivered;
      if (env.DEADMAN_STATE && mergedDelivered) {
        await env.DEADMAN_STATE.put(KV_MERGED_UNCHECKED_KEY, JSON.stringify(nextMergedState));
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
      const hbDelivered = await deliver(
        buildHeartbeatAlert(current.level, current.ageHours, tanishqCurrent.level, tanishqCurrent.ageHours),
      );
      // Recorded only on a confirmed delivery: a heartbeat that never arrived must be retried on the
      // next tick, not marked done for the day (the KV date said 2026-09-21 was sent while the phone
      // showed nothing).
      if (hbDelivered) {
        await env.DEADMAN_STATE.put(KV_HEARTBEAT_KEY, todayIst);
        heartbeatSent = true;
      }
    }
  }

  // Delivery evidence: what THIS run attempted, plus the last attempt persisted across runs, so a
  // silent notification path is visible in ?trigger=1 instead of hiding behind `sent: true`.
  const failed = deliveries.filter((d) => !d.ok);
  const lastAttempt = deliveries.length ? deliveries[deliveries.length - 1] : null;
  if (env.DEADMAN_STATE && lastAttempt) {
    await env.DEADMAN_STATE.put(
      KV_LAST_DELIVERY_KEY,
      JSON.stringify({ atMs: nowMs, ok: lastAttempt.ok, status: lastAttempt.status, id: lastAttempt.id, channel: lastAttempt.channel, title: lastAttempt.title }),
    );
  }
  const lastDelivery = lastAttempt
    ? { atMs: nowMs, ok: lastAttempt.ok, status: lastAttempt.status, id: lastAttempt.id, channel: lastAttempt.channel, title: lastAttempt.title }
    : await loadState(env, KV_LAST_DELIVERY_KEY);

  return {
    level: current.level,
    ageHours: current.ageHours,
    sent: send && mainDelivered,
    tanishqLevel: tanishqCurrent.level,
    tanishqAgeHours: tanishqCurrent.ageHours,
    tanishqSent: tanishqSend,
    heartbeatSent,
    prTriggerHealthSent,
    prTriggerHealthStaleCount,
    mergedUncheckedSent,
    mergedUncheckedCount,
    ntfy: {
      attempted: deliveries.length,
      delivered: deliveries.length - failed.length,
      failed: failed.length,
      failures: failed.map((d) => ({ title: d.title, status: d.status, error: d.error })),
      topicFingerprint: env.NTFY_TOPIC ? await topicFingerprint(env.NTFY_TOPIC) : null,
      lastDelivery,
      // Which channels this deployment is configured with, and which one carried each delivery.
      channels: {
        telegramConfigured: Boolean(env.TELEGRAM_BOT_TOKEN && env.TELEGRAM_CHAT_ID),
        ntfyConfigured: Boolean(env.NTFY_TOPIC),
        deliveredVia: deliveries.filter((d) => d.ok).map((d) => d.channel),
      },
    },
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
      mergedSettleMinutes: MERGED_SETTLE_MINUTES,
      mergedLookbackMinutes: MERGED_LOOKBACK_MINUTES,
      requiredContexts: REQUIRED_CONTEXTS,
    },
  };
}

export default {
  async scheduled(_event, env, ctx) {
    ctx.waitUntil(
      runCheck(env, fetch, Date.now()).then((result) => {
        // ntfy is the only way this Worker can tell anyone anything, so a failed delivery cannot be
        // reported through it: fail the invocation instead, which Cloudflare records as an exception.
        if (result.ntfy && result.ntfy.failed > 0) {
          throw new Error(`ntfy delivery failed: ${JSON.stringify(result.ntfy.failures)}`);
        }
        return result;
      }),
    );
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
