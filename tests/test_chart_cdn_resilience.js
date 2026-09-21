// tests/test_chart_cdn_resilience.js
//
// Chart.js is loaded from cdn.jsdelivr.net. When that request fails, `Chart` is undefined and the
// old renderChart()/renderForecastVsActual() threw at `new Chart(...)`. init() calls renderChart()
// BEFORE renderHero(), so one CDN failure left the hero on its loading skeleton and failed the
// render smoke test (run 35511515077, 2026-09-21). The cards that do not need a chart must survive.
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadApp } from "./helpers/load_app.js";

const DAY = 86400000;
const NOW = Date.parse("2026-09-21T06:00:00Z");
const readings = Array.from({ length: 10 }, (_, i) => ({
  timestamp: new Date(NOW - (9 - i) * DAY).toISOString(),
  "22k": 13500 + i * 10,
  "24k": 14700 + i * 10,
}));
const folds = Array.from({ length: 5 }, (_, i) => ({
  context_end_date: new Date(NOW - (5 - i) * DAY).toISOString().slice(0, 10),
  actuals: [13500 + i],
  naive: [13490 + i],
}));

// The stub DOM hands back a fresh element for every `.parentElement` read, so seed one to make the
// wrapper's hidden state observable.
function withChartWrap(ctx) {
  const wrap = ctx.element("chart-wrap-under-test");
  wrap.hidden = false;
  ctx.element("chart").parentElement = wrap;
  return wrap;
}

test("renderChart: CDN down (Chart undefined) does not throw and hides the chart wrapper", () => {
  const ctx = loadApp({ nowMs: NOW });
  try {
    assert.equal(ctx.run('typeof Chart'), "undefined", "precondition: no Chart global");
    ctx.getComputedStyle = () => ({ getPropertyValue: () => "" }); // exists in every browser; the vm sandbox has none
    const wrap = withChartWrap(ctx);
    assert.doesNotThrow(() => ctx.renderChart(readings, "30"));
    assert.equal(wrap.hidden, true);
  } finally {
    ctx.dispose();
  }
});

test("renderForecastVsActual: CDN down hides the track-record section instead of throwing", () => {
  const ctx = loadApp({ nowMs: NOW });
  try {
    const section = ctx.element("section-track-record");
    section.hidden = false;
    ctx.getComputedStyle = () => ({ getPropertyValue: () => "" }); // exists in every browser; the vm sandbox has none
    assert.doesNotThrow(() => ctx.renderForecastVsActual({ folds }));
    assert.equal(section.hidden, true);
  } finally {
    ctx.dispose();
  }
});

test("renderChart: CDN up still builds the chart and keeps the wrapper visible", () => {
  const ctx = loadApp({ nowMs: NOW });
  try {
    const built = [];
    ctx.Chart = class {
      constructor(el, config) { built.push(config); }
      destroy() {}
    };
    ctx.getComputedStyle = () => ({ getPropertyValue: () => "" });
    const wrap = withChartWrap(ctx);
    wrap.hidden = true; // a previous failed render left it hidden; a later good render must show it
    ctx.renderChart(readings, "30");
    assert.equal(built.length, 1, "exactly one Chart constructed");
    assert.equal(built[0].type, "line");
    assert.equal(wrap.hidden, false);
  } finally {
    ctx.dispose();
  }
});

test("__onChartReady: Chart.js arriving AFTER the first render draws both charts and un-hides them", () => {
  const ctx = loadApp({ nowMs: NOW });
  try {
    ctx.getComputedStyle = () => ({ getPropertyValue: () => "" });
    const wrap = withChartWrap(ctx);
    const section = ctx.element("section-track-record");
    ctx.run("allReadings = " + JSON.stringify(readings));
    ctx.run("lastBacktest = " + JSON.stringify({ folds }));
    ctx.renderChart(readings, "30");
    ctx.renderForecastVsActual({ folds });
    assert.equal(wrap.hidden, true, "precondition: hidden while Chart.js has not arrived");
    assert.equal(section.hidden, true);

    const built = [];
    ctx.Chart = class {
      constructor(el, config) { built.push(config); }
      destroy() {}
    };
    assert.equal(typeof ctx.__onChartReady, "function", "app.js must expose the onload hook");
    ctx.__onChartReady();
    assert.equal(built.length, 2, "main chart and track-record chart both drawn");
    assert.equal(wrap.hidden, false);
    assert.equal(section.hidden, false);
  } finally {
    ctx.dispose();
  }
});

test("__onChartReady: before any data has loaded it is a safe no-op", () => {
  const ctx = loadApp({ nowMs: NOW });
  try {
    ctx.Chart = class { constructor() { throw new Error("must not draw without data"); } destroy() {} };
    assert.doesNotThrow(() => ctx.__onChartReady());
  } finally {
    ctx.dispose();
  }
});

test("index.html: the Chart.js tag is async with the onload hook, never defer (defer gates app.js)", async () => {
  const fs = await import("node:fs");
  const html = fs.readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const tag = html.match(/<script[^>]*chart\.umd\.min\.js[^>]*>/s)?.[0];
  assert.ok(tag, "Chart.js script tag not found");
  assert.match(tag, /\basync\b/);
  assert.doesNotMatch(tag, /\bdefer\b/);
  assert.match(tag, /onload="[^"]*__onChartReady/);
});

test("renderForecastVsActual: CDN up builds the track-record chart", () => {
  const ctx = loadApp({ nowMs: NOW });
  try {
    const built = [];
    ctx.Chart = class {
      constructor(el, config) { built.push(config); }
      destroy() {}
    };
    ctx.getComputedStyle = () => ({ getPropertyValue: () => "" });
    ctx.renderForecastVsActual({ folds });
    assert.equal(built.length, 1, "the guard must not disable the chart when Chart.js loaded");
  } finally {
    ctx.dispose();
  }
});
