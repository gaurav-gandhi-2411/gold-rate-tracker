// tests/test_direction_signal_one_source.js
// GG brief (2026-11-02): how-we-know.html said "Direction signal: On" because the next-rate model's
// test on past days passed (next_fix.track_record.direction_gate_ship), while the main page showed
// no direction (next_fix.direction.show was false: no promotion record) and said "We don't guess
// whether prices will rise or fall next", and P3/P4 below it said there was no proven edge. Every
// statement about the direction signal now comes from i18n.js directionSignalState, so the two
// pages cannot disagree. Drives the REAL how-we-know.js and app.js renders, in English and Hindi.
//
// Run: node --test tests/test_direction_signal_one_source.js  (from repo root)

import { test } from "node:test";
import assert from "node:assert/strict";

import { loadApp } from "./helpers/load_app.js";
import { loadHowWeKnow } from "./helpers/load_how_we_know.js";

const NOW = Date.parse("2026-11-02T18:00:00Z");
const FRESH = new Date(NOW - 3 * 3_600_000).toISOString();
const STALE = new Date(NOW - 20 * 86_400_000).toISOString();

// The 2026-11-02 published shape: the test passed on 163 re-run days, not shown.
const TRACK = {
  n: 163, mae_model: 103.2, mae_flat: 112.1, mae_change_pct: -7.9,
  direction_accuracy: 0.6503, always_up_accuracy: 0.4601, direction_gate_ship: true,
};
function fc({ show = false, prob = 0.62, side = "up", gate = true, active = true, at = FRESH } = {}) {
  return {
    predicted_at: at,
    headline: { predicted_22k: 12825, lower: 12700, upper: 12950 },
    chronos_companion: { status: "success" },
    next_fix: {
      active,
      mode: "after_us_close",
      track_record: { ...TRACK, direction_gate_ship: gate },
      direction: { show, side, probability: show ? prob : prob },
    },
  };
}
const BT = {
  backtest_run_at: FRESH, mae_5d_avg_naive: 241.07, mae_5d_avg_chronos: 288.14,
  wilcoxon_signed_rank_p: 0.0, n_folds: 266, dir_acc_5d_chronos: 0.4925,
  naive_error_recent: { folds_window: 30, pct: 80, n: 30 },
};

function hwk(forecast, lang = "en") {
  const ctx = loadHowWeKnow({ nowMs: NOW, lang });
  try {
    ctx.renderFullMethodology(forecast, BT, null, null, null);
    return ctx.element("how-we-know-body").innerHTML.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");
  } finally {
    ctx.dispose();
  }
}

function mainPage(forecast, lang = "en") {
  const app = loadApp({ nowMs: NOW, lang });
  try {
    const readings = Array.from({ length: 40 }, (_, i) => ({
      timestamp: new Date(NOW - (39 - i) * 86_400_000).toISOString(), "22k": 12800 + i, "24k": 13900, "18k": 10400,
    }));
    app.renderModelSignal(forecast, readings, null, null, null);
    app.renderAccuracySummary(forecast, null);
    return {
      signal: app.element("model-signal-body").innerHTML,
      summary: app.element("methodology-body").innerHTML,
      state: app.pure("directionSignalState")(forecast, NOW),
    };
  } finally {
    app.dispose();
  }
}

test("state: a passed test alone is not on; on needs show, a probability, active and fresh", () => {
  const app = loadApp({ nowMs: NOW });
  try {
    const s = app.pure("directionSignalState");
    assert.deepEqual(s(fc(), NOW), { on: false, record: { n: 163, right: 106, upRight: 75, passed: true } });
    assert.equal(s(fc({ show: true }), NOW).on, true);
    assert.equal(s(fc({ show: true, active: false }), NOW).on, false);
    assert.equal(s(fc({ show: true, at: STALE }), NOW).on, false);
    const noProb = fc({ show: true });
    noProb.next_fix.direction.probability = null;
    assert.equal(s(noProb, NOW).on, false);
    assert.deepEqual(s(null, NOW), { on: false, record: null });
    assert.equal(s({ next_fix: { track_record: { n: 0, direction_accuracy: 0.6, always_up_accuracy: 0.5 } } }, NOW).record, null);
  } finally {
    app.dispose();
  }
});

test("today's shape: how-we-know says Off and why, never On; the main page shows no direction", () => {
  const page = hwk(fc());
  assert.match(page, /Direction signal Status Off — tested, not shown/);
  assert.match(page, /Tested on 163 past days it had not seen: right 106 times, against 75 for "gold usually rises"\. These are re-runs on past data/);
  assert.doesNotMatch(page, /On — for the next price move only/);
  // P3 is about the 5-day model and P4 no longer says a passed test turns it on
  assert.match(page, /Our 5-day AI model was right 49% of the time across 266 test windows/);
  assert.match(page, /We don't claim any edge for it\./);
  assert.match(page, /kept beating the "gold usually rises" guess and was approved to be shown, we'd turn this on/);
  assert.doesNotMatch(page, /in testing, we'd turn this back on/);
  const main = mainPage(fc());
  assert.equal(main.state.on, false);
  assert.doesNotMatch(main.signal, /good-price-direction"/);
  assert.match(main.summary, /We don't guess whether prices will rise or fall next\./);
});

test("on: how-we-know says On, the main page shows the direction, and nothing says it is off", () => {
  const page = hwk(fc({ show: true }));
  assert.match(page, /Direction signal Status On — for the next price move only/);
  assert.match(page, /right 106 times, against 75/);
  assert.doesNotMatch(page, /Off — /);
  // P3's "no edge" and P4's "we'd turn this on" describe the off state only
  assert.doesNotMatch(page, /We don't claim any edge/);
  assert.doesNotMatch(page, /we'd turn this on/);
  const main = mainPage(fc({ show: true }));
  assert.equal(main.state.on, true);
  assert.match(main.signal, /good-price-direction" data-side="up"/);
  assert.doesNotMatch(main.summary, /We don't guess/);
});

test("the two pages agree in every state", () => {
  const cases = [
    fc(), fc({ show: true }), fc({ show: true, gate: true, active: false }),
    fc({ show: true, at: STALE }), fc({ gate: false }), fc({ show: false, gate: false }),
  ];
  for (const f of cases) {
    const page = hwk(f);
    const main = mainPage(f);
    const hwkOn = /Status On — /.test(page);
    const mainOn = /good-price-direction"/.test(main.signal);
    assert.equal(hwkOn, main.state.on, JSON.stringify(f.next_fix));
    assert.equal(mainOn, main.state.on, JSON.stringify(f.next_fix));
    assert.equal(/We don't guess/.test(main.summary), !main.state.on);
  }
});

test("a test that did not pass keeps the older Off wording", () => {
  const page = hwk(fc({ gate: false }));
  assert.match(page, /Off — not yet reliable/);
  assert.doesNotMatch(page, /tested, not shown/);
});

test("Hindi: Off with the computed counts, and no English fallback in the section", () => {
  const page = hwk(fc(), "hi");
  assert.match(page, /बंद — जांचा गया, दिखाया नहीं जाता/);
  assert.match(page, /163 पुराने दिनों पर जांचा/);
  assert.match(page, /106 बार सही/);
  assert.doesNotMatch(page, /Tested on|re-runs on past data|Off — /);
  const on = hwk(fc({ show: true }), "hi");
  assert.match(on, /चालू — सिर्फ़ अगली चाल के लिए/);
  assert.doesNotMatch(on, /Tested on|The chance comes from/); // methDirectionOnSub / Source in Hindi
});
