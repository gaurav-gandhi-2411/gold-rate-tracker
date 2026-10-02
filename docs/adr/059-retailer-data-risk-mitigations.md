# ADR 059: Retailer data risk mitigations and the legal-review precondition

**Status:** Accepted, 2026-09-25. Records GG decision **G1**. Implemented across three PRs:
- #2048: polite access.
- #2053: takedown switch, fallback proof and display-time plausibility gates.
- This PR: this ADR, the retailer-language CI guard, and the inventories below.

**Related:** ADR 025 (source hierarchy, IBJA primary), ADR 026 (Kalyan-anchored fusion), ADR 029 (IBJA-only on runner death), ADR 054 / PR #2038 (no raw third-party *market* series; lists retailer data as "needs GG decision" — this ADR is the retailer-side answer and does not change anything #2038 changes).

## Context

The product reads rates from four retailers:
- **Tanishq:** Playwright scrape on a self-hosted runner. Enriches the live price and is the whole of `data/prices.json`.
- **GRT and Malabar:** national rates. Feed the shadow fusion and the rare tier-3 live fallback.
- **Kalyan:** city boards. Same uses as GRT and Malabar.

GG knows that:
- Tanishq's and GRT's terms prohibit scraping.
- Kalyan's terms prohibit public reproduction.
- Malabar's terms were not found (orchestrator inventory in #2038, not re-verified here).

## Decision (G1)

**Retailer scraping continues, under these mitigations:**

1. **Polite access (#2048).**
   - One identifying UA for the Python adapters (unchanged value), and one consistent browser UA for Tanishq. Per-retry UA/viewport rotation is removed.
   - Same-host spacing of at least 2 s.
   - Capped attempts, with exponential backoff and jitter.
   - HTTP 429, and 503 with Retry-After, are honoured: the run ends for that host and never escalates to a browser load.
   - The always-failing plain-GET probe to Tanishq is cut to once a day.
2. **Neutral, dated, plausible retailer text and figures (#2053, this PR).**
   - Every retailer-named figure is dated.
   - A Tanishq figure more than 12% off the same cycle's IBJA-calibrated estimate is not shown as confirmed. A fusion reading older than 36 h is not shown by name. Adapter rates outside ₹2,000–25,000/g are rejected.
   - `scripts/check_retailer_language.py` fails CI on judgmental words near a retailer name, in EN and HI.
3. **Takedown within hours (#2053).**
   - `config/retailers.json` is read by every fetch and display path.
   - `docs/RETAILER_TAKEDOWN.md` is the runbook.
   - `scripts/build_ibja_derived_prices.py` rebuilds the site's history without Tanishq.
   - The fallback is proven by the real pipeline plus a headless render of the real `app.js`.
4. **Derived only, going forward (GG decision per file, below).**
   - Shadow and research retailer data moves to the private repository (G3), with only hashes kept public.
   - Live-displayed files need a separate GG decision.

**Binding rule:**
- **Nothing commercial happens without a legal review of data rights first.** That covers ads, a paid tier, an API, data licensing, affiliate links, sponsorship, or selling or sharing any dataset.
- The review must cover every source: Tanishq, GRT, Malabar, Kalyan, **IBJA** (see the terms findings below), Yahoo Finance, and FBIL/RBI.
- Until the review is written down and GG signs it off, the product stays free, carries no ads, and offers no data export.
- Any PR that adds a commercial surface must link that review, or it is not mergeable.

## IBJA terms findings (fetched 2026-09-25)

The sweep covered these URLs:
- `https://www.ibjarates.com/`. The IBJA Terms, Disclaimer, Terms and Conditions, Privacy and Cookie policy texts are in-page modals; this is the page `ml/ibja.py` scrapes.
- `https://ibja.co/` (same modal texts).
- `https://www.indiagoldratesapi.com/`, `/Pricing.aspx`, `/Faqs.aspx` (IBJA's official rates API).
- `https://ibjarates.com/robots.txt`: 404.
- `https://www.ibja.co/robots.txt`: disallows only `/cgi-bin/`.

No standalone terms page with a separate URL was found. The modal texts are the terms. Evidence: SHA-256 of the fetched HTML is `4430880f…769df` (ibjarates.com) and `ed223b9a…dfc0e` (Faqs.aspx).

Verbatim clauses:

1. ibjarates.com, **"Important Notice"** (home page): "Any party using the IBJA Gold Price for valuation and pricing activities and in transactions & financial products are advised to subscribe IBJA rates only through OFFICIAL IBJA API (Application Programming Interface)".
2. ibjarates.com and ibja.co, **Disclaimer → "Fair Use Disclaimer"**: "If You wish to use copyrighted material from the Service for your own purposes that go beyond fair use, You must obtain permission from the copyright owner." The Disclaimer also says: "The information contained on the Service is for general information purposes only."
3. ibjarates.com, **Terms and Conditions** (last updated May 19, 2025): "By accessing or using the Service You agree to be bound by these Terms and Conditions." The Terms and Conditions contain no explicit scraping, reuse or redistribution clause.
4. indiagoldratesapi.com **FAQ** (these apply to *API subscribers*):
   - "Is IBJA rates API free? No, It is a paid subscription."
   - On storing data: "you can reseller this database offline/online nor publish these rates on your website directly/indirectly without written permission of IBJA." The original text has the typo "reseller"; the clearly intended meaning is "cannot resell … nor publish".
   - On historical data: "The IBJA API subscribers cannot share or redistribute or resell the subscribed data in any form or in any medium directly or indirectly or publish the rates or publish historic data without the prior written consent from IBJA even after the subsciption period end. Incase found IBJA will initiate legal action."

**What this means for the product.**
- The website terms do not expressly ban scraping or reuse. But republication beyond fair use needs permission, and IBJA "advises" pricing use to go through the paid API.
- The API terms show IBJA's position: publishing IBJA rates or IBJA history on a website needs **written permission**.
- The product does two things with IBJA data:
  - it displays an IBJA-calibrated estimate derived from today's PM rate, and says IBJA is the source;
  - it **publishes raw IBJA history** in `data/ibja_rates.parquet` (all purities, AM/PM, every day), in a public repo.
- #2038 records IBJA as "No restriction found". The clauses above contradict that for the history file. **Surfaced for GG, not silently absorbed.**
- Low-cost mitigation options for GG:
  - (a) Move `ibja_rates.parquet` to the private repo (G3) and keep only hashes public.
  - (b) Write to IBJA (`nagaraj.iyer@ibja.in`) asking permission for free, non-commercial display with attribution.
  - (c) Both.
- **Under the binding rule, any commercial step needs IBJA's written permission, or a paid API licence, first.**

## Inventory: user-visible retailer text and figures (as on master, 2026-09-25)

Sweep scope (rule 85b):
- Files: `i18n.js` EN and HI, `index.html` (meta/og/twitter, first-visit, hero, footer), `manifest.webmanifest`, `app.js`, `how-we-know.html`, `how-we-know.js`, `how-we-know-strings.js`, `og.html`, the `og.png` source, `ml/public_copy.py`, `ml/notifications.py`, README.
- Name forms: Tanishq, Kalyan, Malabar, GRT, and their Devanagari forms.

| Where | Text (EN) | Figure? | Dated? | Assessment |
|---|---|---|---|---|
| `i18n.js` `heroLastConfirmed` (+HI) | "Tanishq last confirmed: ₹{price} ({date})" | yes | yes | Neutral and dated, but implies Tanishq *confirms* our price. Reword (P3). |
| `i18n.js` `calcRateUsedTanishq` (no HI) | "Rate used: 22K ₹{rate}/g — Tanishq store rate" | yes | **no** | Undated. On tier 4 this is a possibly stale figure. Reword (P2). |
| `i18n.js` `heroLocation` (+HI), `index.html:210` | "Tanishq retail price · pan-India" | labels the hero figure | n/a | **Inaccurate on the steady-state tier**: the hero is an IBJA-calibrated estimate, yet this line attributes it to Tanishq. Reword (P1). |
| `i18n.js` `bannerTanishqLongSilent` (+HI) | "Tanishq hasn't confirmed this price in a while…" | no | relative | Implies Tanishq confirms our price. Reword (P4). |
| `i18n.js` `bannerFusion` (+HI) + `fusionSource*` | "…based on other jewellers' rates (GRT, Malabar, Kalyan)…" | no | this cycle | Neutral. Staleness is now gated (36 h) in #2053. |
| `i18n.js` `pageDescription`, `firstVisitText`, `footerBody` (+HI); `index.html` meta/og/twitter/first-visit/footer; `manifest.webmanifest` | "…confirmed against live Tanishq retail when reachable…" | no | no | Factual. "confirmed against" suggests endorsement, so "compared with" is proposed (P5). |
| `ml/notifications.py` T6/T11/T12 | "IBJA->Tanishq calibration…", "Tanishq and IBJA both unavailable", "Tanishq self-hosted runner failing" | no | n/a | **OPS topic only** (`ml/notification_routing.py`), never public. Neutral. |
| `ml/public_copy.py`, `how-we-know*`, `og.html`/`og.png` | none | | | Clean on master. #2038 adds neutral source credits to how-we-know. |
| `data/commentary.json` (LLM text, displayed) | quotes 22K/24K/18K values | yes | ts | Does not name a retailer. The values come from `prices.json`. |

No judgmental wording exists today. `check_retailer_language.py` passes on all 12 files and now guards this in CI.

### Proposed wording changes (user-facing: GG approval; HI needs native review)

| # | Key | EN (proposed) | HI (proposed) |
|---|---|---|---|
| P1 | `heroLocation` → tier-aware: tier 1 keeps a retailer label, estimate tiers use `heroLocationDerived` | tier 1: "Tanishq's listed rate · pan-India"; estimate: "Estimated shop price from IBJA · pan-India" | "Tanishq की सूचीबद्ध दर · पूरे भारत में" / "IBJA पर आधारित दुकान की अनुमानित कीमत · पूरे भारत में" |
| P2 | `calcRateUsedTanishq` (add a date param) | "Rate used: 22K ₹{rate}/g — Tanishq's listed rate on {date}" | "इस्तेमाल की गई दर: 22K ₹{rate}/ग्राम — {date} को Tanishq की सूचीबद्ध दर" |
| P3 | `heroLastConfirmed` | "Tanishq's listed rate on {date}: ₹{price}" | "{date} को Tanishq की सूचीबद्ध दर: ₹{price}" |
| P4 | `bannerTanishqLongSilent` | " We haven't been able to read Tanishq's listed rate recently — the last successful check was {rel}." | " हाल में हम Tanishq की सूचीबद्ध दर नहीं पढ़ पाए — आख़िरी सफल जांच {rel} हुई थी।" |
| P5 | `pageDescription`, `firstVisitText`, `footerBody`, meta/og/twitter, manifest | "confirmed against" → "compared with Tanishq's listed rate" | "…से पुष्टि की जाती है" → "…Tanishq की सूचीबद्ध दर से मिलाकर देखी जाती है" |
| (new, #2053) | `heroLocationDerived` | "Estimated shop price from IBJA · pan-India" | "IBJA पर आधारित दुकान की अनुमानित कीमत · पूरे भारत में" |

A future "store markup meter" (#2022) must follow the pattern "Tanishq's listed rate on {date} is {x}% above the IBJA rate", with a date, a sign and no adjective. It must pass `check_retailer_language.py`.

## Inventory: committed files holding scraped retailer values

Sweep scope, all tracked files under `data/`, `reports/` and `archive/` (63 files, excluding images). Shapes searched:
1. Retailer names (EN) in any text or JSON value or key.
2. JSON keys `22k`/`24k`/`18k`/`rate_22k`/`tanishq_22k`/premium/markup/retail.
3. Every parquet column name, plus string columns containing a retailer name.
4. **Any JSON/JSONL number in the per-gram price band 9,000–20,000, under any key.** This catches series stored as `actuals`, `naive`, `current_22k` and similar.
5. Invertibility: a markup or premium over the public IBJA rate counts as **raw**, because retailer = IBJA × (1 + markup).

| File | Holds | Served to browsers? | Raw or derived | Recommendation |
|---|---|---|---|---|
| `data/prices.json` | 712 Tanishq 22K/24K/18K readings (timestamped, source URL) | **yes** (chart, history, cards) | raw | **GG decides** (options A–C below) |
| `data/backtest.json` | `folds[].actuals`/`naive`: daily Tanishq 22K series | **yes** (forecast-vs-actual, how-we-know) | raw | Follows the `prices.json` decision. Under B, rebuild on IBJA-derived actuals. |
| `data/drift_metrics.json` | `actual_22k` (102 rows, Tanishq) | **yes** | raw | Follows `prices.json` |
| `data/metrics_history.json` | `current_22k`, `actual_next_22k` (displayed price: Tanishq on tier-1 days) | **yes** | raw on tier-1 rows | Follows `prices.json` |
| `data/forecast.json` | `current_22k`, `scraped_at`: the latest Tanishq value on tier 1 | **yes** | raw (1 value) | Live enrichment. Keep under A or B. Remove only on takedown. |
| `data/commentary.json` | LLM text quoting the 22K/24K/18K values | **yes** | raw (values in prose) | Follows `prices.json` |
| `data/next_day_range_shadow.json` | `current_22k`/`actual_next_22k` rows | no (shadow) | raw on tier-1 rows | Move to the private repo (G3), keep a hash |
| `data/fusion_snapshots.parquet` | 1,824 GRT/Malabar/Kalyan/IBJA `rate_22k` rows | no (shadow) | raw | **Private repo (G3)**, hash public |
| `data/shadow_fusion_output.json` | Kalyan-anchored city `value` (= Kalyan's rate) and `markup` (Kalyan/national) | no (shadow) | raw (markup is invertible) | **Private repo (G3)** |
| `data/feature_store/snapshots.parquet` | `tanishq_22k` (108 non-null) | no (research) | raw | **Private repo (G3)**. Coordinate with #2038, which rewrites this file's macro columns. |
| `data/chronos_probe.json` | `tanishq_forecast` p10/p50/p90 (model output) | no (feeds forecast.json) | derived (forecast, not an observation) | Keep |
| `data/calibration.json`, `calibration_band_coverage.json` | fitted slope/intercept/quantiles, coverage | yes | derived aggregates | Keep |
| `data/tanishq_scrape_outcomes.jsonl` | success/skip plus fetch method, **no prices** | no | none | Keep |
| `archive/history_seed_v1_uniform_premium.json` | 444 rows. **Correction to #2038's table:** these are *not* Tanishq prices. They are `yahoo-finance-estimated-GC=F*INR=X/31.1035*1.15` (Yahoo gold × FX × a fixed 1.15). | no | derived from Yahoo (a product of two series); not retailer data | Out of retailer scope. The Yahoo question is #2038's. |
| `data/ibja_rates.parquet` | raw IBJA AM/PM, all purities, 267 days | no (pipeline input) | raw **IBJA** | See the IBJA findings: private repo, or ask IBJA |
| all other `data/`, `reports/` files | aggregates, p-values, coverage counts, strings | | derived | Keep |

Nothing was deleted or rewritten in this PR, and history is not rewritten.

**Options for the live-displayed Tanishq history (`prices.json` and the four files that follow it). GG decides:**
- **A. Status quo.**
  - Keep publishing raw Tanishq history.
  - Pro: no product change.
  - Con: it is exactly the reproduction the terms prohibit. The takedown switch limits the damage after a request but does not prevent it.
- **B. Publish IBJA-derived history. Keep only the latest Tanishq reading public (recommended).**
  - Serve `build_ibja_derived_prices.py` output as the public history.
  - Keep raw Tanishq in the private repo, used for calibration there.
  - Show Tanishq only as today's dated enrichment line.
  - Pro: public history is a pure function of IBJA plus two constants, and the product still says "Tanishq's listed rate on {date}".
  - Con: the chart shows an estimate, not observed retail. That needs an honest label (P1/P3 wording), and the calibration refit moves into the private pipeline, so G3 infrastructure is needed first.
- **C. Short window.**
  - Publish only the last 30 days of Tanishq readings.
  - Pro: smaller footprint.
  - Con: still a reproduction, and it breaks the 90-day chart and support-distance line (#210).

## Consequences

- The takedown switch, polite access and plausibility gates are live-safe defaults: there is no behaviour change unless data is suspect or a retailer is disabled.
- The legal-review rule now blocks monetisation of any kind until data rights, including IBJA's, are settled.
- Open for GG:
  - the `prices.json` family (A/B/C);
  - the move of shadow and research files to G3;
  - the IBJA history file;
  - Tanishq's UA (identify or not);
  - the weekly live canary from a CDN-blocked IP (drop?);
  - Kalyan fetching 4 identical cities (reduce to 1?);
  - the Playwright fallback's `--disable-blink-features=AutomationControlled` flag, which is evasion-flavoured and whose removal may cut the Tanishq success rate.

## Alternatives considered

- **Stop all retailer scraping now.** Rejected by GG (G1). IBJA-only (ADR 029) remains the documented fallback, and the takedown switch makes it one edit per retailer.
- **Per-retailer display flags only, with no fetch gating.** Rejected. A takedown request is about collection as much as display.
- **Guess at a Retry-After when none is sent.** Rejected. A 429 without guidance ends the cycle for that host; the next scheduled run, at least 3 h later, is the retry.
