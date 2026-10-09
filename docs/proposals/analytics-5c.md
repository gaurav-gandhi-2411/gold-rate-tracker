# Usage analytics (item 5c): $0, cookieless, merged OFF

Status: GG decided (D7, 2026-10-05): GoatCounter, cookieless, no IP storage, a short plain-language
privacy note on the site, flag stays OFF. The code is GoatCounter-specific and inert. Nothing is
live. Needs GG only to turn it on: see "To turn it on" below. Supersedes section 3 of
`p5-product-proposals.md`, which was written from recall.

## Constraints (from GG and the project's stance)

$0 per month; no cookies; no personal data; no consent banner; works on a static GitHub Pages PWA
with a service worker; tolerates offline use; no backend that we run.

Current public stance, which must be updated in the same PR that flips the flag:
`README.md` says "No accounts, no ads, no analytics" and discloses jsDelivr and Sentry.
`SECURITY.md` says "no backend, no cookies, no auth". Sentry (`sendDefaultPii: false`) is already a
third party that sees visitor IPs on errors, so "no third party sees visitors" is not a claim the
project can make today; "no analytics" is.

## Options compared

Verification key: VERIFIED = read on the vendor's own page on 2026-10-05; UNVERIFIED = recalled,
not confirmed.

| | GoatCounter (hosted) | Cloudflare Web Analytics | Plausible | Own beacon: Cloudflare Worker + KV |
|---|---|---|---|---|
| Cost | Free "for reasonable public usage"; "running your personal website or small-to-medium business on it is fine, but sending millions of pageviews/day isn't" (goatcounter.com/terms, VERIFIED). Note the terms do not say "non-commercial"; that phrase in the earlier proposal was wrong. | "Available on all plans", including free (developers.cloudflare.com/web-analytics, VERIFIED) | Hosted: pricing page returned 404, so UNVERIFIED (recalled: paid from roughly $9/month, no free tier). Self-host (Community Edition, AGPLv3): software free, "you need to pay for your server" (plausible.io/self-hosted-web-analytics, VERIFIED); needs a server and databases, so breaks the no-backend rule | Workers Free: 100,000 requests/day; KV Free: 100,000 reads, 1,000 writes, 1,000 deletes per day, 1 GB (developers.cloudflare.com/workers/platform/pricing, VERIFIED). KV's 1,000 writes/day is the binding limit: a counter needs a write per count unless batched |
| Cookies | None; the session is a server-side random UUID (VERIFIED, goatcounter.com/help/sessions) | UNVERIFIED: the pages I read did not state cookie or storage behaviour | UNVERIFIED | None, by our own design |
| Personal data | Says it stores neither IP nor User-Agent, only a random UUID per 8-hour session; "probably doesn't require a GDPR consent notice", with caveats that case law is thin and legal advice is recommended (goatcounter.com/gdpr, VERIFIED). That is the vendor's position, not a legal finding | UNVERIFIED (FAQ says query strings are not logged, VERIFIED) | UNVERIFIED | We would see IP at the edge; we can choose not to store it. Our code, our liability |
| Needs an account | Yes, email-verified (VERIFIED) | Yes, a Cloudflare account; works without proxying the site (VERIFIED) | Yes | Yes, a Cloudflare account, plus a Worker we deploy and maintain |
| Static PWA + service worker | Plain GET image `/count?p=...`; no JS library needed (goatcounter.com/help/pixel, VERIFIED) | A JS beacon script from Cloudflare, so one more third-party script for the service worker's cache-first fallback to pass through | JS script | Image or beacon GET to our Worker |
| Offline | A request offline simply fails; nothing is queued | Same | Same | Same |
| Custom events (cards viewed, install) | Yes: `e=true` with a name as the path (VERIFIED) | Not offered as far as the pages I read show (UNVERIFIED) | Yes (UNVERIFIED) | Anything we build |
| Data retention / export | UNVERIFIED | Six months; unsampled beacon data 7 days, then roughly 10% (VERIFIED) | UNVERIFIED | Ours |
| Ad-blocker loss | "About a third of pageviews are missed" by the vendor's own estimate (VERIFIED) | Likely similar (UNVERIFIED) | Similar | Lower, if on our own domain; we have none |

Rejected: a "Pages access log" counter. GitHub Pages exposes no access log, so there is nothing to
read. Rejected: self-hosted Plausible or Umami: needs a server, against the no-backend rule.

### Recommendation: GoatCounter hosted

Smallest footprint that meets every constraint, and the only candidate that (a) is documented to
work as a plain image GET with no script, so nothing new runs in the page, (b) supports named
events, and (c) states what it stores. Risks to accept or reject:
- The "reasonable public usage" clause is discretionary; commercialisation could change what
  "small-to-medium business" means. If this becomes a paid product, re-read the terms and budget for
  the paid plan or a switch (UNVERIFIED: GoatCounter's paid tier price).
- The consent-banner position is the vendor's. GG should confirm with a lawyer before relying on it
  (not legal advice; India's DPDP Act 2023 was not analysed here).
- A third party (GoatCounter) would see visitor IPs in transit, as Sentry and jsDelivr already do.

Second choice: Cloudflare Web Analytics, if GG prefers one vendor; cookie behaviour must be
verified first.

## What the implemented design measures

Counts only: a page view (path without query string or hash), the page language, whether the page
runs as an installed app, an `appinstalled` event, and the referring origin (never the full
referrer URL). No cookie, no localStorage, no sessionStorage, no IndexedDB.

| Question | Answerable? | How |
|---|---|---|
| Daily / weekly unique visitors | Approximately | GoatCounter's per-8-hour-session count; a person visiting on two days counts twice. Treat as "visits", not "people" |
| Returning rate | No | Recognising a returning browser needs stored state or an identifier; we have neither by design. Weekly visits per day is a rough proxy only |
| PWA installs | Partly | `pwa/installed` fires only on browsers that raise `appinstalled` (Chromium); iOS Safari "Add to Home Screen" does not. `mode/standalone` counts visits made from an installed app, which shows the installed base is active |
| Language split | Yes | `lang/en`, `lang/hi` events |
| Which cards are viewed | Not in this PR | Needs an IntersectionObserver on stable card ids in `app.js`; deliberately not added, to keep this change out of the render path. Follow-up if GG wants it |
| Referrers | Maybe, origin only | We send the `r` parameter. GoatCounter's pixel page says "The tracking pixel won't allow recording the referrer or screen size" (VERIFIED 2026-10-05, goatcounter.com/help/pixel) while its own parameter table lists `r`. Whether `r` is honoured is UNVERIFIED until the first real count is checked in the dashboard (step 4 of "To turn it on") |
| Bounce | Weak | GoatCounter reports it as single-page visits; on a one-page app this is mostly "did not open How we know" |
| Notification opt-in funnel | No | There is no push-notification opt-in in the page; if one is added, add one event per step |
| Geography | Provider-side, coarse | Not sent by us |

## Commercialisation thresholds (a proposal, not a measured fact)

No data exists today, so none of these numbers is derived from this site. They are starting
points to be revised after four weeks of real counts.

| Signal (4-week window, after excluding GG's own visits) | Not worth it | Worth testing a paid offer | Strong |
|---|---|---|---|
| Median weekly visits (sessions) | under 200 | 500 to 2,000 | over 2,000 |
| Share of visits with `mode/standalone` | under 5% | 5% to 15% | over 15% |
| Referrers dominated by one origin (for example search) | one-off spike | steady week over week | steady and growing |
| Direct plus installed-app visits as a share of all (a proxy for habit, since returning is unmeasurable) | under 20% | 20% to 40% | over 40% |

Reading: these detect whether there is an audience at all. They cannot show willingness to pay, and
traffic from search is not the same as demand for a paid product. Any decision to charge needs a
separate test (a waitlist or a priced offer), which analytics cannot replace. Visits are
over-counted by people who open the page several times a day and under-counted by ad-blocker users
(about a third by the vendor's estimate).

## Implementation (behind a flag, OFF)

- `flags.js`: `analytics: false`. `analytics.js`: `ANALYTICS_SITE_CODE = ""`. Both must change to
  turn it on. The endpoint is built from the code as `https://<code>.goatcounter.com/count`, so it
  is always https and always GoatCounter's own domain; a code that is not a plain lowercase DNS
  label is refused. It also refuses Global Privacy Control and offline (Do Not Track stopped being honoured on 2026-10-09: see analytics.js).
- GoatCounter URL shape (VERIFIED against goatcounter.com/help/pixel, 2026-10-05):
  `https://<code>.goatcounter.com/count?p=<path>` for a page view; `p=<event name>&e=true` for an
  event; `r=<referrer origin>` when the visitor came from another site. We never send `t` (title),
  `q` (campaign query), `s` (screen size) or `b`. Pinned by `tests/test_analytics.js`.
- While off: no request, no DOM read, no listener, no storage, and no privacy note. Proved by
  `tests/test_analytics.js` (stubs throw on any touch) and `tests/test_analytics_off_headless.js`
  (real page: identical DOM with and without the script; a flag-on variant shows the beacon the
  check would catch).
- Service worker: `analytics.js` is a precached shell file; VERSION bumped to
  `v76-20261005-analytics-flag-off` (legacy mode, hand bump; v74 is the merged direction wording,
  v75 is reserved for the Hindi PR #2397). The worker's fetch handler is untouched, so a
  cross-origin image falls through to the network and is never cached. A failed request offline is
  a dropped image, not a worker error. No Content-Security-Policy exists (checked: no CSP meta tag
  in `index.html`, no `_headers` file, no `connect-src`/`img-src` anywhere in the repo), so the
  GoatCounter host needs no allowlist entry today.

## Privacy note (implemented, flag-gated)

English only; Hindi falls back to English through `t()` (do not add Hindi without a native
speaker). The note is the i18n key `privacyNote` in `i18n.js`. `analytics.js` adds it to the page
footer as `<p id="privacy-note" class="muted">` only when counting is actually on (flag on, site
code set, and the browser is not sending Global Privacy Control). While the flag is
off the page contains no such element and makes no claim about counting; the DOM-equality headless
test asserts this. Text as written:

> We count how many people visit this page, nothing more. The count uses no cookies, keeps no name
> or address, and is skipped if your browser asks not to be tracked. Counts are kept by GoatCounter
> (goatcounter.com).

It passes `scripts/check_plain_language.py` and `scripts/check_retailer_language.py`.

### Flip PR text for README and SECURITY (NOT applied; the flag is off, so today's text is true)

`README.md`, the sentence that now reads "No accounts, no ads, no analytics." becomes:
"No accounts and no ads. It counts visits with GoatCounter, a cookieless counter that keeps no
name, cookie or IP address; the page says so in its footer." (Keep the jsDelivr and Sentry
sentence after it unchanged.)

`SECURITY.md`, the table row "PWA frontend | Static HTML/JS; no backend, no cookies, no auth"
becomes: "Static HTML/JS; no backend, no cookies, no auth. Visit counts go to GoatCounter as an
image request (page path, language, installed-app yes/no, referring site name); see the privacy
note in the footer." The "no cookies" claim stays true: the counter sets none.

## To turn it on (DONE 2026-10-09: GG created the site `gold-rate-tracker` with User-Agent, screen size and location collection off and referrer, language and sessions on; CC set the code, the flag and the note. The steps below are kept as the record)

1. Create the GoatCounter account and site.
   - Open https://www.goatcounter.com/signup (free; the form shows no plan choice or payment step,
     VERIFIED on the signup page text 2026-10-05; the free-use terms are at
     goatcounter.com/terms).
   - Fields (as listed on the signup page, 2026-10-05): "Account name" (this becomes your address
     `https://<account-name>.goatcounter.com`; this is the site code), "Site domain" (optional;
     enter `gaurav-gandhi-2411.github.io`), "Email address" (password resets and notices),
     "Password" (at least 8 characters), and a human check ("Fill in 9 here"). Verify the email
     when it arrives.
   - Choose a short lowercase code with only letters, digits and hyphens, for example
     `gold-rate-tracker`. Whether GoatCounter accepts other characters is UNVERIFIED; the code
     refuses anything outside that set.
   - In the dashboard, under Settings, leave "collect" options at their defaults; do not enable
     any "collect IP/User-Agent/location/screen size" setting if one is offered (the pixel sends
     none of them, and the privacy note says no address is kept).
2. Set the values (one small PR from a branch named `feat/analytics-on`).
   - `analytics.js`: change `const ANALYTICS_SITE_CODE = "";` to `const ANALYTICS_SITE_CODE =
     "<your code>";` (the only place the code lives).
   - `flags.js`: change `analytics: false,` to `analytics: true,`.
   - CSP / allowlists: none needed. There is no CSP meta tag in `index.html` and no headers file.
     The service worker's fetch handler needs no change: the image request to
     `https://<code>.goatcounter.com/count` is cross-origin and falls through to the network
     without being cached. If a CSP is ever added, allow `img-src https://<code>.goatcounter.com`.
3. Flip PR checklist.
   - Bump `VERSION` in `service-worker.js` to the next free number (run
     `python scripts/check_sw_version_guard.py --base origin/master`).
   - Apply the README and SECURITY wording above, in the same PR (the page footer note appears
     automatically when the flag is on).
   - Update the "who sees your visit" disclosure if the page has one, and `tests/test_analytics.js`
     expectations "the shipped flag is off and the shipped site code is empty" (that test is meant
     to fail on the flip; change it to the new shipped values).
   - Run `node --test tests/test_analytics.js`, `node tests/test_analytics_off_headless.js` (its
     flag-off section will need to be rewritten to flag-on, since the page is now counted), and
     the plain-language checks. Get a human merge.
4. How to verify after it is live.
   - Open the live site in a normal window with Do Not Track off, then open
     `https://<code>.goatcounter.com`. Within about a minute the dashboard should show one visit
     for the page path and the `lang/en` event under the events view.
   - Check that the Referrers list shows an origin only. If it stays empty for visits from another
     site, the pixel is not honouring `r` (see the Referrers row above); that is a known limit,
     not a fault.
   - Check that no cookie is set (browser dev tools, Application, Cookies) and that the only new
     request is the image GET to `<code>.goatcounter.com/count`.
   - With Do Not Track on, confirm no new count appears and the footer note is absent.
5. How to switch it off. Set `analytics: false` in `flags.js` (or empty the site code), bump the
   service-worker VERSION, and revert the README and SECURITY wording to "no analytics". The page
   then makes no request and shows no note. The GoatCounter account can be deleted from its
   settings; deleting it is optional because nothing calls it any more.

## Open decisions for GG

1. DECIDED (D7, 2026-10-05): GoatCounter. You create the account; no key or account exists in the
   repo, and none is needed beyond the public site code.
2. Whether a lawyer confirms the "no consent banner" position for India before launch.
3. Whether to add card-view events (follow-up, touches `app.js`).
4. Whether the thresholds above are the ones you want to hold yourself to.
