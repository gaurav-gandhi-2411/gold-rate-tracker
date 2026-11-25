"""ci-health: CI_HEALTH_PAT was rejected (HTTP 401). Say so ONCE, then let the other checks carry on.

CI_HEALTH_PAT is used by exactly one ci-health step: reading master's branch protection (needs
repository-administration read). When it expires that read fails with HTTP 401. Before 2026-11-25 the
step then failed closed on EVERY ci-health run (about 8 a day) and wrote an ops-log line each time, a
stream of red runs and entries for one cause that only GG can fix. The other ci-health checks
(check-price still running, the live site fresh, the share card) use github.token and are unaffected.

This script is what the step runs on a 401: one `::warning`, and ONE ops-log entry per RENEW_AFTER_DAYS
(7) with the exact renewal pointer, never a value. token-expiry.yml separately pages GG's phone about the
rejected token (at most once a day); this is only the quiet side. Never raises, always exits 0.

    python scripts/ci_health_token_rejected.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta

import ops_log

SOURCE = "ci-health"
TITLE = "CI_HEALTH_PAT was rejected (expired or revoked)"
RENEW_AFTER = timedelta(days=7)
BODY = (
    "GitHub rejected CI_HEALTH_PAT (HTTP 401): it has expired or been revoked. Only ci-health's read of "
    "master's required-check list is skipped; its checks that check-price is running, that the live "
    "site is fresh and that the share card is right still run. GG renews it (own account only): "
    "docs/FOR_GG.md section 0, then `gh workflow run token-expiry.yml --ref master` shows OK."
)


def recently_logged(text: str, now: datetime) -> bool:
    """True when the ops log already holds this entry within RENEW_AFTER."""
    for line in text.splitlines():
        try:
            e = json.loads(line)
            ts = datetime.strptime(str(e["ts"]), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        except (ValueError, KeyError, TypeError):
            continue
        if e.get("source") == SOURCE and e.get("title") == TITLE and now - ts < RENEW_AFTER:
            return True
    return False


def run(
    now: datetime, environ: dict[str, str] | None = None, gh: ops_log.GhFn = ops_log.run_gh
) -> int:
    env = dict(os.environ) if environ is None else environ
    print(f"::warning title=ci-health-token::{BODY}")
    repo = env.get("GITHUB_REPOSITORY", "")
    seen = False
    if repo:
        try:
            got = ops_log.read_remote(repo, gh)
            seen = bool(got) and recently_logged(got[1], now)
        except ops_log.OpsLogWriteError:
            seen = False  # unreadable: a duplicate entry is better than none
    if seen:
        print("ci-health-token: already in the ops log within 7 days; not writing it again")
    else:
        ops_log.record(SOURCE, TITLE, BODY, environ=env, gh=gh)
    return 0


if __name__ == "__main__":
    sys.exit(run(datetime.now(UTC)))
