// tests/test_chart_year_labels.js — GG P0 2026-11-05: the trend chart must never draw a gap as a
// one-day move, and its dates must carry the year whenever the plotted points span two years.
//
// Before: a category axis (one slot per row) with "19 Jan", "17 Jul" labels. A 101-day gap in the
// IBJA history sat one slot wide and read as a +21.8% single-day jump.
//
// Runs the REAL renderChart from app.js against a stub Chart constructor that records the config.
//
// Run: node --test tests/test_chart_year_labels.js  (from repo root)

import assert from "assert/strict";
import { test } from "node:test";
import { loadApp } from "./helpers/load_app.js";

const app = loadApp();
const spansYears = app.pure("spansYears");
const chartTickLabel = app.pure("chartTickLabel");
const fmtDateShort = app.pure("fmtDateShort");

// Capture what renderChart hands to Chart.js.
app.run(`
  globalThis.__chartConfigs = [];
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => "" });
  globalThis.Chart = class { constructor(_ctx, cfg) { __chartConfigs.push(cfg); } destroy() {} update() {} };
`);
function renderWith(rows, range = "all") {
  app.run(`__chartConfigs.length = 0; derivedSeries = ${JSON.stringify(rows)};`);
  app.run(`renderChart([], ${JSON.stringify(range)})`);
  return app.run("__chartConfigs[0]");
}
const row = (d, v) => ({ timestamp: `${d}T11:30:00.000Z`, "22k": v });

test("spansYears: one IST calendar year vs two", () => {
  assert.equal(spansYears(["2026-06-01T11:30:00.000Z", "2026-11-05T11:30:00.000Z"]), false);
  assert.equal(spansYears(["2025-12-31T11:30:00.000Z", "2026-01-02T11:30:00.000Z"]), true);
  // 2025-12-31 20:00 UTC is already 1 Jan 2026 in IST
  assert.equal(spansYears(["2025-12-31T20:00:00.000Z", "2026-03-01T11:30:00.000Z"]), false);
});

test("date formatting carries the year only when asked", () => {
  const iso = "2024-03-01T11:30:00.000Z";
  assert.match(fmtDateShort(iso, true), /2024/);
  assert.doesNotMatch(fmtDateShort(iso), /2024/);
  assert.match(chartTickLabel(Date.parse(iso), true), /2024/);
});

test("multi-year chart: time axis, year on every tick and tooltip, gaps not joined", () => {
  const cfg = renderWith([row("2024-03-01", 5787), row("2024-04-13", 6803), row("2026-06-01", 14460), row("2026-06-02", 14400)]);
  const x = cfg.options.scales.x;
  assert.equal(x.type, "linear", "x-axis must be time-proportional, not one slot per row");
  const pts = cfg.data.datasets[0].data;
  assert.equal(pts[1].x - pts[0].x, 43 * 86_400_000, "a 43-day gap must be 43 days wide");
  for (const ms of [pts[0].x, pts[2].x, (pts[0].x + pts[3].x) / 2]) {
    assert.match(x.ticks.callback(ms), /20\d\d/, "every tick on a multi-year chart shows its year");
  }
  assert.ok(cfg.data.labels.every((l) => /20\d\d/.test(l)), "tooltip/callout dates carry the year");
  const gap = cfg.data.datasets[0].spanGaps;
  assert.equal(typeof gap, "number", "spanGaps must be a max gap, not true (join everything)");
  assert.ok(gap < 43 * 86_400_000 && gap >= 4 * 86_400_000, `spanGaps ${gap}`);
});

test("single-year chart: ticks stay short", () => {
  const cfg = renderWith([row("2026-06-01", 14460), row("2026-06-02", 14400), row("2026-11-05", 13400)]);
  assert.doesNotMatch(cfg.options.scales.x.ticks.callback(cfg.data.datasets[0].data[0].x), /2026/);
});
