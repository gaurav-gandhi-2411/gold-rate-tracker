# Project Spec: Φ25 — Clean-IP Tanishq Fetch via Cloudflare Worker + repository_dispatch

## Goal
Restore reliable, near-real-time Tanishq price capture — the core purpose of the
product (early notification of price drops). The in-CI scrape fails on 12-45% of runs
because GitHub Actions runner IPs (and Indian IPs) hit a Cloudflare hard-block. A
Cloudflare Worker running from a non-Indian edge IP has been VERIFIED to fetch the page
with HTTP 200 and parse the live rates (probe 2026-06-11: outbound_cf_ray …-SIN,
status 200, 22k/24k/18k parsed, rates_valid true).

Move the price FETCH onto a scheduled Cloudflare Worker (clean IP, ~400ms, no browser),
which POSTs the rates into the existing pipeline via GitHub `repository_dispatch`. The
entire existing pipeline (IBJA append, inference, drop-alert in update-and-notify.js,
commit, feature store) runs UNCHANGED. The existing in-CI Playwright scrape is KEPT as a
fallback for any cycle the Worker misses.

## Verified facts (probe 2026-06-11)
- CF Worker edge (Singapore PoP) → tanishq.co.in/gold-rate.html → 200, full HTML, prices
  in static `span.goldpurity-rate` data attributes. parseGoldRates()/validate() (existing
  scrape.js exports) parse it correctly.
- GitHub Actions runner IPs and local Indian IPs → 403 hard-block (requests path 0/8
  success in production; Playwright carries 100% of successful runs, ~55-87%).
- repository_dispatch architecture confirmed by Φ24-diagnosis: update-and-notify.js
  consumes stdin {timestamp, 22k, 24k, 18k}; reading["22k"] must be truthy; the rest of
  the pipeline is dispatch-agnostic.

## Current state (read CURRENT_STATE.md + docs/PROGRESS.md first)
- scraper/scrape.js exports parseGoldRates(html) and validate() — REUSE these in the
  Worker (copy, since the Worker is a separate deploy target — keep logic in sync).
- scraper/update-and-notify.js: appends to prices.json, fires ntfy drop alert if 22k
  dropped >= DROP_THRESHOLD (100). Unchanged.
- check-price.yml: schedule cron "0 */3 * * *", workflow_dispatch, push triggers; scrape
  step has continue-on-error: true; commit step git-adds data/*.
- H5 (Φ22) activates on calibration flip (~June 12) as the customer-facing floor when a
  price IS stale; independent of this change.

## Scope (in)
1. **Cloudflare Worker** (new, lives in repo under `worker/` or `cf-worker/`):
   - Scheduled (cron) trigger every 3h (align with or slightly offset from the CI cron).
   - Fetches the Tanishq gold-rate page with the verified Chrome-UA + Accept headers.
   - Parses 22k/24k/18k via the same regex logic as parseGoldRates(); runs the same
     validate() sanity checks (values present + plausible range). On parse/validate
     failure or non-200/CF-body: do NOT dispatch (let the in-CI Playwright fallback
     handle that cycle); log the failure.
   - On success: POST repository_dispatch to the GitHub API with
     event_type "tanishq-price" and client_payload {22k,24k,18k,timestamp}.
   - The GitHub token is read from a Worker secret (wrangler secret), NEVER hardcoded.
   - wrangler.toml committed (config), but NO secret values in the repo.
2. **check-price.yml — 2 diffs** (minimal, per Φ24-diagnosis):
   - Add `repository_dispatch: types: [tanishq-price]` to `on:`.
   - Make the scrape step branch on event: if event_name == repository_dispatch, build
     the reading from client_payload (via ENV vars, NOT shell-interpolated into the
     script — injection-safe) and pipe to update-and-notify.js; else run the existing
     scrape.js → update-and-notify.js path unchanged.
   - Do NOT reorder any other pipeline step.
3. **Token + secrets doc** `docs/CLEAN_IP_FETCH.md`: how the fine-grained GitHub PAT is
   scoped (single repo, `actions: write` / contents as needed for dispatch only — NOT
   broad), how it's stored as a Worker secret, the Worker deploy/cron setup, and how to
   rotate. Owner-actionable steps clearly marked (token creation, wrangler deploy).
4. **Tests**:
   - Worker parse/validate logic unit-tested (mock HTML: valid → dispatch payload;
     403/CF-body → no dispatch; missing span → no dispatch; out-of-range → no dispatch).
     No live network in tests.
   - check-price.yml dispatch branch: a test/CI-lint confirming the repository_dispatch
     reading is built from client_payload and reaches update-and-notify.js identically
     to a scrape (same stdin shape). Mock the payload.
   - Confirm the existing Playwright-path tests still pass unchanged.

## Scope (out — do NOT build)
- Deleting/replacing the in-CI Playwright scrape (it's the fallback for missed cycles).
- Any change to prices.json schema, update-and-notify.js logic, IBJA, inference, H5,
  calibration, notifications, or the feature store.
- Storing any secret/token value in the repo.
- A self-hosted runner (RCE risk on a public repo — explicitly forbidden).
- Oracle/VM provisioning (the CF Worker is the verified host; VM not needed).

## Tech stack
- Cloudflare Worker (JS, Workers free tier — verified working). wrangler for deploy.
- Existing Node scrape.js parse/validate logic (copied into the Worker, kept in sync).

## Verification commands
```yaml
- name: tests-js
  cmd: node --test            # worker parse/validate + dispatch-branch tests
  required: true
- name: lint
  cmd: ruff check . && ruff format --check .   # python untouched; keep green
  required: true
```

## Subagent usage rules
- executor writes/edits; verifier runs tests/lint. Orchestrator delegates code.

## Escalation rules (orchestrator must ask before doing)
- If the dispatch branch cannot be added WITHOUT reordering check-price.yml pipeline
  steps, STOP and report.
- If keeping the Worker's parse logic in sync with scrape.js requires changing
  scrape.js's existing exports/behavior, STOP and report (the Playwright path must stay
  byte-stable).
- The Worker must NEVER hardcode the token; if wrangler secret flow is unclear, STOP and
  document for the owner rather than guessing.
- Confirm the repository_dispatch payload is consumed via ENV vars, not shell-string-
  interpolated (injection safety) — if the safe path is unclear, STOP.

## Hard rules (existing project)
- The in-CI Playwright path stays as fallback; do not remove it.
- repository_dispatch reading must produce prices.json entries identical in shape to a
  scrape (timestamp, 22k, 24k, 18k) — no schema drift.
- No secret values committed. Token is fine-grained, single-repo, minimum scope.
- Tests mock HTTP; no live network (norm #11).
- Branch hygiene per CONTRIBUTING.md; local master is a read-only mirror; no [skip ci];
  CI green.

## Budget
- Soft target: 1 CC session. Hard cap: escalate after 15 executor invocations.

## Success criteria (verify ALL before done)
- Worker code committed (worker/ + wrangler.toml, no secrets); parse/validate unit tests
  green (valid→payload, 403/CF/missing/out-of-range→no dispatch).
- check-price.yml accepts repository_dispatch[tanishq-price] and builds an
  injection-safe reading from client_payload that reaches update-and-notify.js
  identically to a scrape; existing scheduled/Playwright path unchanged; no step reorder.
- docs/CLEAN_IP_FETCH.md: owner steps for PAT creation (scoped), wrangler secret set,
  Worker deploy + cron, rotation.
- All CI green; clean squash body; CURRENT_STATE.md + PROGRESS.md (Φ25) + Decision Log
  updated (clean-IP fetch architecture; Playwright now fallback; requests-in-CI path
  noted as 0% on runner IPs).
- OWNER POST-MERGE (documented, not code): create the scoped PAT, set it as the Worker
  secret, `wrangler deploy` the Worker, confirm one repository_dispatch run lands a fresh
  price in prices.json with source=repository_dispatch — the real proof the fix is live.

## Build order (orchestrator may adjust)
1. Worker: fetch + parse/validate + dispatch POST; unit tests (mock HTML) first.
2. wrangler.toml (cron trigger, no secrets); docs/CLEAN_IP_FETCH.md owner steps.
3. check-price.yml 2-diff (repository_dispatch trigger + injection-safe dispatch branch);
   dispatch-branch test; confirm Playwright path tests unchanged.
4. CURRENT_STATE.md + PROGRESS.md + Decision Log.
5. (Owner, post-merge) PAT + wrangler secret + deploy + verify a live dispatch lands.
