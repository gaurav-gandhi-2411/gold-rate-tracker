// tests/smoke_render_live.js
//
// Post-deploy render smoke test. Loads the LIVE deployed site in headless
// Chromium and asserts the page actually RENDERED real data — not just that
// it returned HTTP 200. curl/fetch checks cannot catch a JS render failure
// (SRI block, a throwing script, a failed fetch, or a stale service-worker
// cache serving an incompatible shell to a returning visitor) because none
// of those execute the page's JS. This is exactly the class of bug that
// shipped invisibly for 11 merged PRs until service-worker.js's VERSION
// contract was restored in PR #252 — see that PR/service-worker.js's
// CACHE INVALIDATION CONTRACT comment for the mechanism.
//
// Three checks, each independently reported:
//   1. Fresh load renders real content (no prior service-worker install).
//   2. A same-session reload (service-worker now installed + controlling
//      the page, cache-first shell serving in effect) still renders real
//      content. This proves the *current* deploy's SW doesn't break its own
//      reload path — it cannot retroactively prove an OLD cached shell from
//      a past deploy would render correctly (that shell no longer exists to
//      test), which is why check 3 exists as a direct proxy for that class.
//   3. The live-served service-worker.js VERSION matches the one in this
//      checkout — a direct, cheap proxy for "did the deploy actually
//      propagate," which is the same drift class (declared vs. served) that
//      made the SRI hypothesis look plausible during the original incident
//      (a transient CDN edge-cache lag, not a code defect).
//
// Run: node tests/smoke_render_live.js   (from repo root, after
//      `npm ci && npx playwright install --with-deps chromium` in scraper/)
// Exit code 0 = all checks passed. Non-zero = at least one failed; failure
// reasons are printed to stdout as JSON.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import pkg from "../scraper/node_modules/playwright/index.js";

const { chromium } = pkg;

// Override lets this be pointed at a local server for self-testing the
// smoke test itself (e.g. against a deliberately broken shell) without
// touching the default production target.
const LIVE_URL = process.env.SMOKE_LIVE_URL || "https://gaurav-gandhi-2411.github.io/gold-rate-tracker/";
const RENDER_TIMEOUT_MS = 20_000;

const __dirname = path.dirname(fileURLToPath(import.meta.url));

function extractVersion(source) {
  const m = source.match(/const VERSION\s*=\s*"([^"]+)"/);
  return m ? m[1] : null;
}

// Returns { ok, reasons } — never throws, so callers can run every check
// even after an earlier one fails.
async function assertRendered(page, label) {
  const reasons = [];
  try {
    // renderMethodology() is the LAST step in app.js's init sequence — it
    // waits on the forecast plus four more Promise.allSettled fetches
    // (backtest/commentary/drift/coverage) after the history table already
    // rendered. Wait for all three surfaces together, not just history,
    // or this check races ahead of methodology and reports a false failure.
    await page.waitForFunction(
      () => {
        const history = document.getElementById("history-body");
        const meth = document.getElementById("methodology-body");
        return (
          history && !history.textContent.includes("Loading") &&
          meth && !meth.textContent.includes("Loading model details")
        );
      },
      { timeout: RENDER_TIMEOUT_MS }
    );
  } catch {
    reasons.push(`${label}: history table and/or methodology panel never left their "Loading…" placeholders within ${RENDER_TIMEOUT_MS}ms`);
  }

  const priceText = ((await page.locator("#hero-price").textContent()) || "").trim();
  if (!/[\d][\d,]{2,}/.test(priceText) || priceText === "—") {
    reasons.push(`${label}: #hero-price has no rendered number (got "${priceText}")`);
  }

  const historyText = (await page.locator("#history-body").textContent()) || "";
  if (historyText.includes("Loading") || !/₹[\d,]+/.test(historyText)) {
    reasons.push(`${label}: #history-body did not populate real rows (got "${historyText.slice(0, 60)}...")`);
  }

  const methText = (await page.locator("#methodology-body").textContent()) || "";
  if (methText.includes("Loading model details")) {
    reasons.push(`${label}: #methodology-body still shows "Loading model details…"`);
  }

  return { ok: reasons.length === 0, reasons };
}

async function runOnce() {
  const results = { checks: [], ok: true };

  const browser = await chromium.launch();
  try {
    const context = await browser.newContext();
    const page = await context.newPage();

    // Check 1: fresh load, no prior service-worker install.
    await page.goto(LIVE_URL, { waitUntil: "load" });
    const fresh = await assertRendered(page, "fresh-load");
    results.checks.push({ name: "fresh-load", ...fresh });

    // Give the service-worker registration (fired on window 'load' in
    // index.html) a moment to install + activate before reloading.
    await page.waitForTimeout(2000);

    // Check 2: same-context reload — service-worker now controls the page.
    await page.reload({ waitUntil: "load" });
    const returning = await assertRendered(page, "returning-visitor-reload");
    results.checks.push({ name: "returning-visitor-reload", ...returning });

    // Check 3: live service-worker.js VERSION matches this checkout's.
    const localSwPath = path.join(__dirname, "..", "service-worker.js");
    const localVersion = extractVersion(fs.readFileSync(localSwPath, "utf8"));
    const liveSwResp = await page.request.get(`${LIVE_URL}service-worker.js`);
    const liveVersion = extractVersion(await liveSwResp.text());
    const versionMatch = localVersion !== null && localVersion === liveVersion;
    results.checks.push({
      name: "sw-version-deploy-sync",
      ok: versionMatch,
      reasons: versionMatch
        ? []
        : [`local service-worker.js VERSION "${localVersion}" != live "${liveVersion}" — deploy may not have propagated yet`],
    });

    results.ok = results.checks.every((c) => c.ok);
  } finally {
    await browser.close();
  }

  return results;
}

async function main() {
  // One retry after a settle delay: this workflow is triggered right on
  // deployment_status success, and a fresh Pages deploy can take a few
  // seconds to propagate across GitHub's CDN edges — the same transient
  // edge-cache lag observed during the original incident diagnosis (a
  // stale cached HTML on the very first post-deploy request, self-resolved
  // on the next request). Reporting failure only if it reproduces on a
  // second attempt avoids paging on that lag instead of a real regression.
  const first = await runOnce();
  let final = first;
  if (!first.ok) {
    console.log(JSON.stringify({ attempt: 1, ...first }, null, 2));
    console.log("First attempt failed — waiting 15s and retrying once before reporting failure...");
    await new Promise((r) => setTimeout(r, 15_000));
    final = await runOnce();
  }

  console.log(JSON.stringify({ attempt: final === first ? 1 : 2, ...final }, null, 2));
  process.exit(final.ok ? 0 : 1);
}

main().catch((err) => {
  console.error(JSON.stringify({ ok: false, checks: [], error: String(err && err.stack ? err.stack : err) }, null, 2));
  process.exit(1);
});
