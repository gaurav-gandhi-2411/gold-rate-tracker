// tests/test_vol_regime.js
// P3a/P3b (audit 2026-09): volCtx.regime and driver-context's premium/driver
// fields must never silently substitute a specific claim ("normal" regime,
// "0% change", "everything flat") for a field that's actually absent —
// audit finding (e) + its two siblings found in the same sweep. Functions
// inlined from app.js since app.js has no module system (see test_good_price.js
// for the established pattern).
//
// Run: node --test tests/test_vol_regime.js  (from repo root)

import { test } from "node:test";
import assert from "node:assert/strict";

// ── Inline copy of app.js's regime-selection branch (renderOutlook, ~line 1429) ──
// Must match app.js exactly. Returns a key instead of calling t(), so tests
// don't need the i18n catalogue.
function selectVolNoteKey(volCtx) {
  if (volCtx && typeof volCtx.half_width === "number" && !volCtx.is_degraded) {
    const regime = volCtx.regime;
    if (regime === "elevated") return "volNoteElevated";
    if (regime === "calm") return "volNoteCalm";
    if (regime === "normal") return "volNoteNormal";
    return "volNoteFallback"; // absent/unrecognized -- no regime claim
  }
  return "volNoteFallback"; // degraded or absent half_width
}

test("selectVolNoteKey: regime='elevated' -> elevated note", () => {
  assert.equal(selectVolNoteKey({ half_width: 365, is_degraded: false, regime: "elevated" }), "volNoteElevated");
});

test("selectVolNoteKey: regime='calm' -> calm note", () => {
  assert.equal(selectVolNoteKey({ half_width: 365, is_degraded: false, regime: "calm" }), "volNoteCalm");
});

test("selectVolNoteKey: regime='normal' -> normal note", () => {
  assert.equal(selectVolNoteKey({ half_width: 365, is_degraded: false, regime: "normal" }), "volNoteNormal");
});

test("selectVolNoteKey: regime field ABSENT (stale cached forecast.json) -> fallback, NOT normal", () => {
  const volCtx = { half_width: 365, is_degraded: false }; // no `regime` key at all
  assert.equal(selectVolNoteKey(volCtx), "volNoteFallback");
});

test("selectVolNoteKey: regime is an unrecognized string -> fallback, NOT normal", () => {
  const volCtx = { half_width: 365, is_degraded: false, regime: "unknown_future_value" };
  assert.equal(selectVolNoteKey(volCtx), "volNoteFallback");
});

test("selectVolNoteKey: half_width missing entirely -> fallback", () => {
  assert.equal(selectVolNoteKey({}), "volNoteFallback");
});

// ── Inline copy of app.js's driver-context guard + three-branch logic ──────────
// (renderDriverContext, ~line 1525). Must match app.js exactly.
function driverStateBranch(ds, w30) {
  if (!ds || typeof ds.usd_inr_30d_pct_change !== "number" || typeof ds.gold_usd_30d_pct_change !== "number") {
    return "HIDDEN";
  }
  const _DC_DRIVER_THRESHOLD_PCT = 2.0;
  const _DC_PREMIUM_THRESHOLD_PCT = 1.0;

  const inrPct  = ds.usd_inr_30d_pct_change;
  const goldPct = ds.gold_usd_30d_pct_change;
  const premAvailable = w30 && typeof w30.delta_pct_premium === "number";
  const premPct30 = premAvailable ? w30.delta_pct_premium : 0;

  const inrMoved  = Math.abs(inrPct)  > _DC_DRIVER_THRESHOLD_PCT;
  const goldMoved = Math.abs(goldPct) > _DC_DRIVER_THRESHOLD_PCT;
  const premMoved = premAvailable && Math.abs(premPct30) > _DC_PREMIUM_THRESHOLD_PCT;

  if (inrMoved || goldMoved) return "MOVED";
  if (premMoved) return "driverPremiumDominated";
  if (!premAvailable) return "driverStateUnavailable";
  return "driverAllFlat";
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
