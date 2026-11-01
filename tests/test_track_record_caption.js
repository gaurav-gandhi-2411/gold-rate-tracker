// tests/test_track_record_caption.js
// Brief B2 (2026-11-01): the track-record caption said "Days before 7 October are the same method
// run again on past data; later days are estimates made at the time." That was a hand-typed date:
// data/backtest.json's p3_past_errors.rows then held 14 October as a re-run day (retro: true) after
// 7 October, and once the 30-day window moves past 7 October the sentence describes nothing on the
// chart. The caption now counts the plotted days by their retro flag. Drives the REAL
// renderForecastVsActual (app.js) and the REAL trackRecordCaption (i18n.js) via tests/helpers.
//
// Run: node --test tests/test_track_record_caption.js  (from repo root)

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

import { loadApp } from "./helpers/load_app.js";

const NOW = Date.parse("2026-11-01T12:00:00Z");
const FRESH = new Date(NOW - 3 * 86_400_000).toISOString();

const row = (day, retro, err = 10) => ({ day, err, in_range: true, retro });

function caption(rows, lang = "en") {
  const app = loadApp({ nowMs: NOW, lang });
  try {
    app.Chart = class { constructor() {} destroy() {} };
    app.getComputedStyle = () => ({ getPropertyValue: () => "" });
    const section = app.element("section-track-record");
    const cap = { textContent: "(unset)" };
    section.querySelector = (sel) => (sel === ".track-record-caption" ? cap : null);
    app.renderForecastVsActual({ p3_past_errors: { rows }, backtest_run_at: FRESH });
    return cap.textContent;
  } finally {
    app.dispose();
  }
}

test("mixed days: counts re-run and live days, including a re-run day after the first live day", () => {
  const rows = [
    row("2026-10-05", true), row("2026-10-06", true), row("2026-10-07", false),
    row("2026-10-08", false), row("2026-10-14", true), row("2026-10-15", false),
  ];
  const text = caption(rows);
  assert.match(text, /3 of these 6 days are the same method run again on past data; the other 3 are estimates made at the time\./);
  assert.doesNotMatch(text, /7 October/);
  assert.match(text, /^In a test on past days: /);
  assert.match(text, /This is a look back, not today's estimate\.$/);
});

test("all live: says so with the count", () => {
  const text = caption([row("2026-10-20", false), row("2026-10-21", false), row("2026-10-22", false)]);
  assert.match(text, /All 3 days are estimates made at the time\./);
});

test("all re-run: says so with the count", () => {
  const text = caption([row("2026-09-01", true), row("2026-09-02", true), row("2026-09-03", true)]);
  assert.match(text, /All 3 days are the same method run again on past data\./);
});

test("fail closed: a day with no retro flag is counted as a re-run, never as live", () => {
  const text = caption([row("2026-10-20", false), row("2026-10-21", undefined), row("2026-10-22", null)]);
  assert.match(text, /2 of these 3 days are the same method run again on past data; the other one is an estimate made at the time\./);
});

test("singular: one re-run day reads 'is', in English and Hindi", () => {
  const rows = [row("2026-10-20", true), row("2026-10-21", false), row("2026-10-22", false)];
  assert.match(caption(rows), /1 of these 3 days is the same method run again on past data; the other 2 are estimates/);
  assert.match(caption(rows, "hi"), /इन 3 दिनों में से 1 दिन उसी तरीक़े को पुराने आँकड़ों पर दोबारा चलाकर निकाला गया है; बाकी 2 दिन/);
  const one = [row("2026-10-20", true), row("2026-10-21", true), row("2026-10-22", false)];
  assert.match(caption(one, "hi"), /बाकी 1 दिन उस समय किया गया अनुमान है।/);
});

test("only the plotted days count: rows beyond the last 30 are left out", () => {
  const old = Array.from({ length: 10 }, (_, i) => row(`2026-08-${String(i + 10).padStart(2, "0")}`, true));
  const recent = Array.from({ length: 30 }, (_, i) => row(`2026-09-${String(i + 1).padStart(2, "0")}`, false));
  assert.match(caption([...old, ...recent]), /All 30 days are estimates made at the time\./);
});

test("Hindi: the same counts in the consensus wording", () => {
  const rows = [row("2026-10-06", true), row("2026-10-07", false), row("2026-10-14", true), row("2026-10-15", false)];
  const text = caption(rows, "hi");
  assert.match(text, /इन 4 दिनों में से 2 दिन उसी तरीक़े को पुराने आँकड़ों पर दोबारा चलाकर निकाले गए हैं; बाकी 2 दिन उस समय किए गए अनुमान हैं।/);
  assert.doesNotMatch(text, /7 अक्टूबर/);
});

test("no counts (static data-i18n pass): names no number and no date", () => {
  const app = loadApp({ nowMs: NOW });
  try {
    const t = app.pure("t");
    const text = t("trackRecordCaption");
    assert.match(text, /Some days are the same method run again on past data; the others are estimates made at the time\./);
    assert.doesNotMatch(text, /\d/);
  } finally {
    app.dispose();
  }
});

test("index.html's static caption matches the no-count English string", () => {
  const html = fs.readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const m = html.match(/data-i18n="trackRecordCaption">([^<]+)<\/p>/);
  assert.ok(m, "caption element not found");
  const app = loadApp({ nowMs: NOW });
  try {
    assert.equal(m[1], app.pure("t")("trackRecordCaption"));
  } finally {
    app.dispose();
  }
});
