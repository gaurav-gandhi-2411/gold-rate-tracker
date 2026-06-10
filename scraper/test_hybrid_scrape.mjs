// scraper/test_hybrid_scrape.mjs
// Hybrid-scrape tests (Φ24): requests-first path + Playwright fallback.
//
// Run: node --test test_hybrid_scrape.mjs  (from scraper/ directory)
//
// Norm #11: no live network.  All tests use a local HTTP mock server or
// operate on pure HTML strings.  Playwright is used only for the fallback
// integration tests, which require browsers to be pre-installed.

import assert from "assert/strict";
import { test } from "node:test";
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dir = dirname(fileURLToPath(import.meta.url));

// ── Set ENV overrides BEFORE dynamic import so module constants are short ─────
process.env.SCRAPER_SELECTOR_TIMEOUT_MS = "300";
process.env.SCRAPER_RETRY_DELAYS_MS = "50,50";
process.env.SCRAPER_NAV_TIMEOUT_MS = "10000";

const { hybridScrape, fetchWithRequests, isCFChallengeHtml, parseGoldRates } =
  await import("./scrape.js");

// ── HTML fixtures ─────────────────────────────────────────────────────────────

const CF_CHALLENGE_HTML = readFileSync(
  resolve(__dir, "..", "tests", "fixtures", "cf_challenge.html"),
  "utf8",
);

const GOLD_RATE_HTML = `<!DOCTYPE html>
<html><head><title>Gold Rate Today - Tanishq</title></head>
<body>
<span class="goldpurity-rate"
      data-goldrate22kt="14010"
      data-goldrate24kt="15284"
      data-goldrate18kt="11463">14010</span>
</body>
</html>`;

const NO_SPAN_HTML = `<!DOCTYPE html>
<html><head><title>Gold Rate Today - Tanishq</title></head>
<body><p>Prices temporarily unavailable.</p></body>
</html>`;

const MALFORMED_HTML = `<html><body>XhF%%garbled content%%no useful structure%%</body></html>`;

// ── Mock HTTP server (supports per-response status codes) ─────────────────────

/**
 * Starts a local HTTP server that serves `responses` in order.
 * The last entry is repeated for any additional requests.
 * Only counts requests to the root path "/"; other paths get 404 without
 * incrementing the counter.
 *
 * @param {{ status: number, html: string }[]} responses
 */
function startMockServer(responses) {
  let reqCount = 0;
  const server = createServer((req, res) => {
    if (req.url !== "/") {
      res.writeHead(404);
      res.end();
      return;
    }
    const r = responses[Math.min(reqCount, responses.length - 1)];
    reqCount++;
    res.writeHead(r.status, { "Content-Type": "text/html; charset=utf-8" });
    res.end(r.html);
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      resolve({
        url: `http://127.0.0.1:${port}`,
        getCount: () => reqCount,
        close: () => new Promise((r) => server.close(r)),
      });
    });
  });
}

// ── isCFChallengeHtml() pure-function tests (no network) ─────────────────────

test("isCFChallengeHtml: detects 'Just a moment...' title", () => {
  const html = `<html><head><title>Just a moment...</title></head><body></body></html>`;
  assert.equal(isCFChallengeHtml(html), true);
});

test("isCFChallengeHtml: returns false for normal Tanishq page", () => {
  assert.equal(isCFChallengeHtml(GOLD_RATE_HTML), false);
});

// ── parseGoldRates() pure-function tests (no network) ────────────────────────

test("parseGoldRates: extracts all three rates from valid HTML", () => {
  const rates = parseGoldRates(GOLD_RATE_HTML);
  assert.deepEqual(rates, { rate22: 14010, rate24: 15284, rate18: 11463 });
});

test("parseGoldRates: returns null when goldpurity-rate span absent", () => {
  assert.equal(parseGoldRates(NO_SPAN_HTML), null);
});

// ── fetchWithRequests() mock-HTTP test (no Playwright) ───────────────────────

test("Φ24 requests path: 200 with valid prices → correct rates", async () => {
  const mock = await startMockServer([{ status: 200, html: GOLD_RATE_HTML }]);
  try {
    const result = await fetchWithRequests(mock.url);
    assert.equal(result["22k"], 14010);
    assert.equal(result["24k"], 15284);
    assert.equal(result["18k"], 11463);
    assert.ok(result.timestamp.endsWith("Z"), "timestamp should be UTC ISO-8601");
    assert.deepEqual(Object.keys(result).sort(), [
      "18k", "22k", "24k", "source", "timestamp",
    ]);
  } finally {
    await mock.close();
  }
});

// ── hybridScrape() fallback integration tests (uses Playwright) ───────────────
// Each test: requests path consumes response[0] (failure trigger), then
// Playwright navigates and gets response[1] (GOLD_RATE_HTML → success).

test("Φ24 hybrid: requests 403 → falls back to Playwright → succeeds", async () => {
  const mock = await startMockServer([
    { status: 403, html: "" },
    { status: 200, html: GOLD_RATE_HTML },
  ]);
  try {
    const result = await hybridScrape(mock.url);
    assert.equal(result["22k"], 14010);
    assert.equal(mock.getCount(), 2, "one request each: requests-path + Playwright");
  } finally {
    await mock.close();
  }
});

test("Φ24 hybrid: requests 200 + CF challenge body → falls back to Playwright → succeeds", async () => {
  // 200 status but challenge body — the trigger the user explicitly added.
  const mock = await startMockServer([
    { status: 200, html: CF_CHALLENGE_HTML },
    { status: 200, html: GOLD_RATE_HTML },
  ]);
  try {
    const result = await hybridScrape(mock.url);
    assert.equal(result["22k"], 14010);
    assert.equal(mock.getCount(), 2);
  } finally {
    await mock.close();
  }
});

test("Φ24 hybrid: requests 200 + no goldpurity-rate span → falls back to Playwright → succeeds", async () => {
  const mock = await startMockServer([
    { status: 200, html: NO_SPAN_HTML },
    { status: 200, html: GOLD_RATE_HTML },
  ]);
  try {
    const result = await hybridScrape(mock.url);
    assert.equal(result["22k"], 14010);
    assert.equal(mock.getCount(), 2);
  } finally {
    await mock.close();
  }
});

test("Φ24 hybrid: requests 200 + malformed HTML → falls back to Playwright → succeeds", async () => {
  const mock = await startMockServer([
    { status: 200, html: MALFORMED_HTML },
    { status: 200, html: GOLD_RATE_HTML },
  ]);
  try {
    const result = await hybridScrape(mock.url);
    assert.equal(result["22k"], 14010);
    assert.equal(mock.getCount(), 2);
  } finally {
    await mock.close();
  }
});
