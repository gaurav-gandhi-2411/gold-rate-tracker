# ADR 021 — H5: IBJA-Calibrated Fallback Price (display provenance)

Status: Proposed (build behind existing calibration.valid gate; activates on unlock ~2026-06-19)
Date: 2026-06-08
Supersedes/relates: ADR 016 (scraper hardening; H5 was the deferred item), ADR 017
(un-blocked H5 on calibration), Φ20 (stale banner reads scraped_at).

## Context
The Tanishq retail price is scraped from a Cloudflare-protected site via shared GitHub
Actions runner IPs. CF blocks them at the IP-reputation level; retries cannot defeat
this on the free tier (ADR 016). On a failed scrape, no new prices.json entry is
written, so the last-good entry silently persists and the page shows a stale retail
price. Φ20 made this honest (banner reads scraped_at, "last confirmed price from Xh
ago"), but the page still shows nothing fresher.

IBJA pm_916 is fetched via plain HTTP (not Cloudflare-protected), is reliably fresh,
and is the authoritative India benchmark. data/calibration.py fits a HuberRegressor
mapping ibja_pm_916 (INR/g) -> tanishq_22k (INR/g): `tanishq_22k = slope*ibja_per_g +
intercept`, with residual_std available once n>=30 pairs. The model is currently
dormant (valid:false, ~21-26/30 pairs, ETA ~2026-06-19) and auto-activates at n=30.

H5 uses this calibration to serve an IBJA-derived estimate of the retail price when the
Tanishq scrape is stale, so the customer sees a fresh approximate price instead of a
stale one. This ADR pins the provenance and honesty decisions; the spec implements them.

## Decision

### 1. A dedicated provenance field `price_source` in forecast.json
inference.py writes `price_source: "tanishq_scrape" | "ibja_calibrated"`.
Rejected: inferring the state from `chronos_companion.calibration_applied`. That flag
denotes *Chronos horizon* calibration — a semantically different thing from
*current-price* fallback. Conflating them is a latent bug. Provenance gets its own field.

### 2. One shared staleness threshold (8h), not two
H5 injects the IBJA-derived price into the displayed current price only when the
Tanishq scrape is older than the SAME 8h threshold the Φ20 banner uses. A single shared
constant. Rejected: a separate (e.g. 24h) H5 trigger, which would create an 8-24h dead
zone where the banner says "stale" but the page still shows the stale scraped price
with no fallback — the exact confusion H5 exists to remove.

### 3. Three honest display states
| State | Condition | Banner |
|---|---|---|
| Scraped-fresh | price_source==tanishq_scrape AND scrape age < 8h | hidden |
| IBJA-derived-fresh | price_source==ibja_calibrated AND IBJA fresh | "Approximate — live retail scrape unavailable; estimated from IBJA benchmark" |
| Genuinely stale | neither fresh (e.g. IBJA also failed) | "Live price update unavailable — last confirmed price from Xh ago" (Φ20 copy) |

### 4. Show the estimate as a range, not a false-precise point
An IBJA-derived price is an estimate and is displayed as one: "≈ Rs.X (est. Rs.low–Rs.high)"
using ±residual_std (post-fit). Rejected: a bare point number, which implies precision
the model doesn't have and contradicts the project's honesty posture (ADR 012). The
honest band is a product differentiator vs. false-precise "gold tip" apps. residual_std
is fit in INR/g and MUST be scaled correctly to the displayed 22k price.

### 5. The feature store is NOT touched — store and display deliberately diverge
The feature store keeps recording the last *scraped* tanishq_22k with its true (stale)
asof_date (carry detectable via asof_date < as_of_date). An IBJA-derived price is NEVER
written into the store's tanishq_22k. Rationale: the store is ground truth for future
model training and must capture only genuinely observed retail prices; the display is
best-available for the user. This divergence is correct and intentional; documented in
FEATURE_STORE.md so it is not "fixed" later.

### 6. Build now, behind the existing gate
All H5 logic is gated on `calibration.valid`. When false (today), behavior is identical
to current. When the refit flips it true at n=30 (~2026-06-19), H5 activates
automatically. T6 (calibration-unlock alert) already notifies the owner. Note: on the
flip, a stale page may visibly jump from the carried scrape price to the IBJA-derived
estimate — expected, not a glitch.

## Consequences
- Positive: customer sees a fresh, honestly-bounded estimate through a scrape outage;
  the gap the user originally reported is closed, not merely labeled.
- Positive: provenance is explicit and consumable system-wide.
- Negative/accepted: an estimate is not the true retail price; the band + "Approximate"
  label make this explicit. If IBJA also fails, the page falls through to the honest
  genuinely-stale state.
- The estimate is only as good as the calibration; residual_std communicates that.
