// tests/test_vol_regime.js
// P3a/P3b (audit 2026-09): volCtx.regime and driver-context's premium/driver
// fields must never silently substitute a specific claim ("normal" regime,
// "0% change", "everything flat") for a field that's actually absent —
// audit finding (e) + its two siblings found in the same sweep. Drives the REAL
// renderModelSignal / renderDriverContext from app.js (tests/helpers/load_app.js);
// the branches used to be pasted here as copies ("must match app.js exactly").
//
// Run: node --test tests/test_vol_regime.js  (from repo root)

import { test } from "node:test";
import assert from "node:assert/strict";

import { loadApp } from "./helpers/load_app.js";

const SENTINEL = "\u0000";
const reEscape = (x) => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
// Exact-match a rendered string against an i18n template, ignoring only the {param} slot.
function matchesTemplate(app, text, key, params) {
  const tpl = app.t(key, params);
  return new RegExp("^" + reEscape(tpl).replace(reEscape(SENTINEL), ".*") + "$", "s").test(text);
}

// Enough daily readings for computeGoodPriceSignals to return a verdict, so renderModelSignal
// reaches the volatility note.
function dailyReadings(n = 45) {
  const out = [];
  for (let i = n - 1; i >= 0; i--) {
    const ts = new Date(Date.now() - i * 86_400_000).toISOString();
    out.push({ timestamp: ts, "22k": 14000 + (n - i) * 5, "24k": 15200, "18k": 11400 });
  }
  return out;
}

// Runs the real renderModelSignal and reports which volatility-note template it rendered.
function selectVolNoteKey(volCtx) {
  const app = loadApp();
  try {
    const fc = { headline: { lower: 13800, upper: 14200, conformal_pi_half: 200, vol_context: volCtx } };
    app.renderModelSignal(fc, dailyReadings(), null, null, null);
    const html = app.element("model-signal-body").innerHTML;
    const m = html.match(/<p class="outlook-volatility-note">([\s\S]*?)<\/p>/);
    if (!m) return null;
    for (const key of ["volNoteElevated", "volNoteCalm", "volNoteNormal", "volNoteFallback"]) {
      if (matchesTemplate(app, m[1], key, { z: SENTINEL })) return key;
    }
    throw new Error(`volatility note matches no known template: ${m[1]}`);
  } finally {
    app.dispose();
  }
}

test("selectVolNoteKey: regime='elevated' -> elevated note", () => {
  assert.equal(selectVolNoteKey({ typical_move_5d: 185, is_degraded: false, regime: "elevated" }), "volNoteElevated");
});

test("selectVolNoteKey: regime='calm' -> calm note", () => {
  assert.equal(selectVolNoteKey({ typical_move_5d: 185, is_degraded: false, regime: "calm" }), "volNoteCalm");
});

test("selectVolNoteKey: regime='normal' -> normal note", () => {
  assert.equal(selectVolNoteKey({ typical_move_5d: 185, is_degraded: false, regime: "normal" }), "volNoteNormal");
});

test("selectVolNoteKey: regime field ABSENT (stale cached forecast.json) -> fallback, NOT normal", () => {
  const volCtx = { typical_move_5d: 185, is_degraded: false }; // no `regime` key at all
  assert.equal(selectVolNoteKey(volCtx), "volNoteFallback");
});

test("selectVolNoteKey: regime is an unrecognized string -> fallback, NOT normal", () => {
  const volCtx = { typical_move_5d: 185, is_degraded: false, regime: "unknown_future_value" };
  assert.equal(selectVolNoteKey(volCtx), "volNoteFallback");
});

test("selectVolNoteKey: vol_context empty -> NO note (never a stand-in number)", () => {
  assert.equal(selectVolNoteKey({}), null);
});

// 2026-09-25: the note must show the MEASURED typical move, never the floored
// one-standard-deviation half_width it used to show (overstated ~1.9x).
test("volatility note: typical_move_5d absent (cached pre-fix forecast.json) -> NO note", () => {
  assert.equal(selectVolNoteKey({ half_width: 365, is_degraded: false, regime: "normal" }), null);
});

test("volatility note: typical_move_5d null (too few pairs) -> NO note", () => {
  const volCtx = { half_width: 365, is_degraded: false, regime: "normal", typical_move_5d: null };
  assert.equal(selectVolNoteKey(volCtx), null);
});

test("volatility note: degraded estimate -> NO note, even with static_pi_half present", () => {
  const volCtx = { half_width: 730, static_pi_half: 729.8, is_degraded: true, typical_move_5d: 185 };
  assert.equal(selectVolNoteKey(volCtx), null);
});

test("volatility note: shows typical_move_5d rounded to Rs.10, not half_width", () => {
  const app = loadApp();
  try {
    const volCtx = { half_width: 365, is_degraded: false, regime: "normal", typical_move_5d: 185 };
    const fc = { headline: { lower: 13800, upper: 14200, conformal_pi_half: 200, vol_context: volCtx } };
    app.renderModelSignal(fc, dailyReadings(), null, null, null);
    const html = app.element("model-signal-body").innerHTML;
    const m = html.match(/<p class="outlook-volatility-note">([\s\S]*?)<\/p>/);
    assert.ok(m, "note rendered");
    assert.equal(m[1], app.t("volNoteNormal", { z: app.pure("fmtINR")(190) }));
    assert.ok(!m[1].includes("365") && !m[1].includes("350"), "half_width must not leak into the note");
  } finally {
    app.dispose();
  }
});

// Runs the real renderDriverContext and reports which driver-state branch it rendered.
function driverStateBranch(ds, w30) {
  const app = loadApp();
  try {
    const fc = { driver_context: { macro_fresh: true, windows: { "7d": undefined, "30d": w30 }, driver_state: ds } };
    app.renderDriverContext(fc);
    if (app.element("driver-context-section").hidden) return "HIDDEN";
    const m = app.element("driver-context-body").innerHTML.match(/<p class="driver-state">([\s\S]*?)<\/p>/);
    if (!m) throw new Error("real renderDriverContext rendered no driver-state text");
    for (const key of ["driverPremiumDominated", "driverStateUnavailable", "driverAllFlat"]) {
      if (m[1] === app.t(key)) return key;
    }
    return "MOVED"; // any of the rupee/gold "moved" sentences
  } finally {
    app.dispose();
  }
}

test("driverStateBranch: ds absent -> section hidden", () => {
  assert.equal(driverStateBranch(null, { delta_pct_premium: 0.1 }), "HIDDEN");
});

test("driverStateBranch: ds.usd_inr_30d_pct_change missing -> section hidden, not '0% change'", () => {
  const ds = { gold_usd_30d_pct_change: 0.5 }; // usd_inr field absent
  assert.equal(driverStateBranch(ds, { delta_pct_premium: 0.1 }), "HIDDEN");
});

test("driverStateBranch: drivers muted, premium genuinely measured flat -> driverAllFlat", () => {
  const ds = { usd_inr_30d_pct_change: 0.1, gold_usd_30d_pct_change: 0.1 };
  assert.equal(driverStateBranch(ds, { delta_pct_premium: 0.1 }), "driverAllFlat");
});

test("driverStateBranch: drivers muted, premium window unmeasured (null) -> driverStateUnavailable, NOT driverAllFlat", () => {
  const ds = { usd_inr_30d_pct_change: 0.1, gold_usd_30d_pct_change: 0.1 };
  const w30 = { delta_pct_premium: null }; // ml/drivers.py's degraded/insufficient-data shape
  assert.equal(driverStateBranch(ds, w30), "driverStateUnavailable");
});

test("driverStateBranch: drivers muted, w30 window itself absent -> driverStateUnavailable, NOT driverAllFlat", () => {
  const ds = { usd_inr_30d_pct_change: 0.1, gold_usd_30d_pct_change: 0.1 };
  assert.equal(driverStateBranch(ds, undefined), "driverStateUnavailable");
});

test("driverStateBranch: a driver actually moved -> MOVED (unaffected by premium availability)", () => {
  const ds = { usd_inr_30d_pct_change: 3.0, gold_usd_30d_pct_change: 0.1 };
  assert.equal(driverStateBranch(ds, undefined), "MOVED");
});
