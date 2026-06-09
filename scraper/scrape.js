// scrape.js
// Loads the Tanishq gold rate page in a real headless browser and extracts
// 22K, 24K, and 18K rates per gram using DOM data attributes.
//
// Output (when run as entry module): prints a single JSON line to stdout, e.g.
//   {"timestamp":"2026-05-09T08:00:00.000Z","22k":14010,"24k":15284,"18k":11463}
//
// On failure it exits non-zero so the GitHub Action fails loudly instead of
// silently writing bad data.
//
// Exports hybridScrape(), scrapeWithRetry(), fetchWithRequests(),
// isCFChallengeHtml(), parseGoldRates(), isCloudflareChallenge(),
// extractRates(), validate() for unit testing.

import { chromium } from "playwright";
import { fileURLToPath } from "url";

// ── Target URL ──────────────────────────────────────────────────────────────
// Inject SCRAPER_TARGET_URL in tests to point at a local mock HTTP server.
const TARGET_URL =
  process.env.SCRAPER_TARGET_URL ??
  "https://www.tanishq.co.in/gold-rate.html?lang=en_IN";

// ── Validation thresholds (per gram, INR) ────────────────────────────────────
const RANGE_MIN = 2000;
const RANGE_MAX = 25000;
const RATIO_22_24_MIN = 0.905; // theoretical 91.67%, allow ±1%
const RATIO_22_24_MAX = 0.925;
const RATIO_18_24_MIN = 0.73; // theoretical 75%, allow ±2%
const RATIO_18_24_MAX = 0.77;

// ── Retry / timeout constants ────────────────────────────────────────────────
// ENV overrides exist solely for test injection; never set in production.
const MAX_RETRIES = 3;

// Delays between retry attempts (ms). Index 0 = delay before attempt 2, etc.
const RETRY_DELAYS_MS = process.env.SCRAPER_RETRY_DELAYS_MS
  ? process.env.SCRAPER_RETRY_DELAYS_MS.split(",").map(Number)
  : [5000, 15000]; // 5s then 15s

// Playwright navigation timeout (ms).
const NAV_TIMEOUT_MS = parseInt(process.env.SCRAPER_NAV_TIMEOUT_MS ?? "60000", 10);

// Selector wait timeout (ms). Kept short in tests to avoid 30s hangs on
// FM-2 (selector drift) test fixtures.
const SELECTOR_TIMEOUT_MS = parseInt(
  process.env.SCRAPER_SELECTOR_TIMEOUT_MS ?? "30000",
  10,
);

// ── Browser fingerprint rotation (H3) ────────────────────────────────────────
// Rotate viewport and UA per attempt to reduce per-session CF fingerprinting.
const VIEWPORTS = [
  { width: 1280, height: 800 },
  { width: 1366, height: 768 },
  { width: 1440, height: 900 },
];

// Keep UA strings current with Chrome stable. Chrome releases ~every 4 weeks.
// Last bumped: 2026-06-07 (Chrome 148 — Playwright 1.60.0 bundles Chromium 148.0.7778.96).
// Update when the installed Playwright Chromium version lags by >2 major versions vs. this UA.
const USER_AGENTS = [
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
];

// ── CF challenge detection (H2) ───────────────────────────────────────────────

/**
 * Returns true if the current page is a Cloudflare bot challenge page.
 *
 * CF challenge pages carry a distinctive title ("Just a moment...") and/or
 * body markup with CF-specific identifiers. Checking these markers takes
 * <100ms on a loaded page, allowing fast retry before the 30s
 * waitForSelector timeout burns.
 */
export async function isCloudflareChallenge(page) {
  try {
    const title = await page.title();
    if (title === "Just a moment..." || title.startsWith("Attention Required")) {
      return true;
    }
    // Scan only the first 1000 chars — CF identifiers appear near the top.
    const snippet = await page.evaluate(
      () => document.body?.innerHTML?.slice(0, 1000) ?? "",
    );
    return (
      snippet.includes("cf-challenge") ||
      snippet.includes("_cf_chl_") ||
      snippet.includes("cf-browser-verification")
    );
  } catch {
    return false;
  }
}

// ── Rate extraction ───────────────────────────────────────────────────────────

/**
 * Extract gold rates from the Tanishq page using the `data-goldrate*`
 * attributes on the `span.goldpurity-rate` element.
 *
 * Waits up to SELECTOR_TIMEOUT_MS for the JS-rendered widget to appear.
 * Throws a non-retryable error on timeout (could be FM-2 selector drift or
 * FM-5 partial load — both look identical; caller must not retry on timeout).
 *
 * @returns {{ rate22: number, rate24: number, rate18: number }}
 */
export async function extractRates(page) {
  await page.waitForSelector("span.goldpurity-rate[data-goldrate22kt]", {
    timeout: SELECTOR_TIMEOUT_MS,
  });

  const rates = await page.evaluate(() => {
    const el = document.querySelector("span.goldpurity-rate[data-goldrate22kt]");
    if (!el) return null;
    return {
      rate22: parseInt(el.dataset.goldrate22kt, 10),
      rate24: parseInt(el.dataset.goldrate24kt, 10),
      rate18: parseInt(el.dataset.goldrate18kt, 10),
    };
  });

  if (!rates) throw new Error("goldpurity-rate element not found after waiting");
  return rates;
}

// ── Validation ────────────────────────────────────────────────────────────────

/**
 * Validate extracted rates. Throws with a descriptive message if any check
 * fails so the workflow fails visibly rather than silently writing bad data.
 * Validation failures are NOT retryable (deterministic data problem).
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

// ── Requests path (Φ24) ──────────────────────────────────────────────────────
// Plain HTTP fetch attempted before Playwright — eliminates Chromium startup
// (~8-15 s) when CF passes a browser-UA GET.  Falls back to Playwright on any
// failure so future CF tightening degrades gracefully.
//
// The requests path accepts the result ONLY when ALL of:
//   1. HTTP status 200 (response.ok)
//   2. Body is NOT a CF challenge/interstitial page (CF can return 200 + challenge HTML)
//   3. goldpurity-rate span present with all three karat data attributes
//   4. Extracted values pass validate()
//
// scrapeWithRetry() (Playwright-only) is kept UNCHANGED; hybridScrape() wraps it.

const REQUESTS_TIMEOUT_MS = 10_000;

const REQUESTS_HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
  Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
  "Accept-Language": "en-IN,en;q=0.9",
};

/**
 * Returns true when an HTML string looks like a Cloudflare challenge/interstitial page.
 * Mirrors the marker set in isCloudflareChallenge() but operates on a raw HTML string.
 * CF can return 200 with a challenge body, so HTTP status alone is not sufficient.
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

/**
 * Parse goldpurity-rate span data attributes from a raw HTML string.
 * Returns { rate22, rate24, rate18 } or null when the span or any attribute
 * is absent, empty, or non-numeric.
 *
 * [^>]* in the regex matches any char including newlines because it is a
 * negated character class (not the . metachar), so multi-line attribute
 * formatting is handled without the /s flag.
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

/**
 * Attempt to fetch and parse gold rates using a plain HTTP GET (no browser).
 * Throws on any failure; callers fall back to Playwright via scrapeWithRetry().
 *
 * @param {string} [targetUrl]
 * @returns {Promise<{timestamp: string, "22k": number, "24k": number, "18k": number, source: string}>}
 */
export async function fetchWithRequests(targetUrl = TARGET_URL) {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), REQUESTS_TIMEOUT_MS);
  let response;
  try {
    response = await fetch(targetUrl, {
      headers: REQUESTS_HEADERS,
      signal: ac.signal,
    });
  } finally {
    clearTimeout(timer);
  }

  if (!response.ok) {
    throw new Error(`requests path: HTTP ${response.status}`);
  }

  const html = await response.text();

  if (isCFChallengeHtml(html)) {
    throw new Error("requests path: CF challenge/interstitial page detected");
  }

  const rates = parseGoldRates(html);
  if (!rates) {
    throw new Error("requests path: goldpurity-rate span absent or incomplete");
  }

  const { rate22, rate24, rate18 } = rates;
  validate(rate22, rate24, rate18); // throws "Rate validation failed: …" on bad data

  return {
    timestamp: new Date().toISOString(),
    "22k": rate22,
    "24k": rate24,
    "18k": rate18,
    source: targetUrl,
  };
}

// ── Single attempt ────────────────────────────────────────────────────────────

/**
 * One scrape attempt. Tags errors as `retryable: true` for transient failures
 * so the retry loop can decide whether to try again.
 *
 * Retryable:  navigation errors, CF challenge page (H2)
 * Not retryable: waitForSelector timeout (FM-2 selector drift looks identical
 *                to FM-5 partial load — both are deterministic from this
 *                attempt's perspective), validate() failures (FM-6)
 *
 * @param {string} targetUrl
 * @param {number} attemptIndex  zero-based
 * @returns {{ timestamp, "22k", "24k", "18k", source }}
 */
async function scrapeAttempt(targetUrl, attemptIndex) {
  const viewport = VIEWPORTS[attemptIndex % VIEWPORTS.length];
  const userAgent = USER_AGENTS[attemptIndex % USER_AGENTS.length];

  const browser = await chromium.launch({
    headless: true,
    args: ["--no-sandbox", "--disable-blink-features=AutomationControlled"],
  });

  const context = await browser.newContext({
    userAgent,
    viewport,
    locale: "en-IN",
    timezoneId: "Asia/Kolkata", // H3: CI runner defaults UTC; India timezone reduces fingerprint gap
    extraHTTPHeaders: { "Accept-Language": "en-IN,en;q=0.9" }, // H3
  });

  const page = await context.newPage();

  try {
    // Navigation errors (DNS failure, timeout) are transient → retryable
    try {
      await page.goto(targetUrl, {
        waitUntil: "domcontentloaded",
        timeout: NAV_TIMEOUT_MS,
      });
    } catch (navErr) {
      throw Object.assign(navErr, { retryable: true });
    }

    // H2: Detect CF challenge in <100ms — skip the 30s waitForSelector timeout
    if (await isCloudflareChallenge(page)) {
      throw Object.assign(
        new Error(`Cloudflare challenge page (attempt ${attemptIndex + 1}/${MAX_RETRIES})`),
        { retryable: true, isCFBlock: true },
      );
    }

    // extractRates → waitForSelector timeout is NOT retryable (FM-2 / FM-5)
    const { rate22, rate24, rate18 } = await extractRates(page);
    const { r22_24, r18_24 } = validate(rate22, rate24, rate18);

    process.stderr.write(
      `[scraper] attempt ${attemptIndex + 1} OK\n` +
        `22K: ₹${rate22.toLocaleString("en-IN")}\n` +
        `24K: ₹${rate24.toLocaleString("en-IN")}\n` +
        `18K: ₹${rate18.toLocaleString("en-IN")}\n` +
        `ratios: 22/24=${r22_24.toFixed(3)} ✓  18/24=${r18_24.toFixed(3)} ✓\n`,
    );

    return {
      timestamp: new Date().toISOString(),
      "22k": rate22,
      "24k": rate24,
      "18k": rate18,
      source: targetUrl,
    };
  } catch (err) {
    // Dump page body for diagnosis on non-CF errors (helps diagnose DOM changes)
    if (!err.isCFBlock) {
      try {
        const bodyText = await page.evaluate(() => document.body.innerText);
        process.stderr.write("\n=== PAGE TEXT (first 3000 chars) ===\n");
        process.stderr.write(bodyText.slice(0, 3000));
        process.stderr.write("\n=== END PAGE TEXT ===\n");
      } catch (_) {}
    }
    throw err;
  } finally {
    await browser.close();
  }
}

// ── Retry wrapper (H1) ────────────────────────────────────────────────────────

/**
 * Scrape Tanishq gold rates with retry.
 *
 * H1: up to 3 attempts with backoff (5s, 15s between retries).
 *     Each retry creates a fresh browser context with rotated fingerprint.
 * H2: Cloudflare challenge pages detected in <100ms and retried immediately,
 *     avoiding the 30s waitForSelector timeout per blocked attempt.
 *
 * HONEST FRAMING (see ADR 016):
 * Retry reduces TRANSIENT Cloudflare challenges (JS challenge that resolves
 * on a second request from the same IP). It does NOT defeat IP-level CF
 * blocking — if the runner's outbound IP is flagged, all 3 attempts hit the
 * same IP and all fail. The backstop for sustained IP blocks is the next
 * scheduled cron run (~6h later on a likely-different IP). Do not interpret
 * this retry logic as "Cloudflare fixed" — it reduces the gap rate from
 * transient failures while graceful staleness handling covers the rest.
 *
 * @param {string} [targetUrl]  Defaults to TARGET_URL; injectable for tests.
 * @returns {Promise<{timestamp, "22k", "24k", "18k", source}>}
 */
export async function scrapeWithRetry(targetUrl = TARGET_URL) {
  let lastError;

  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    try {
      return await scrapeAttempt(targetUrl, attempt);
    } catch (err) {
      lastError = err;
      const retryable = err.retryable === true;

      if (!retryable) {
        // Deterministic failure (selector absent / validation error) — do not retry.
        process.stderr.write(
          `[scraper] fatal on attempt ${attempt + 1}: ${err.message}\n`,
        );
        break;
      }

      if (attempt < MAX_RETRIES - 1) {
        const delay =
          RETRY_DELAYS_MS[attempt] ?? RETRY_DELAYS_MS[RETRY_DELAYS_MS.length - 1];
        const kind = err.isCFBlock ? "CF block" : "transient error";
        process.stderr.write(
          `[scraper] attempt ${attempt + 1}/${MAX_RETRIES} failed (${kind}): ${err.message}\n` +
            `[scraper] retrying in ${delay / 1000}s...\n`,
        );
        await new Promise((r) => setTimeout(r, delay));
      } else {
        process.stderr.write(
          `[scraper] all ${MAX_RETRIES} attempts failed. Last: ${err.message}\n`,
        );
      }
    }
  }

  throw lastError;
}

// ── Hybrid orchestration (Φ24) ───────────────────────────────────────────────

/**
 * Production entry point.  Tries a plain HTTP GET first (fast, no Chromium);
 * on any failure falls back to the unchanged Playwright scrapeWithRetry() path.
 * Logs [scraper] fetch_method= to stderr on both paths for hit-rate monitoring.
 *
 * scrapeWithRetry() is kept UNCHANGED so existing hardening tests call it
 * directly without triggering the requests path.
 *
 * @param {string} [targetUrl]
 * @returns {Promise<{timestamp: string, "22k": number, "24k": number, "18k": number, source: string}>}
 */
export async function hybridScrape(targetUrl = TARGET_URL) {
  try {
    const result = await fetchWithRequests(targetUrl);
    process.stderr.write("[scraper] fetch_method=requests\n");
    return result;
  } catch (requestsErr) {
    process.stderr.write(
      `[scraper] requests path failed (${requestsErr.message}) — falling back to Playwright\n`,
    );
  }

  const result = await scrapeWithRetry(targetUrl);
  process.stderr.write("[scraper] fetch_method=playwright\n");
  return result;
}

// ── Entry point ───────────────────────────────────────────────────────────────

const __filename = fileURLToPath(import.meta.url);
if (process.argv[1] === __filename) {
  hybridScrape()
    .then((result) => console.log(JSON.stringify(result)))
    .catch((err) => {
      console.error("Scrape failed:", err.message);
      process.exit(1);
    });
}
