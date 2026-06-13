// test_worker.mjs
// Node.js test suite for the Cloudflare Worker.
// Run with: node --test test_worker.mjs  (from the worker/ directory)
//
// All fixtures are inline — no disk reads, no live network calls.

import assert from "assert/strict";
import { test } from "node:test";

const { parseGoldRates, isCFChallengeHtml, validate, runScheduled } = await import("./index.js");

// ── HTML fixtures ─────────────────────────────────────────────────────────────

const VALID_HTML = `<!DOCTYPE html>
<html><head><title>Gold Rate Today</title></head>
<body>
<span class="goldpurity-rate"
      data-goldrate22kt="14010"
      data-goldrate24kt="15284"
      data-goldrate18kt="11463">14010</span>
</body></html>`;

const CF_HTML = `<html><head><title>Just a moment...</title></head><body>cf-challenge</body></html>`;

const NO_SPAN_HTML = `<!DOCTYPE html><html><head><title>Gold Rate</title></head><body><p>No rates.</p></body></html>`;

const OUT_OF_RANGE_HTML = `<!DOCTYPE html>
<html><head><title>Gold Rate Today</title></head>
<body>
<span class="goldpurity-rate"
      data-goldrate22kt="100"
      data-goldrate24kt="120"
      data-goldrate18kt="80">100</span>
</body></html>`;

// ── Mock fetch helper ─────────────────────────────────────────────────────────

function makeMockFetch({
  tanishqStatus = 200,
  tanishqHtml = VALID_HTML,
  dispatchStatus = 204,
  dispatchBody = "",
} = {}) {
  const dispatches = [];
  const requests = []; // full {url, opts} for the GitHub dispatch POST(s)
  const fetchFn = async (url, opts) => {
    if (url.includes("tanishq")) {
      return {
        ok: tanishqStatus >= 200 && tanishqStatus < 300,
        status: tanishqStatus,
        text: async () => tanishqHtml,
      };
    }
    if (url.includes("github.com")) {
      requests.push({ url, opts });
      dispatches.push(JSON.parse(opts.body));
      return {
        ok: dispatchStatus >= 200 && dispatchStatus < 300,
        status: dispatchStatus,
        text: async () => dispatchBody,
      };
    }
    throw new Error(`Unexpected URL: ${url}`);
  };
  return { fetchFn, dispatches, requests };
}

const MOCK_ENV = { GITHUB_TOKEN: "ghp_fake", GITHUB_OWNER: "test-owner", GITHUB_REPO: "test-repo" };

// ── Pure function tests ───────────────────────────────────────────────────────

test("parseGoldRates: extracts 22k/24k/18k from valid HTML", () => {
  assert.deepEqual(parseGoldRates(VALID_HTML), { rate22: 14010, rate24: 15284, rate18: 11463 });
});

test("parseGoldRates: returns null when span absent", () => {
  assert.equal(parseGoldRates(NO_SPAN_HTML), null);
});

test("isCFChallengeHtml: detects Just a moment title", () => {
  assert.equal(isCFChallengeHtml(CF_HTML), true);
});

test("isCFChallengeHtml: returns false for normal page", () => {
  assert.equal(isCFChallengeHtml(VALID_HTML), false);
});

test("validate: throws on out-of-range 22k", () => {
  assert.throws(() => validate(100, 120, 80), /validation failed/i);
});

// ── runScheduled integration tests ───────────────────────────────────────────

test("runScheduled: valid HTML → dispatches correct payload", async () => {
  const { fetchFn, dispatches } = makeMockFetch({ tanishqHtml: VALID_HTML });
  await runScheduled(MOCK_ENV, fetchFn);
  assert.equal(dispatches.length, 1);
  assert.equal(dispatches[0].event_type, "tanishq-price");
  assert.equal(dispatches[0].client_payload["22k"], 14010);
  assert.equal(dispatches[0].client_payload["24k"], 15284);
  assert.equal(dispatches[0].client_payload["18k"], 11463);
  assert.ok(typeof dispatches[0].client_payload.timestamp === "string");
  assert.ok(dispatches[0].client_payload.timestamp.endsWith("Z"));
});

test("runScheduled: HTTP 403 → no dispatch", async () => {
  const { fetchFn, dispatches } = makeMockFetch({ tanishqStatus: 403, tanishqHtml: "" });
  await runScheduled(MOCK_ENV, fetchFn);
  assert.equal(dispatches.length, 0);
});

test("runScheduled: CF challenge body (200) → no dispatch", async () => {
  const { fetchFn, dispatches } = makeMockFetch({ tanishqHtml: CF_HTML });
  await runScheduled(MOCK_ENV, fetchFn);
  assert.equal(dispatches.length, 0);
});

test("runScheduled: missing goldpurity-rate span → no dispatch", async () => {
  const { fetchFn, dispatches } = makeMockFetch({ tanishqHtml: NO_SPAN_HTML });
  await runScheduled(MOCK_ENV, fetchFn);
  assert.equal(dispatches.length, 0);
});

test("runScheduled: out-of-range values → no dispatch", async () => {
  const { fetchFn, dispatches } = makeMockFetch({ tanishqHtml: OUT_OF_RANGE_HTML });
  await runScheduled(MOCK_ENV, fetchFn);
  assert.equal(dispatches.length, 0);
});

test("runScheduled: missing GITHUB_TOKEN → no dispatch", async () => {
  const { fetchFn, dispatches } = makeMockFetch();
  await runScheduled({ GITHUB_OWNER: "x", GITHUB_REPO: "y" }, fetchFn);
  assert.equal(dispatches.length, 0);
});

// ── Dispatch request construction (GitHub REST API requirements) ───────────────
// These guard the exact bug that caused the production 403: GitHub rejects any
// request without a User-Agent, and Cloudflare Workers' fetch() sends none by
// default. The earlier mock only inspected the body, so a missing/malformed
// header set went undetected.

test("runScheduled: dispatch POST uses correct URL and method", async () => {
  const { fetchFn, requests } = makeMockFetch({ tanishqHtml: VALID_HTML });
  await runScheduled(MOCK_ENV, fetchFn);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "https://api.github.com/repos/test-owner/test-repo/dispatches");
  assert.equal(requests[0].opts.method, "POST");
});

test("runScheduled: dispatch POST sends all GitHub-required headers incl. User-Agent", async () => {
  const { fetchFn, requests } = makeMockFetch({ tanishqHtml: VALID_HTML });
  await runScheduled(MOCK_ENV, fetchFn);
  assert.equal(requests.length, 1);
  const h = requests[0].opts.headers;
  // The load-bearing assertion: GitHub returns 403 without a User-Agent.
  assert.ok(
    typeof h["User-Agent"] === "string" && h["User-Agent"].length > 0,
    "dispatch POST must send a non-empty User-Agent header",
  );
  assert.equal(h.Accept, "application/vnd.github+json");
  assert.equal(h["X-GitHub-Api-Version"], "2022-11-28");
  assert.match(h.Authorization, /^Bearer .+/);
  assert.equal(h["Content-Type"], "application/json");
});

test("runScheduled: non-2xx dispatch logs status AND response body", async () => {
  const { fetchFn } = makeMockFetch({
    dispatchStatus: 403,
    dispatchBody: '{"message":"Request forbidden: missing User-Agent"}',
  });
  const errors = [];
  const orig = console.error;
  console.error = (msg) => errors.push(String(msg));
  try {
    await runScheduled(MOCK_ENV, fetchFn);
  } finally {
    console.error = orig;
  }
  assert.ok(
    errors.some((e) => e.includes("403") && e.includes("missing User-Agent")),
    `expected a log line with the 403 status and the response body, got: ${JSON.stringify(errors)}`,
  );
});

// WS1 failure mode #2: GitHub dispatch POST fails (403/401/404/422). Every
// status must be logged WITH the response body so `wrangler tail` shows the
// exact reason, and the Worker must NOT throw (CF marks a throwing cron run as
// failed). The Worker writes nothing locally, so "no bad write" is inherent —
// a failed dispatch simply means no repository_dispatch event, and the in-CI
// scrape path remains the source of truth for that cycle.
for (const { status, body } of [
  { status: 401, body: '{"message":"Bad credentials"}' },
  { status: 403, body: '{"message":"Resource not accessible by personal access token"}' },
  { status: 404, body: '{"message":"Not Found"}' },
  { status: 422, body: '{"message":"Validation Failed"}' },
]) {
  test(`runScheduled: dispatch HTTP ${status} → logs status + body, no throw`, async () => {
    const { fetchFn } = makeMockFetch({ dispatchStatus: status, dispatchBody: body });
    const errors = [];
    const orig = console.error;
    console.error = (msg) => errors.push(String(msg));
    let threw = false;
    try {
      await runScheduled(MOCK_ENV, fetchFn); // must resolve, never reject
    } catch {
      threw = true;
    } finally {
      console.error = orig;
    }
    assert.equal(threw, false, "runScheduled must not throw on a failed dispatch");
    const msg = JSON.parse(body).message;
    assert.ok(
      errors.some((e) => e.includes(String(status)) && e.includes(msg)),
      `expected a log line with status ${status} and body ${JSON.stringify(msg)}, got: ${JSON.stringify(errors)}`,
    );
  });
}

test("runScheduled: Tanishq fetch throws (network error) → logged, no dispatch, no throw", async () => {
  const dispatches = [];
  const errors = [];
  const fetchFn = async (url) => {
    if (url.includes("tanishq")) throw new Error("ECONNRESET");
    dispatches.push(url);
    return { ok: true, status: 204, text: async () => "" };
  };
  const orig = console.error;
  console.error = (msg) => errors.push(String(msg));
  let threw = false;
  try {
    await runScheduled(MOCK_ENV, fetchFn);
  } catch {
    threw = true;
  } finally {
    console.error = orig;
  }
  assert.equal(threw, false);
  assert.equal(dispatches.length, 0, "no dispatch when the Tanishq fetch itself throws");
  assert.ok(errors.some((e) => e.includes("ECONNRESET")), "the fetch error must be logged");
});

test("runScheduled: dispatch fetch throws (network error) → logged, no throw", async () => {
  const errors = [];
  const fetchFn = async (url, opts) => {
    if (url.includes("tanishq")) {
      return { ok: true, status: 200, text: async () => VALID_HTML };
    }
    throw new Error("dispatch socket hang up");
  };
  const orig = console.error;
  console.error = (msg) => errors.push(String(msg));
  let threw = false;
  try {
    await runScheduled(MOCK_ENV, fetchFn);
  } catch {
    threw = true;
  } finally {
    console.error = orig;
  }
  assert.equal(threw, false);
  assert.ok(
    errors.some((e) => e.includes("dispatch fetch threw") && e.includes("socket hang up")),
    `expected a 'dispatch fetch threw' log, got: ${JSON.stringify(errors)}`,
  );
});
