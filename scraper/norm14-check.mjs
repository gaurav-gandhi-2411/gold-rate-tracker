// Norm #14 computed-style verification for Ψ3C.1 latent CSS fixes
import { chromium } from 'playwright';

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 428, height: 926 } });
const page = await ctx.newPage();
await page.goto('http://localhost:8002/', { waitUntil: 'domcontentloaded', timeout: 15000 });

// Check 1: BEFORE data loads — elements still have hidden attr from HTML
const earlyCheck = await page.evaluate(() => {
  const heroChange = document.getElementById('hero-change');
  const sparkline  = document.getElementById('sparkline-wrap');
  return {
    heroChange: {
      hidden_attr: heroChange?.hidden,
      computed_display: heroChange ? getComputedStyle(heroChange).display : 'missing',
    },
    sparklineWrap: {
      hidden_attr: sparkline?.hidden,
      computed_display: sparkline ? getComputedStyle(sparkline).display : 'missing',
    },
  };
});

console.log('[NORM #14] Check 1 — initial hidden state (before JS load):');
const hcPass1 = earlyCheck.heroChange.hidden_attr === true && earlyCheck.heroChange.computed_display === 'none';
const swPass1 = earlyCheck.sparklineWrap.hidden_attr === true && earlyCheck.sparklineWrap.computed_display === 'none';
console.log('  hero-change    hidden=', earlyCheck.heroChange.hidden_attr, '| display=', earlyCheck.heroChange.computed_display, '| PASS:', hcPass1);
console.log('  sparkline-wrap hidden=', earlyCheck.sparklineWrap.hidden_attr, '| display=', earlyCheck.sparklineWrap.computed_display, '| PASS:', swPass1);

// Wait for JS to load data and reveal elements
await page.waitForTimeout(3500);

// Check 2: After JS reveals them, explicitly re-set hidden=true and verify display=none
const explicitCheck = await page.evaluate(() => {
  const heroChange = document.getElementById('hero-change');
  const sparkline  = document.getElementById('sparkline-wrap');
  // Re-hide them to simulate the hidden state
  if (heroChange) heroChange.hidden = true;
  if (sparkline)  sparkline.hidden  = true;
  return {
    heroChange: {
      hidden_attr: heroChange?.hidden,
      computed_display: heroChange ? getComputedStyle(heroChange).display : 'missing',
    },
    sparklineWrap: {
      hidden_attr: sparkline?.hidden,
      computed_display: sparkline ? getComputedStyle(sparkline).display : 'missing',
    },
  };
});

console.log('\n[NORM #14] Check 2 — explicitly re-hidden (el.hidden = true, post-load):');
const hcPass2 = explicitCheck.heroChange.hidden_attr === true && explicitCheck.heroChange.computed_display === 'none';
const swPass2 = explicitCheck.sparklineWrap.hidden_attr === true && explicitCheck.sparklineWrap.computed_display === 'none';
console.log('  hero-change    hidden=', explicitCheck.heroChange.hidden_attr, '| display=', explicitCheck.heroChange.computed_display, '| PASS:', hcPass2);
console.log('  sparkline-wrap hidden=', explicitCheck.sparklineWrap.hidden_attr, '| display=', explicitCheck.sparklineWrap.computed_display, '| PASS:', swPass2);

const allPass = hcPass1 && swPass1 && hcPass2 && swPass2;
console.log('\n[NORM #14] Overall result:', allPass ? 'ALL PASS ✓' : 'FAILURES DETECTED ✗');

await browser.close();
process.exit(allPass ? 0 : 1);
