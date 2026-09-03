// Pure logic for the dead-man's switch, kept free of any Workers-runtime API
// (fetch, KV bindings) so it can be unit-tested with plain Node -- the
// Workers entry point (index.mjs) is a thin wiring layer around this file.

export const WARN_THRESHOLD_HOURS = 5;
export const ESCALATE_THRESHOLD_HOURS = 10;

// Q4 (audit 2026-09-03): T12 cannot fire when the self-hosted runner is
// offline (deliberate design, docs/RUNBOOK.md), and forecast.json's
// predicted_at stays fresh forever via the IBJA-calibrated fallback even
// with Tanishq permanently dead -- so a permanent runner failure produces
// ZERO alerts today, from anything. This channel watches
// forecast.json.scraped_at (the timestamp of the last SUCCESSFUL Tanishq
// reading -- already public, already exists, set from prices.json's latest
// entry in ml/inference.py; no new field needed) independently of the
// predicted_at channel above.
//
// Thresholds derived from the observed gap distribution between
// consecutive successful Tanishq readings, data/prices.json, last 30 days
// as of 2026-09-03 (n=156 readings, 155 gaps): median 2.92h, p90 5.99h,
// p95 14.21h, p99 29.43h, max 61.31h.
//   WARN = 24h: comfortably past p95 (14.21h) with margin, so routine bad
//     cycles rarely trigger it -- only 4 of 155 gaps (2.6%) in the sample
//     window exceeded 24h. Round number, easy to reason about ("a full day
//     of silence").
//   ESCALATE = 72h (3x WARN): past every gap observed in the 30-day sample
//     (max 61.31h) -- would not have false-fired once in that window, while
//     still bounding total silence to 3 days instead of forever (today's
//     actual state: no bound at all).
export const TANISHQ_WARN_HOURS = 24;
export const TANISHQ_ESCALATE_HOURS = 72;

// How often to re-send an alert while the same level persists, so a
// multi-hour outage doesn't page every 30 min but also isn't a single
// fire-and-forget that gets missed. Matches the existing alert catalog's
// "once per day" reminder pattern for sustained conditions (T9/T9_ESCALATE
// in ml/notifications.py) at a tighter cadence, since this switch exists
// specifically to catch an outage every GH-Actions-hosted alert would miss.
export const REMINDER_INTERVAL_HOURS = 6;

const PUBLIC_FORECAST_URL = "https://gaurav-gandhi-2411.github.io/gold-rate-tracker/data/forecast.json";

export { PUBLIC_FORECAST_URL };

/**
 * Classify how stale a forecast.json payload is, given its predicted_at
 * field and the current time. Returns level "ok" | "warn" | "escalate" |
 * "unverifiable" -- the last one covers both an unparseable predicted_at
 * and (via classifyFetchFailure below) an unreachable/non-200 fetch, so a
 * transport failure is never silently treated as "fine" (rule 98a: a
 * guard's data fetch must fail closed, never open).
 */
export function classifyStaleness(predictedAtIso, nowMs) {
  if (typeof predictedAtIso !== "string" || predictedAtIso.length === 0) {
    return { level: "unverifiable", ageHours: null, reason: "predicted_at missing or not a string" };
  }
  const predictedAtMs = Date.parse(predictedAtIso);
  if (Number.isNaN(predictedAtMs)) {
    return { level: "unverifiable", ageHours: null, reason: `predicted_at unparseable: ${predictedAtIso}` };
  }
  const ageHours = (nowMs - predictedAtMs) / 3_600_000;
  if (ageHours >= ESCALATE_THRESHOLD_HOURS) return { level: "escalate", ageHours, reason: null };
  if (ageHours >= WARN_THRESHOLD_HOURS) return { level: "warn", ageHours, reason: null };
  return { level: "ok", ageHours, reason: null };
}

/** Same "unverifiable" level for a fetch that never produced a parseable body. */
export function classifyFetchFailure(reason) {
  return { level: "unverifiable", ageHours: null, reason };
}

/**
 * Classify how long it's been since forecast.json's scraped_at (the last
 * SUCCESSFUL Tanishq reading) -- independent of classifyStaleness above,
 * since forecast.json.predicted_at can stay perfectly fresh (IBJA-
 * calibrated fallback) for weeks while scraped_at goes silent. Same shape/
 * levels as classifyStaleness so both channels share decideActionGeneric
 * and the same KV-state/dedup machinery.
 */
export function classifyTanishqSilence(scrapedAtIso, nowMs) {
  if (typeof scrapedAtIso !== "string" || scrapedAtIso.length === 0) {
    return { level: "unverifiable", ageHours: null, reason: "scraped_at missing or not a string" };
  }
  const scrapedAtMs = Date.parse(scrapedAtIso);
  if (Number.isNaN(scrapedAtMs)) {
    return { level: "unverifiable", ageHours: null, reason: `scraped_at unparseable: ${scrapedAtIso}` };
  }
  const ageHours = (nowMs - scrapedAtMs) / 3_600_000;
  if (ageHours >= TANISHQ_ESCALATE_HOURS) return { level: "escalate", ageHours, reason: null };
  if (ageHours >= TANISHQ_WARN_HOURS) return { level: "warn", ageHours, reason: null };
  return { level: "ok", ageHours, reason: null };
}

function fmtAge(ageHours) {
  return ageHours === null || ageHours === undefined ? "unknown" : `${ageHours.toFixed(1)}h`;
}

/**
 * Build the ntfy title/body/priority/tags for a given level. Returns null
 * for "ok" (nothing to send). Priority/tag conventions match ml/notifications.py's
 * existing catalog: 4="warning" (routine), 5="rotating_light,warning" +
 * urgent delivery (sustained failure) -- see T9/T9_ESCALATE.
 */
export function buildAlert(level, ageHours, reason) {
  const age = fmtAge(ageHours);
  if (level === "escalate") {
    return {
      title: "Gold Tracker: SUSTAINED forecast staleness (dead-man's switch)",
      body:
        `data/forecast.json's predicted_at is ${age} old (>= ${ESCALATE_THRESHOLD_HOURS}h). ` +
        "Checked from Cloudflare, outside GitHub Actions -- every other alert in this project " +
        "shares fate with the pipeline it monitors; this one doesn't. check-price.yml is very " +
        `likely not running. ${PUBLIC_FORECAST_URL}`,
      priority: 5,
      tags: "rotating_light,warning",
    };
  }
  if (level === "warn") {
    return {
      title: "Gold Tracker: forecast staleness (dead-man's switch)",
      body:
        `data/forecast.json's predicted_at is ${age} old (>= ${WARN_THRESHOLD_HOURS}h). ` +
        `Checked from Cloudflare, outside GitHub Actions. If this keeps climbing toward ` +
        `${ESCALATE_THRESHOLD_HOURS}h, check-price.yml is likely stuck or not running. ${PUBLIC_FORECAST_URL}`,
      priority: 4,
      tags: "warning,hourglass",
    };
  }
  if (level === "unverifiable") {
    return {
      title: "Gold Tracker: dead-man's switch could not verify the site",
      body:
        `Could not fetch or parse the public forecast.json (${reason || "unknown reason"}). ` +
        "This does NOT necessarily mean the pipeline is down -- it may mean this check itself " +
        `is broken (GitHub Pages outage, schema change). Investigate both. ${PUBLIC_FORECAST_URL}`,
      priority: 4,
      tags: "warning,mag",
    };
  }
  return null;
}

function recoveredAlert() {
  return {
    title: "Gold Tracker: forecast staleness resolved",
    body: "data/forecast.json is fresh again (dead-man's switch, checked from Cloudflare).",
    priority: 3,
    tags: "white_check_mark",
  };
}

/**
 * Q4 alert copy for the Tanishq-silence channel. Deliberately distinct
 * framing from buildAlert above: the FORECAST is not stale in this
 * scenario (IBJA-calibrated fallback keeps it fresh) -- what's silent is
 * specifically the Tanishq confirmation, which today the page itself gives
 * the user no way to notice (see Q4c: price_source stays "ibja_calibrated"
 * and renders identically whether Tanishq confirmed 2h ago or 3 weeks ago).
 */
export function buildTanishqAlert(level, ageHours, reason) {
  const age = fmtAge(ageHours);
  if (level === "escalate") {
    return {
      title: "Gold Tracker: Tanishq confirmation silent for days (dead-man's switch)",
      body:
        `data/forecast.json's scraped_at (last SUCCESSFUL Tanishq reading) is ${age} old ` +
        `(>= ${TANISHQ_ESCALATE_HOURS}h) -- past every gap seen in 30 days of normal operation. ` +
        "The site is still serving IBJA-calibrated estimates fine; nothing on the page tells " +
        "users this. The self-hosted runner has very likely died permanently -- check " +
        `docs/RUNBOOK.md's self-hosted runner section. ${PUBLIC_FORECAST_URL}`,
      priority: 5,
      tags: "rotating_light,warning",
    };
  }
  if (level === "warn") {
    return {
      title: "Gold Tracker: Tanishq confirmation quiet (dead-man's switch)",
      body:
        `data/forecast.json's scraped_at is ${age} old (>= ${TANISHQ_WARN_HOURS}h) -- longer than ` +
        "~97% of gaps observed in 30 days of normal operation. Might just be a slow cycle; if it " +
        `keeps climbing toward ${TANISHQ_ESCALATE_HOURS}h, the self-hosted runner is likely offline. ${PUBLIC_FORECAST_URL}`,
      priority: 3,
      tags: "hourglass",
    };
  }
  if (level === "unverifiable") {
    return {
      title: "Gold Tracker: dead-man's switch could not verify Tanishq freshness",
      body:
        `Could not read scraped_at from the public forecast.json (${reason || "unknown reason"}). ${PUBLIC_FORECAST_URL}`,
      priority: 3,
      tags: "warning,mag",
    };
  }
  return null;
}

function tanishqRecoveredAlert() {
  return {
    title: "Gold Tracker: Tanishq confirmation resumed",
    body:
      "data/forecast.json's scraped_at is fresh again -- the self-hosted runner is confirming " +
      "readings (dead-man's switch, checked from Cloudflare).",
    priority: 2,
    tags: "white_check_mark",
  };
}

/**
 * Decide whether to send an alert this run, given the freshly computed
 * level/age and the previously stored state (or null on first run / after
 * KV eviction). Pure function -- no fetch, no KV, no Date.now() -- so every
 * branch is directly testable.
 *
 * Dedup policy (beyond the literal WARN-5h/ESCALATE-10h spec, added per
 * rule 98a/existing-catalog convention and stated explicitly here rather
 * than silently): alert once on entering a level, then re-alert at most
 * once every REMINDER_INTERVAL_HOURS while the same level persists, so a
 * multi-hour outage doesn't fire every 30 min but also isn't a single
 * notification that's easy to miss. A transition back to "ok" from a
 * non-ok level sends one low-priority recovery notice.
 */
/**
 * G4a: IST calendar date (YYYY-MM-DD) for a given UTC timestamp. Matches the
 * rest of this project's alert catalog (ml/notifications.py's T-series
 * "once per IST day" gating) rather than UTC, so heartbeat cadence reads
 * the same way every other daily alert in this project already does.
 */
export function istDateString(nowMs) {
  const IST_OFFSET_MS = 5.5 * 3_600_000;
  return new Date(nowMs + IST_OFFSET_MS).toISOString().slice(0, 10);
}

/**
 * G4a: without a heartbeat, this switch cannot report its own liveness --
 * silence is ambiguous between "everything is fine" and "the switch itself
 * died" (Cloudflare account issue, quota exhaustion, a bad deploy). Once per
 * IST calendar day, independent of whatever WARN/ESCALATE/recovery alert may
 * also have fired this run -- the heartbeat's job is specifically to make
 * absence-of-alerts informative, not to report site staleness (that's
 * decideAction's job). Priority 1 (below T-series' informational tier, e.g.
 * T8 in ml/notifications.py) since "I'm still running" is the least urgent
 * thing this switch ever says.
 */
export function shouldSendHeartbeat(lastHeartbeatDateIst, nowMs) {
  const todayIst = istDateString(nowMs);
  return { send: lastHeartbeatDateIst !== todayIst, todayIst };
}

// Q4 (audit 2026-09-03): tanishqLevel/tanishqAgeHours are optional trailing
// params (existing callers passing just currentLevel/ageHours are
// unaffected) -- when given, the daily heartbeat also states Tanishq's own
// silence channel, so a human reading heartbeats has a chance to notice a
// climbing scraped_at age well before it ever reaches WARN/ESCALATE.
export function buildHeartbeatAlert(currentLevel, ageHours, tanishqLevel = null, tanishqAgeHours = null) {
  const age = fmtAge(ageHours);
  const tanishqSuffix =
    tanishqLevel !== null
      ? ` Tanishq confirmation: ${fmtAge(tanishqAgeHours)} old, level: ${tanishqLevel}.`
      : "";
  return {
    title: "Gold Tracker: dead-man's switch heartbeat",
    body: `Still running, checked from Cloudflare. Current forecast.json age: ${age}, level: ${currentLevel}.${tanishqSuffix} If this daily heartbeat ever stops arriving, the switch itself has gone down.`,
    priority: 1,
    tags: "heartbeat",
  };
}

// Shared by decideAction (forecast-staleness channel) and decideTanishqAction
// (Q4, audit 2026-09-03: Tanishq-silence channel) so the dedup/reminder
// state-machine logic is defined once -- only the alert copy differs between
// the two channels, via buildAlertFn/recoveredAlertFn. Each channel keeps
// its own KV state key (see index.mjs) so the two never share or clobber
// each other's dedup timing.
function decideActionGeneric(current, previousState, nowMs, buildAlertFn, recoveredAlertFn) {
  const prevLevel = previousState ? previousState.level : "ok";
  const prevSentAtMs = previousState ? previousState.lastSentAtMs : null;

  if (current.level === "ok") {
    if (prevLevel === "ok") {
      return { send: false, alert: null, nextState: { level: "ok", lastSentAtMs: prevSentAtMs } };
    }
    return { send: true, alert: recoveredAlertFn(), nextState: { level: "ok", lastSentAtMs: nowMs } };
  }

  const levelChanged = current.level !== prevLevel;
  const dueForReminder =
    prevSentAtMs !== null && nowMs - prevSentAtMs >= REMINDER_INTERVAL_HOURS * 3_600_000;
  const shouldSend = levelChanged || prevSentAtMs === null || dueForReminder;

  if (!shouldSend) {
    return {
      send: false,
      alert: null,
      nextState: { level: current.level, lastSentAtMs: prevSentAtMs },
    };
  }
  return {
    send: true,
    alert: buildAlertFn(current.level, current.ageHours, current.reason),
    nextState: { level: current.level, lastSentAtMs: nowMs },
  };
}

export function decideAction(current, previousState, nowMs) {
  return decideActionGeneric(current, previousState, nowMs, buildAlert, recoveredAlert);
}

/** Q4: same state machine, Tanishq-silence alert copy. */
export function decideTanishqAction(current, previousState, nowMs) {
  return decideActionGeneric(current, previousState, nowMs, buildTanishqAlert, tanishqRecoveredAlert);
}
