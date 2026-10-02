// scraper/test_retailer_enabled.mjs -- the Node reader of config/retailers.json
// (ADR 059 takedown switch). Dependency-free (node builtins only).
// Run: node --test scraper/test_retailer_enabled.mjs

import assert from "assert/strict";
import { test } from "node:test";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import {
  DEFAULT_CONFIG_PATH,
  EXIT_DISABLED,
  KNOWN_RETAILERS,
  isRetailerEnabled,
  loadRetailerFlags,
} from "./retailer-enabled.mjs";

const CLI = fileURLToPath(new URL("./retailer-enabled.mjs", import.meta.url));

function writeConfig(obj) {
  const dir = mkdtempSync(join(tmpdir(), "retailers-"));
  const p = join(dir, "retailers.json");
  writeFileSync(p, typeof obj === "string" ? obj : JSON.stringify(obj));
  return p;
}

const allOn = () => ({ retailers: Object.fromEntries(KNOWN_RETAILERS.map((r) => [r, { enabled: true }])) });

test("committed config keeps every retailer enabled (this PR changes no live behaviour)", () => {
  const flags = loadRetailerFlags(DEFAULT_CONFIG_PATH);
  for (const r of KNOWN_RETAILERS) assert.equal(flags[r], true, r);
});

test("disabled retailer reads false; unknown name throws", () => {
  const cfg = allOn();
  cfg.retailers.tanishq.enabled = false;
  const p = writeConfig(cfg);
  assert.equal(isRetailerEnabled("tanishq", p), false);
  assert.equal(isRetailerEnabled("grt", p), true);
  assert.throws(() => isRetailerEnabled("joyalukkas", p));
});

test("malformed configs fail loudly, never default on or off", () => {
  const missing = allOn();
  delete missing.retailers.kalyan;
  const nonBool = allOn();
  nonBool.retailers.grt.enabled = "false";
  for (const bad of ["{not json", { nope: 1 }, missing, nonBool]) {
    assert.throws(() => loadRetailerFlags(writeConfig(bad)));
  }
  assert.throws(() => loadRetailerFlags(join(tmpdir(), "definitely-absent-retailers.json")));
});

test("CLI exit codes: 0 enabled, 10 disabled, 1 config error", () => {
  const cfg = allOn();
  cfg.retailers.tanishq.enabled = false;
  const run = (path, name) =>
    spawnSync(process.execPath, [CLI, name], { env: { ...process.env, RETAILERS_CONFIG_PATH: path } }).status;
  const p = writeConfig(cfg);
  assert.equal(run(p, "grt"), 0);
  assert.equal(run(p, "tanishq"), EXIT_DISABLED);
  assert.equal(run(writeConfig("{bad"), "tanishq"), 1);
});
