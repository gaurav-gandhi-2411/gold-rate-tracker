"""scripts/goatcounter_weekly.py -- one private line of visitor numbers for GG's weekly note.

Reads the last 7 days from GoatCounter's read API (``GET /api/v0/stats/total`` and
``/api/v0/stats/toprefs``) with the repo secret ``GOATCOUNTER_TOKEN`` (a token with the
read-statistics permission only) and writes ONE short sentence to the file given by ``--out``:
the weekly visitor total and the top referrer HOSTS with counts.

Rules this keeps (the repo and its run logs are public):
  * nothing is printed except a status word; the sentence goes to ``--out`` only, and the workflow
    (visitors-weekly.yml) sends it straight to the private ntfy topic;
  * only host names of referrers, never full URLs or paths; a referrer seen fewer than
    ``MIN_COUNT`` times is left out; no per-visit data, paths, places, browsers or devices are read;
  * it FAILS QUIETLY: no token, a refused token or an unreachable API each print one status line and
    exit 0 without writing anything, so the schedule never turns red before GG has added the secret.

    GOATCOUNTER_TOKEN=... python scripts/goatcounter_weekly.py --out "$RUNNER_TEMP/visitors.txt"
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SITE = "gold-rate-tracker"  # <code>.goatcounter.com, the same code analytics.js sends to
DAYS = 7
MIN_COUNT = 3  # a source seen fewer times than this is not named (small counts identify people)
TOP_N = 3
TIMEOUT_S = 20

Fetch = Callable[[str, dict[str, str]], dict[str, Any]]


def host_of(name: str) -> str:
    """The host part of a referrer ('https://www.google.com/search?q=x' -> 'google.com')."""
    raw = name.strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlparse(raw if "//" in raw else "//" + raw)
    host = (parsed.hostname or "").lower()
    return host.removeprefix("www.")


def summarise(total: dict[str, Any], refs: dict[str, Any], days: int = DAYS) -> str:
    """One plain sentence from the two API answers; counts only, hosts only."""
    visitors = int(total.get("total", 0))
    by_host: dict[str, int] = {}
    for row in refs.get("stats", []):
        host = host_of(str(row.get("name", "")))
        if host:
            by_host[host] = by_host.get(host, 0) + int(row.get("count", 0))
    named = sorted(
        ((h, c) for h, c in by_host.items() if c >= MIN_COUNT), key=lambda hc: (-hc[1], hc[0])
    )[:TOP_N]
    text = f"Visitors in the last {days} days: {visitors}."
    if named:
        text += " Top sources: " + ", ".join(f"{h} ({c})" for h, c in named) + "."
    return text


def window(now: datetime, days: int = DAYS) -> tuple[str, str]:
    """(start, end) as the API wants them: rounded down to the hour, ISO 8601 with a Z."""
    end = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (end - timedelta(days=days)).strftime(fmt), end.strftime(fmt)


def http_get(url: str, headers: dict[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        body: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
    return body


def run(
    token: str | None,
    out: Path,
    now: datetime | None = None,
    fetch: Fetch = http_get,
    site: str = SITE,
) -> str:
    """Write the sentence to ``out`` and return a status word; never raises on API trouble."""
    if not token:
        return "skipped: GOATCOUNTER_TOKEN is not set"
    start, end = window(now or datetime.now(UTC))
    base = f"https://{site}.goatcounter.com/api/v0/stats"
    query = urllib.parse.urlencode({"start": start, "end": end})
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        total = fetch(f"{base}/total?{query}", headers)
        refs = fetch(f"{base}/toprefs?{query}&limit=20", headers)
    except urllib.error.HTTPError as err:
        return f"skipped: GoatCounter answered HTTP {err.code}"
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as err:
        return f"skipped: GoatCounter not reachable ({type(err).__name__})"
    out.write_text(summarise(total, refs) + "\n", encoding="utf-8", newline="\n")
    return "written"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    ap.add_argument("--out", type=Path, required=True, help="file for the one-sentence summary")
    args = ap.parse_args(argv)
    print(run(os.environ.get("GOATCOUNTER_TOKEN"), args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
