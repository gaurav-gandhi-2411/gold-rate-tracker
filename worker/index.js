// worker/index.js
// Cloudflare Workers ES module — scheduled handler that fetches Tanishq gold
// rates via a plain HTTP GET and dispatches a repository_dispatch event to
// GitHub Actions when valid rates are found.
//
// Self-contained deploy artifact: parse/validate/CF-detection logic is copied
// verbatim from scraper/scrape.js to avoid a build step.  Keep in sync with
// that file whenever the shared constants or logic change.
//
// Exports: parseGoldRates, isCFChallengeHtml, validate, runScheduled
// Default export: { scheduled(event, env, ctx) }

// ── Validation thresholds (per gram, INR) ─────────────────────────────────────
// SYNC CONTRACT — these four constants must stay byte-for-byte identical to
// scraper/scrape.js.  If you update one file, update the other.
const RANGE_MIN = 2000;
const RANGE_MAX = 25000;
const RATIO_22_24_MIN = 0.905; // theoretical 91.67%, allow ±1%
const RATIO_22_24_MAX = 0.925;
const RATIO_18_24_MIN = 0.73; // theoretical 75%, allow ±2%
const RATIO_18_24_MAX = 0.77;

// ── Request constants ─────────────────────────────────────────────────────────
const TANISHQ_URL = "https://www.tanishq.co.in/gold-rate.html?lang=en_IN";
const GITHUB_API = "https://api.github.com";
const FETCH_HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
  Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
  "Accept-Language": "en-IN,en;q=0.9",
};

// ── CF challenge detection ────────────────────────────────────────────────────

/**
 * Returns true when an HTML string looks like a Cloudflare challenge/interstitial page.
 * Mirrors the marker set in isCloudflareChallenge() but operates on a raw HTML string.
 * CF can return 200 with a challenge body, so HTTP status alone is not sufficient.
 *
 * SYNC CONTRACT — logic must stay byte-for-byte identical to isCFChallengeHtml()
 * in scraper/scrape.js.
 *
 * @param {string} html
 * @returns {boolean}
 */
export function isCFChallengeHtml(html) {
  const head = html.slice(0, 4000);
  return (
    /<title[^>]*>Just a moment\.\.\.<\/title>/i.test(head) ||
    /<title[^>]*>Attention Required/i.test(head) ||
    html.includes("cf-challenge") ||
    html.includes("_cf_chl_") ||
    html.includes("cf-browser-verification")
  );
}

// ── Rate extraction ───────────────────────────────────────────────────────────

/**
 * Parse goldpurity-rate span data attributes from a raw HTML string.
 * Returns { rate22, rate24, rate18 } or null when the span or any attribute
 * is absent, empty, or non-numeric.
 *
 * SYNC CONTRACT — logic must stay byte-for-byte identical to parseGoldRates()
 * in scraper/scrape.js.
 *
 * @param {string} html
 * @returns {{ rate22: number, rate24: number, rate18: number } | null}
 */
export function parseGoldRates(html) {
  const spanMatch = html.match(
    /<span[^>]+class="[^"]*goldpurity-rate[^"]*"([^>]*)>/,
  );
  if (!spanMatch) return null;
  const attrs = spanMatch[1];
  const rate22 = parseInt(attrs.match(/data-goldrate22kt="(\d+)"/)?.[1], 10);
  const rate24 = parseInt(attrs.match(/data-goldrate24kt="(\d+)"/)?.[1], 10);
  const rate18 = parseInt(attrs.match(/data-goldrate18kt="(\d+)"/)?.[1], 10);
  if (!Number.isFinite(rate22) || !Number.isFinite(rate24) || !Number.isFinite(rate18)) {
    return null;
  }
  return { rate22, rate24, rate18 };
}

// ── Validation ────────────────────────────────────────────────────────────────

/**
 * Validate extracted rates. Throws with a descriptive message if any check
 * fails so the workflow fails visibly rather than silently writing bad data.
 * Validation failures are NOT retryable (deterministic data problem).
 *
 * SYNC CONTRACT — logic must stay byte-for-byte identical to validate()
 * in scraper/scrape.js.
 *
 * @param {number} rate22
 * @param {number} rate24
 * @param {number} rate18
 * @returns {{ r22_24: number, r18_24: number }}
 */
export function validate(rate22, rate24, rate18) {
  const fail = (msg) => {
    throw new Error(
      `Rate validation failed: ${msg}\n` +
        `  Extracted: 22K=₹${rate22}, 24K=₹${rate24}, 18K=₹${rate18}`,
    );
  };

  for (const [label, val] of [
    ["22K", rate22],
    ["24K", rate24],
    ["18K", rate18],
  ]) {
    if (!Number.isFinite(val) || val < RANGE_MIN || val > RANGE_MAX) {
      fail(`${label}=₹${val} is outside ₹${RANGE_MIN}–₹${RANGE_MAX}`);
    }
  }

  if (!(rate18 < rate22 && rate22 < rate24)) {
    fail(`expected 18K < 22K < 24K but got ${rate18} < ${rate22} < ${rate24}`);
  }

  const r22_24 = rate22 / rate24;
  const r18_24 = rate18 / rate24;

  if (r22_24 < RATIO_22_24_MIN || r22_24 > RATIO_22_24_MAX) {
    fail(
      `22K/24K ratio ${r22_24.toFixed(4)} outside [${RATIO_22_24_MIN}, ${RATIO_22_24_MAX}]`,
    );
  }
  if (r18_24 < RATIO_18_24_MIN || r18_24 > RATIO_18_24_MAX) {
    fail(
      `18K/24K ratio ${r18_24.toFixed(4)} outside [${RATIO_18_24_MIN}, ${RATIO_18_24_MAX}]`,
    );
  }

  return { r22_24, r18_24 };
}

// ── Scheduled handler logic ───────────────────────────────────────────────────

/**
 * Core scheduled handler logic with an injectable fetch implementation.
 * Exported for unit testing without a live CF Worker runtime.
 *
 * Does NOT throw — CF scheduled handlers must not throw; all error paths log
 * and return early so the Worker runtime does not mark the cron invocation as
 * failed due to an unhandled exception.
 *
 * @param {{ GITHUB_TOKEN?: string, GITHUB_OWNER?: string, GITHUB_REPO?: string }} env
 * @param {typeof fetch} fetchFn  Injectable fetch (real or mock)
 * @returns {Promise<void>}
 */
export async function runScheduled(env, fetchFn) {
  // 1. Guard: required secrets must be present.
  if (!env.GITHUB_TOKEN || !env.GITHUB_OWNER || !env.GITHUB_REPO) {
    console.error("[worker] missing required env vars (GITHUB_TOKEN / GITHUB_OWNER / GITHUB_REPO) — skipping dispatch");
    return;
  }

  // 2. Fetch Tanishq page.
  let response;
  try {
    response = await fetchFn(TANISHQ_URL, { headers: FETCH_HEADERS });
  } catch (err) {
    console.error(`[worker] Tanishq fetch threw: ${err.message} — skipping dispatch`);
    return;
  }

  if (!response.ok) {
    console.error(`[worker] Tanishq fetch failed: HTTP ${response.status} — skipping dispatch`);
    return;
  }

  const html = await response.text();

  // 3. CF challenge check.
  if (isCFChallengeHtml(html)) {
    console.error("[worker] CF challenge/interstitial — skipping dispatch");
    return;
  }

  // 4. Parse rates.
  const rates = parseGoldRates(html);
  if (!rates) {
    console.error("[worker] goldpurity-rate span absent or incomplete — skipping dispatch");
    return;
  }

  // 5. Validate.
  try {
    validate(rates.rate22, rates.rate24, rates.rate18);
  } catch (err) {
    console.error(`[worker] validation failed: ${err.message} — skipping dispatch`);
    return;
  }

  // 6. Build payload.
  const payload = {
    event_type: "tanishq-price",
    client_payload: {
      "22k": rates.rate22,
      "24k": rates.rate24,
      "18k": rates.rate18,
      timestamp: new Date().toISOString(),
    },
  };

  // 7. Dispatch repository_dispatch to GitHub.
  let dispatchResponse;
  try {
    dispatchResponse = await fetchFn(
      `${GITHUB_API}/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.GITHUB_TOKEN}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      },
    );
  } catch (err) {
    console.error(`[worker] dispatch fetch threw: ${err.message}`);
    return;
  }

  // 8. Log outcome.
  if (!dispatchResponse.ok) {
    console.error(`[worker] dispatch failed: HTTP ${dispatchResponse.status}`);
  } else {
    console.log(`[worker] dispatched: 22k=${rates.rate22}`);
  }
}

// ── Default CF Workers export ─────────────────────────────────────────────────

export default {
  async scheduled(event, env, ctx) {
    await runScheduled(env, fetch);
  },
};
