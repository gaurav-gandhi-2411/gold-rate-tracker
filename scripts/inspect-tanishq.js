// scripts/inspect-tanishq.js
// Diagnose the Tanishq gold rate page structure.
// Usage (from repo root): node --experimental-vm-modules scripts/inspect-tanishq.js
// Or: cd scraper && node ../scripts/inspect-tanishq.js

import { chromium } from "playwright";
import { writeFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

const URL = "https://www.tanishq.co.in/gold-rate.html?lang=en_IN";
const TMP = tmpdir();

async function main() {
  const browser = await chromium.launch({
    headless: true,
    args: ["--no-sandbox", "--disable-blink-features=AutomationControlled"],
  });
  const context = await browser.newContext({
    userAgent:
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    viewport: { width: 1280, height: 800 },
    locale: "en-IN",
  });
  const page = await context.newPage();

  console.log("Loading page …");
  await page.goto(URL, { waitUntil: "networkidle", timeout: 90000 });
  console.log("Network idle. Waiting 8 s for JS widgets …");
  await page.waitForTimeout(8000);

  // ── 1. Save HTML and text ──────────────────────────────────────────────────
  const html  = await page.content();
  const text  = await page.evaluate(() => document.body.innerText);

  const htmlPath = join(TMP, "tanishq.html");
  const txtPath  = join(TMP, "tanishq.txt");
  writeFileSync(htmlPath, html);
  writeFileSync(txtPath,  text);
  console.log(`\nSaved HTML → ${htmlPath}`);
  console.log(`Saved text → ${txtPath}`);

  // ── 2. Find karat labels in text ──────────────────────────────────────────
  console.log("\n── Karat label occurrences in body text ──");
  const lines = text.split("\n");
  ["22 KT", "22KT", "22 K", "24 KT", "24KT", "24 K", "18 KT", "18KT", "18 K",
   "22 Karat", "24 Karat", "18 Karat"].forEach(label => {
    lines.forEach((line, i) => {
      if (line.toLowerCase().includes(label.toLowerCase())) {
        const snippet = line.slice(0, 120).trim();
        console.log(`  [line ${i + 1}] "${label}" → ${snippet}`);
      }
    });
  });

  // ── 3. Text context around 22/24/18 KT ────────────────────────────────────
  console.log("\n── 30-char context after each karat mention (body text) ──");
  for (const kt of ["22 KT", "24 KT", "18 KT", "22KT", "24KT", "18KT"]) {
    let pos = 0;
    while (true) {
      const idx = text.toUpperCase().indexOf(kt.toUpperCase(), pos);
      if (idx === -1) break;
      const ctx = text.slice(idx, idx + 80).replace(/\n/g, "↵");
      console.log(`  "${kt}" at char ${idx}: …${ctx}…`);
      pos = idx + 1;
      if (pos - idx > 50000) break; // safety
    }
  }

  // ── 4. DOM: elements with data-* or class containing rate/price/gold ──────
  console.log("\n── DOM elements with rate/price/gold in class or data-* ──");
  const rateEls = await page.evaluate(() => {
    const candidates = [];
    document.querySelectorAll("*").forEach(el => {
      const cls = el.className || "";
      const ds  = JSON.stringify(el.dataset || {});
      const combined = (typeof cls === "string" ? cls : "") + ds;
      if (/rate|price|gold/i.test(combined) && el.children.length < 6) {
        const txt = (el.innerText || "").slice(0, 100).trim().replace(/\n/g, "↵");
        if (txt) {
          candidates.push({
            tag: el.tagName,
            id: el.id || null,
            className: (typeof cls === "string" ? cls.slice(0, 80) : ""),
            dataset: ds.slice(0, 120),
            text: txt,
          });
        }
      }
    });
    return candidates.slice(0, 60); // cap output
  });
  rateEls.forEach(e =>
    console.log(`  <${e.tag}${e.id ? ' id="' + e.id + '"' : ''} class="${e.className}"> ds=${e.dataset} → "${e.text}"`)
  );

  // ── 5. Tables ─────────────────────────────────────────────────────────────
  console.log("\n── <table> rows containing digits ──");
  const tableRows = await page.evaluate(() => {
    const rows = [];
    document.querySelectorAll("table tr").forEach(tr => {
      const t = tr.innerText.replace(/\n/g, " | ").trim();
      if (/\d{4,}/.test(t)) rows.push(t.slice(0, 200));
    });
    return rows.slice(0, 20);
  });
  tableRows.forEach(r => console.log("  " + r));

  // ── 6. Inline <script> tags with numeric gold-rate-like values ────────────
  console.log("\n── Inline <script> bodies containing 5-digit numbers ──");
  const scripts = await page.evaluate(() => {
    const found = [];
    document.querySelectorAll("script:not([src])").forEach(s => {
      const txt = s.textContent || "";
      if (/\b\d{5}\b/.test(txt) && txt.length < 50000) {
        // Only capture if it looks price-like (INR, rate, gold, karat)
        if (/rate|price|gold|karat|22|24|18/i.test(txt)) {
          found.push(txt.slice(0, 1500));
        }
      }
    });
    return found.slice(0, 5);
  });
  scripts.forEach((s, i) => {
    console.log(`\n  [script ${i}] (${s.length} chars shown)`);
    console.log("  " + s.replace(/\n/g, "\n  ").slice(0, 1000));
  });

  // ── 7. Network JSON responses that look like gold rates ───────────────────
  // (only what's already in memory — we can't replay requests)
  const jsonData = await page.evaluate(() => {
    // Look for window-level JSON blobs
    const out = [];
    try {
      for (const key of Object.keys(window)) {
        if (/gold|rate|price|metal/i.test(key)) {
          out.push({ key, val: JSON.stringify(window[key]).slice(0, 300) });
        }
      }
    } catch (_) {}
    return out;
  });
  if (jsonData.length) {
    console.log("\n── window.* keys matching gold/rate/price/metal ──");
    jsonData.forEach(({ key, val }) => console.log(`  window.${key} = ${val}`));
  }

  await browser.close();
  console.log("\n── Done. Check /tmp/tanishq.txt for full body text. ──");
}

main().catch(err => {
  console.error("Inspector failed:", err);
  process.exit(1);
});
