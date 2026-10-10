"""scripts/goatcounter_weekly.py: counts and hosts only, quiet without a token, nothing published."""

from __future__ import annotations

import sys
import urllib.error
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import goatcounter_weekly as gw

NOW = datetime(2026, 10, 12, 7, 41, 12, tzinfo=UTC)


def _fetch(total: dict[str, Any], refs: dict[str, Any], seen: list[str] | None = None):
    def fetch(url: str, headers: dict[str, str]) -> dict[str, Any]:
        if seen is not None:
            seen.append(url)
        return refs if "/toprefs" in url else total

    return fetch


def test_window_is_seven_whole_hours_back_in_utc() -> None:
    assert gw.window(NOW) == ("2026-10-05T07:00:00Z", "2026-10-12T07:00:00Z")


def test_hosts_only_small_counts_dropped_and_ranked() -> None:
    refs = {
        "stats": [
            {"name": "https://www.google.com/search?q=gold+rate+today+private", "count": 9},
            {"name": "google.com", "count": 2},  # same host, adds up to 11
            {"name": "https://t.co/abc123", "count": 4},
            {"name": "https://one-off.example/page", "count": 1},  # below MIN_COUNT: not named
            {"name": "", "count": 50},  # direct visits have no host
        ]
    }
    text = gw.summarise({"total": 123}, refs)
    assert text == "Visitors in the last 7 days: 123. Top sources: google.com (11), t.co (4)."
    for leaked in ("search", "q=", "abc123", "one-off", "/page"):
        assert leaked not in text


def test_no_sources_still_gives_the_total() -> None:
    assert gw.summarise({"total": 0}, {"stats": []}) == "Visitors in the last 7 days: 0."


def test_without_a_token_it_is_quiet_and_writes_nothing(tmp_path: Path) -> None:
    out = tmp_path / "v.txt"
    status = gw.run(None, out, NOW, fetch=lambda *_: pytest.fail("must not call the API"))
    assert status.startswith("skipped") and not out.exists()
    assert gw.run("", out, NOW).startswith("skipped")


def test_a_refused_or_unreachable_api_is_quiet_and_never_echoes_the_token(tmp_path: Path) -> None:
    out = tmp_path / "v.txt"

    def refuse(url: str, headers: dict[str, str]) -> dict[str, Any]:
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)  # type: ignore[arg-type]

    def down(url: str, headers: dict[str, str]) -> dict[str, Any]:
        raise urllib.error.URLError("no route")

    refused = gw.run("SECRET-TOKEN", out, NOW, fetch=refuse)
    unreachable = gw.run("SECRET-TOKEN", out, NOW, fetch=down)
    assert refused == "skipped: GoatCounter answered HTTP 403"
    assert unreachable.startswith("skipped: GoatCounter not reachable")
    assert "SECRET-TOKEN" not in refused + unreachable and not out.exists()


def test_with_a_token_it_asks_only_for_total_and_toprefs_and_writes_the_sentence(
    tmp_path: Path,
) -> None:
    out, seen = tmp_path / "v.txt", []
    refs = {"stats": [{"name": "https://news.example/story", "count": 5}]}
    status = gw.run("tok", out, NOW, fetch=_fetch({"total": 7}, refs, seen))
    assert status == "written"
    assert out.read_text(encoding="utf-8") == (
        "Visitors in the last 7 days: 7. Top sources: news.example (5).\n"
    )
    assert [u.split("/stats/")[1].split("?")[0] for u in seen] == ["total", "toprefs"]
    assert all(
        u.startswith("https://gold-rate-tracker.goatcounter.com/api/v0/stats/") for u in seen
    )


def test_main_prints_only_a_status_word(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("GOATCOUNTER_TOKEN", raising=False)
    assert gw.main(["--out", str(tmp_path / "v.txt")]) == 0
    assert capsys.readouterr().out.strip() == "skipped: GOATCOUNTER_TOKEN is not set"


def test_workflow_never_prints_uploads_or_commits_the_numbers() -> None:
    wf = (ROOT / ".github" / "workflows" / "visitors-weekly.yml").read_text(encoding="utf-8")
    assert "upload-artifact" not in wf and "git push" not in wf and "git commit" not in wf
    assert (
        "cat " not in wf and 'echo "$(' not in wf
    )  # the sentence is only handed to curl as a file
    assert "contents: read" in wf and "GOATCOUNTER_TOKEN" in wf and "NTFY_TOPIC" in wf
    assert "--data-binary" in wf


def test_basic_auth_is_tried_when_bearer_is_refused(tmp_path: Path) -> None:
    seen: list[str] = []

    def fetch(url: str, headers: dict[str, str]) -> dict[str, Any]:
        seen.append(headers["Authorization"].split()[0])
        if headers["Authorization"].startswith("Bearer"):
            raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)  # type: ignore[arg-type]
        return {"stats": []} if "/toprefs" in url else {"total": 4}

    out = tmp_path / "v.txt"
    assert gw.run("tok", out, NOW, fetch=fetch) == "written"
    assert seen == ["Bearer", "Basic", "Basic"]
    assert out.read_text(encoding="utf-8") == "Visitors in the last 7 days: 4.
"
