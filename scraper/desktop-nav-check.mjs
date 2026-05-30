import { chromium } from 'playwright';
import { mkdirSync } from 'fs';
import { join } from 'path';
const OUT = 'C:\\Users\\gaura\\temp\\psi3c1-desktop-fix';
mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch({ headless: true });

for (const [W, H, label] of [[1280, 900, 'desktop-1280'], [768, 1024, 'tablet-768'], [428, 926, 'iphone-428']]) {
  const ctx = await browser.newContext({ viewport: { width: W, height: H } });
  const page = await ctx.newPage();
  await page.goto('http://localhost:8002/', { waitUntil: 'networkidle', timeout: 20000 });
  await page.waitForTimeout(2000);
  await page.screenshot({ path: join(OUT, `${label}.png`), fullPage: false });
  const navVisible = await page.locator('.bottom-nav').isVisible();
  const mainPad = await page.evaluate(() => getComputedStyle(document.querySelector('main')).paddingBottom);
  console.log(`[${label}] bottom-nav visible: ${navVisible} | main padding-bottom: ${mainPad}`);
  await ctx.close();
}

await browser.close();
console.log('saved to', OUT);
