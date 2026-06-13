// test_dispatch_validate.mjs
// Node.js test suite for dispatch-validate.js.
// Run with: node --test test_dispatch_validate.mjs  (from scraper/)
//
// No live network calls — all test data is inline.

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "child_process";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

import { validateDispatchPayload } from "./dispatch-validate.js";

const __dir = dirname(fileURLToPath(import.meta.url));
const SCRIPT = resolve(__dir, "dispatch-validate.js");

// ── Pure function tests ───────────────────────────────────────────────────────

describe("validateDispatchPayload", () => {
  it("valid env → reading with correct values", () => {
    const r = validateDispatchPayload({
      DISPATCH_22K: "14010",
      DISPATCH_24K: "15284",
      DISPATCH_18K: "11463",
      DISPATCH_TIMESTAMP: "2026-06-11T12:00:00.000Z",
    });
    assert.ok(r.reading, "should have reading");
    assert.equal(r.reading["22k"], 14010);
    assert.equal(r.reading["24k"], 15284);
    assert.equal(r.reading["18k"], 11463);
    assert.equal(r.reading.timestamp, "2026-06-11T12:00:00.000Z");
  });

  it("valid env → reading keys match scrape output shape (incl. source)", () => {
    const r = validateDispatchPayload({
      DISPATCH_22K: "14010",
      DISPATCH_24K: "15284",
      DISPATCH_18K: "11463",
      DISPATCH_TIMESTAMP: "2026-06-11T12:00:00.000Z",
    });
    // scrape.js entries carry a `source`; the dispatch reading does too, so the
    // appended prices.json shapes match and origin is explicit.
    assert.deepEqual(Object.keys(r.reading).sort(), ["18k", "22k", "24k", "source", "timestamp"]);
  });

  it("valid env → reading is tagged source=repository_dispatch", () => {
    const r = validateDispatchPayload({
      DISPATCH_22K: "14010",
      DISPATCH_24K: "15284",
      DISPATCH_18K: "11463",
      DISPATCH_TIMESTAMP: "2026-06-11T12:00:00.000Z",
    });
    assert.equal(r.reading.source, "repository_dispatch");
  });

  it("22k out-of-range → error, no reading", () => {
    const r = validateDispatchPayload({
      DISPATCH_22K: "100",
      DISPATCH_24K: "120",
      DISPATCH_18K: "80",
      DISPATCH_TIMESTAMP: "...",
    });
    assert.ok(r.error);
    assert.ok(!r.reading);
  });

  it("non-numeric 22k → error, no reading", () => {
    const r = validateDispatchPayload({
      DISPATCH_22K: "not-a-number",
      DISPATCH_24K: "15284",
      DISPATCH_18K: "11463",
      DISPATCH_TIMESTAMP: "...",
    });
    assert.ok(r.error);
    assert.ok(!r.reading);
  });

  it("missing 22k (empty string) → error, no reading", () => {
    const r = validateDispatchPayload({
      DISPATCH_22K: "",
      DISPATCH_24K: "15284",
      DISPATCH_18K: "11463",
      DISPATCH_TIMESTAMP: "...",
    });
    assert.ok(r.error);
    assert.ok(!r.reading);
  });

  it("ordering violated (22k > 24k) → error, no reading", () => {
    const r = validateDispatchPayload({
      DISPATCH_22K: "15000",
      DISPATCH_24K: "14000",
      DISPATCH_18K: "11000",
      DISPATCH_TIMESTAMP: "...",
    });
    assert.ok(r.error);
    assert.ok(!r.reading);
  });

  it("ratio violated (in-range + ordered but wrong purity ratio) → error, no reading", () => {
    // 13000 < 14000 < 24000 (ordering OK), all within [2000, 25000] (range OK),
    // but 22k/24k = 0.583 is far outside [0.905, 0.925]. This is the garbage-but-
    // plausible payload that range+ordering checks alone would let through.
    const r = validateDispatchPayload({
      DISPATCH_22K: "14000",
      DISPATCH_24K: "24000",
      DISPATCH_18K: "13000",
      DISPATCH_TIMESTAMP: "2026-06-13T12:00:00.000Z",
    });
    assert.ok(r.error, "ratio violation must be rejected");
    assert.match(r.error, /ratio/);
    assert.ok(!r.reading);
  });

  it("missing DISPATCH_TIMESTAMP → uses fallback timestamp", () => {
    const r = validateDispatchPayload({
      DISPATCH_22K: "14010",
      DISPATCH_24K: "15284",
      DISPATCH_18K: "11463",
    });
    assert.ok(r.reading, "should have reading");
    assert.ok(r.reading.timestamp, "should have a timestamp");
    assert.ok(r.reading.timestamp.endsWith("Z"), "timestamp should be UTC ISO-8601");
  });
});

// ── CLI integration tests ─────────────────────────────────────────────────────

describe("CLI", () => {
  it("valid payload → stdout has JSON reading, exit 0", () => {
    const result = spawnSync("node", [SCRIPT], {
      env: {
        ...process.env,
        DISPATCH_22K: "14010",
        DISPATCH_24K: "15284",
        DISPATCH_18K: "11463",
        DISPATCH_TIMESTAMP: "2026-06-11T12:00:00.000Z",
      },
    });
    assert.equal(result.status, 0);
    const output = JSON.parse(result.stdout.toString());
    assert.equal(output["22k"], 14010);
    assert.equal(output["24k"], 15284);
    assert.equal(output["18k"], 11463);
    assert.equal(output.timestamp, "2026-06-11T12:00:00.000Z");
    assert.equal(output.source, "repository_dispatch");
  });

  it("malformed payload (out-of-range 22k) → stdout EMPTY, exit 0 — no prices.json write", () => {
    // Verifies the core safety property: malformed dispatch payload never reaches
    // update-and-notify.js because the YAML's `[ -s /tmp/dispatch_reading.json ]`
    // check will fail on an empty file.
    const result = spawnSync("node", [SCRIPT], {
      env: {
        ...process.env,
        DISPATCH_22K: "100",
        DISPATCH_24K: "120",
        DISPATCH_18K: "80",
      },
    });
    assert.equal(result.status, 0, "exit code must be 0 (miss, not hard failure)");
    assert.equal(
      result.stdout.toString(),
      "",
      "stdout must be empty — empty file → [ -s ] fails → update-and-notify not called",
    );
  });
});
