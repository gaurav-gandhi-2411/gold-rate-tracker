// Pure logic for the dead-man's switch, kept free of any Workers-runtime API
// (fetch, KV bindings) so it can be unit-tested with plain Node -- the
// Workers entry point (index.mjs) is a thin wiring layer around this file.

export const WARN_THRESHOLD_HOURS = 5;
export const ESCALATE_THRESHOLD_HOURS = 10;

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
export function decideAction(current, previousState, nowMs) {
  const prevLevel = previousState ? previousState.level : "ok";
  const prevSentAtMs = previousState ? previousState.lastSentAtMs : null;

  if (current.level === "ok") {
    if (prevLevel === "ok") {
      return { send: false, alert: null, nextState: { level: "ok", lastSentAtMs: prevSentAtMs } };
    }
    return { send: true, alert: recoveredAlert(), nextState: { level: "ok", lastSentAtMs: nowMs } };
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
    alert: buildAlert(current.level, current.ageHours, current.reason),
    nextState: { level: current.level, lastSentAtMs: nowMs },
  };
}
