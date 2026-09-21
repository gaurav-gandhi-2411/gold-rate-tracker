// tests/test_sentry_init.js
//
// The Sentry bundle is an async <script>, so it can finish before OR after app.js runs. The old init was
// `if (typeof Sentry !== "undefined") Sentry.init(...)` at app.js load: when the bundle lost the race
// (the usual async case) init never ran and every Sentry.captureException afterwards was a silent no-op,
// even with a valid DSN. initSentry() must work in both orders and initialise exactly once.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { loadApp } from "./helpers/load_app.js";

const fakeSentry = () => {
  const calls = { init: [] };
  return { calls, sdk: { init: (opts) => calls.init.push(opts), captureException() {} } };
};

test("Sentry.init: bundle finished BEFORE app.js -> initialised at load, once", () => {
  const { calls, sdk } = fakeSentry();
  const ctx = loadApp({ globals: { Sentry: sdk } });
  try {
    assert.equal(calls.init.length, 1);
    assert.match(calls.init[0].dsn, /^https:\/\/[0-9a-f]{32}@o\d+\.ingest\.[a-z.]*sentry\.io\/\d+$/);
    assert.ok(!calls.init[0].dsn.includes("PLACEHOLDER"), "the placeholder DSN must be gone");
    assert.equal(calls.init[0].sendDefaultPii, false);
    assert.equal(calls.init[0].tracesSampleRate, 0);
  } finally {
    ctx.dispose();
  }
});

test("Sentry.init: bundle finished AFTER app.js -> onload hook initialises it (the old bug: never)", () => {
  const ctx = loadApp();
  try {
    assert.equal(ctx.run('typeof Sentry'), "undefined", "precondition: bundle has not loaded yet");
    const { calls, sdk } = fakeSentry();
    ctx.Sentry = sdk; // the async script finished
    assert.equal(typeof ctx.__onSentryReady, "function", "app.js must expose the onload hook");
    ctx.__onSentryReady(); // what the tag's onload attribute calls
    assert.equal(calls.init.length, 1);
  } finally {
    ctx.dispose();
  }
});

test("Sentry.init: idempotent -- load-time init plus onload never initialises twice", () => {
  const { calls, sdk } = fakeSentry();
  const ctx = loadApp({ globals: { Sentry: sdk } });
  try {
    ctx.__onSentryReady();
    ctx.__onSentryReady();
    assert.equal(calls.init.length, 1);
  } finally {
    ctx.dispose();
  }
});

test("Sentry.init: no bundle at all (blocked/offline) -> the hook is a safe no-op", () => {
  const ctx = loadApp();
  try {
    assert.doesNotThrow(() => ctx.__onSentryReady());
  } finally {
    ctx.dispose();
  }
});

test("Sentry.init: a non-Pages origin is labelled development, not production", () => {
  const { calls, sdk } = fakeSentry();
  const ctx = loadApp({ globals: { Sentry: sdk } }); // the vm sandbox's location has no hostname
  try {
    assert.equal(calls.init[0].environment, "development");
  } finally {
    ctx.dispose();
  }
});

test("index.html: the Sentry tag stays async and has the onload hook", () => {
  const html = fs.readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const tag = html.match(/<script[^>]*browser\.sentry-cdn\.com[^>]*>/s)?.[0];
  assert.ok(tag, "Sentry script tag not found");
  assert.match(tag, /\basync\b/);
  assert.doesNotMatch(tag, /\bdefer\b/);
  assert.match(tag, /onload="[^"]*__onSentryReady/);
});
