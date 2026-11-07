# Notifications: two audiences, two topics

## Policy since 2026-11-06: ntfy carries gold-price information only

Owner decision. Anything about the system's own health goes to the **ops log**, not the phone. The
tables below are the 2026-09-21 inventory and are kept as history: every row marked OPS is now an
ops-log entry (see "Ops log" below), except the K3 and K4 rows named here.

What may still reach ntfy (enforced by `tests/test_ntfy_keep_list.py`, which enumerates every sender
and fails on an unlisted one):

| Key | What | Where |
|---|---|---|
| K1 | Daily gold-price summary (22K/24K/18K, change, next range, live accuracy, one line on whether another model has beaten the live one) | `daily-digest.yml`, `scripts/gg_digest.py` |
| K2 | Monday summary (the same plus the week's high and low) | `monday-push.yml`, `scripts/gg_digest.py` |
| K3 | The site shows a wrong or stale price (or does not render) | `scripts/live_freshness.py`, `scripts/live_share_card.py` (`ci-health.yml`), `render-smoke.yml`, worker-deadman forecast staleness |
| K4 | Only the owner can fix it: token expiry, data sync token rejected (HTTP 401) | `scripts/check_token_expiry.py`, `.github/actions/bot-pr-sync` |
| K5 | Public subscriber price alerts T1, T2, T3, T4, T8_MORNING, T8_EVENING, T15 | `ml/notifications.py` |

Stopped (now ops log): the share-card workflow failing twice and its recovery (`scripts/og_run_alert.py`; a card that is
wrong or stale is still K3 via `live_share_card.py`), bot PR stuck, bot-pr-sync allowlist rejection, bot_branch_push rescue, the three
ci-health "could not verify" / "required check red" / "check-price stopped" pages, the weekly cadence
digest, visitors-weekly (status word only; the owner reads GoatCounter directly), `notify-gg.yml`,
scraper-canary's push (its GitHub issue stays), ops triggers T5, T6, T7, T9, T9_ESCALATE, T10-T14,
T16-T19 (still evaluated, still deduplicated; recorded instead of POSTed), and worker-deadman's
Tanishq-silence, PR trigger health, merged-unchecked and heartbeat channels.

### Ops log

`scripts/ops_log.py` appends a section to the job summary and prints
`::warning title=ops-alert::<source>: <title> -- <body>`. Read the last week across all runs with
`python scripts/read_ops_log.py [--days 7]` (it reads each emitting workflow separately, checks the API's run
count against the rows read, and fails with a message, never prints a partial list, if `gh`, the API or a count fails;
see `docs/WEEKLY_ROUTINE.md` for the call cost). The weekly report is where the owner sees these. The repo is public: nothing
in an ops-log body may be a secret or a visitor count.

#### Ops-log file: how it is written, and what it does not cover

Every sender also appends one JSON line to `ops_log.jsonl` on the orphan branch `ops-log`
(`scripts/ops_log.py`; the pure-bash twin `scripts/ops_log_fallback.sh` for the self-hosted Windows
runner). Limits and residuals, stated so nobody has to rediscover them:

- The whole append stops after 25 s with one warning; `source`/`title` are cut at 200 characters, `body`
  at 500; lines older than 120 days go, and above about 800 KB the oldest go down to about 700 KB (the
  bash twin has the age prune, not the size cap).
- The bash twin cannot create the `ops-log` branch (it warns and writes nothing until a Python writer has).
- The bot-PR sweep and the self-hosted scrape path write with the bot PAT (`CI_MERGE_PAT`).
- A job-level `permissions:` block replaces the workflow's, so the jobs that gained `contents: write`
  repeat their workflow's other scopes (a test fails if one is dropped or lowered).
- `scraper-canary.yml` also runs on `pull_request`: a same-repository PR runs its code with a
  `contents: write` token (collaborators only; fork PR tokens are read-only; the writer skips
  `pull_request` events anyway).

Written 2026-09-21 (AQ2). Code: `ml/notification_routing.py` (routing), `ml/public_copy.py` (public
copy). Tests: `tests/test_notification_routing.py`.

| | PUBLIC | OPS |
|---|---|---|
| Secret | `NTFY_TOPIC_PUBLIC` (new) | `NTFY_TOPIC` (the pre-existing topic; the owner is already subscribed) |
| Audience | Non-technical readers | The owner only |
| Content | Gold prices, ups and downs, digests | Everything about the system's health |

**Rule, non-negotiable: routing defaults to OPS.** A message reaches PUBLIC only if its trigger id is on
`PUBLIC_ALLOWLIST` in `ml/notification_routing.py`. An id nobody classified goes to OPS. If
`NTFY_TOPIC_PUBLIC` is unset, PUBLIC messages fall back to OPS (nothing is dropped while it is being
set up). Tests fail if a trigger id the module can emit is unclassified, if any other file (workflow,
Worker, script) can reach the public secret, or if a constructed unlisted message reaches PUBLIC.

## Every notification the system can emit (AQ2a)

Verified by sweeping `ml/notifications.py` (AST, 15 `_make_alert` sites), every `ntfy.sh` call in
`.github/workflows` and `.github/actions` (8 sites), and the Worker source (13 alert types).
Current copy is quoted; where PUBLIC, the new copy is in the last column.

| # | Notification | Trigger | Current copy (title) | Audience | Reasoning | New public title |
|---|---|---|---|---|---|---|
| 1 | T1 | 7-day fall of at least 0.5% | Gold: 22K prices are down this week | **PUBLIC** | A price fact | Gold is down this week |
| 2 | T2 | 7-day rise of at least 0.5% | Gold: 22K prices are up this week | **PUBLIC** | A price fact | Gold is up this week |
| 3 | T3 | Move of Rs.150+ between readings | Gold: Rs.200 up detected (+1.4%) | **PUBLIC** | A price fact | Gold price up Rs. 200 |
| 4 | T4 | Sunday weekly digest (Monday make-up) | Gold Weekly: 22K Rs.14215 | **PUBLIC** | Digest | Weekly gold summary: Rs. 14,215 per gram |
| 5 | T8_MORNING | First run 08:00-13:59 IST | Gold morning: Rs.14215 (down Rs.115) | **PUBLIC** | Digest | Gold this morning: Rs. 14,215 per gram |
| 6 | T8_EVENING | First run 18:00-21:59 IST | Gold evening: Rs.14215 | **PUBLIC** | Digest | Gold this evening: Rs. 14,215 per gram |
| 7 | T5 | Companion model on backup | Gold: companion model running on backup | OPS | Internal; users cannot act on it | |
| 8 | T6 | Calibration unlocked | Gold: calibration unlocked | OPS | Internal milestone | |
| 9 | T7 | Daily check | Gold daily check: Rs.14215 | OPS | Body says "System working normally": a heartbeat, not a digest (T8 is the public digest) | |
| 10 | T9 | IBJA reading stale | Gold Tracker: IBJA data stale (Nd) | OPS | Data-source health | |
| 11 | T9_ESCALATE | Sustained IBJA outage | Gold Tracker: SUSTAINED IBJA outage (Nd) | OPS | Data-source health | |
| 12 | T10 | No new feature-store snapshot | Gold Tracker: feature-store snapshot gap (Nd) | OPS | Internal dataset | |
| 13 | T11 | Tanishq and IBJA both unavailable | Gold Tracker: Tanishq and IBJA both unavailable | OPS | Health. *Open question:* users may want a plain "price data is delayed" notice; that would be a new allowlisted type with its own copy | |
| 14 | T12 | Self-hosted scrape failing 3x | Gold Tracker: Tanishq self-hosted runner failing | OPS | Infrastructure | |
| 15 | T13 | No usable snapshot for 2 weekdays | Gold Tracker: direction dataset stalled (N weekdays) | OPS | Internal dataset | |
| 16 | render-smoke | Live site failed to render | Gold Tracker: live site not rendering | OPS | Ops | |
| 17 | scraper-canary | Tanishq DOM changed | Gold Tracker: Scraper DOM broken | OPS | Ops | |
| 18 | check-price | Bot PR unmerged | Gold Tracker: bot PR stuck unmerged | OPS | CI | |
| 19 | ci-health | Required check red on master | Gold Tracker: required CI check red on master | OPS | CI | |
| 20 | ci-health | Cannot read master's CI | Gold Tracker: ci-health.yml could not verify master's CI state | OPS | CI | |
| 21 | bot-pr-sync | Data diff outside allowlist | Gold Tracker: bot-pr-sync allowlist rejected a commit | OPS | CI | |
| 22 | weekly-backtest | Weekly cadence digest | Gold Tracker: weekly cadence digest | OPS | Ops digest (AP3b's `resolvable_at_n` line will land here) | |
| 23-35 | Worker (13 types) | Forecast staleness (warn, sustained, resolved, could not verify), Tanishq silence (quiet, silent, resumed, could not verify), daily heartbeat, PR trigger-health (+ could not verify), merged-PR scan (+ could not verify) | "Gold Tracker: ... (dead-man's switch)" etc. | OPS | Ops; the Worker holds only `NTFY_TOPIC` and cannot reach PUBLIC | |

Not on the list on purpose: nothing else in the repo emits a notification.

## Public copy standard (AQ2c)

Enforced by tests on the real output of every public message function:

* Plain language: no jargon (IBJA, CI, model, calibration, trigger, snapshot ...), no module names,
  file paths, URLs or internal ids in the text. The site link travels in the `Click` header.
* No directional forecast of any kind (the #1796 rule); messages describe what the price already did.
* One price format everywhere: `Rs. 14,215` (space, thousands separator). ASCII on purpose: ntfy
  titles cannot carry the rupee sign.
* Every body stands alone (it repeats the price), whatever the client shows.

**Hindi: recommend yes, but not yet.** The site is bilingual and these are the readers most likely to
prefer it. Do it as a second line in the same message (ntfy has no per-user language), after a native
speaker reviews the six templates. Machine-translating product copy without review is worse than
English only. Each template is a pure function, so adding a `hi` variant is small.

## Write-protection (AQ2d): the risk, stated plainly

On free ntfy.sh **anyone who knows a topic name can publish to it**, and every subscriber knows the
topic name (they typed it in). So any one subscriber could post anything, including a fake price or a
link, to every other subscriber, and there is no way to tell it from ours in the app.

$0 mitigations, in order of value:

1. **Unguessable name**, about 24+ random characters. It stops strangers finding the topic, not a
   subscriber who has it.
2. **A documented rotation procedure** (below). If abuse happens, change the topic and re-share.
3. **A GitHub-side poll of the public topic** (ntfy's `?poll=1`) that flags any message whose title
   does not match one of the six templates. It detects abuse within hours; it cannot prevent it.

What money or self-hosting adds: a paid ntfy plan or self-hosting supports **access control**
(write-protected topics, a publish token only we hold), which is the only real fix. Self-hosting on a
free VM is $0 in money but not in effort or reliability, and it puts the availability of a public
service on the owner's machine.

**Recommendation:** ship with 1 and 2 now, add 3 as a small follow-up, and treat a paid tier as the
answer if the audience grows beyond people the owner knows. Do not describe the topic as "official
alerts" anywhere until it is write-protected.

## Secrets and drift (AQ2e)

| Secret | GitHub Actions | Cloudflare Worker | Notes |
|---|---|---|---|
| `NTFY_TOPIC` (OPS, existing) | yes | yes | The two copies must be identical; this is the pair that drifts |
| `NTFY_TOPIC_PUBLIC` (new) | yes, `check-price.yml` notifications step only | **no** | Only GitHub publishes public messages |

Drift check (proposal, not built): the Worker already reports `ntfy.topicFingerprint` (first 8 hex of
sha256 of its topic) on `?trigger=1`. A scheduled job computes the same fingerprint from
`secrets.NTFY_TOPIC`, fetches the Worker's, and pages on mismatch. Fingerprints are safe to publish
only for an unguessable topic (32 bits of hash lets someone confirm a guess offline). The public
topic is held by GitHub alone, so it has nothing to drift against.

## Subscribe path for non-technical readers (AQ2f), proposal only

A short "Get price alerts" section on the site (no new page): three steps, plain words.

1. Install the free **ntfy** app (App Store / Google Play links).
2. Tap **one-tap subscribe** (an `ntfy://` link that opens the app on the public topic).
3. Or scan the QR code.

Needs: the public topic name, the two store links, a QR image, EN/HI strings, loading/empty/error
states per the frontend rules. Because the topic name is embedded in the page, it is public by
construction, which is why the write-protection section matters. Not built: it is user-facing copy and
needs the topic name first.

## Setup: what the owner must do

1. Choose the public topic name: 24+ random characters, e.g. `gold-` followed by 24 random letters and
   digits. Do not reuse or derive it from the ops topic.
2. GitHub: Settings, Secrets and variables, Actions, **New repository secret**, name
   `NTFY_TOPIC_PUBLIC`, value the topic name.
3. Merge the PR carrying this file. Until step 2 is done, public messages keep going to the ops topic.
4. After both, the next digest should arrive on the public topic only. Confirm on a phone
   subscribed to it **and** with `https://ntfy.sh/<public-topic>/json?poll=1&since=1h`.

Rotation: create a new name, replace the GitHub secret, tell subscribers, and stop using the old one.
