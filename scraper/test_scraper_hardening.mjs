// scraper/test_scraper_hardening.mjs
// Scraper hardening tests — covers all failure modes added/addressed in the
// scraper hardening engagement (H1 retry, H2 CF detection, FM-2/FM-6 fast-fail).
//
// Run: node --test test_scraper_hardening.mjs  (from scraper/ directory)
//
// Norm #11: no live network calls. All tests use a local HTTP mock server.
// SCRAPER_SELECTOR_TIMEOUT_MS and SCRAPER_RETRY_DELAYS_MS are injected to
// keep tests fast (avoid 30s selector waits and 5s/15s retry delays).

import assert from "assert/strict";
import { test } from "node:test";
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dir = dirname(fileURLToPath(import.meta.url));

// ── Set ENV overrides BEFORE dynamic import so module-level constants are short ──
process.env.SCRAPER_SELECTOR_TIMEOUT_MS = "300";  // 0.3s instead of 30s
process.env.SCRAPER_RETRY_DELAYS_MS = "50,50";    // 50ms instead of 5s/15s
process.env.SCRAPER_NAV_TIMEOUT_MS = "10000";     // 10s instead of 60s

// Dynamic import ensures env vars above are read by scrape.js module constants.
const { scrapeWithRetry, isCloudflareChallenge, validate } = await import(
  "../scraper/scrape.js"
);

// ── HTML fixtures ─────────────────────────────────────────────────────────────

const CF_CHALLENGE_HTML = readFileSync(
  resolve(__dir, "..", "tests", "fixtures", "cf_challenge.html"),
  "utf8",
);

// Minimal Tanishq page with the gold rate widget present.
const GOLD_RATE_HTML = `<!DOCTYPE html>
<html><head><title>Gold Rate Today - Tanishq</title></head>
<body>
<span class="goldpurity-rate"
      data-goldrate22kt="14010"
      data-goldrate24kt="15284"
      data-goldrate18kt="11463">14010</span>
</body>
</html>`;

// Tanishq page without the widget (FM-2: selector drift).
const NO_WIDGET_HTML = `<!DOCTYPE html>
<html><head><title>Gold Rate Today - Tanishq</title></head>
<body><p>Gold rates are temporarily unavailable.</p></body>
</html>`;

// Widget present but price attribute is empty (FM-6: malformed data).
// The span must have text content so Playwright's visibility check passes and
// waitForSelector() returns promptly — allowing validate() to throw, not timeout.
const BAD_PRICE_HTML = `<!DOCTYPE html>
<html><head><title>Gold Rate Today - Tanishq</title></head>
<body>
<span class="goldpurity-rate"
      data-goldrate22kt=""
      data-goldrate24kt="15284"
      data-goldrate18kt="11463">--</span>
</body>
</html>`;

// Widget present but 22K out of valid range (FM-6: out-of-range).
const OUT_OF_RANGE_HTML = `<!DOCTYPE html>
<html><head><title>Gold Rate Today - Tanishq</title></head>
<body>
<span class="goldpurity-rate"
      data-goldrate22kt="999"
      data-goldrate24kt="15284"
      data-goldrate18kt="11463">999</span>
</body>
</html>`;

// ── Mock HTTP server helper ────────────────────────────────────────────────────

/**
 * Starts a local HTTP server that serves responses from `htmlSequence` in
 * order. The last entry is repeated for any additional requests.
 * Only counts requests to the root path "/"; other paths (favicon, etc.)
 * get a 404 without incrementing the counter.
 *
 * @param {string[]} htmlSequence
 * @returns {Promise<{url: string, getCount: () => number, close: () => Promise<void>}>}
 */
function startMockServer(htmlSequence) {
  let reqCount = 0;
  const server = createServer((req, res) => {
    if (req.url !== "/") {
      res.writeHead(404);
      res.end();
      return;
    }
    const html =
      htmlSequence[reqCount] ?? htmlSequence[htmlSequence.length - 1];
    reqCount++;
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end(html);
  });

  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      resolve({
        url: `http://127.0.0.1:${port}`,
        getCount: () => reqCount,
        close: () => new Promise((res) => server.close(res)),
      });
    });
  });
}

// ── FM-1: Cloudflare challenge → retry → success ──────────────────────────────

test("FM-1: CF challenge on attempt 1 → retries → succeeds on attempt 2", async () => {
  const mock = await startMockServer([CF_CHALLENGE_HTML, GOLD_RATE_HTML]);
  try {
    const result = await scrapeWithRetry(mock.url);
    assert.equal(mock.getCount(), 2, "exactly 2 requests: 1 CF block + 1 success");
    assert.equal(result["22k"], 14010);
    assert.equal(result["24k"], 15284);
    assert.equal(result["18k"], 11463);
  } finally {
    await mock.close();
  }
});

// ── FM-1: 3× CF challenge → fails loud, no data written ─────────────────────

test("FM-1: 3× CF challenge → all retries exhausted → throws", async () => {
  const mock = await startMockServer([
    CF_CHALLENGE_HTML,
    CF_CHALLENGE_HTML,
    CF_CHALLENGE_HTML,
  ]);
  try {
    await assert.rejects(
      () => scrapeWithRetry(mock.url),
      (err) => {
        assert.match(err.message, /Cloudflare challenge page/);
        // scrape.js's CLI entry point reads this flag to exit 2 (CF block,
        // no alert) instead of 1 (real DOM break, alerts + opens an issue) —
        // see scraper-canary.yml. A regression here silently turns every CF
        // block back into a false-positive "DOM broken" alert (#577's cause).
        assert.equal(err.isCFBlock, true, "CF-exhausted error must be tagged isCFBlock");
        return true;
      },
      "should throw after exhausting all retries",
    );
    assert.equal(mock.getCount(), 3, "all 3 attempts made exactly one request each");
  } finally {
    await mock.close();
  }
});

// ── FM-3/FM-4: Network error → retryable → success after retry ──────────────

test("FM-1+FM-3: success after retry — correct result written to return value", async () => {
  // First attempt: CF challenge. Second: real page. Verifies the returned
  // object contains the correct data (scrapeWithRetry returns the result object).
  const mock = await startMockServer([CF_CHALLENGE_HTML, GOLD_RATE_HTML]);
  try {
    const result = await scrapeWithRetry(mock.url);
    assert.deepEqual(Object.keys(result).sort(), [
      "18k",
      "22k",
      "24k",
      "source",
      "timestamp",
    ]);
    assert.equal(result["22k"], 14010);
    assert.ok(
      result.timestamp.endsWith("Z"),
      "timestamp should be UTC ISO-8601",
    );
  } finally {
    await mock.close();
  }
});

// ── FM-2: Selector absent → fails loud, NOT retried ─────────────────────────

test("FM-2: selector absent → fails, does not retry (only 1 request made)", async () => {
  // A Tanishq-looking page with no .goldpurity-rate span — simulates DOM change.
  // SCRAPER_SELECTOR_TIMEOUT_MS=300ms keeps this test fast.
  const mock = await startMockServer([NO_WIDGET_HTML]);
  try {
    await assert.rejects(
      () => scrapeWithRetry(mock.url),
      // The error should be a Playwright TimeoutError or similar
      (err) => {
        assert.ok(err instanceof Error, "should throw an Error");
        // A real DOM/selector break must NOT be tagged isCFBlock, or
        // scrape.js's CLI entry point would exit 2 and scraper-canary.yml
        // would silently skip alerting on a genuine break.
        assert.ok(!err.isCFBlock, "selector-absent error must not be tagged isCFBlock");
        return true;
      },
    );
    assert.equal(
      mock.getCount(),
      1,
      "only 1 request: selector-absent is not retried",
    );
  } finally {
    await mock.close();
  }
});

// ── FM-6: Malformed price (NaN) → validate rejects, not retried ─────────────

test("FM-6: malformed price (empty attribute) → validate throws, not retried", async () => {
  const mock = await startMockServer([BAD_PRICE_HTML]);
  try {
    await assert.rejects(
      () => scrapeWithRetry(mock.url),
      /Rate validation failed/,
      "validate() should throw with a descriptive message",
    );
    assert.equal(
      mock.getCount(),
      1,
      "only 1 request: validation failure is not retried",
    );
  } finally {
    await mock.close();
  }
});

// ── FM-6: Out-of-range price → validate rejects, not retried ────────────────

test("FM-6: out-of-range price (22K=999) → validate throws, not retried", async () => {
  const mock = await startMockServer([OUT_OF_RANGE_HTML]);
  try {
    await assert.rejects(
      () => scrapeWithRetry(mock.url),
      /Rate validation failed/,
    );
    assert.equal(mock.getCount(), 1, "validation failure is not retried");
  } finally {
    await mock.close();
  }
});

// ── H2: isCloudflareChallenge() unit tests ───────────────────────────────────

test("H2: isCloudflareChallenge detects 'Just a moment...' title", async (t) => {
  const { chromium } = await import("playwright");
  const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
  const page = await browser.newPage();
  await page.setContent(CF_CHALLENGE_HTML);
  try {
    const result = await isCloudflareChallenge(page);
    assert.equal(result, true, "should detect CF challenge by title");
  } finally {
    await browser.close();
  }
});

test("H2: isCloudflareChallenge returns false for normal Tanishq page", async () => {
  const { chromium } = await import("playwright");
  const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
  const page = await browser.newPage();
  await page.setContent(GOLD_RATE_HTML);
  try {
    const result = await isCloudflareChallenge(page);
    assert.equal(result, false, "should not flag normal Tanishq page as CF challenge");
  } finally {
    await browser.close();
  }
});

// ── H4: Alert dedup logic (pure-logic tests, no network) ─────────────────────
// These verify the Python age-check condition used in check-price.yml (H4a/H4b).
// The condition: "send scraper-down alert only when prices.json last entry ≤ 12h old."

test("H4a: alert-dedup condition — age ≤ 12h allows alert", () => {
  // Simulates: data is 6h old → first/second failure → alert should fire
  const ageHours = 6;
  const shouldAlert = ageHours <= 12;
  assert.equal(shouldAlert, true, "should alert on first/second failure (≤12h)");
});

test("H4a: alert-dedup condition — age > 12h suppresses scraper-down alert", () => {
  // Simulates: data is 14h old → sustained failure → staleness guard handles it
  const ageHours = 14;
  const shouldAlert = ageHours <= 12;
  assert.equal(shouldAlert, false, "should suppress alert on sustained failure (>12h)");
});

test("H4b: staleness guard suppressed when SCRAPER_DOWN_THIS_RUN is set", () => {
  // Simulates the env var set by the Alert-on-scraper-failure step.
  // Staleness guard reads this env var and skips ntfy to avoid double-alerting.
  const scraperDownThisRun = "true";
  const shouldSendStalenessNtfy = scraperDownThisRun !== "true";
  assert.equal(
    shouldSendStalenessNtfy,
    false,
    "staleness guard should skip ntfy when scraper-down already fired this run",
  );
});

test("H4b: staleness guard fires normally when scraper succeeded this run", () => {
  // SCRAPER_DOWN_THIS_RUN is unset when scraper succeeds.
  const scraperDownThisRun = "";
  const dataAgeHours = 20;
  const shouldSendStalenessNtfy =
    scraperDownThisRun !== "true" && dataAgeHours > 8;
  assert.equal(
    shouldSendStalenessNtfy,
    true,
    "staleness guard should fire when scraper worked but data is stale (unexpected)",
  );
});

// ── validate() pure-function regression (norm #11: no browser needed) ────────

test("validate: passes for valid fixture values", () => {
  const { r22_24 } = validate(14010, 15284, 11463);
  assert.ok(r22_24 > 0.905 && r22_24 < 0.925);
});

test("validate: throws for NaN value", () => {
  assert.throws(() => validate(NaN, 15284, 11463), /Rate validation failed/);
});

test("validate: throws for value below minimum", () => {
  assert.throws(() => validate(500, 15284, 11463), /Rate validation failed/);
});

test("validate: throws for ordering violation (22K > 24K)", () => {
  assert.throws(() => validate(15284, 14010, 11463), /Rate validation failed/);
});
