# Dead-man's switch (Cloudflare Worker)

Independently checks that the **public** site — `data/forecast.json`, fetched
from `https://gaurav-gandhi-2411.github.io/gold-rate-tracker/data/forecast.json`,
never anything under `github.com/.../repos/...` — is fresh, and alerts to the
existing ntfy topic at **WARN (>=5h stale)** / **ESCALATE (>=10h stale)**.
It also posts a low-priority **daily heartbeat** so its own silence is
informative (G4a): without it, no ntfy message could ever distinguish "the
site is fine" from "the switch itself died" (Cloudflare account issue, quota
exhaustion, a bad deploy). One heartbeat per IST calendar day, independent of
whether a WARN/ESCALATE also fired that day.

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
- `test/deadman.test.mjs` — 26 cases (`node --test`), including a synthetic
  stale payload asserting the ESCALATE alert actually fires, dedup-window
  behavior, and fail-closed handling of an unreachable/unparseable payload.
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

### 6. Confirm it's live

Two independent checks — do both:

**a. Manual on-demand trigger.** The Worker also responds to a plain HTTP
GET (separate from the cron path, for exactly this purpose). Visit the
`*.workers.dev` URL printed in step 5, or:

```
curl https://gold-rate-tracker-deadman.<your-subdomain>.workers.dev
```

Expect a JSON body like `{"level":"ok","ageHours":1.2,"sent":false}` (or
`"warn"`/`"escalate"` if the real site happens to be stale right now — if
so, you should also receive a real ntfy notification within a few seconds).

**b. Cron actually firing.** In the Cloudflare dashboard: **Workers &
Pages → gold-rate-tracker-deadman → Triggers** tab, confirm the Cron
Trigger `*/30 * * * *` is listed and enabled. After waiting up to 30
minutes, **Logs** (or `wrangler tail` run locally) should show an
invocation with `"source":"cron"` in the trace.

### 7. (Optional but recommended) Force a real end-to-end alert test

To see a real ntfy notification arrive without waiting for an actual
outage: temporarily lower `WARN_THRESHOLD_HOURS`/`ESCALATE_THRESHOLD_HOURS`
in `src/deadman.mjs` to something below the site's current real age (e.g.
`0` / `0.01`), `wrangler deploy`, hit the `.workers.dev` URL once (step 6a),
confirm the ntfy notification arrives, then **revert the thresholds back to
5/10 and redeploy**. Do not leave the lowered thresholds live.

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
