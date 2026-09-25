# GitHub Pages deploy: legacy build → Actions build switch-over

## Why this exists

`service-worker.js`'s `VERSION` const has to change every time a precached shell file
(HTML/CSS/JS/icons/fonts — the full list is `scripts/sw_shell_files.py`'s output) changes,
or an installed client never re-fetches the new shell (lint.yml's `sw-version-guard` job
enforces this). Until this change, `VERSION` was hand-typed in every such PR, and several
open PRs all landed on `v58`/`v59` independently — they conflict with each other on merge
purely because the version string is a shared, hand-picked value.

The fix: `VERSION` is now generated automatically from a sha256 hash of the shell files'
exact bytes (`scripts/stamp_sw_version.py`), computed at **build time**, so no PR ever
needs to touch it and PRs can never collide on it again.

The catch: GitHub Pages for this repo currently builds via the **legacy Jekyll pipeline**
straight off `master` —

```
$ gh api repos/gaurav-gandhi-2411/gold-rate-tracker/pages
{"build_type": "legacy", "source": {"branch": "master", "path": "/"}, ...}
```

— which has no step that can run a script. There is no way to compute a content hash
inside that build. The only way to stamp `VERSION` at build time is to build via a GitHub
Actions workflow instead (`.github/workflows/pages-deploy.yml`, added by this PR), and
Actions-based Pages builds only run once a repo's **Pages source setting** is switched to
"GitHub Actions" — a **Settings** change, which only GG can make (not something any CI
job or script can do on its own).

## What is safe to merge now, and why

Everything in this PR is inert until GG performs the switch-over below:

- `pages-deploy.yml`'s `deploy` job is gated on `if: vars.PAGES_BUILD_MODE == 'actions'`,
  a **repository variable** that does not exist yet. With no variable set, the condition
  evaluates false and the job never runs, on every push to `master`, indefinitely.
- Even if that job somehow ran, GitHub only *executes* an Actions-based Pages-deploying
  workflow's `actions/deploy-pages` step once the Pages **source setting** itself is
  "GitHub Actions" — while it's still "Deploy from a branch" (its current state), the
  legacy build keeps being the only thing that publishes the live site, regardless of
  what any workflow does.
- `scripts/check_sw_version_guard.py` (the new guard logic, see below) reads the same
  `PAGES_BUILD_MODE` variable and defaults to `"legacy"` when it is unset — i.e. the
  **current, hand-bump rule stays fully in force** for every PR merged before the switch.
- No shell file's content changes in this PR. `service-worker.js`'s committed `VERSION`
  line is untouched (still the last hand-typed value) — this PR does not itself require a
  bump.

So merging this PR changes nothing about what the live site serves or what PRs are
required to do, until GG deliberately performs the steps below.

## What was verified about build-output parity (legacy vs. Actions build)

A local Jekyll/Ruby toolchain was not available in this environment (`ruby`, `bundle`,
`jekyll` all absent), so the Actions build's Jekyll step (`actions/jekyll-build-pages`)
could not be run end-to-end locally. What was verified instead:

1. **This PR does not touch `_config.yml`.** Both the legacy build and
   `pages-deploy.yml`'s `actions/jekyll-build-pages` step run Jekyll against the exact
   same committed `_config.yml`, so its `exclude:` list is honoured identically by
   construction — the same input config produces the same output file set regardless of
   which pipeline invokes Jekyll.
2. **`actions/jekyll-build-pages` is GitHub's own Actions-native replacement for the
   legacy Pages Jekyll build** (offered directly in the Pages UI's "GitHub Actions" starter
   workflow) — its stated purpose is byte-for-byte parity with the legacy build's Jekyll
   version and plugin set, not an independent reimplementation.
3. **Concretely walked `_config.yml`'s `exclude:` list against `git ls-files`** (558
   tracked files) plus Jekyll's own default excludes (dotfiles/dot-directories,
   `_`-prefixed paths, `node_modules`) to compute the exact set of files either pipeline
   would copy into the Pages output: **54 files**, all of them either the PWA shell
   (`index.html`, `app.js`, `style.css`, `service-worker.js`, `i18n.js`, `flags.js`,
   `how-we-know*`, `manifest.webmanifest`, `icons/`, `fonts/`), the 18 `data/*.json` /
   `.parquet` / `.jsonl` files not individually excluded, or root files (`README.md`,
   `LICENSE`, `favicon.svg`, `og.png`). This list is unaffected by anything in this PR.
   (Aside, unrelated to this change and not fixed here: `worker-deadman/` is not in
   `_config.yml`'s exclude list, so its 6 files are — and already were, under the legacy
   build — served at the live Pages URL too. Pre-existing, out of scope for this PR.)
4. `scripts/stamp_sw_version.py` only ever rewrites the `VERSION` line inside
   `service-worker.js` in the build workspace, and only if `--check` is not passed — it
   changes no other file, so it cannot itself alter which files end up in the Jekyll
   output set.

Net: the Actions build's file **set** is verified identical to the legacy build's by
construction (same config, same Jekyll semantics per GitHub's own action). The one
absolute confirmation still pending is a live dry run of the actual `jekyll-build-pages`
step's byte output — see step 3 of the switch-over below, which exists specifically to
catch anything this static analysis couldn't (e.g. an undocumented Jekyll version
difference).

## GG's switch-over steps

1. **Set the repository variable.** Settings → Secrets and variables → Actions →
   Variables tab → New repository variable:
   - Name: `PAGES_BUILD_MODE`
   - Value: `actions`

   At this point `pages-deploy.yml`'s `deploy` job will run on the next push to `master`
   (or via manual dispatch, step 2) and lint.yml's `sw-version-guard` will start enforcing
   the new "don't hand-edit VERSION" rule on PRs — but the live site is **still** served by
   the legacy build until step 3, because Pages' source setting hasn't changed yet.

2. **Dry-run the new build without going live.** Actions tab → "Pages Deploy (GitHub
   Actions build)" → Run workflow → run on `master`. Watch it build and (if Pages' source
   is still "Deploy from a branch") its `actions/deploy-pages` step will report the
   deployment was not applied because Pages isn't in Actions mode yet — everything up to
   that step (checkout, stamp, Jekyll build, upload-pages-artifact) still runs and can be
   inspected via the job's artifact download, which is the concrete parity check step 3
   above deferred to. Confirm in that artifact:
   - `service-worker.js`'s `VERSION` line reads `const VERSION = "sh-<16 hex chars>";`.
   - The file set matches the 54-file list in the previous section (spot-check a few:
     `index.html`, `app.js`, `data/prices.json`, `icons/icon-192.png` present;
     `scripts/`, `ml/`, `docs/` absent).

3. **Flip the actual Pages source.** Settings → Pages → Build and deployment → Source:
   change "Deploy from a branch" to "GitHub Actions". This is the step that goes live —
   from this point, `master` pushes deploy via `pages-deploy.yml`, not the legacy builder.

4. **Trigger a real deploy and verify the live site.** Push to `master` (or Actions →
   "Pages Deploy (GitHub Actions build)" → Run workflow) and wait for the `deploy` job to
   finish. Then, from a real browser (not `curl` — the check needs to exercise the actual
   service worker install path a user goes through):
   - Open `https://gaurav-gandhi-2411.github.io/gold-rate-tracker/`, hard-refresh.
   - DevTools → Application → Service Workers: confirm the active worker registers
     `service-worker.js` and, in the Sources/Network tab, confirm the fetched
     `service-worker.js`'s `VERSION` line starts with `"sh-"`.
   - Confirm the page renders normally (price card, chart, methodology accordion) — i.e.
     the shell fetched under the new build is functionally identical to before.
   - No 404s in DevTools → Network for any of the precached shell files or `data/*.json`.

5. **How to verify no user sees a broken shell during the switch.** GitHub's Actions-based
   Pages deploy is atomic at the CDN level (the artifact is fully uploaded and the edge
   config is swapped only after `actions/deploy-pages` succeeds) — there is no window
   where a client fetches a half-built site. The real risk is a client that had an
   **old service worker already installed** before the switch: the CACHE INVALIDATION
   CONTRACT (see the comment block at the top of `service-worker.js`) already handles this
   for every ordinary deploy — `registration.update()` on load forces a byte-check against
   the newly deployed `service-worker.js`, and because this deploy actually is a byte
   change (any hand-typed `VERSION` → a `"sh-..."` value), every previously-installed
   client will correctly detect the change and re-fetch on its very next visit, the same as
   any other VERSION bump. No special-case handling is needed for the switch itself.

## Rollback

If anything looks wrong in step 4 (broken shell, stale data, missing files):

1. Settings → Pages → Source: change back to "Deploy from a branch" → `master` → `/`. This
   takes effect immediately and reverts to the exact legacy build that was serving the
   site before step 3 — no code revert needed, since this PR changed no shell file.
2. Unset (or leave unset — legacy mode is `check_sw_version_guard.py`'s default) the
   `PAGES_BUILD_MODE` repository variable so `sw-version-guard` returns to requiring
   hand-bumped `VERSION` on PRs.
3. If any PR merged in the meantime relied on the "actions" mode (no manual `VERSION`
   bump), it will need a manual `VERSION` bump added before its shell changes take effect
   under the legacy build again — check open/recently-merged PRs' `service-worker.js`
   diffs for this.
