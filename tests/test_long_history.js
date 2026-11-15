// tests/test_long_history.js — the long-term view (flag long_history, OFF): GG 2026-11-06.
//
// 2022 - May 2026 is only sampled (13 of 41 months with data have one day, 12 of 53 months none),
// so with the flag ON the "All" range adds one UNJOINED dot per month with data (the median, n,
// min and max in the tooltip), on a year axis, with the method stated. With the flag OFF, or on any
// other range, the chart must be exactly what it was.
//
// Runs the REAL renderChart from app.js against a stub Chart that records the config.
//
// Run: node --test tests/test_long_history.js  (from repo root)

import assert from "assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import { loadApp } from "./helpers/load_app.js";

const app = loadApp();
const longHistoryDots = app.pure("longHistoryDots");
const t = (key, args) => app.run(`t(${JSON.stringify(key)}, ${JSON.stringify(args ?? {})})`);
const setLang = (lang) => app.run(`currentLang = ${JSON.stringify(lang)}`);

app.run(`
  globalThis.__chartConfigs = [];
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => "" });
  globalThis.Chart = class { constructor(_ctx, cfg) { __chartConfigs.push(cfg); } destroy() {} update() {} };
`);

const daily = (d, v) => ({ timestamp: `${d}T11:30:00.000Z`, "22k": v });
const DAILY = [daily("2026-06-01", 14460), daily("2026-06-02", 14400), daily("2026-11-05", 13400)];
const sample = (month, v, n, min = v - 20, max = v + 20) =>
  ({ month, timestamp: `${month}-15T11:30:00.000Z`, "22k": v, n, min, max });
const DOC = {
  months_total: 53, months_with_data: 3,
  samples: [sample("2022-01", 4528, 3, 4486, 4528), sample("2024-03", 5787, 1, 5787, 5787), sample("2026-05", 14052, 11, 13726, 14983)],
};

function render({ flag, range = "all", doc = DOC, rows = DAILY } = {}) {
  app.run(`globalThis.isFeatureOn = (n) => ${flag ? 'n === "long_history"' : "false"};`);
  app.run(`__chartConfigs.length = 0; derivedSeries = ${JSON.stringify(rows)}; longHistory = ${JSON.stringify(doc)};`);
  app.run(`renderChart([], ${JSON.stringify(range)})`);
  return app.run("__chartConfigs[0]");
}

test("flag OFF: one dataset, 'index' interaction, no note -- exactly the current chart", () => {
  const cfg = render({ flag: false });
  assert.equal(cfg.data.datasets.length, 1);
  assert.equal(cfg.options.interaction.mode, "index");
  assert.equal(app.element("chart-long-history-note").hidden, true);
  assert.equal(app.element("chart-long-history-note").textContent, "");
  assert.equal(cfg.options.scales.x.min, Date.parse("2026-06-01T11:30:00.000Z"));
});

test("flag ON but not 'All', or not the estimate series: still no dots", () => {
  assert.equal(render({ flag: true, range: "30" }).data.datasets.length, 1);
  assert.equal(app.element("chart-long-history-note").hidden, true);
});

test("flag ON, All: monthly dots as a second dataset, never joined, year on the axis", () => {
  const cfg = render({ flag: true });
  assert.equal(cfg.data.datasets.length, 2);
  const [line, dots] = cfg.data.datasets;
  assert.equal(line.data.length, 3, "the daily line is untouched");
  assert.equal(dots.showLine, false, "dots are not joined");
  assert.ok(dots.pointRadius > 0 && dots.backgroundColor === "transparent");
  assert.equal(dots.data.length, 3);
  assert.ok(dots.data.every((d) => d.x < line.data[0].x), "no dot overlaps the daily series");
  assert.equal(cfg.options.scales.x.min, dots.data[0].x, "the axis starts at the first dot");
  assert.match(cfg.options.scales.x.ticks.callback(dots.data[0].x), /2022/);
  assert.equal(cfg.options.interaction.mode, "nearest");
});

test("flag ON: the method note states the counts, in English and Hindi", () => {
  setLang("en");
  render({ flag: true });
  const el = app.element("chart-long-history-note");
  assert.equal(el.hidden, false);
  assert.match(el.textContent, /Before June 2026 we only have scattered official-rate days/);
  assert.match(el.textContent, /50 of the 53 months have no data/);
  assert.match(el.textContent, /Dots are not joined/);
  setLang("hi");
  render({ flag: true });
  assert.match(app.element("chart-long-history-note").textContent, /53 में से 50 महीनों का कोई डेटा नहीं/);
  setLang("en");
});

test("tooltip: a dot says month, middle-of-n and range; a single day says so; the line is unchanged", () => {
  setLang("en");
  const cfg = render({ flag: true });
  const cb = cfg.options.plugins.tooltip.callbacks;
  assert.equal(cb.title([{ datasetIndex: 1, dataIndex: 0 }]), "Jan 2022");
  assert.equal(cb.label({ datasetIndex: 1, dataIndex: 0, parsed: { y: 4528 } }),
    "22K estimate: ≈ ₹4,528, the middle of 3 official-rate days (₹4,486 – ₹4,528)");
  assert.match(cb.label({ datasetIndex: 1, dataIndex: 1, parsed: { y: 5787 } }), /the only official-rate day we have this month/);
  assert.equal(cb.title([{ datasetIndex: 0, dataIndex: 2 }]), "5 Nov 2026");
  assert.equal(cb.label({ datasetIndex: 0, dataIndex: 2, parsed: { y: 13400 } }), "22K estimate: ≈ ₹13,400");
  setLang("hi");
  assert.match(cb.label({ datasetIndex: 1, dataIndex: 0, parsed: { y: 4528 } }), /3 दिनों का बीच का भाव/);
  setLang("en");
});

test("tapping a dot does not pin the daily line's callout; tapping the line still does", () => {
  const cfg = render({ flag: true });
  app.run("chartPinnedIndex = null; chart = { update() {} };");
  cfg.options.onClick({}, [{ datasetIndex: 1, index: 0 }]);
  assert.equal(app.run("chartPinnedIndex"), null);
  cfg.options.onClick({}, [{ datasetIndex: 0, index: 1 }]);
  assert.equal(app.run("chartPinnedIndex"), 1);
});

test("longHistoryDots: drops overlap with the daily series and malformed rows; keeps order; karat", () => {
  const first = Date.parse("2026-06-01T11:30:00.000Z");
  const doc = { samples: [
    sample("2026-05", 14052, 11), sample("2022-01", 4400, 2),
    sample("2026-06", 14100, 5),                       // overlaps the daily series: dropped
    { month: "x", timestamp: "garbage", "22k": 1, n: 1 }, // bad date
    { month: "y", timestamp: "2023-01-15T11:30:00.000Z", "22k": "9", n: 1 }, // bad value
    { month: "z", timestamp: "2023-02-15T11:30:00.000Z", "22k": 9000, n: 0 }, // bad n
  ] };
  const dots = longHistoryDots(doc, first, 22);
  assert.deepEqual(dots.map((d) => d.month), ["2022-01", "2026-05"]);
  assert.deepEqual(longHistoryDots(doc, first, 24).map((d) => d.y), [Math.round(4400 * 24 / 22), Math.round(14052 * 24 / 22)]);
  assert.deepEqual(longHistoryDots(null, first), []);
  assert.deepEqual(longHistoryDots({ samples: "no" }, first), []);
});

test("the flag is ON in flags.js (GG's decision 2026-11-13; changing it needs a SW VERSION bump)", () => {
  const flags = fs.readFileSync(new URL("../flags.js", import.meta.url), "utf8");
  assert.match(flags, /^\s*long_history:\s*true,/m);
  assert.doesNotMatch(flags, /long_history:\s*false/);
});
