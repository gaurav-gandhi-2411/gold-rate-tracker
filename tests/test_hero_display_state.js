// tests/test_hero_display_state.js — E2 (GG decision 2026-09-25) + ADR 059 display gates.
//
// Drives the REAL renderHero()/heroDisplayState() from app.js (tests/helpers/load_app.js)
// and asserts the exact hero label text, EN and HI, for every state the hero can be in:
// fresh Tanishq, not fresh (<=36h / >36h), blocked for days, no estimate (inference tier 4),
// Tanishq disabled (IBJA-derived history), implausible reading, fusion estimate. Then a
// property test over a grid of all states asserts the one invariant that matters: an
// estimate is NEVER labelled as Tanishq's price, and no Tanishq-named figure is shown that
// fails the 12% gate.
//
// GG decisions 4b/4c (2026-09-25), on top of E2: a Tanishq reading past 36 h is still shown
// WITH its figure, date and time (never a date alone); "today's change" is Tanishq-vs-
// previous-Tanishq only and is shown only when the hero shows Tanishq's own latest reading;
// the trend chart plots the IBJA-based estimate series, labelled as an estimate.
//
// Run: node --test tests/test_hero_display_state.js   (from repo root)

import { test } from "node:test";
import assert from "node:assert/strict";

import { loadApp } from "./helpers/load_app.js";

// 2026-09-25 10:40 IST = 05:10 UTC.
const NOW = Date.parse("2026-09-25T05:10:00Z");
const HOUR = 3_600_000;
const iso = (hoursAgo) => new Date(NOW - hoursAgo * HOUR).toISOString();
const TANISHQ_RE = /tanishq|तनिष्क/i;

// Two Tanishq rows: the latest `hoursAgo` old at `value`, one 5h before it.
function tanishqRows(hoursAgo, value) {
  return [
    { timestamp: iso(hoursAgo + 5), "22k": value - 20, "24k": value + 1200, "18k": value - 2500 },
    { timestamp: iso(hoursAgo), "22k": value, "24k": value + 1300, "18k": value - 2400 },
  ];
}
function derivedRows(value) {
  return [
    { timestamp: iso(30), "22k": value - 40, "24k": value + 1200, "18k": value - 2500, source: "ibja_calibrated_derived" },
    { timestamp: iso(6), "22k": value, "24k": value + 1300, "18k": value - 2400, source: "ibja_calibrated_derived" },
  ];
}
const ibja = (value) => ({ price_source: "ibja_calibrated", current_22k: value, ibja_asof: iso(18), scraped_at: iso(40), est_low: value - 60, est_high: value + 60 });
const fusion = (value) => ({ price_source: "fusion_consensus", current_22k: value, fusion_sources: ["grt", "malabar"], scraped_at: iso(40) });
const tier1 = (value, scrapedH) => ({ price_source: "tanishq_scrape", current_22k: value, scraped_at: iso(scrapedH) });

// Render the real hero and read back what a visitor sees.
function render(readings, forecast, lang) {
  const app = loadApp({ nowMs: NOW, lang });
  try {
    app.renderHero(readings, forecast);
    const loc = app.element("hero-location");
    const line = app.element("hero-last-confirmed");
    const change = app.element("hero-change");
    return {
      price: String(app.element("hero-price").innerHTML).replace(/<[^>]+>/g, ""),
      label: loc.hidden ? null : loc.textContent,
      line: line.hidden ? null : line.textContent,
      changeShown: !change.hidden,
      kind: app.pure("heroDisplayState")(readings, forecast, NOW).kind,
    };
  } finally {
    app.dispose();
  }
}

// ── State table: exact label text, EN + HI ─────────────────────────────────────
const STATES = [
  {
    name: "fresh Tanishq (1h old, plausible)",
    readings: tanishqRows(1, 14040), forecast: tier1(14040, 1),
    en: { price: "₹14,040", label: "Tanishq's listed 22K rate, checked 9:40 AM today", line: null },
    hi: { price: "₹14,040", label: "Tanishq की सूचीबद्ध 22K दर, आज 9:40 am जांची गई", line: null },
  },
  {
    name: "fresh Tanishq newer than an estimate-tier forecast.json",
    readings: tanishqRows(2, 14040), forecast: ibja(14100),
    en: { price: "₹14,040", label: "Tanishq's listed 22K rate, checked 8:40 AM today", line: null },
    hi: { price: "₹14,040", label: "Tanishq की सूचीबद्ध 22K दर, आज 8:40 am जांची गई", line: null },
  },
  {
    name: "not fresh (20h) -> estimate + dated Tanishq figure",
    readings: tanishqRows(20, 14040), forecast: ibja(14100),
    en: { price: "≈ ₹14,100", label: "Our estimate for today, based on the IBJA rate", line: "Tanishq's listed rate, checked 2:40 PM yesterday: ₹14,040" },
    hi: { price: "≈ ₹14,100", label: "आज के लिए हमारा अनुमान, IBJA दर पर आधारित", line: "Tanishq की सूचीबद्ध दर, कल 2:40 pm जांची गई: ₹14,040" },
  },
  {
    name: "stale 40h (past 36h) -> estimate + old Tanishq figure WITH date and time",
    readings: tanishqRows(40, 14040), forecast: ibja(14100),
    en: { price: "≈ ₹14,100", label: "Our estimate for today, based on the IBJA rate", line: "Tanishq's listed rate when last checked, 6:40 PM, 23 Sept: ₹14,040 — not updated since" },
    hi: { price: "≈ ₹14,100", label: "आज के लिए हमारा अनुमान, IBJA दर पर आधारित", line: "Tanishq की सूचीबद्ध दर, आख़िरी बार 23 सित॰, 6:40 pm जांची गई: ₹14,040 — तब से अपडेट नहीं हुई" },
  },
  {
    name: "blocked for days (Tanishq 5 days old) -> estimate + old Tanishq figure WITH date and time",
    readings: tanishqRows(120, 13900), forecast: ibja(14100),
    en: { price: "≈ ₹14,100", label: "Our estimate for today, based on the IBJA rate", line: "Tanishq's listed rate when last checked, 10:40 AM, 20 Sept: ₹13,900 — not updated since" },
    hi: { price: "≈ ₹14,100", label: "आज के लिए हमारा अनुमान, IBJA दर पर आधारित", line: "Tanishq की सूचीबद्ध दर, आख़िरी बार 20 सित॰, 10:40 am जांची गई: ₹13,900 — तब से अपडेट नहीं हुई" },
  },
  {
    name: "fusion estimate (Tanishq + IBJA both down)",
    readings: tanishqRows(20, 14040), forecast: fusion(14080),
    en: { price: "≈ ₹14,080", label: "Our estimate for today, based on other jewellers' listed rates", line: "Tanishq's listed rate, checked 2:40 PM yesterday: ₹14,040" },
    hi: { price: "≈ ₹14,080", label: "आज के लिए हमारा अनुमान, दूसरे जौहरियों की सूचीबद्ध दरों पर आधारित", line: "Tanishq की सूचीबद्ध दर, कल 2:40 pm जांची गई: ₹14,040" },
  },
  {
    name: "no estimate this cycle (inference tier 4) -> last Tanishq reading, dated",
    readings: tanishqRows(30, 14040), forecast: tier1(14040, 30),
    en: { price: "₹14,040", label: "Tanishq's listed 22K rate, last checked 4:40 AM yesterday", line: null },
    hi: { price: "₹14,040", label: "Tanishq की सूचीबद्ध 22K दर, आख़िरी बार कल 4:40 am जांची गई", line: null },
  },
  {
    name: "Tanishq disabled (takedown: IBJA-derived history) -> estimate, no Tanishq name",
    readings: derivedRows(14100), forecast: ibja(14100),
    en: { price: "≈ ₹14,100", label: "Our estimate for today, based on the IBJA rate", line: null },
    hi: { price: "≈ ₹14,100", label: "आज के लिए हमारा अनुमान, IBJA दर पर आधारित", line: null },
  },
  {
    name: "implausible fresh reading (+20% vs IBJA estimate) -> estimate, reading not shown",
    readings: tanishqRows(1, 16920), forecast: ibja(14100),
    en: { price: "≈ ₹14,100", label: "Our estimate for today, based on the IBJA rate", line: null },
    hi: { price: "≈ ₹14,100", label: "आज के लिए हमारा अनुमान, IBJA दर पर आधारित", line: null },
  },
  {
    name: "implausible newer reading on a Tanishq-tier cycle -> the inference-gated reading",
    readings: tanishqRows(1, 17000), forecast: tier1(14040, 3),
    en: { price: "₹14,040", label: "Tanishq's listed 22K rate, checked 7:40 AM today", line: null },
    hi: { price: "₹14,040", label: "Tanishq की सूचीबद्ध 22K दर, आज 7:40 am जांची गई", line: null },
  },
];

for (const st of STATES) {
  for (const lang of ["en", "hi"]) {
    test(`hero state [${lang}]: ${st.name}`, () => {
      const got = render(st.readings, st.forecast, lang);
      assert.equal(got.price, st[lang].price);
      assert.equal(got.label, st[lang].label);
      assert.equal(got.line, st[lang].line);
    });
  }
}

test("today's change: shown only when the hero shows Tanishq's own latest reading", () => {
  // Hero = fresh Tanishq, or last Tanishq with no estimate (tier 4): shown.
  assert.equal(render(tanishqRows(1, 14040), tier1(14040, 1), "en").changeShown, true);
  assert.equal(render(tanishqRows(30, 14040), tier1(14040, 30), "en").changeShown, true);
  // Every estimate hides it: stale 20h / 40h / 5 days, implausible, fusion, and a takedown's
  // IBJA-derived history (GG 4b: never a change next to an estimate).
  assert.equal(render(tanishqRows(20, 14040), ibja(14100), "en").changeShown, false);
  assert.equal(render(tanishqRows(40, 14040), ibja(14100), "en").changeShown, false);
  assert.equal(render(tanishqRows(120, 13900), ibja(14100), "en").changeShown, false);
  assert.equal(render(tanishqRows(1, 16920), ibja(14100), "en").changeShown, false);
  assert.equal(render(tanishqRows(20, 14040), fusion(14080), "en").changeShown, false);
  assert.equal(render(derivedRows(14100), ibja(14100), "en").changeShown, false);
  assert.equal(render(derivedRows(14100), null, "en").changeShown, false);
  // Hero = the inference-gated value (the newer row failed the gate): that figure is not the
  // latest row, so a change computed from the rows would describe another reading -> hidden.
  assert.equal(render(tanishqRows(1, 17000), tier1(14040, 3), "en").changeShown, false);
});

// Reads the real renderHero's change row: the stub element keeps whatever is assigned to it,
// so give #hero-change a querySelector that returns remembered sub-elements.
function renderChange(readings, forecast) {
  const app = loadApp({ nowMs: NOW, lang: "en" });
  try {
    const sub = {};
    const el = app.element("hero-change");
    el.querySelector = (sel) => (sub[sel] ||= { textContent: "" });
    app.renderHero(readings, forecast);
    return el.hidden ? null : { amount: sub[".hero-change-amount"].textContent, label: sub[".hero-change-label"].textContent };
  } finally {
    app.dispose();
  }
}

test("today's change is Tanishq-vs-previous-Tanishq only: an IBJA-derived row in between is ignored (no false +Rs.24)", () => {
  // Previous Tanishq 14,000 (30h ago), a derived estimate row 14,016 (20h ago), latest Tanishq
  // 14,040 (1h ago). Mixing would give +₹24 (14,040 - 14,016); the real Tanishq move is +₹40.
  const rows = [
    { timestamp: iso(30), "22k": 14000, "24k": 15270, "18k": 11455 },
    { timestamp: iso(20), "22k": 14016, "24k": 15290, "18k": 11467, source: "ibja_calibrated_derived" },
    { timestamp: iso(1), "22k": 14040, "24k": 15316, "18k": 11487 },
  ];
  assert.deepEqual(renderChange(rows, tier1(14040, 1)), { amount: "+₹40", label: "since last" });
  // Same rows, but the hero shows an estimate: no change at all.
  assert.equal(renderChange(rows.slice(0, 2), ibja(14100)), null);
});

// ── The public Tanishq set (GG 4c): what must stay public for "today's change" ──
// DEFINITION (for #2075's migration): public data/prices.json keeps, per the rules below, only
// real Tanishq rows (fields timestamp, 22k, 24k, 18k, source):
//   * every real Tanishq row on the newest reading's IST day (the newest reading itself and,
//     when it has one, today's earliest reading -- "today's" baseline), plus
//   * the last real Tanishq row before that IST day (the "previous" reading: the "since last"
//     baseline, and the 3% day-boundary guard's reference).
// With 6 visits a day that is at most 7 rows. Everything older can be encrypted. This test
// proves the set is sufficient: the real computeTodayChange gives the identical answer on the
// full history and on the public set, for every history shape below.
function publicTanishqSet(rows) {
  const real = rows.filter((r) => !(typeof r.source === "string" && r.source.startsWith("ibja_calibrated")));
  if (!real.length) return [];
  const day = (r) => new Date(r.timestamp).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" });
  const lastDay = day(real[real.length - 1]);
  let i = real.length - 1;
  while (i > 0 && day(real[i - 1]) === lastDay) i--;
  return real.slice(Math.max(0, i - 1));
}

test("public Tanishq set: today's change is identical on the full history and on the public set", () => {
  const app = loadApp({ nowMs: NOW, lang: "en" });
  try {
    const change = app.pure("computeTodayChange");
    const r = (h, v) => ({ timestamp: iso(h), "22k": v, "24k": v + 1276, "18k": v - 2553, source: "tanishq" });
    const shapes = {
      "3 visits today, rate moved at 10:40": [r(60, 13900), r(30, 14000), r(26, 14000), r(9, 14000), r(0.5, 14040), r(0.1, 14040)],
      "single visit today": [r(60, 13900), r(30, 14000), r(26, 14010), r(1, 14040)],
      "no visit today, last two yesterday": [r(60, 13900), r(30, 14000), r(26, 14010)],
      ">3% day-boundary jump (guard)": [r(60, 13900), r(30, 14000), r(9, 14600), r(1, 14650)],
      "derived rows interleaved": [r(60, 13900), { ...r(40, 14016), source: "ibja_calibrated_derived" }, r(30, 14000), r(1, 14040)],
      "only one reading": [r(1, 14040)],
    };
    for (const [name, rows] of Object.entries(shapes)) {
      const real = rows.filter((x) => !x.source.startsWith("ibja_calibrated"));
      const pub = publicTanishqSet(rows);
      assert.ok(pub.length <= 7, name);
      assert.deepEqual(change(pub), change(real), name);
    }
  } finally {
    app.dispose();
  }
});

// ── Chart: the IBJA-based estimate series, labelled as an estimate (GG 4c) ─────
function renderChartWith(readings, derived, lang) {
  const captured = {};
  class FakeChart {
    constructor(_ctx, cfg) { captured.cfg = cfg; }
    destroy() {}
    update() {}
  }
  const app = loadApp({ nowMs: NOW, lang, globals: { Chart: FakeChart, getComputedStyle: () => ({ getPropertyValue: () => "" }) } });
  try {
    app.run(`derivedSeries = ${JSON.stringify(derived)}`);
    app.renderChart(readings, "all");
    const ds = captured.cfg.data.datasets[0];
    const note = app.element("chart-source-note");
    return {
      label: ds.label,
      data: [...ds.data],
      tooltip: captured.cfg.options.plugins.tooltip.callbacks.label({ parsed: { y: ds.data[ds.data.length - 1] } }),
      note: note.hidden ? null : note.textContent,
      estimate: app.run("chartIsEstimate"),
    };
  } finally {
    app.dispose();
  }
}
const DERIVED_SERIES = [
  { timestamp: iso(72), "22k": 14090 },
  { timestamp: iso(48), "22k": 14131 },
  { timestamp: iso(24), "22k": 14016 },
];

for (const lang of ["en", "hi"]) {
  test(`chart [${lang}]: plots the IBJA-based series, labelled as an estimate, never mixed with Tanishq`, () => {
    const got = renderChartWith(tanishqRows(1, 14040), DERIVED_SERIES, lang);
    assert.deepEqual(got.data, [14090, 14131, 14016]); // no 14,040 Tanishq point in the line
    assert.equal(got.estimate, true);
    if (lang === "en") {
      assert.equal(got.label, "22K estimate (₹/g)");
      assert.equal(got.tooltip, "22K estimate: ≈ ₹14,016");
      assert.equal(got.note, "Our estimate, based on the IBJA rate · one point per IBJA working day");
    } else {
      assert.equal(got.label, "22K अनुमान (₹/ग्राम)");
      assert.equal(got.tooltip, "22K अनुमान: ≈ ₹14,016");
      assert.equal(got.note, "हमारा अनुमान, IBJA दर पर आधारित · IBJA के हर कामकाजी दिन का एक बिंदु");
    }
    assert.ok(!TANISHQ_RE.test(`${got.label} ${got.tooltip} ${got.note}`));
  });
}

test("chart: without the derived file it falls back to Tanishq rows only, labelled as Tanishq's", () => {
  const mixed = [...derivedRows(14100).slice(0, 1), ...tanishqRows(1, 14040)];
  for (const derived of [null, [], [{ timestamp: iso(5), "22k": 14000 }], [{ bad: 1 }, { timestamp: iso(5) }]]) {
    const got = renderChartWith(mixed, derived, "en");
    assert.equal(got.estimate, false);
    assert.equal(got.label, "22K (₹/g)");
    assert.equal(got.note, "Tanishq's listed 22K rate, as we checked it");
    assert.ok(got.data.every((v) => v !== 14100 - 40), `derived row leaked into the Tanishq line ${got.data}`);
  }
});

test("chart: a takedown (IBJA-derived prices.json) is plotted as an estimate even without the file", () => {
  const got = renderChartWith(derivedRows(14100), null, "en");
  assert.equal(got.estimate, true);
  assert.equal(got.label, "22K estimate (₹/g)");
  assert.ok(!TANISHQ_RE.test(`${got.label} ${got.note}`));
});

test("empty prices.json keeps the existing '—' path with no label", () => {
  const got = render([], ibja(14100), "en");
  assert.equal(got.price, "—");
  assert.equal(got.label, null);
});

test("the gates mirror ml/inference.py (no second, drifting definition)", async () => {
  const fs = await import("node:fs");
  const py = fs.readFileSync("ml/inference.py", "utf8");
  const app = loadApp({ nowMs: NOW });
  try {
    const dev = Number(py.match(/_TANISHQ_IBJA_MAX_DEVIATION: float = ([\d.]+)/)[1]);
    const age = Number(py.match(/_FUSION_MAX_AGE_H: float = ([\d.]+)/)[1]);
    const stale = Number(py.match(/_STALE_THRESHOLD_H: int = (\d+)/)[1]);
    assert.equal(app.run("TANISHQ_IBJA_MAX_DEVIATION"), dev);
    assert.equal(app.run("RETAILER_READING_MAX_AGE_H"), age);
    assert.equal(app.run("STALE_THRESHOLD_H"), stale);
  } finally {
    app.dispose();
  }
});

// ── Property test: every combination, both languages ───────────────────────────
test("property: an estimate is never labelled as Tanishq's price; every Tanishq figure passes the gates", () => {
  const ages = [0.5, 7.9, 8.1, 20, 35.9, 36.1, 72, 500];
  const ratios = [1.0, 0.95, 1.119, 1.121, 0.87, 1.3]; // Tanishq reading / estimate
  const est = 14100;
  let cases = 0;
  for (const lang of ["en", "hi"]) {
    for (const age of ages) {
      for (const ratio of ratios) {
        const tv = Math.round(est * ratio);
        const forecasts = [ibja(est), fusion(est), tier1(tv, age), tier1(est, Math.min(age, 3)), null];
        for (const derived of [false, true]) {
          const readings = derived ? derivedRows(tv) : tanishqRows(age, tv);
          for (const fc of forecasts) {
            cases++;
            const g = render(readings, fc, lang);
            const ctx = JSON.stringify({ lang, age, ratio, derived, fc, g });
            const figure = Number((g.price.match(/[\d,]+/) || ["NaN"])[0].replace(/,/g, ""));
            const isEstimate = g.price.includes("≈");
            // Never blank, never unlabelled: a shown figure always has a label.
            if (Number.isFinite(figure)) assert.ok(g.label, `unlabelled figure ${ctx}`);
            // THE invariant: an estimate is never labelled as Tanishq's.
            if (isEstimate) assert.ok(!TANISHQ_RE.test(g.label || ""), `estimate labelled Tanishq ${ctx}`);
            if (derived) {
              assert.ok(!TANISHQ_RE.test(`${g.label} ${g.line}`), `Tanishq named after takedown ${ctx}`);
            }
            // A Tanishq-labelled headline figure is a real Tanishq value, never the estimate
            // figure (unless they happen to be equal), and within 12% of the estimate.
            if (TANISHQ_RE.test(g.label || "")) {
              assert.ok(!isEstimate && !derived, ctx);
              const tanishqValues = [tv, fc && fc.price_source === "tanishq_scrape" ? fc.current_22k : null];
              assert.ok(tanishqValues.includes(figure), `Tanishq label on a non-Tanishq figure ${ctx}`);
              if (fc && fc.price_source !== "tanishq_scrape") assert.ok(Math.abs(figure - est) / est <= 0.12, ctx);
            }
            // The secondary Tanishq line (GG 4b): whenever it exists it carries the Tanishq
            // figure AND a clock time -- never a date alone -- and the figure passed the 12%
            // gate. Past 36 h it says so ("not updated since").
            if (g.line) {
              const lineFig = g.line.match(/₹([\d,]+)/);
              assert.ok(lineFig, `Tanishq line without its figure ${ctx}`);
              const v = Number(lineFig[1].replace(/,/g, ""));
              assert.equal(v, tv, `line figure is not the Tanishq reading ${ctx}`);
              assert.ok(/\d{1,2}:\d{2}/.test(g.line), `Tanishq line without a time ${ctx}`);
              assert.ok(Math.abs(v - est) / est <= 0.12, `implausible line figure ${ctx}`);
              const saysOld = /not updated since|तब से अपडेट नहीं हुई/.test(g.line);
              assert.equal(saysOld, age > 36, `old-reading wording wrong for age ${age} ${ctx}`);
            }
            // Today's change only next to Tanishq's own latest reading, never an estimate.
            if (isEstimate) assert.equal(g.changeShown, false, `change shown next to an estimate ${ctx}`);
          }
        }
      }
    }
  }
  assert.ok(cases >= 900, `ran ${cases} cases`);
});
