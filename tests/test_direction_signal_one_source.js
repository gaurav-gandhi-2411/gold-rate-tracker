// tests/test_direction_signal_one_source.js
// GG brief (2026-11-02): how-we-know.html said "Direction signal: On" while P3 below it said there
// was no edge, and the main page said "We don't guess whether prices will rise or fall next".
// What is actually true: the next-rate model's direction is approved (data/direction_promotion_record.json,
// 2026-10-02) and ml/inference.py publishes next_fix.direction.show === true only in
// "after_us_close" mode (from the US close until India's next morning rate); in the two hold modes
// the estimate is the latest official rate and has no direction. P3/P4 are about a different
// model, the 5-day AI model. Every statement about the direction signal now comes from
// i18n.js directionSignalState, so the two pages cannot disagree, and the off text gives the real
// reason. Drives the REAL how-we-know.js and app.js renders, in English and Hindi.
//
// Run: node --test tests/test_direction_signal_one_source.js  (from repo root)

import { test } from "node:test";
import assert from "node:assert/strict";

import { loadApp } from "./helpers/load_app.js";
import { loadHowWeKnow } from "./helpers/load_how_we_know.js";

const NOW = Date.parse("2026-11-02T18:00:00Z");
const FRESH = new Date(NOW - 3 * 3_600_000).toISOString();
const STALE = new Date(NOW - 20 * 86_400_000).toISOString();

// The 2026-11-02 published record: 163 days, 16 of them calls made at the time.
const TRACK = {
  n: 163, n_forward: 16, mae_model: 103.2, mae_flat: 112.1, mae_change_pct: -7.9,
  direction_accuracy: 0.6503, always_up_accuracy: 0.4601, direction_gate_ship: true,
};
// show/prob/mode as ml/inference.py publishes them: after_us_close carries a probability and
// show = gate passed AND promoted; the hold modes carry show false and a null probability.
function fc({
  mode = "after_us_close", show = mode === "after_us_close", prob = 0.62, side = "up",
  gate = true, active = true, at = FRESH, range = true, demoted = false, violations = [],
} = {}) {
  const hold = mode !== "after_us_close";
  return {
    predicted_at: at,
    headline: range ? { predicted_22k: 12825, lower: 12700, upper: 12950 } : { predicted_22k: 12825 },
    chronos_companion: { status: "success" },
    next_fix: {
      active,
      mode,
      track_record: { ...TRACK, direction_gate_ship: gate },
      demotion: { demoted, reasons: demoted ? ["direction floor"] : [] },
      leak_guard: { checked: true, n_inputs: 1, violations },
      direction: hold ? { show, side: "unclear", probability: null } : { show, side, probability: prob },
    },
  };
}
const BT = {
  backtest_run_at: FRESH, mae_5d_avg_naive: 241.07, mae_5d_avg_chronos: 288.14,
  wilcoxon_signed_rank_p: 0.0, n_folds: 266, dir_acc_5d_chronos: 0.4925, dir_acc_5d_naive: 0.5,
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

const RECORD = { n: 163, right: 106, upRight: 75, passed: true, nForward: 16 };

test("state: on only with show === true, a probability in [0, 1], a range, active and fresh", () => {
  const app = loadApp({ nowMs: NOW });
  try {
    const s = app.pure("directionSignalState");
    assert.deepEqual(s(fc(), NOW), { on: true, hold: false, record: RECORD });
    assert.deepEqual(s(fc({ mode: "after_afternoon_rate" }), NOW), { on: false, hold: true, record: RECORD });
    assert.equal(s(fc({ mode: "after_morning_rate" }), NOW).hold, true);
    // gate passed, no promotion record: off, and not the hold (the model is not holding a rate)
    assert.deepEqual(s(fc({ show: false }), NOW), { on: false, hold: false, record: RECORD });
    assert.equal(s(fc({ show: "true" }), NOW).on, false);
    assert.equal(s(fc({ show: 1 }), NOW).on, false);
    for (const prob of [null, Number.NaN, Infinity, 1.7, -0.1, "0.62"]) {
      assert.equal(s(fc({ prob }), NOW).on, false, String(prob));
    }
    assert.equal(s(fc({ prob: 1 }), NOW).on, true);
    assert.equal(s(fc({ range: false }), NOW).on, false); // the main page shows it inside the range line
    const inactive = s(fc({ active: false }), NOW);
    assert.equal(inactive.on, false);
    assert.equal(inactive.hold, false);
    // ml/nextfix.py also falls back to the afternoon hold when the model is demoted or the leak
    // guard found a violation; then the direction does not come back after the US close
    assert.equal(s(fc({ mode: "after_afternoon_rate", demoted: true }), NOW).hold, false);
    assert.equal(s(fc({ mode: "after_afternoon_rate", violations: ["x"] }), NOW).hold, false);
    const stale = s(fc({ mode: "after_afternoon_rate", at: STALE }), NOW);
    assert.equal(stale.on, false);
    assert.equal(stale.hold, false); // out of date is not the normal hold
    assert.deepEqual(s(null, NOW), { on: false, hold: false, record: null });
    assert.equal(s({ next_fix: { track_record: { n: 0, direction_accuracy: 0.6, always_up_accuracy: 0.5 } } }, NOW).record, null);
    assert.equal(s({ next_fix: { track_record: { ...TRACK, direction_accuracy: Number.NaN } } }, NOW).record, null);
    const noFwd = fc();
    delete noFwd.next_fix.track_record.n_forward;
    assert.equal(s(noFwd, NOW).record.nForward, null);
    noFwd.next_fix.track_record.n_forward = 999;
    assert.equal(s(noFwd, NOW).record.nForward, null);
  } finally {
    app.dispose();
  }
});

test("the 2026-11-02 afternoon: Off until the US close, with the real reason and record", () => {
  const page = hwk(fc({ mode: "after_afternoon_rate" }));
  assert.match(page, /Direction signal Status Off until the US market closes/);
  assert.match(page, /Tested on 163 past days it had not seen: right 106 times, against 75 for "gold usually rises"\. 16 of those days were calls made at the time; the rest are re-runs on past data\./);
  assert.match(page, /only from the US market's close until India's next morning rate is set/);
  assert.doesNotMatch(page, /On — for the next price move only/);
  // the wrong reasons of the first draft
  assert.doesNotMatch(page, /tested, not shown|separate approval|so we do not show it/);
  const main = mainPage(fc({ mode: "after_afternoon_rate" }));
  assert.equal(main.state.on, false);
  assert.doesNotMatch(main.signal, /good-price-direction"/);
  assert.match(main.summary, /We say whether the next official rate is more likely to be higher or lower only after the US market closes/);
  assert.doesNotMatch(main.summary, /We don't guess/); // false: it is approved and comes back
});

test("after the US close: On on both pages, and nothing says it is off", () => {
  const page = hwk(fc());
  assert.match(page, /Direction signal Status On — for the next price move only/);
  assert.match(page, /right 106 times, against 75 for "gold usually rises"\. 16 of those days were calls made at the time/);
  assert.match(page, /The re-run days also helped us pick the model/);
  assert.doesNotMatch(page, /Off until|Off right now|Off — /);
  const main = mainPage(fc());
  assert.equal(main.state.on, true);
  assert.match(main.signal, /good-price-direction" data-side="up"/);
  assert.doesNotMatch(main.summary, /We don't guess/);
});

test("P3/P4 are about the 5-day model, compare it with a coin toss, and show in every state", () => {
  for (const f of [fc(), fc({ mode: "after_morning_rate" })]) {
    const page = hwk(f);
    assert.match(page, /About the 5-day AI model/);
    assert.match(page, /Our 5-day AI model called the direction right 49% of the time across 266 test windows; a coin toss would be right about half the time\. We don't claim any edge for it, and it is not what gives the up-or-down call in "Direction signal" above\./);
    assert.match(page, /If the 5-day model started reliably beating a coin toss in testing/);
    // the claim that contradicted the page's own always-up count (75 of 163)
    assert.doesNotMatch(page, /risen far more often|close to as well|not mostly up/);
  }
});

test("out of date data: Off right now, not the hold reason", () => {
  const page = hwk(fc({ mode: "after_afternoon_rate", at: new Date(NOW - 20 * 86_400_000).toISOString() }));
  assert.match(page, /Status Off right now/);
  assert.match(page, /only while our latest data is complete and up to date/);
  assert.doesNotMatch(page, /Off until the US market closes/);
  // after the US close but not shown: not the hold either
  assert.match(hwk(fc({ show: false })), /Status Off right now/);
  for (const f of [fc({ mode: "after_afternoon_rate", demoted: true }),
    fc({ mode: "after_afternoon_rate", violations: ["x"] })]) {
    const page = hwk(f);
    assert.match(page, /Status Off right now/);
    assert.match(page, /and the model is passing its regular checks/);
    assert.doesNotMatch(page, /comes back after the next US close/);
    assert.match(mainPage(f).summary, /We are not saying right now whether prices are more likely to rise or fall\./);
  }
});

test("the two pages agree in every state", () => {
  const cases = [
    fc(), fc({ mode: "after_morning_rate" }), fc({ mode: "after_afternoon_rate" }),
    fc({ active: false }), fc({ at: STALE }), fc({ gate: false, show: false }), fc({ show: false }),
    fc({ show: "true" }), fc({ show: 1 }), fc({ prob: null }), fc({ prob: 1.7 }), fc({ range: false }),
    fc({ mode: "after_afternoon_rate", demoted: true }), fc({ mode: "after_morning_rate", violations: ["x"] }),
  ];
  for (const f of cases) {
    for (const lang of ["en", "hi"]) {
      const page = hwk(f, lang);
      const main = mainPage(f, lang);
      const hwkOn = lang === "en" ? /Status On — /.test(page) : /चालू — /.test(page);
      const mainOn = /good-price-direction"/.test(main.signal);
      const label = `${lang} ${JSON.stringify(f.next_fix.direction)} ${f.next_fix.mode} active=${f.next_fix.active}`;
      assert.equal(hwkOn, main.state.on, label);
      assert.equal(mainOn, main.state.on, label);
      const homeHold = /only after the US market closes|सिर्फ़ अमेरिकी बाज़ार बंद होने के बाद/.test(main.summary);
      const hwkHold = /Off until the US market closes|अमेरिकी बाज़ार बंद होने तक बंद/.test(page);
      assert.equal(homeHold, hwkHold, label);
      assert.equal(homeHold, main.state.hold, label);
      const homeOff = /We don't guess|हम यह अंदाज़ा नहीं लगाते|only after the US market closes|सिर्फ़ अमेरिकी बाज़ार बंद होने के बाद|We are not saying right now|अभी हम यह नहीं बता रहे/;
      assert.equal(homeOff.test(main.summary), !main.state.on, label);
      // "we don't guess" only while the model's test has not passed
      assert.equal(/We don't guess|हम यह अंदाज़ा नहीं लगाते/.test(main.summary),
        !main.state.on && !main.state.record?.passed, label);
    }
  }
});

test("a test that did not pass keeps the older Off wording", () => {
  const page = hwk(fc({ gate: false, show: false }));
  assert.match(page, /Off — not yet reliable/);
  assert.doesNotMatch(page, /Off until|Off right now/);
});

test("Hindi: the off reason and the record, with no English fallback in the section", () => {
  const page = hwk(fc({ mode: "after_afternoon_rate" }), "hi");
  assert.match(page, /अमेरिकी बाज़ार बंद होने तक बंद/);
  assert.match(page, /163 पुराने दिनों पर जांचा/);
  assert.match(page, /106 बार सही/);
  assert.match(page, /इनमें से 16 दिनों के अंदाज़े उसी समय लगाए गए थे/);
  assert.match(page, /5 दिन वाले AI मॉडल के बारे में/);
  assert.doesNotMatch(page, /Tested on|re-runs on past data|Off until|Off right now|coin toss|5-day AI/);
  // the main page's off line in Hindi, never the English fallback
  const hold = mainPage(fc({ mode: "after_afternoon_rate" }), "hi").summary;
  assert.match(hold, /अगले आधिकारिक भाव के बढ़ने की संभावना ज़्यादा है या घटने की/);
  assert.doesNotMatch(hold, /We say whether|We don't guess/);
  const paused = mainPage(fc({ mode: "after_afternoon_rate", demoted: true }), "hi").summary;
  assert.match(paused, /अभी हम यह नहीं बता रहे कि भाव बढ़ने की संभावना ज़्यादा है या घटने की/);
  assert.doesNotMatch(paused, /We are not saying/);
  const on = hwk(fc(), "hi");
  assert.match(on, /चालू — सिर्फ़ अगली चाल के लिए/);
  assert.doesNotMatch(on, /Tested on|The chance comes from|re-run days/);
});

test("Hindi: the main page's direction line itself, in every side, with no English fallback", () => {
  const cases = [
    [fc({ side: "up", prob: 0.62 }), /अगले आधिकारिक भाव के <strong>बढ़ने<\/strong> की संभावना ज़्यादा है \(करीब 62% संभावना\)/],
    [fc({ side: "down", prob: 0.7 }), /अगले आधिकारिक भाव के <strong>घटने<\/strong> की संभावना ज़्यादा है \(करीब 70% संभावना\)/],
    [fc({ side: "unclear", prob: 0.5 }), /इतना नहीं बदला कि अगले आधिकारिक भाव के बढ़ने या घटने के बारे में कुछ कहा जा सके/],
  ];
  for (const [f, line] of cases) {
    const { signal } = mainPage(f, "hi");
    assert.match(signal, line);
    assert.match(signal, /रुपये के हिसाब से बदलता है, पर कुछ देर से। हम दुनिया के बाज़ार में सोने के भाव का अंदाज़ा नहीं लगाते।/);
    assert.doesNotMatch(signal, /World gold prices|follows world gold|We do not predict/);
  }
});
