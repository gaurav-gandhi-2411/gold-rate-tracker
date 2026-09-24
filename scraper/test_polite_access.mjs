// scraper/test_polite_access.mjs
// Polite-access tests (ADR 059, decision G1a): backoff with jitter, Retry-After
// parsing, 429 ends the cycle without a Playwright escalation, one consistent UA,
// and the requests-probe gating.
//
// Run: node --test test_polite_access.mjs  (from scraper/ directory)
// No live network: a local mock HTTP server only. The Playwright-429 test needs
// the Playwright browser installed (same requirement as test_hybrid_scrape.mjs).

import assert from "assert/strict";
import { test } from "node:test";
import { createServer } from "node:http";

process.env.SCRAPER_SELECTOR_TIMEOUT_MS = "300";
process.env.SCRAPER_RETRY_DELAYS_MS = "50,50";
process.env.SCRAPER_NAV_TIMEOUT_MS = "10000";

const {
  backoffDelayMs,
  parseRetryAfterMs,
  shouldTryRequestsPath,
  hybridScrape,
  scrapeWithRetry,
  USER_AGENT,
} = await import("./scrape.js");

function startMockServer(status, headers = {}, body = "<html><body>slow down</body></html>") {
  const seen = [];
  const server = createServer((req, res) => {
    if (req.url !== "/") {
      res.writeHead(404);
      res.end();
      return;
    }
    seen.push(req.headers["user-agent"]);
    res.writeHead(status, { "Content-Type": "text/html", ...headers });
    res.end(body);
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      resolve({
        url: `http://127.0.0.1:${server.address().port}`,
        seen,
        close: () => new Promise((r) => server.close(r)),
      });
    });
  });
}

test("backoffDelayMs: exponential, jittered within [d/2, d], capped", () => {
  assert.equal(backoffDelayMs(1, { rand: () => 0 }), 2500);
  assert.equal(backoffDelayMs(1, { rand: () => 1 }), 5000);
  assert.equal(backoffDelayMs(2, { rand: () => 0 }), 5000);
  assert.equal(backoffDelayMs(2, { rand: () => 1 }), 10000);
  assert.equal(backoffDelayMs(30, { rand: () => 1 }), 60000);
});

test("parseRetryAfterMs: seconds, HTTP-date, and unparseable -> null (never 0)", () => {
  assert.equal(parseRetryAfterMs("30"), 30000);
  const now = Date.parse("2026-09-25T12:00:00Z");
  assert.equal(parseRetryAfterMs("Fri, 25 Sep 2026 12:00:10 GMT", now), 10000);
  assert.equal(parseRetryAfterMs(null), null);
  assert.equal(parseRetryAfterMs(""), null);
  assert.equal(parseRetryAfterMs("later"), null);
});

test("shouldTryRequestsPath: probes once per UTC day while the probe keeps losing", () => {
  const pw = (ts) => ({ timestamp: ts, outcome: "success", fetch_method: "playwright" });
  const now = new Date("2026-09-25T09:00:00Z");
  assert.equal(shouldTryRequestsPath([], now), true);
  assert.equal(shouldTryRequestsPath([pw("2026-09-24T21:00:00Z")], now), true); // first run today
  assert.equal(shouldTryRequestsPath([pw("2026-09-25T03:00:00Z")], now), false); // already ran today
  const withReq = [
    pw("2026-09-25T00:00:00Z"),
    { timestamp: "2026-09-25T03:00:00Z", outcome: "success", fetch_method: "requests" },
  ];
  assert.equal(shouldTryRequestsPath(withReq, now), true); // probe is working again
});

test("requests-path 429 ends the run: no Playwright escalation, single request", async () => {
  const srv = await startMockServer(429, { "Retry-After": "120" });
  try {
    await assert.rejects(hybridScrape(srv.url), (err) => err.rateLimited === true);
    assert.equal(srv.seen.length, 1);
    assert.equal(srv.seen[0], USER_AGENT);
  } finally {
    await srv.close();
  }
});

test("requests-path 503 + Retry-After is honoured the same way", async () => {
  const srv = await startMockServer(503, { "Retry-After": "600" });
  try {
    await assert.rejects(hybridScrape(srv.url), (err) => err.rateLimited === true);
    assert.equal(srv.seen.length, 1);
  } finally {
    await srv.close();
  }
});

test("Playwright path: HTTP 429 page load is not retried, same UA as requests path", async () => {
  const srv = await startMockServer(429);
  try {
    await assert.rejects(scrapeWithRetry(srv.url), (err) => err.rateLimited === true);
    assert.equal(srv.seen.length, 1);
    assert.equal(srv.seen[0], USER_AGENT);
  } finally {
    await srv.close();
  }
});
