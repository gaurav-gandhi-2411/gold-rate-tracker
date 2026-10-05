# Usage analytics (item 5c): $0, cookieless, merged OFF

Status: proposal plus inert code. Nothing is live. Needs GG: pick a provider, create the account,
flip the flag. Supersedes section 3 of `p5-product-proposals.md`, which was written from recall.

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
| Referrers | Yes, origin only | `r` parameter |
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

- `flags.js`: `analytics: false`. `analytics.js`: `ANALYTICS_ENDPOINT = ""`. Both must change to
  turn it on; the code refuses a non-https endpoint, Do Not Track, Global Privacy Control, and
  offline.
- While off: no request, no DOM read, no listener, no storage. Proved by
  `tests/test_analytics.js` (stubs throw on any touch) and `tests/test_analytics_off_headless.js`
  (real page: identical DOM with and without the script; a flag-on variant shows the beacon the
  check would catch).
- Service worker: `analytics.js` is a precached shell file; VERSION bumped (legacy mode, hand
  bump). The worker's fetch handler is untouched, so a cross-origin image is passed through to the
  network and never cached. A failed request offline is a dropped image, not a worker error.
- Flipping it on later is a separate human change: set the endpoint, set the flag true, bump the
  service-worker VERSION, update README and SECURITY, add the privacy copy below, and add the
  vendor to the "who sees your visit" disclosure.

## Privacy copy (not live; for the flip PR)

English, for the footer or How we know:

> We count visits to this page without cookies and without storing who you are. Each count records
> which page was opened, the language, whether the page runs as an installed app, and the website
> you came from (the site name only). Your IP address is used by our counting service to tell
> visits apart for a few hours and is not stored. We do not know who you are and cannot see you
> across visits. If your browser sends Do Not Track, we send nothing.

Hindi: PLACEHOLDER. Needs a native-speaker review before use, like the other Hindi strings; do not
machine-translate into the page.

README line to replace "No accounts, no ads, no analytics": "No accounts and no ads. Visit counts
come from a cookieless counter (GoatCounter) that stores no personal data; see the privacy note."

## Open decisions for GG

1. Provider (recommendation: GoatCounter). You create the account; no key or account exists in the
   repo, and none is needed beyond the public endpoint URL.
2. Whether a lawyer confirms the "no consent banner" position for India before launch.
3. Whether to add card-view events (follow-up, touches `app.js`).
4. Whether the thresholds above are the ones you want to hold yourself to.
