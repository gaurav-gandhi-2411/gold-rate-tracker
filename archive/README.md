Deprecated artifacts retained for reference. Not used by any live code path.

- `history_seed_v1_uniform_premium.json`: see docs/adr/010-drop-synthetic-seed.md for the rationale.
- `spec.md`: the Φ25 spec (clean-IP Tanishq fetch through a Cloudflare Worker and
  repository_dispatch). It was retired 2026-07-16, after Tanishq extended its Cloudflare challenge to
  Workers egress, and moved here from the repo root 2026-09-26. See docs/PROGRESS.md (Φ25 entry) and
  docs/RUNBOOK.md. Older mentions of "spec.md" in docs/PROGRESS.md, ADR 015 and
  scripts/run_phi7d.py refer to earlier phase specs that used the same root path, not to this file.
