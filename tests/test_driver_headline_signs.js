// tests/test_driver_headline_signs.js
// The weekly "what moved the price" headline words each part by its own sign (ADR 058 follow-up,
// verifier note on #2073). The old templates assumed the rupee's sign ("plus a bit from the
// rupee", "the rupee added back") and were wrong on 5 of 42 replayed days. Property test: over
// every sign combination of the gold / rupee / premium contributions, the rendered sentence's
// direction words match the signs, in EN and HI. Drives the REAL renderDriverContext
// (tests/helpers/load_app.js).
//
// Run: node --test tests/test_driver_headline_signs.js  (from repo root)

import { test } from "node:test";
import assert from "node:assert/strict";

import { loadApp } from "./helpers/load_app.js";

const WORDS = {
  en: {
    up: "Gold is up about", down: "Gold is down about",
    goldAdded: "global gold prices added", goldTookOff: "global gold prices took off",
    goldFlat: "global gold prices were roughly flat",
    inrAdded: "a weaker rupee added", inrTookOff: "a stronger rupee took off",
    inrFlat: "the rupee was roughly flat",
  },
  hi: { // gold clause checked with its verb below
    up: "महंगा हुआ है", down: "सस्ता हुआ है",
    goldFlat: "वैश्विक सोने की कीमतें लगभग स्थिर रहीं",
    inrAdded: "कमज़ोर रुपये ने", inrTookOff: "मज़बूत रुपये ने",
    inrFlat: "रुपया लगभग स्थिर रहा",
  },
};

function render(lang, gold, inr, prem) {
  const app = loadApp({ lang });
  try {
    const total = gold + inr + prem;
    const fc = {
      driver_context: {
        macro_fresh: true,
        windows: {
          "7d": {
            attribution_valid: true,
            total_move_rs_per_g: total,
            gold_usd_contrib_rs_per_g: gold,
            usdinr_contrib_rs_per_g: inr,
            premium_contrib_rs_per_g: prem,
          },
          "30d": { delta_pct_premium: 0.1 },
        },
        driver_state: { usd_inr_30d_pct_change: 0.5, gold_usd_30d_pct_change: 0.5 },
      },
    };
    app.renderDriverContext(fc);
    const html = app.element("driver-context-body").innerHTML;
    const m = html.match(/<p class="driver-headline">([\s\S]*?)<\/p>/);
    return { total, headline: m ? m[1] : null };
  } finally {
    app.dispose();
  }
}

const has = (s, w) => s.includes(w);
// Magnitudes: clearly moved (300), "roughly flat" (5), zero; premium also takes both signs.
const LEVELS = [-300, -5, 0, 5, 300];
const PREM = [-40, 0, 40];

for (const lang of ["en", "hi"]) {
  test(`every sign combination: direction words match the signs (${lang})`, () => {
    const w = WORDS[lang];
    let checked = 0;
    for (const gold of LEVELS) for (const inr of LEVELS) for (const prem of PREM) {
      const { total, headline } = render(lang, gold, inr, prem);
      const ctx = `${lang} gold=${gold} inr=${inr} prem=${prem}: ${headline}`;
      if (Math.round(total) === 0) {
        assert.equal(headline, null, `no "about Rs 0" headline -- ${ctx}`);
        continue;
      }
      assert.ok(headline, ctx);
      // Lead follows the week's total
      assert.equal(has(headline, w.up), total > 0, ctx);
      assert.equal(has(headline, w.down), total < 0, ctx);
      if (Math.abs(gold) <= 10 && Math.abs(inr) <= 10) { checked++; continue; } // "mix" sentence
      // Gold part follows gold's own sign
      if (lang === "en") {
        assert.equal(has(headline, w.goldAdded), gold > 10, ctx);
        assert.equal(has(headline, w.goldTookOff), gold < -10, ctx);
      } else {
        const goldClause = headline.match(/वैश्विक सोने की कीमतों ने करीब ₹[\d,]+ (जोड़े|घटाए)/);
        assert.equal(Boolean(goldClause), Math.abs(gold) > 10, ctx);
        if (goldClause) assert.equal(goldClause[1], gold > 0 ? "जोड़े" : "घटाए", ctx);
      }
      assert.equal(has(headline, w.goldFlat), Math.abs(gold) <= 10, ctx);
      // Rupee part follows the rupee's own sign (+ = weaker rupee = adds to the price)
      assert.equal(has(headline, w.inrAdded), inr > 10, ctx);
      assert.equal(has(headline, w.inrTookOff), inr < -10, ctx);
      assert.equal(has(headline, w.inrFlat), Math.abs(inr) <= 10, ctx);
      if (lang === "hi" && Math.abs(inr) > 10) {
        assert.ok(new RegExp(`रुपये ने करीब ₹[\\d,]+ ${inr > 0 ? "जोड़े" : "घटाए"}`).test(headline), ctx);
      }
      // Old wording that assumed a sign must never come back
      assert.ok(!/added back|plus a bit from|वापस जोड़/.test(headline), ctx);
      checked++;
    }
    assert.ok(checked >= 60, `checked ${checked} combinations`);
  });
}

test("bigger part is named first", () => {
  const { headline } = render("en", -120, 400, 0);
  assert.ok(headline.indexOf("a weaker rupee added") < headline.indexOf("global gold prices took off"), headline);
});

test("total that JS rounds to 0 (e.g. -0.5) renders no headline", () => {
  assert.equal(render("en", 100, -100.5, 0).headline, null);
});
