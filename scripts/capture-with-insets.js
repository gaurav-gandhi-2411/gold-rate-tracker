// Capture with simulated safe-area insets for Dynamic Island test.
// Usage: node scripts/capture-with-insets.js <width> <height> <top> <outpath>

const { chromium } = require("../scraper/node_modules/playwright");

const [,, w, h, top, outPath] = process.argv;
const PORT = 8765;
const IOS_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1";

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({
    viewport: { width: +w, height: +h },
    deviceScaleFactor: 2,
    userAgent: IOS_UA,
  });
  const page = await ctx.newPage();
  await page.route("https://fonts.googleapis.com/**", r => r.fulfill({ status: 200, contentType: "text/css", body: "" }));
  await page.route("https://fonts.gstatic.com/**",   r => r.fulfill({ status: 200, contentType: "font/woff2", body: Buffer.alloc(0) }));
  await page.goto(`http://localhost:${PORT}`, { waitUntil: "networkidle", timeout: 15000 });
  // Simulate safe-area-inset-top (Dynamic Island = 59px, notch = 47px).
  await page.addStyleTag({ content: `
    :root {
      --simulated-sat: ${+top}px;
    }
    body {
      padding-top: ${+top}px !important;
    }
    .freshness-pill {
      top: ${+top}px !important;
    }
  `});
  await page.waitForFunction(() => {
    const p = document.getElementById("hero-price");
    return p && p.textContent.trim() !== "—";
  }, { timeout: 8000 }).catch(() => {});
  await page.waitForTimeout(600);
  await page.screenshot({ path: outPath, fullPage: true });
  console.log("saved:", outPath);
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
