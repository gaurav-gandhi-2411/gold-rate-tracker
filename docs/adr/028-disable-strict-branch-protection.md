# ADR 028 — Disable `strict` Required-Status-Checks and Admin Enforcement on `master`

**Status:** Accepted — implemented directly against GitHub branch protection (no code change).

**Date:** 2026-08-06

**Deciders:** GG (owner), CC (implementor)

---

## Context

10 PRs (#293–#300, #459, #633 — nine Dependabot version-bump PRs plus one CI-gate addition) were
stuck unmergeable, failing with GitHub's "2 of 2 required status checks are expected" even after
their `lint`/`pwa-js` checks had genuinely passed on their head SHA.

Diagnosed rather than assumed (see PR discussion history same day): the checks were correctly
linked and passing — this was **not** a recurrence of bug #4 (checks reported against the wrong
SHA, unlinked from the PR). `master`'s branch protection had `required_status_checks.strict: true`,
which requires a PR's checks to be re-verified against the *current* tip of `master` before merge
is allowed. `master` receives automated commits from `check-price.yml` and related workflows —
price updates, OG image refreshes, fusion-snapshot updates — at roughly a 15–30 minute cadence
during active hours. Every one of these advances `master`'s tip and, under `strict`, invalidates
every open PR's "up to date" status, regardless of what the PR actually touches.

A structural point beyond the immediate friction: every one of those automated commits carries
`[skip ci]` in its message, which suppresses `lint.yml`'s `push`-triggered run on `master` itself
(see `service-worker.js`'s CACHE INVALIDATION CONTRACT comment and `lint.yml`'s own header comment
for the precedent — this is the same `[skip ci]` mechanism that caused the VERSION-bump gap fixed
in PR #252, restated here for a different consequence). `master`'s own tip was not being freshly
re-verified by the commits that `strict` was requiring every PR to catch up to. `strict` was
enforcing a guarantee — "only merge against a verified-current base" — that had already broken for
exactly the commit category causing the friction.

Investigated whether an existing mechanism already mitigated this: no GitHub merge queue is
configured (`merge_queue: null`), and no scheduled branch-auto-sync workflow exists in
`.github/workflows/`.

Separately, `enforce_admins` was found to be `true` (not `false` as initially assumed when this
change was scoped) — meaning `gh pr merge --admin` could not bypass branch protection either,
which independently explained why `--admin` attempts were failing with the same error.

## Decision

Two changes to `master`'s branch protection, nothing else:

- `required_status_checks.strict`: `true` → `false`. `lint` and `pwa-js` remain required — a PR
  cannot merge without them passing on its own head SHA — but merge no longer requires
  re-verification against `master`'s latest tip first.
- `enforce_admins.enabled`: `true` → `false`, confirmed as an explicit, separate decision (not
  assumed) once the discrepancy above was surfaced.

`required_status_checks.contexts` (`["lint", "pwa-js"]`) and every other branch protection field
(`required_signatures`, `required_linear_history`, `allow_force_pushes`, `allow_deletions`,
`block_creations`, `required_conversation_resolution`, `lock_branch`, `allow_fork_syncing`) are
byte-identical before and after — verified by recording the full config before the change and
diffing field-by-field after, not assumed from the two PATCH/DELETE calls alone.

### Verified, not assumed, that the gate still bites

A throwaway PR (#676) with a deliberately-broken `lint` (unused import/variable) was opened against
`master` after both changes. Result: `lint` failed as expected, and GitHub reported
`mergeable: MERGEABLE` (no git conflict) with `mergeStateStatus: BLOCKED` — proving branch
protection still refuses to merge a PR with a failing required check, with `strict:false` and
`enforce_admins:false` both in effect. Closed without merging; branch deleted.

## Alternatives considered

**Keep `strict`, add a scheduled branch-auto-sync workflow.** Preserves the full
re-verify-against-current-master guarantee, but only narrows the race window rather than
eliminating it (a bot commit can still land between a sync and the merge click), and burns real CI
minutes re-running `lint`+`pwa-js` (each ~3 min) across every open PR on every sync cycle against
commits that — per the `[skip ci]` point above — never carried genuine code risk in the first
place. Rejected: new maintained infrastructure for a problem it only partially solves.

**GitHub native merge queue.** Purpose-built for exactly this class of problem and would fully
solve it. Rejected as disproportionate machinery for a single-collaborator repo (see Consequences)
— revisit if the collaborator count changes (see Revisit trigger).

**Scope `strict` to exempt `[skip ci]`/data-only commits specifically.** GitHub's native branch
protection has no path- or message-based conditionality for `required_status_checks.strict` — this
would require a custom bot/Action synthesizing an "effectively up to date" status, which is
meaningfully more custom engineering and a new instance of the class of control this repo's own
history has already been burned by once: one whose actual surface (which commits it correctly
recognizes as safe to ignore) could quietly stop matching what its name implies. Rejected as
disproportionate complexity relative to the risk being managed.

## Consequences

**Positive:**
- The 10 stuck PRs are unblocked as soon as their own checks pass, without a sync-then-immediately-merge
  race against the next bot commit.
- No new infrastructure to maintain, monitor, or have silently drift out of sync with its own
  assumptions.
- `lint`/`pwa-js` still unconditionally gate every merge — verified directly (PR #676), not assumed
  from reading the config change alone.

**Negative / honest limits — residual risk accepted:**
- Two genuinely overlapping **code** PRs open concurrently could now merge without being
  cross-tested against each other's changes (each was only verified against whatever `master`
  looked like when its own checks last ran, not against the other PR). `strict` was the mechanism
  that would have caught this.
- This repo has exactly one collaborator with write access (`gaurav-gandhi-2411`; see
  `docs/RUNBOOK.md`), and this session's actual PR pattern was small, largely sequential, and
  rarely file-overlapping (the one real conflict class observed this session — #294/#295 needing
  `@dependabot recreate` — was a genuine content conflict from workflow-file drift, not something
  `strict` would have prevented either, since `strict` only re-verifies *checks*, not merge-ability
  against concurrent diffs). The probability of the scenario `strict` protected against is low for
  this repo's actual usage, not a theoretical risk being waved away — but it is not zero, and this
  ADR does not claim otherwise.
- `enforce_admins:false` also means any future admin-level merge (via `--admin` or the GitHub UI's
  admin override) bypasses required checks entirely, not just the `strict` re-verification. This is
  a broader trust extension than the `strict` change alone and was made as an explicit, separate
  confirmed decision, not a default assumption.

## Revisit trigger

Reconsider `strict` (or move directly to a GitHub merge queue) if either becomes true:
- A second regular contributor joins with write access — the solo-maintainer assumption underlying
  "low probability of concurrent conflicting code PRs" no longer holds.
- Concurrent, file-overlapping code PRs become a regular pattern rather than the rare exception
  observed to date.

No calendar-based revisit — this is a usage-pattern-triggered reassessment, not a scheduled one.
