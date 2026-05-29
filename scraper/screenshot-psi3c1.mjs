// Ψ3C.1 verification: bottom nav, app header, scrollspy, latent CSS fixes
import { chromium } from 'playwright';
import { mkdirSync } from 'fs';
import { join } from 'path';

const BASE_URL = 'http://localhost:8002/';
const OUT = 'C:\\Users\\gaura\\temp\\psi3c1-verify';
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ headless: true });

async function shoot(page, name, clip) {
  const opts = { path: join(OUT, `${name}.png`), fullPage: false };
  if (clip) opts.clip = clip;
  await page.screenshot(opts);
  console.log('saved:', opts.path);
}

// ── 1. iPhone 14 Plus — primary viewport ──────────────────────────────────────
{
  const W = 428, H = 926;
  const ctx  = await browser.newContext({ viewport: { width: W, height: H } });
  const page = await ctx.newPage();
  page.on('console', msg => { if (msg.type() === 'error') console.error('[iphone] error:', msg.text()); });
  await page.goto(BASE_URL, { waitUntil: 'networkidle', timeout: 20000 });
  await page.waitForTimeout(2500);

  // Full page screenshot at top (shows header + hero + bottom nav)
  await shoot(page, '01-iphone-top', { x: 0, y: 0, width: W, height: H });

  // Zoom: app header area
  const header = page.locator('.app-header');
  const hBox   = await header.boundingBox();
  if (hBox) await shoot(page, '02-iphone-header', { x: 0, y: 0, width: W, height: Math.max(hBox.height + 4, 56) });

  // Zoom: bottom nav
  const nav   = page.locator('.bottom-nav');
  const nBox  = await nav.boundingBox();
  if (nBox) await shoot(page, '03-iphone-bottom-nav', { x: 0, y: nBox.y - 2, width: W, height: nBox.height + 4 });

  // Verify header: app-title present, location-label gone
  const titleEl = page.locator('.app-title');
  const titleText = await titleEl.textContent();
  console.log('[iphone] app-title text:', titleText);
  console.log('[iphone] app-title visible:', await titleEl.isVisible());

  // Verify location in hero area (not header)
  const heroLoc = page.locator('#hero-location');
  console.log('[iphone] hero-location visible:', await heroLoc.isVisible());

  // Verify header height ≥ 52px
  if (hBox) console.log('[iphone] header height:', Math.round(hBox.height), '≥52:', hBox.height >= 52);

  // Verify bottom nav present and visible
  const navVisible = await nav.isVisible();
  console.log('[iphone] bottom-nav visible:', navVisible);

  // Verify active item is "Home" initially (section-home)
  const activeItem = await page.evaluate(() => {
    const active = document.querySelector('.bottom-nav-item.active');
    return active ? active.dataset.section : null;
  });
  console.log('[iphone] initial active nav section:', activeItem);

  // ── Scroll to Trend section, verify scrollspy updates ──────────────────────
  await page.evaluate(() => {
    const el = document.getElementById('section-trend');
    if (el) el.scrollIntoView({ behavior: 'instant' });
  });
  await page.waitForTimeout(400);

  const activeAfterScroll = await page.evaluate(() => {
    const active = document.querySelector('.bottom-nav-item.active');
    return active ? active.dataset.section : null;
  });
  console.log('[iphone] active nav after scrolling to section-trend:', activeAfterScroll);

  // Screenshot mid-scroll: Trend section visible + scrollspy
  await shoot(page, '04-iphone-scrollspy-trend', { x: 0, y: 0, width: W, height: H });

  // Zoom: bottom nav showing Trend active
  const nBox2 = await nav.boundingBox();
  if (nBox2) await shoot(page, '05-iphone-nav-trend-active', { x: 0, y: nBox2.y - 2, width: W, height: nBox2.height + 4 });

  // Scroll to History
  await page.evaluate(() => {
    const el = document.getElementById('section-history');
    if (el) el.scrollIntoView({ behavior: 'instant' });
  });
  await page.waitForTimeout(400);
  const activeHistory = await page.evaluate(() => {
    const a = document.querySelector('.bottom-nav-item.active');
    return a ? a.dataset.section : null;
  });
  console.log('[iphone] active nav after scrolling to section-history:', activeHistory);

  // ── NORM #14: Computed-style verification of latent CSS fixes ──────────────
  // Both hero-change and sparkline-wrap start with hidden attribute.
  // Must verify via getComputedStyle NOT el.hidden.
  const csCheck = await page.evaluate(() => {
    const heroChange = document.getElementById('hero-change');
    const sparkline  = document.getElementById('sparkline-wrap');
    const heroChangeCs  = heroChange  ? getComputedStyle(heroChange).display  : 'element missing';
    const sparklineCs   = sparkline   ? getComputedStyle(sparkline).display   : 'element missing';
    return {
      heroChange: {
        hidden_attr:   heroChange?.hidden,
        computed_display: heroChangeCs,
        is_hidden_correctly: heroChangeCs === 'none',
      },
      sparklineWrap: {
        hidden_attr:   sparkline?.hidden,
        computed_display: sparklineCs,
        is_hidden_correctly: sparklineCs === 'none',
      },
    };
  });
  console.log('\n[NORM #14] Computed-style verification:');
  console.log('  hero-change  hidden attr:', csCheck.heroChange.hidden_attr,
              '| computed display:', csCheck.heroChange.computed_display,
              '| PASS:', csCheck.heroChange.is_hidden_correctly);
  console.log('  sparkline-wrap hidden attr:', csCheck.sparklineWrap.hidden_attr,
              '| computed display:', csCheck.sparklineWrap.computed_display,
              '| PASS:', csCheck.sparklineWrap.is_hidden_correctly);

  // Footer must not be hidden behind bottom nav: check footer bottom vs nav top
  const footerCheck = await page.evaluate(() => {
    const footer = document.querySelector('.site-footer');
    const nav    = document.querySelector('.bottom-nav');
    if (!footer || !nav) return { ok: false, reason: 'elements missing' };
    const footerRect = footer.getBoundingClientRect();
    const navRect    = nav.getBoundingClientRect();
    return { footerBottom: Math.round(footerRect.bottom), navTop: Math.round(navRect.top), ok: true };
  });
  // Scroll to bottom first
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.waitForTimeout(300);
  await shoot(page, '06-iphone-footer-bottom', { x: 0, y: 0, width: W, height: H });
  const footerCheck2 = await page.evaluate(() => {
    const footer = document.querySelector('.site-footer');
    const nav    = document.querySelector('.bottom-nav');
    if (!footer || !nav) return { ok: false };
    const fRect = footer.getBoundingClientRect();
    const nRect = nav.getBoundingClientRect();
    // Footer should be fully above nav top when at bottom of page
    return {
      footerBottom: Math.round(fRect.bottom),
      navTop: Math.round(nRect.top),
      footerClearNav: fRect.bottom <= nRect.top,
    };
  });
  console.log('\n[iphone] footer/nav clearance at bottom of page:',
              'footer.bottom=', footerCheck2.footerBottom,
              'nav.top=', footerCheck2.navTop,
              'clear=', footerCheck2.footerClearNav);

  await ctx.close();
}

// ── 2. Tablet 768px ──────────────────────────────────────────────────────────
{
  const W = 768, H = 1024;
  const ctx  = await browser.newContext({ viewport: { width: W, height: H } });
  const page = await ctx.newPage();
  await page.goto(BASE_URL, { waitUntil: 'networkidle', timeout: 20000 });
  await page.waitForTimeout(2000);
  await shoot(page, '07-tablet-full', { x: 0, y: 0, width: W, height: H });
  const navVisible = await page.locator('.bottom-nav').isVisible();
  const hBox = await page.locator('.app-header').boundingBox();
  console.log('[tablet] bottom-nav visible:', navVisible, '| header-height:', hBox ? Math.round(hBox.height) : 'N/A');
  await ctx.close();
}

// ── 3. Desktop 1280px ────────────────────────────────────────────────────────
{
  const W = 1280, H = 900;
  const ctx  = await browser.newContext({ viewport: { width: W, height: H } });
  const page = await ctx.newPage();
  await page.goto(BASE_URL, { waitUntil: 'networkidle', timeout: 20000 });
  await page.waitForTimeout(2000);
  await shoot(page, '08-desktop-full', { x: 0, y: 0, width: W, height: H });
  const navVisible = await page.locator('.bottom-nav').isVisible();
  console.log('[desktop] bottom-nav visible:', navVisible);
  await ctx.close();
}

await browser.close();
console.log('\nAll screenshots saved to', OUT);
