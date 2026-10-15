"""Warn before a GitHub token expires, and say so at once when one is rejected.

Why (2026-10-14): the data-sync token CI_MERGE_PAT expired, every data sync failed with HTTP 401, and
nothing paged for about a day because no PR was ever created for the stuck-PR alert to find. GitHub
sends a ``github-authentication-token-expiration`` header on API responses for tokens that have an
expiry. This reads ONLY that header (and the status code) from a cheap call (``GET /rate_limit``, which
does not count against the rate limit) made with each token. It never prints a token or a response body.

Paging (private ntfy topic, plain words): once each at 30, 14 and 7 days before expiry, and on a 401
(rejected, revoked or expired) at most once a day per token. A page names the secret, the date and
the FOR_GG file, never a value. "Once each" is kept in a small state file (the workflow stores it in the
Actions cache): a state is keyed by the token's expiry date, so renewing a token starts a fresh set.
A token with no expiry is reported as such and never paged.

Run (the workflow does this; secrets exist only there):
    python scripts/check_token_expiry.py --state state.json CI_MERGE_PAT CI_HEALTH_PAT
Exit 0 when every token was checked (even if a page was sent); 1 when a token's secret is missing or the
probe itself failed (not a 401), so the run is visibly red.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

PROBE_URL = "https://api.github.com/rate_limit"
HEADER = "github-authentication-token-expiration"
THRESHOLDS = (30, 14, 7)  # days before expiry
DOC = "docs/FOR_GG.md"


def parse_expiry(value: str | None) -> datetime | None:
    """``2026-11-14 10:20:00 UTC`` (GitHub's format) or an ISO string -> aware UTC datetime."""
    if not value:
        return None
    v = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S UTC", "%Y-%m-%d %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            d = datetime.strptime(v, fmt)
        except ValueError:
            continue
        return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)
    return None


def probe(token: str) -> tuple[int, str | None]:
    """(HTTP status, expiry header or None). Reads nothing else from the response."""
    req = urllib.request.Request(
        PROBE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "gold-rate-tracker-token-expiry",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.headers.get(HEADER)
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get(HEADER) if e.headers else None


def send_ntfy(topic: str, title: str, message: str) -> bool:
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": "high", "Tags": "warning,key"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def decide(
    name: str, status: int, expiry: datetime | None, now: datetime, state: dict
) -> tuple[list[tuple[str, str]], dict, str]:
    """Pure: (pages as (title, message), new state, one-line report). Never includes a secret."""
    state = json.loads(json.dumps(state))  # a copy
    pages: list[tuple[str, str]] = []
    today = now.date().isoformat()
    if status == 401:
        key = f"{name}|rejected"
        if state.get(key, {}).get("paged_on") != today:
            state[key] = {"paged_on": today}
            pages.append(
                (
                    f"Gold Tracker: {name} was rejected",
                    f"GitHub rejects the {name} token (HTTP 401): it has expired or been revoked. "
                    f"Renew it and update the repository secret of the same name; steps are in {DOC} on master.",
                )
            )
        return pages, state, f"{name}: REJECTED (HTTP 401)"
    if status != 200:
        return pages, state, f"{name}: probe failed (HTTP {status}), not judged"
    if expiry is None:
        return pages, state, f"{name}: OK, no expiry date reported by GitHub (never paged)"
    days = (expiry - now).total_seconds() / 86400.0
    key = f"{name}|{expiry.date().isoformat()}"
    paged = set(state.get(key, {}).get("paged", []))
    crossed = [t for t in THRESHOLDS if days <= t]
    fresh = [t for t in crossed if t not in paged]
    if fresh:
        state[key] = {"paged": sorted(paged | set(crossed))}
        left = max(0, int(days))
        when = "has expired" if days <= 0 else f"expires in {left} day{'s' if left != 1 else ''}"
        pages.append(
            (
                f"Gold Tracker: {name} {when}",
                f"The {name} token {when} (on {expiry.date().isoformat()}). Renew it and update the "
                f"repository secret of the same name before then; steps are in {DOC} on master.",
            )
        )
    return pages, state, f"{name}: OK, expires {expiry.date().isoformat()} ({int(days)} days left)"


def run(
    names: list[str],
    state_path: Path,
    environ: dict[str, str],
    now: datetime,
    probe_fn: Callable[[str], tuple[int, str | None]] = probe,
    send_fn: Callable[[str, str, str], bool] = send_ntfy,
) -> int:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        if not isinstance(state, dict):
            state = {}
    except (OSError, ValueError):
        state = {}
    topic = environ.get("NTFY_TOPIC", "")
    failed = False
    for name in names:
        token = environ.get(name, "")
        if not token:
            print(f"{name}: secret is not available to this run (empty)")
            failed = True
            continue
        try:
            status, header = probe_fn(token)
        except Exception as e:  # network down etc.: say what kind, never the token
            print(f"{name}: probe error {type(e).__name__}, not judged")
            failed = True
            continue
        pages, state, line = decide(name, status, parse_expiry(header), now, state)
        print(line)
        if status not in (200, 401):
            failed = True
        for title, message in pages:
            if not topic:
                print(f"{name}: would page ({title}) but NTFY_TOPIC is not set")
            elif send_fn(topic, title, message):
                print(f"{name}: paged GG ({title})")
            else:
                print(f"{name}: page could not be sent ({title})")
                failed = True
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("names", nargs="+", help="environment variable names holding the tokens")
    ap.add_argument("--state", type=Path, required=True)
    args = ap.parse_args(argv)
    today = date.today()  # only for the log header; decisions use the injected clock below
    print(f"token expiry check, {today.isoformat()}")
    return run(args.names, args.state, dict(os.environ), datetime.now(UTC))


if __name__ == "__main__":
    sys.exit(main())
