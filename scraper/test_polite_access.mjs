// scraper/test_polite_access.mjs
// Polite-access tests (ADR 059, decision G1a): backoff with jitter, Retry-After
// parsing, 429 ends the cycle without a Playwright escalation, and the
// requests-probe gating.
//
// Also pins the BROWSER SETTINGS (GG decision E3, 2026-09-25): the per-retry
// UA/viewport rotation and Playwright launch args must stay byte-identical to
// master -- #2048 briefly collapsed the rotation to one UA, which E3 reverted.
// This file asserts both the static config and the live retry behaviour.
//
// Run: node --test test_polite_access.mjs  (from scraper/ directory)
// No live network: a local mock HTTP server only. The Playwright tests need
// the Playwright browser installed (same requirement as test_hybrid_scrape.mjs).

import assert from "assert/strict";
import { test } from "node:test";
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { resolve as resolvePath, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dir = dirname(fileURLToPath(import.meta.url));

process.env.SCRAPER_SELECTOR_TIMEOUT_MS = "300";
process.env.SCRAPER_RETRY_DELAYS_MS = "50,50";
process.env.SCRAPER_NAV_TIMEOUT_MS = "10000";

const {
  backoffDelayMs,
  parseRetryAfterMs,
  shouldTryRequestsPath,
  hybridScrape,
  scrapeWithRetry,
  VIEWPORTS,
  USER_AGENTS,
} = await import("./scrape.js");

const CF_CHALLENGE_HTML = readFileSync(
  resolvePath(__dir, "..", "tests", "fixtures", "cf_challenge.html"),
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

/**
 * Serves `responses` in order (last entry repeats), capturing the
 * User-Agent header seen on each request -- used to prove per-retry rotation.
 * @param {{ status: number, html: string }[]} responses
 */
function startSequencedServer(responses) {
  const seen = [];
  const server = createServer((req, res) => {
    if (req.url !== "/") {
      res.writeHead(404);
      res.end();
      return;
    }
    seen.push(req.headers["user-agent"]);
    const r = responses[Math.min(seen.length - 1, responses.length - 1)];
    res.writeHead(r.status, { "Content-Type": "text/html; charset=utf-8" });
    res.end(r.html);
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
    assert.equal(srv.seen[0], USER_AGENTS[0]);
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

test("Playwright path: HTTP 429 page load is not retried, first-attempt UA matches requests path", async () => {
  const srv = await startMockServer(429);
  try {
    await assert.rejects(scrapeWithRetry(srv.url), (err) => err.rateLimited === true);
    assert.equal(srv.seen.length, 1);
    assert.equal(srv.seen[0], USER_AGENTS[0]);
  } finally {
    await srv.close();
  }
});

// ── E3: browser settings must stay identical to master ───────────────────────

test("E3: UA rotation list is unchanged from master (3 entries, pinned)", () => {
  assert.deepEqual(USER_AGENTS, [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
  ]);
});

test("E3: viewport rotation list is unchanged from master (3 entries, pinned)", () => {
  assert.deepEqual(VIEWPORTS, [
    { width: 1280, height: 800 },
    { width: 1366, height: 768 },
    { width: 1440, height: 900 },
  ]);
});

test("E3: Playwright launch args (incl. anti-detection flag) are unchanged from master", () => {
  const src = readFileSync(resolvePath(__dir, "scrape.js"), "utf8");
  assert.match(
    src,
    /args:\s*\[\s*"--no-sandbox",\s*"--disable-blink-features=AutomationControlled"\s*\]/,
    "chromium.launch() args must stay exactly --no-sandbox + --disable-blink-features=AutomationControlled",
  );
  // No identifying token (e.g. a contact email/URL) was added to any Tanishq UA.
  for (const ua of USER_AGENTS) {
    assert.ok(!ua.includes("@") && !ua.includes("gold-rate-tracker"), `UA must stay an ordinary browser string: ${ua}`);
  }
});

test("E3: per-retry rotation is live end-to-end -- attempt 1 and attempt 2 send different, listed UAs", async () => {
  const srv = await startSequencedServer([
    { status: 200, html: CF_CHALLENGE_HTML }, // attempt 1: CF challenge -> retryable
    { status: 200, html: GOLD_RATE_HTML }, // attempt 2: succeeds
  ]);
  try {
    const result = await scrapeWithRetry(srv.url);
    assert.equal(result["22k"], 14010);
    assert.equal(srv.seen.length, 2);
    assert.equal(srv.seen[0], USER_AGENTS[0]);
    assert.equal(srv.seen[1], USER_AGENTS[1]);
    assert.notEqual(srv.seen[0], srv.seen[1]);
  } finally {
    await srv.close();
  }
});
