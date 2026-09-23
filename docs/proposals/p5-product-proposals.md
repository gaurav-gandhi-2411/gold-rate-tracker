# P5 product proposals — search, Hindi, analytics, share cards

Status: **proposal only — nothing here is implemented.** Each item ends with the decision GG must make.
Written 2026-09-23 against `master` @ `0f22c69e`. Facts marked **[verified]** were checked in the repo
or against the live site on that date; anything else is marked **[recall — unverified]**.

The jewellery cost estimator (the first P5 item) is a separate PR and not covered here.

---

## 1. Search visibility for "gold rate today"

**What exists [verified]:** `index.html` already has a keyword-relevant `<title>` ("Gold Rate Today · Is it
a good price?"), a meta description, and full Open Graph + Twitter card tags. **Missing:** no
`<link rel="canonical">`, no schema.org structured data (JSON-LD), no `sitemap.xml`
(`/gold-rate-tracker/sitemap.xml` → 404), and no `robots.txt` (`gaurav-gandhi-2411.github.io/robots.txt` →
404). The site lives on a project-Pages subpath of a `github.io` domain.

**Problem:** "gold rate today" is a head term dominated by large, established Indian publishers and
jewellers **[recall — unverified]**. A subpath of `github.io` with no backlinks is unlikely to rank for
the head term no matter what tags it has; long-tail queries ("22K gold rate IBJA today",
"is gold cheap today India") are the realistic target.

**Proposal (cheap, reversible, in order):**
1. Add `<link rel="canonical">` pointing at the live URL.
2. Add a `sitemap.xml` under `/gold-rate-tracker/` and submit it in Google Search Console (free; needs
   GG to verify site ownership, e.g. via an HTML-file or meta-tag method).
3. Add JSON-LD `WebPage`/`Dataset`-style structured data describing the page. **Do not** mark up the
   price as an offer/product price — it is an estimate, and structured data that overstates it would
   contradict the page's own honesty rules.
4. `robots.txt` **cannot** be added from this repo: crawlers only read it at the domain root, which
   belongs to a different repo (the user site). Only worth pursuing with (5).
5. Larger lever, deferred already in `docs/RUNBOOK.md` → *Custom domain (deferred)*: a custom domain
   (~₹800–1,000/year **[recall — unverified]**) gives a root you control and a brand that can earn links.

**Cost:** $0 for 1–3. **Effort:** ~half a day. **Risk:** low; head-tag changes only.
**Measure it:** Search Console impressions/clicks — which is also the only analytics that needs no
page script (see 3).

**GG decides:** (a) OK to verify the site in Search Console under your Google account? (b) Revisit the
deferred custom domain now, or keep deferred?

## 2. Hindi — pending native-speaker review

**What exists [verified]:** Hindi is already fully wired, not a proposal: `i18n.js` has `en` and `hi`
catalogues with **257/257 keys at parity** (none missing in `hi`), a header toggle (`हिं`), language
persisted in `localStorage`, `navigator.language` fallback, `<html lang>` set dynamically, and
Devanagari font preloads for Hindi visitors. Last copy polish: PR #776. **No record in the repo** of a
native-speaker review. The OG share image (`og.html`) and share text are English-only in the OG image;
the share *text* has a Hindi variant.

**Problem:** a translation nobody fluent has signed off on is a trust risk for exactly the buyers it
targets — awkward or wrong financial wording ("estimate", "range", "base rate") reads as careless.
Also, every language is served from one URL chosen client-side, so search engines only ever index the
English page — Hindi gets **no** search visibility today.

**Proposal:**
1. One native-speaker review pass of the `hi` catalogue (257 strings; export to a two-column sheet
   en | hi | reviewer note). Priority: strings that make claims (verdict, band, "not a forecast",
   disclaimers).
2. After review only: a `?lang=hi` (or `/hi/`) URL with `hreflang` alternates so Hindi can be indexed.

**Cost:** $0 if a volunteer reviewer; otherwise a paid review. **Effort:** export script ~1h; review is
the reviewer's time. **Risk:** low; (2) touches routing, so it's a separate, later PR.

**GG decides:** who reviews (a named native speaker), and whether Hindi search visibility (step 2) is
wanted at all.

## 3. $0 cookieless analytics

**What exists [verified]:** no analytics of any kind. Third-party calls today: Chart.js from jsDelivr
and Sentry error reporting (`index.html`). So there is currently **no data on how many people use the
site**, which makes every product priority on this list a guess.

**Options [recall — unverified; verify pricing/terms before choosing]:**

| Option | Cost | Cookies | Notes |
|---|---|---|---|
| Google Search Console | Free | None on page | Search traffic only; no page script at all. Do regardless (see 1). |
| GoatCounter (hosted) | Free for non-commercial use | No | One `<script>`; aggregate counts only; open source. |
| Cloudflare Web Analytics | Free | No | JS beacon; needs a Cloudflare account; works without proxying the site. |
| Self-hosted Umami / Plausible CE | "Free" software | No | Needs a server + database — breaks this project's no-server model. Not recommended. |

**Privacy:** cookieless, aggregate-only counters are generally treated as not needing a consent
banner **[recall — unverified; not legal advice]**. India's DPDP Act 2023 applies to personal data; an
IP-derived, non-stored, aggregate count is designed to avoid holding personal data, but GG should
confirm rather than rely on this note. Whatever is chosen should be disclosed in the page footer and
README (the README currently says "no analytics").

**Recommendation:** Search Console now (no script). If page-level counts are wanted, GoatCounter:
smallest footprint, no account tie-in beyond itself.
**Cost:** $0. **Effort:** ~1h + README/footer copy. **Risk:** low; adds one third-party script.

**GG decides:** page analytics yes/no; if yes, which provider (and it's your account).

## 4. Shareable price cards

**What exists [verified]:** a Share button already exists (`app.js` `shareSnapshot`): Web Share API
with the current price as text + the page URL, clipboard fallback. Chat apps then pull the page's live
`og:image`, which is **regenerated after every successful Check Gold Price run**
(`generate-og-image.yml` → Playwright screenshot of `og.html` at 1200×630 → `og.png`).

**Problem:** the preview image is (a) English-only, (b) cached by chat apps per URL, so a card shared
later can show an old price **[recall — unverified: WhatsApp/Telegram cache duration]**, and (c) a
picture of the site, not a card designed to be forwarded.

**Proposal options:**
- **A. Tune the existing OG image (recommended first):** today `og.html` prints a *relative* age
  ("Updated 2h ago", computed at render time) **[verified]** — once a chat app caches the image, that
  label keeps saying "2h ago" indefinitely. Replace it with an absolute date/time (IST) and label the
  price as an estimate (the card currently has no such label **[verified]**). Cheapest; fixes the
  "old price that looks current" risk.
- **B. Client-side card:** draw a price card on a `<canvas>` in the browser (price, date, "estimate",
  URL, Hindi or English per the user's language) and share it as an image via
  `navigator.share({ files })` where supported, falling back to today's text share. Always current,
  localised, no server. Browser support for sharing files varies **[recall — unverified]** — must keep
  the text fallback.

**Cost:** $0 either way. **Effort:** A ~2h; B ~1 day + device testing. **Risk:** A low; B moderate
(new UI + per-device share behaviour).

**GG decides:** A only, or A then B.

---

## Suggested order

1 (canonical + sitemap + Search Console) → 3 (measure) → 4A → 2 (review) → 4B / Hindi URLs. Measuring
first means the later items can be judged on real usage instead of guesses.
