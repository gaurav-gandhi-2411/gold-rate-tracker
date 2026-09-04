# Dead-man's switch (Cloudflare Worker)

Independently checks that the **public** site — `data/forecast.json`, fetched
from `https://gaurav-gandhi-2411.github.io/gold-rate-tracker/data/forecast.json`,
never anything under `github.com/.../repos/...` — is fresh, and alerts to the
existing ntfy topic on TWO independent channels:

1. **Forecast staleness** — `predicted_at` age, **WARN (>=9h)** / **ESCALATE
   (>=10h)** (V1, audit 2026-09-04: re-derived from the post-recovery
   inter-run gap distribution — the old WARN=5h sat below the current
   median gap and paged on ~39% of normal cycles; see `src/deadman.mjs`'s
   own comment for the full arithmetic). Catches "the pipeline stopped
   running entirely."
2. **Tanishq confirmation silence** (Q4, audit 2026-09-03; thresholds
   recalibrated + corroboration added R2, audit 2026-09-04) — `scraped_at`
   age (the last SUCCESSFUL Tanishq reading), **WARN (>=48h)** / **ESCALATE
   (>=72h)**, corroborated by `tanishq_selfhosted_health.json` (also public,
   same origin, zero new dependency): once past WARN, if that file's own
   `last_updated_utc` is ALSO stale past 9h (3 missed 3h cycles — meaning
   the self-hosted job hasn't executed at all, not just failed to scrape),
   escalates immediately regardless of the 72h wait. Catches a scenario
   channel 1 structurally cannot: the self-hosted runner dying permanently.
   `predicted_at` stays fresh forever via the IBJA-calibrated fallback even
   with Tanishq dead for weeks, T12 (`ml/notifications.py`) only fires when
   the runner is online and jobs are *failing* (not when it's offline, by
   design — see `docs/RUNBOOK.md`), and the page itself gives users no
   indication Tanishq confirmation has stopped (`price_source` stays
   `"ibja_calibrated"` and renders identically whether Tanishq confirmed 2h
   ago or 3 weeks ago — see `fix/tier-degradation-visible`). Before this
   channel existed, a permanent runner failure produced **zero alerts from
   anything, ever**.

   Thresholds derived from the observed gap distribution between successful
   Tanishq readings over the last 30 days (n=154 gaps: median 2.93h, p90
   5.99h, p95 14.21h, p99 29.43h, max 61.31h) against an explicit
   false-alarm budget of **<=1 false WARN/month**: the original WARN=24h
   produced ~4 false alarms/month (a muted alert is a non-functioning
   control) and sat *below* the largest observed normal gap (61.31h, a
   known/diagnosed transient runner outage, not a failure) — guaranteeing
   false alarms. WARN=48h and ESCALATE=72h both clear the budget (1/month
   and 0/month respectively in the same 30-day sample) — see the
   constants' own comments in `src/deadman.mjs` for the full arithmetic.

It also posts a low-priority **daily heartbeat** — stating BOTH channels'
current level/age — so its own silence is informative (G4a): without it, no
ntfy message could ever distinguish "the site is fine" from "the switch
itself died" (Cloudflare account issue, quota exhaustion, a bad deploy). One
heartbeat per IST calendar day, independent of whether either channel also
fired that day.

**Why this exists, and why it's not a GitHub Actions workflow:** every other
alert in this project (T1–T13 in `ml/notifications.py`, the CI-scheduled
`check-price.yml` staleness checks) runs *inside* the same GitHub Actions
pipeline it's meant to catch failing. If GitHub Actions itself stops firing
(quota exhaustion, an account issue, a scheduling outage), every one of those
alerts goes silent at exactly the moment they're needed. This switch runs on
Cloudflare's own Cron Trigger scheduler — a completely independent
infrastructure provider — so it keeps checking even if GitHub Actions never
runs again.

## What's here

- `src/deadman.mjs` — pure staleness-classification and alert-dedup logic. No
  `fetch`, no KV, no Workers runtime APIs — fully unit-testable with plain Node.
- `src/index.mjs` — the actual Worker: wires `deadman.mjs` to a real `fetch()`
  of the public site, a KV-backed "last alert sent" state (so a multi-hour
  outage doesn't re-alert every 30 min), and a POST to `ntfy.sh`.
- `test/deadman.test.mjs` — 58 cases (`node --test`), including a synthetic
  stale payload asserting the ESCALATE alert actually fires, dedup-window
  behavior, fail-closed handling of an unreachable/unparseable payload,
  (Q4) the Tanishq-silence channel's own classification/dedup/alert-copy
  cases plus the "one fetch failure alerts once, not twice" guard, and
  (R2c) the health-file corroboration logic in both directions (stale
  health escalates early; fresh health suppresses escalation and names the
  scrape-failure hypothesis instead) plus its own fail-closed handling of a
  missing/unparseable health signal.
  Wired into CI as a lint gate only (`.github/workflows/lint.yml`,
  `pwa-js` job) — this does **not** make the switch's own operation depend
  on GitHub Actions in any way; it only lints the source before deployment.
- `wrangler.toml` — Cron Trigger every 30 min, KV binding for dedup state.

## Manual deployment — do this by hand, in order

Everything below was verified against a live, already-authenticated
Cloudflare account during this session (`wrangler whoami` →
`gg5678g@gmail.com`, account ID `65e3c4a67e072063692db52be17bab3d`, same
account the retired `gold-rate-tanishq-worker` ran on 2026-06-13–2026-06-25
with zero billing issues). Nothing here has been deployed yet — the code is
written and tested, not live.

### 1. Confirm you're logged in as the right account

```
cd worker-deadman
wrangler whoami
```

Expect `gg5678g@gmail.com` / account ID `65e3c4a67e072063692db52be17bab3d`.
If it shows a different account, run `wrangler login` first.

### 2. Create the KV namespace for dedup state

```
wrangler kv namespace create DEADMAN_STATE
```

This prints a namespace `id` (a 32-char hex string). Copy it.

### 3. Put that id into `wrangler.toml`

Open `worker-deadman/wrangler.toml` and replace the line

```
id = "REPLACE_WITH_KV_NAMESPACE_ID"
```

with the id from step 2. Save.

### 4. Set the ntfy topic as a Wrangler secret

This must be the **exact same topic** already stored as this repo's
`NTFY_TOPIC` GitHub Actions secret (Settings → Secrets and variables →
Actions), so the switch posts to the same place you already subscribe to.
Neither this session nor any file in this repo has ever had that value —
it's a secret for a reason, so you'll need to type it yourself:

```
wrangler secret put NTFY_TOPIC
```

Paste your topic value when prompted (no echo).

### 5. Deploy

```
wrangler deploy
```

Expect output ending in a `https://gold-rate-tracker-deadman.<your-subdomain>.workers.dev`
URL and a `Cron Trigger` line showing `*/30 * * * *`.

### 6. Confirm it's deployed (not yet proof it's live — see step 8)

Two independent checks — do both:

**a. Manual on-demand trigger.** The Worker also responds to a plain HTTP
GET (separate from the cron path, for exactly this purpose). Visit the
`*.workers.dev` URL printed by step 5's `wrangler deploy` output, or:

```
curl https://gold-rate-tracker-deadman.<your-subdomain>.workers.dev
```

Expect a JSON body like `{"level":"ok","ageHours":1.2,"sent":false}` (or
`"warn"`/`"escalate"` if the real site happens to be stale right now — if
so, you should also receive a real ntfy notification within a few seconds).
A non-200 response or a body missing `level`/`ageHours` means step 5 did
not actually produce a working deployment — stop and re-check step 5's
output before continuing.

**b. Cron registered.** In the Cloudflare dashboard, go to:
`https://dash.cloudflare.com/<account-id>/workers/services/view/gold-rate-tracker-deadman/production/triggers`
(replace `<account-id>` with `65e3c4a67e072063692db52be17bab3d`, the
account confirmed in step 1) — the **Triggers** tab. Confirm a Cron Trigger
row reads `*/30 * * * *` and its toggle is on/enabled. This confirms the
trigger is *registered*, not that it has *fired* — that's step 8b.

### 7. Set up log visibility before you need it

Dashboard: `https://dash.cloudflare.com/<account-id>/workers/services/view/gold-rate-tracker-deadman/production/observability/logs`
(**Workers & Pages → gold-rate-tracker-deadman → Logs** tab). Leave this
tab open, or in a second terminal run:

```
wrangler tail gold-rate-tracker-deadman
```

Either surface will show you a live invocation the moment one happens —
you need this open *before* step 8b, not after, since Cloudflare Logs only
capture invocations from the moment observability is first queried onward
in some dashboard views (`wrangler tail` always captures from when you
start it).

### 8. Mandatory: prove the Worker is genuinely live, not just deployed

A successful `wrangler deploy` (step 5) and a green curl (step 6a) only
prove the code runs when *you* invoke it by hand. Neither proves the cron
trigger fires unattended, and neither proves an alert actually reaches
ntfy end-to-end. Do both of the following before considering this
deployment done — do not skip on the assumption steps 5–6 were enough.

**a. Force a real ESCALATE alert (proves the alert path end-to-end).**

1. In `worker-deadman/src/deadman.mjs`, temporarily change:
   ```
   export const WARN_THRESHOLD_HOURS = 9;
   export const ESCALATE_THRESHOLD_HOURS = 10;
   ```
   to `0` and `0.01` respectively.
2. `wrangler deploy`
3. Hit the `.workers.dev` URL once (same command as step 6a).
4. Confirm within a few seconds:
   - the curl response body shows `"level":"escalate"`
   - a real ntfy notification arrives on your phone/client for the topic
     set in step 4, titled "Gold Tracker: dead-man's switch ESCALATE"
5. **Revert** the two threshold values back to `5` and `10` in
   `src/deadman.mjs`.
6. `wrangler deploy` again. Re-run step 6a's curl and confirm `"level"`
   has returned to `"ok"` (or `"warn"`, matching the site's real current
   age — never `"escalate"` unless the site is genuinely stale). Do not
   leave the lowered thresholds live even briefly longer than needed to
   see the ntfy alert land.

**b. Confirm the cron fires unattended (proves the Worker survives without
being manually poked).**

With step 7's log view already open, wait up to 30 minutes (the cron
interval) without touching the Worker. Confirm a new invocation appears
in Logs / `wrangler tail` with a trigger source of `cron` (not `fetch`/
on-demand). If 30 minutes pass with zero cron-triggered invocations, the
Trigger shown in step 6b is registered but not actually firing — treat
that as undeployed and escalate rather than assuming it will start later.

Both 8a and 8b must pass. 8a alone proves the alert *logic and delivery*
work; 8b alone proves the *scheduler* works. Neither implies the other —
this repo's own history is a scheduled-trigger silently not firing while
everything else about the workflow looked healthy (`docs/RUNBOOK.md`,
2026-08-27 and 2026-09-03 incidents), so do not accept "the Trigger is
listed in the dashboard" as proof it will actually fire.

## What this does NOT do

- It does not fix or restart anything — it only alerts. If GitHub Actions is
  genuinely down, a human still has to intervene (`workflow_dispatch` a
  run, or diagnose why the schedule stopped firing — see
  `docs/RUNBOOK.md`).
- It does not depend on, call, or authenticate to any GitHub API — it reads
  only the public GitHub Pages site, same as any anonymous visitor.
- It does not replace T9/T9_ESCALATE (IBJA-source staleness) or any other
  entry in `ml/notifications.py`'s alert catalog — those catch different,
  narrower failure modes and still run inside the pipeline. This is
  specifically the "what if the pipeline's alerting itself goes silent"
  backstop.

## Cost

Zero, at the traffic this produces. Verified against Cloudflare's published
Workers Free plan limits (`developers.cloudflare.com/workers/platform/limits/`,
checked 2026-08-27): 48 cron invocations/day against a 100,000/day quota, 1
Cron Trigger against a 5-per-account Free-plan limit (0 currently in use —
the retired worker's triggers were removed with it). KV usage (`kv/platform/limits/`,
same date): at most 48 writes/day and 48 reads/day against Free-plan quotas
of 1,000 writes/day and 100,000 reads/day.
