// dispatch-validate.js
// Reads DISPATCH_22K/24K/18K/DISPATCH_TIMESTAMP from ENV, validates using the
// same constants as scrape.js and worker/index.js, writes JSON to stdout if
// valid, writes nothing to stdout if invalid.
//
// SYNC CONTRACT — the four numeric constants below must stay byte-for-byte
// identical to scraper/scrape.js and worker/index.js.  Update all three files
// together whenever thresholds change.
//
// CLI contract (critical for check-price.yml):
//   exit 0 on BOTH valid and invalid payloads.
//   stdout has JSON on valid; stdout is empty on invalid.
//   The YAML step uses `[ -s /tmp/dispatch_reading.json ]` to detect rejection;
//   a non-zero exit would confuse `set -e` in the else-branch logic.
//
// Exports: validateDispatchPayload

import { fileURLToPath } from "url";

// ── Validation thresholds (per gram, INR) ────────────────────────────────────
// SYNC CONTRACT — these four constants must stay byte-for-byte identical to
// scraper/scrape.js and worker/index.js.
const RANGE_MIN = 2000;
const RANGE_MAX = 25000;
const RATIO_22_24_MIN = 0.905; // theoretical 91.67%, allow ±1%
const RATIO_22_24_MAX = 0.925;
const RATIO_18_24_MIN = 0.73; // theoretical 75%, allow ±2%
const RATIO_18_24_MAX = 0.77;

/**
 * Validate a dispatch payload from environment variables.
 *
 * Reads DISPATCH_22K, DISPATCH_24K, DISPATCH_18K, DISPATCH_TIMESTAMP from
 * the provided env object (typically process.env).  Applies the same range,
 * ordering, and ratio checks as scrape.js validate().
 *
 * Returns { reading: { timestamp, "22k", "24k", "18k", source } } on success.
 * Returns { error: "descriptive message" } on any failure.
 * Never throws.
 *
 * The reading carries source: "repository_dispatch" so the appended prices.json
 * entry self-documents its origin (vs scrape.js entries, whose source is the
 * Tanishq URL). Consumers such as the feature store can distinguish the path.
 *
 * @param {Record<string, string | undefined>} env
 * @returns {{ reading: { timestamp: string, "22k": number, "24k": number, "18k": number, source: string } } | { error: string }}
 */
export function validateDispatchPayload(env) {
  const r22 = parseInt(env.DISPATCH_22K, 10);
  const r24 = parseInt(env.DISPATCH_24K, 10);
  const r18 = parseInt(env.DISPATCH_18K, 10);
  const ts = env.DISPATCH_TIMESTAMP || new Date().toISOString();

  for (const [label, val] of [["22k", r22], ["24k", r24], ["18k", r18]]) {
    if (!Number.isFinite(val) || val < RANGE_MIN || val > RANGE_MAX) {
      return { error: `${label}=${val} outside [${RANGE_MIN}, ${RANGE_MAX}]` };
    }
  }
  if (!(r18 < r22 && r22 < r24)) {
    return { error: `ordering violated: ${r18} < ${r22} < ${r24}` };
  }
  const r22_24 = r22 / r24;
  const r18_24 = r18 / r24;
  if (r22_24 < RATIO_22_24_MIN || r22_24 > RATIO_22_24_MAX) {
    return { error: `22k/24k ratio ${r22_24.toFixed(4)} outside [${RATIO_22_24_MIN}, ${RATIO_22_24_MAX}]` };
  }
  if (r18_24 < RATIO_18_24_MIN || r18_24 > RATIO_18_24_MAX) {
    return { error: `18k/24k ratio ${r18_24.toFixed(4)} outside [${RATIO_18_24_MIN}, ${RATIO_18_24_MAX}]` };
  }
  return {
    reading: { timestamp: ts, "22k": r22, "24k": r24, "18k": r18, source: "repository_dispatch" },
  };
}

// ── Entry point ───────────────────────────────────────────────────────────────

const __filename = fileURLToPath(import.meta.url);
if (process.argv[1] === __filename) {
  const result = validateDispatchPayload(process.env);
  if (result.reading) {
    process.stderr.write(
      `[dispatch] 22k=${result.reading["22k"]} validated OK — piping to update-and-notify\n`,
    );
    process.stdout.write(JSON.stringify(result.reading));
    process.exit(0);
  } else {
    process.stderr.write(`[dispatch] REJECTED: ${result.error}\n`);
    // Write NOTHING to stdout — `[ -s /tmp/dispatch_reading.json ]` must fail.
    process.exit(0);
  }
}
