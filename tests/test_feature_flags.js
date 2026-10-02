// tests/test_feature_flags.js — flags.js's FEATURE_FLAGS/isFeatureOn(), no browser required.
//
// flags.js is a plain global script (no module system, no DOM dependency) — it only touches
// `location`/`URLSearchParams`, so each test loads it into a fresh vm context with a custom
// `location` instead of pulling in load_app.js's full DOM stub (which app.js/i18n.js need,
// flags.js does not).

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SRC = fs.readFileSync(path.join(ROOT, "flags.js"), "utf8");

function loadFlags({ hostname = "gaurav-gandhi-2411.github.io", search = "" } = {}, source = SRC) {
  const sandbox = { location: { hostname, search }, URLSearchParams, console };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: "flags.js" });
  // `function` declarations (isFeatureOn) become properties of the sandbox automatically;
  // `const FEATURE_FLAGS` does not (it lives in the script's declarative scope, same as
  // load_app.js's own ctx.run() convention documents) -- read it back explicitly.
  sandbox.FEATURE_FLAGS = vm.runInContext("FEATURE_FLAGS", sandbox);
  return sandbox;
}

test("FEATURE_FLAGS: every declared flag defaults to false", () => {
  const ctx = loadFlags();
  const names = Object.keys(ctx.FEATURE_FLAGS);
  assert.ok(names.length > 0, "flag registry must not be empty");
  for (const name of names) {
    assert.equal(ctx.FEATURE_FLAGS[name], false, `${name} must default off`);
  }
});

test("FEATURE_FLAGS: is frozen -- callers cannot flip a flag at runtime", () => {
  const ctx = loadFlags();
  assert.ok(Object.isFrozen(ctx.FEATURE_FLAGS));
});

test("isFeatureOn: unknown flag name is always false, even on localhost with a matching ?ff=", () => {
  const ctx = loadFlags({ hostname: "localhost", search: "?ff=nonexistent_flag" });
  assert.equal(ctx.isFeatureOn("nonexistent_flag"), false);
});

test("isFeatureOn: production origin -- ?ff= override is ignored", () => {
  const ctx = loadFlags({ hostname: "gaurav-gandhi-2411.github.io", search: "?ff=markup_meter" });
  assert.equal(ctx.isFeatureOn("markup_meter"), false);
});

test("isFeatureOn: an arbitrary non-local hostname also ignores ?ff=", () => {
  const ctx = loadFlags({ hostname: "example.test", search: "?ff=markup_meter" });
  assert.equal(ctx.isFeatureOn("markup_meter"), false);
});

test("isFeatureOn: localhost -- ?ff= override is honoured for the named flag only", () => {
  const ctx = loadFlags({ hostname: "localhost", search: "?ff=markup_meter" });
  assert.equal(ctx.isFeatureOn("markup_meter"), true);
  assert.equal(ctx.isFeatureOn("wait_or_buy"), false, "not in the ?ff= list");
});

test("isFeatureOn: 127.0.0.1 -- ?ff= override honours a comma-separated list", () => {
  const ctx = loadFlags({ hostname: "127.0.0.1", search: "?ff=markup_meter,wait_or_buy" });
  assert.equal(ctx.isFeatureOn("markup_meter"), true);
  assert.equal(ctx.isFeatureOn("wait_or_buy"), true);
  assert.equal(ctx.isFeatureOn("good_price_v2"), false, "not in the ?ff= list");
});

test("isFeatureOn: localhost with no ?ff= param at all -- still false", () => {
  const ctx = loadFlags({ hostname: "localhost", search: "" });
  assert.equal(ctx.isFeatureOn("markup_meter"), false);
});

test("isFeatureOn: a comma-list entry with surrounding whitespace still matches", () => {
  const ctx = loadFlags({ hostname: "localhost", search: "?ff=%20markup_meter%20,wait_or_buy" });
  assert.equal(ctx.isFeatureOn("markup_meter"), true);
  assert.equal(ctx.isFeatureOn("wait_or_buy"), true);
});

test("isFeatureOn: FEATURE_FLAGS[name] === true wins on every host, no ?ff= needed", () => {
  // flags.js's own FEATURE_FLAGS is frozen and can't be mutated after load -- this loads a
  // one-off source variant with a single flag flipped, the same way GG would ship a real
  // flag turn-on (edit the literal in flags.js), to prove the production path works.
  const flipped = SRC.replace("markup_meter: false,", "markup_meter: true,");
  assert.notEqual(flipped, SRC, "precondition: the replace must actually match something");
  const ctx = loadFlags({ hostname: "gaurav-gandhi-2411.github.io", search: "" }, flipped);
  assert.equal(ctx.isFeatureOn("markup_meter"), true);
  assert.equal(ctx.isFeatureOn("wait_or_buy"), false, "unrelated flags stay off");
});
