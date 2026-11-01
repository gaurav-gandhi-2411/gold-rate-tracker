// tests/test_hwk_range_explainer.js
// Brief B2 (2026-11-01): how-we-know.html said the next-day range was "based on how much the price
// has typically moved by the next check over our last 30 test runs" whatever set the range. The 30
// was hand-typed, and it describes only the older flat range (ml/inference.py _CONFORMAL_FOLDS,
// backtest.json naive_error_recent.folds_window). While the next-rate model is active the range is
// ml/nextfix.py's band (its recent misses, scaled by current volatility), so the page now says that.
// It also said India's official rate "is set once each afternoon"; IBJA sets a morning and an
// afternoon rate, and the model estimates the next afternoon rate. Drives the REAL
// renderFullMethodology (tests/helpers/load_how_we_know.js), in English and Hindi.
//
// Run: node --test tests/test_hwk_range_explainer.js  (from repo root)

import { test } from "node:test";
import assert from "node:assert/strict";

import { loadHowWeKnow } from "./helpers/load_how_we_know.js";

const NOW = Date.parse("2026-11-01T12:00:00Z");
const FRESH = new Date(NOW - 5 * 86_400_000).toISOString();

const HEADLINE = { predicted_22k: 12665, lower: 12542, upper: 12788 };
const FC_LEGACY = { headline: HEADLINE };
const FC_MODEL = {
  headline: HEADLINE,
  next_fix: {
    active: true,
    mode: "after_us_close",
    range_record: { coverage: 0.803, n: 142 },
    track_record: { n: 266, mae_model: 61.2, mae_flat: 71.9, mae_change_pct: -14.9 },
  },
};
const BT = {
  backtest_run_at: FRESH,
  mae_5d_avg_naive: 241.07,
  mae_5d_avg_chronos: 288.14,
  wilcoxon_signed_rank_p: 0.0,
  n_folds: 266,
  dir_acc_5d_chronos: 0.4925,
  naive_error_recent: { folds_window: 30, pct: 80, n: 30 },
};
const COVERAGE = { coverage: 0.73, n: 63, generated_at_utc: FRESH };

function render({ fc, bt = BT, lang = "en" }) {
  const ctx = loadHowWeKnow({ nowMs: NOW, lang });
  try {
    ctx.renderFullMethodology(fc, bt, null, COVERAGE, null);
    return ctx.element("how-we-know-body").innerHTML;
  } finally {
    ctx.dispose();
  }
}

test("model active: the explainer describes the model's band, with no hand-typed run count", () => {
  const html = render({ fc: FC_MODEL });
  assert.match(html, /The range comes from how far the official rate has recently landed from our estimate/);
  assert.doesNotMatch(html, /test runs/);
});

test("legacy range: the run count comes from backtest.json's naive_error_recent.folds_window", () => {
  const html = render({ fc: FC_LEGACY, bt: { ...BT, naive_error_recent: { folds_window: 45 } } });
  assert.match(html, /by the next check over our last 45 test runs/);
  assert.match(html, /It's based on just the last 45 test runs, so it's a small sample/);
  assert.doesNotMatch(html, /\b30 test runs/);
});

test("legacy range, window missing: no number is guessed", () => {
  const { naive_error_recent: _drop, ...btNoWindow } = BT;
  const html = render({ fc: FC_LEGACY, bt: btNoWindow });
  assert.match(html, /by the next check over our recent test runs/);
  assert.match(html, /It's based on just the most recent test runs/);
  assert.doesNotMatch(html, /last \d+ test runs/);
});

test("legacy range, no backtest at all: the explainer still renders without a number", () => {
  const html = render({ fc: FC_LEGACY, bt: null });
  assert.match(html, /over our recent test runs/);
});

test("official rate: set twice a working day, the model estimates the next afternoon rate", () => {
  const html = render({ fc: FC_MODEL });
  assert.match(html, /India's official rate is set twice each working day, in the morning and the afternoon/);
  assert.match(html, /estimate the next afternoon rate/);
  assert.doesNotMatch(html, /once each afternoon/);
});

test("Hindi: model explainer, computed count, and the twice-a-day rate", () => {
  const model = render({ fc: FC_MODEL, lang: "hi" });
  assert.match(model, /रेंज इस पर आधारित है कि हाल में आधिकारिक भाव हमारे अनुमान से कितना दूर आया/);
  assert.match(model, /हर कामकाजी दिन दो बार तय होता है, सुबह और दोपहर में/);
  assert.doesNotMatch(model, /दोपहर में एक बार/);
  const legacy = render({ fc: FC_LEGACY, lang: "hi", bt: { ...BT, naive_error_recent: { folds_window: 45 } } });
  assert.match(legacy, /हमारे पिछले 45 टेस्ट में/);
  assert.match(legacy, /यह सिर्फ़ पिछले 45 टेस्ट पर आधारित है/);
  const none = render({ fc: FC_LEGACY, lang: "hi", bt: null });
  assert.match(none, /हमारे हाल के टेस्ट में/);
});
