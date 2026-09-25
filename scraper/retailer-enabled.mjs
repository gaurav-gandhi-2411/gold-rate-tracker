// scraper/retailer-enabled.mjs
// Node reader for the retailer takedown switch, config/retailers.json (ADR 059,
// docs/RETAILER_TAKEDOWN.md). Same fail-loud rules as ml/retailers.py: a missing
// file, bad JSON, a missing/unknown retailer or a non-boolean flag is an error,
// never a silent "on" or "off".
//
// CLI:  node scraper/retailer-enabled.mjs <retailer>
//   exit 0  -> enabled   (prints "enabled")
//   exit 10 -> disabled  (prints "disabled")
//   exit 1  -> config error (message on stderr) -- workflows must treat as failure
//
// RETAILERS_CONFIG_PATH overrides the file path (tests only).

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
export const DEFAULT_CONFIG_PATH = resolve(dirname(__filename), "..", "config", "retailers.json");
export const KNOWN_RETAILERS = ["tanishq", "grt", "malabar", "kalyan"];
export const EXIT_DISABLED = 10;

export function loadRetailerFlags(path = process.env.RETAILERS_CONFIG_PATH ?? DEFAULT_CONFIG_PATH) {
  let raw;
  try {
    raw = JSON.parse(readFileSync(path, "utf8"));
  } catch (err) {
    throw new Error(`retailer switch unreadable (${path}): ${err.message}`);
  }
  const retailers = raw && typeof raw === "object" ? raw.retailers : null;
  if (!retailers || typeof retailers !== "object") {
    throw new Error(`${path}: expected a top-level 'retailers' object`);
  }
  const names = Object.keys(retailers).sort();
  if (names.join(",") !== [...KNOWN_RETAILERS].sort().join(",")) {
    throw new Error(`${path}: retailers must be exactly ${KNOWN_RETAILERS.join(", ")}; got ${names.join(", ")}`);
  }
  const flags = {};
  for (const name of names) {
    const enabled = retailers[name]?.enabled;
    if (typeof enabled !== "boolean") {
      throw new Error(`${path}: retailers.${name}.enabled must be true or false`);
    }
    flags[name] = enabled;
  }
  return flags;
}

export function isRetailerEnabled(name, path) {
  if (!KNOWN_RETAILERS.includes(name)) throw new Error(`unknown retailer '${name}'`);
  return loadRetailerFlags(path)[name];
}

if (process.argv[1] === __filename) {
  try {
    const on = isRetailerEnabled(process.argv[2]);
    console.log(on ? "enabled" : "disabled");
    process.exit(on ? 0 : EXIT_DISABLED);
  } catch (err) {
    console.error(`retailer-enabled: ${err.message}`);
    process.exit(1);
  }
}
