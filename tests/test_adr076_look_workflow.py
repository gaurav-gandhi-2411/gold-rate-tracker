"""The ADR 076 look workflow must never publish decrypted data (the repo is public) and must run the
script with its pre-registered defaults (no option overrides)."""

from __future__ import annotations

import re
from pathlib import Path

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "adr076-look.yml"


def _text() -> str:
    return WF.read_text(encoding="utf-8")


def test_it_is_manual_read_only_and_uploads_nothing() -> None:
    t = _text()
    assert re.search(r"^on:\s*\n\s+workflow_dispatch:\s*$", t, re.M)
    assert "schedule:" not in t and "push:" not in t and "pull_request" not in t
    assert re.search(r"^permissions:\s*\n\s+contents: read\s*$", t, re.M)
    for forbidden in ("upload-artifact", "git push", "git commit", "gh pr", "contents: write"):
        assert forbidden not in t, forbidden


def test_it_runs_the_look_with_the_registered_defaults_and_prints_aggregates_only() -> None:
    t = _text()
    assert "uses: ./.github/actions/decrypt-data" in t and "secrets.DATA_ENC_KEY" in t
    cmd = re.search(r"run: (python scripts/check_adr066_promotion\.py[^\n]*)", t)
    assert cmd, "the check command is missing"
    flags = re.findall(r"--[a-z-]+", cmd.group(1))
    assert flags == ["--adr", "--out-dir"], flags  # no --min-fixes/--beta/... overrides
    assert "--adr 076" in cmd.group(1)
    assert '--out-dir "${RUNNER_TEMP}/adr076_report"' in t
    assert not re.search(r"\b(cat|head|tail|print)\b[^\n]*data/", t)
    assert "echo" not in t.replace("# ", "")
