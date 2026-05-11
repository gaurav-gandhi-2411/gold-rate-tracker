// Captures full-page screenshots at target viewports for UI audit.
// Usage: node scripts/capture-screenshots.js
// Requires: Python HTTP server serving root at PORT (default 8765)

const { chromium } = require("../scraper/node_modules/playwright");
const path = require("path");
const fs = require("fs");

const PORT = 8765;
const BASE_URL = `http://localhost:${PORT}`;
const OUT_DIR = path.join(__dirname, "..", "docs", "screenshots", "before");

const VIEWPORTS = [
  { width: 320,  height: 568,  label: "320x568"   },
  { width: 375,  height: 667,  label: "375x667"   },
  { width: 390,  height: 844,  label: "390x844"   },
  { width: 393,  height: 852,  label: "393x852"   },
  { width: 430,  height: 932,  label: "430x932"   },
  { width: 768,  height: 1024, label: "768x1024"  },
  { width: 1024, height: 768,  label: "1024x768"  },
  { width: 1280, height: 800,  label: "1280x800"  },
  { width: 1440, height: 900,  label: "1440x900"  },
  { width: 1920, height: 1080, label: "1920x1080" },
];

// iOS Safari UA — needed so `env(safe-area-inset-*)` is exercised
const IOS_UA =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) " +
  "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1";

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });

  const browser = await chromium.launch({ headless: true });

  for (const vp of VIEWPORTS) {
    const isMobile = vp.width < 768;
    const context = await browser.newContext({
      viewport:          { width: vp.width, height: vp.height },
      deviceScaleFactor: isMobile ? 2 : 1,
      userAgent:         isMobile ? IOS_UA : undefined,
      // Simulate iOS safe areas on phones
      ...(isMobile && vp.height >= 844
        ? { extraHTTPHeaders: {} }   // placeholder; real safe-area via CSS env()
        : {}),
    });

    const page = await context.newPage();

    // Intercept font requests to avoid network flakiness in CI
    // (fonts are optional for layout screenshots)
    await page.route("https://fonts.googleapis.com/**", route => route.fulfill({
      status: 200,
      contentType: "text/css",
      body: "",
    }));
    await page.route("https://fonts.gstatic.com/**", route => route.fulfill({
      status: 200,
      contentType: "font/woff2",
      body: Buffer.alloc(0),
    }));

    await page.goto(BASE_URL, { waitUntil: "networkidle", timeout: 15000 });

    // Wait for JS to populate data (hero price, cards, chart)
    await page.waitForFunction(() => {
      const p = document.getElementById("hero-price");
      return p && p.textContent.trim() !== "—";
    }, { timeout: 8000 }).catch(() => {
      console.warn(`  [warn] ${vp.label}: hero price still '—' after 8s`);
    });

    // Extra settle for chart.js canvas render
    await page.waitForTimeout(600);

    const outPath = path.join(OUT_DIR, `${vp.label}.png`);
    await page.screenshot({ path: outPath, fullPage: true });
    console.log(`  ✓ ${vp.label}  →  ${outPath}`);

    await context.close();
  }

  await browser.close();
  console.log("\nDone. Screenshots in", OUT_DIR);
}

main().catch(err => { console.error(err); process.exit(1); });
