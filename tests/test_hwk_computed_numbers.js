// tests/test_hwk_computed_numbers.js
// Brief B2 (2026-11-01): two numbers on how-we-know.html were hand-typed into the copy --
// the backtest's year span ("from 2022–2026") and the claim age limit ("in the last 14 days").
// Both now come from their sources: the span from data/backtest.json's folds[].context_end_date,
// the age limit from i18n.js's CLAIM_MAX_AGE_DAYS. These tests drive the REAL
// renderFullMethodology (tests/helpers/load_how_we_know.js), in English and Hindi.
//
// Run: node --test tests/test_hwk_computed_numbers.js  (from repo root)

import { test } from "node:test";
import assert from "node:assert/strict";

import { loadHowWeKnow } from "./helpers/load_how_we_know.js";

const DAY_MS = 86_400_000;
const NOW = Date.parse("2026-11-01T12:00:00Z");
const isoDaysAgo = (days) => new Date(NOW - days * DAY_MS).toISOString();

const FC_WITH_RANGE = { headline: { predicted_22k: 14000, lower: 13800, upper: 14200 } };
const BT_BASE = {
  backtest_run_at: isoDaysAgo(5),
  mae_5d_avg_naive: 241.07,
  mae_5d_avg_chronos: 288.14,
  wilcoxon_signed_rank_p: 0.0,
  n_folds: 266,
  dir_acc_5d_chronos: 0.4925,
};
const folds = (...dates) => dates.map((d, i) => ({ fold_id: i, context_end_date: d }));

function render({ fc = FC_WITH_RANGE, bt = null, bandCoverage = null, lang = "en" } = {}) {
  const ctx = loadHowWeKnow({ nowMs: NOW, lang });
  try {
    ctx.renderFullMethodology(fc, bt, null, null, bandCoverage);
    return ctx.element("how-we-know-body").innerHTML;
  } finally {
    ctx.dispose();
  }
}

test("year span comes from the folds: first and last context_end_date years", () => {
  const html = render({ bt: { ...BT_BASE, folds: folds("2022-03-15", "2024-01-02", "2026-10-15") } });
  assert.match(html, /Tested over 266 time windows from 2022–2026:/);
});

test("year span follows the data: folds starting in 2023 say 2023, not a hand-typed 2022", () => {
  const html = render({ bt: { ...BT_BASE, folds: folds("2023-05-01", "2027-02-01") } });
  assert.match(html, /from 2023–2027:/);
  assert.doesNotMatch(html, /2022/);
});

test("year span: all folds in one year prints that year alone", () => {
  const html = render({ bt: { ...BT_BASE, folds: folds("2026-01-05", "2026-10-15") } });
  assert.match(html, /time windows from 2026:/);
});

test("year span: no usable fold dates drops the span instead of guessing one", () => {
  for (const f of [undefined, [], [{ fold_id: 0 }], [{ context_end_date: "bad" }]]) {
    const html = render({ bt: { ...BT_BASE, folds: f } });
    assert.match(html, /Tested over 266 time windows:/);
    assert.doesNotMatch(html, /time windows from/);
  }
});

test("year span, Hindi: the same computed span, wording otherwise unchanged", () => {
  const html = render({ lang: "hi", bt: { ...BT_BASE, folds: folds("2022-03-15", "2026-10-15") } });
  assert.match(html, /2022–2026 के बीच 266 अलग-अलग समय-खंडों पर टेस्ट किया गया/);
  const none = render({ lang: "hi", bt: { ...BT_BASE, folds: [] } });
  assert.match(none, /निकले। 266 अलग-अलग समय-खंडों पर/);
  assert.doesNotMatch(none, /के बीच 266/);
});

const FC_WITH_BAND = { nominal_coverage: 80, band_half_width: 245.7 };
const STALE_BAND = { coverage: 0.709, n: 86, generated_at_utc: isoDaysAgo(30) };

test("band accuracy unknown: the day count is i18n.js's CLAIM_MAX_AGE_DAYS", async () => {
  const fs = await import("node:fs");
  const i18n = fs.readFileSync(new URL("../i18n.js", import.meta.url), "utf8");
  const days = Number(i18n.match(/const CLAIM_MAX_AGE_DAYS = (\d+);/)[1]);
  const html = render({ fc: FC_WITH_BAND, bandCoverage: STALE_BAND });
  assert.match(html, new RegExp(`No measurement in the last ${days} days`));
  const caller = fs.readFileSync(new URL("../how-we-know.js", import.meta.url), "utf8");
  assert.match(caller, /tHwk\("methBandAccuracyUnknown", \{ days: CLAIM_MAX_AGE_DAYS \}\)/);
});

test("band accuracy unknown, Hindi: same computed day count", () => {
  const html = render({ lang: "hi", fc: FC_WITH_BAND, bandCoverage: STALE_BAND });
  assert.match(html, /पिछले 14 दिनों में कोई माप नहीं हुआ/);
});

test("no hand-typed year span or day limit is left in the two templates", async () => {
  const fs = await import("node:fs");
  const src = fs.readFileSync(new URL("../how-we-know-strings.js", import.meta.url), "utf8");
  for (const key of ["methAccurateP1", "methBandAccuracyUnknown"]) {
    const lines = src.split("\n").filter((l, i, a) => l.includes(`${key}:`) || (i > 0 && a[i - 1].includes(`${key}:`) && !a[i - 1].includes("`")));
    for (const l of lines) assert.doesNotMatch(l, /2022–2026|\b14 (days|दिनों)/, `${key} still hand-types a number: ${l.slice(0, 80)}`);
  }
});
