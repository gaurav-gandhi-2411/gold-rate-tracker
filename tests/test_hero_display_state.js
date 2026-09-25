// tests/test_hero_display_state.js — E2 (GG decision 2026-09-25) + ADR 059 display gates.
//
// Drives the REAL renderHero()/heroDisplayState() from app.js (tests/helpers/load_app.js)
// and asserts the exact hero label text, EN and HI, for every state the hero can be in:
// fresh Tanishq, not fresh (<=36h / >36h), blocked for days, no estimate (inference tier 4),
// Tanishq disabled (IBJA-derived history), implausible reading, fusion estimate. Then a
// property test over a grid of all states asserts the one invariant that matters: an
// estimate is NEVER labelled as Tanishq's price, and no Tanishq-named figure is shown that
// fails the 12% / 36h gates.
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
    name: "blocked for days (Tanishq 5 days old) -> estimate, Tanishq dated without figure",
    readings: tanishqRows(120, 13900), forecast: ibja(14100),
    en: { price: "≈ ₹14,100", label: "Our estimate for today, based on the IBJA rate", line: "Tanishq's listed rate was last checked 10:40 AM, 20 Sept — too old to show here" },
    hi: { price: "≈ ₹14,100", label: "आज के लिए हमारा अनुमान, IBJA दर पर आधारित", line: "Tanishq की सूचीबद्ध दर आख़िरी बार 20 सित॰, 10:40 am जांची गई थी — यहां दिखाने के लिए बहुत पुरानी है" },
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

test("today's change is hidden next to an estimate built from Tanishq rows, shown otherwise", () => {
  assert.equal(render(tanishqRows(1, 14040), tier1(14040, 1), "en").changeShown, true);
  assert.equal(render(tanishqRows(20, 14040), ibja(14100), "en").changeShown, false);
  assert.equal(render(derivedRows(14100), ibja(14100), "en").changeShown, true);
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
            // The secondary Tanishq line: a figure only when <=36h old and within 12%.
            const lineFig = g.line && g.line.match(/₹([\d,]+)/);
            if (lineFig) {
              const v = Number(lineFig[1].replace(/,/g, ""));
              assert.equal(v, tv, `line figure is not the Tanishq reading ${ctx}`);
              assert.ok(age <= 36, `line figure older than 36h ${ctx}`);
              assert.ok(Math.abs(v - est) / est <= 0.12, `implausible line figure ${ctx}`);
            }
          }
        }
      }
    }
  }
  assert.ok(cases >= 900, `ran ${cases} cases`);
});
