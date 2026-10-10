"""The ADR 066 check workflow must never publish decrypted data (the repo is public)."""

from __future__ import annotations

import re
from pathlib import Path

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "adr066-check.yml"


def _text() -> str:
    return WF.read_text(encoding="utf-8")


def test_it_is_manual_read_only_and_uploads_nothing() -> None:
    t = _text()
    assert re.search(r"^on:\s*\n\s+workflow_dispatch:\s*$", t, re.M)
    assert "schedule:" not in t and "push:" not in t and "pull_request" not in t
    assert re.search(r"^permissions:\s*\n\s+contents: read\s*$", t, re.M)
    for forbidden in ("upload-artifact", "git push", "git commit", "gh pr", "contents: write"):
        assert forbidden not in t, forbidden


def test_it_decrypts_with_the_repo_action_and_runs_the_unmodified_check() -> None:
    t = _text()
    assert "uses: ./.github/actions/decrypt-data" in t and "secrets.DATA_ENC_KEY" in t
    assert "python scripts/check_adr066_promotion.py" in t
    # the report file goes to the runner's temp dir, not the workspace (nothing there is uploaded)
    assert '--out-dir "${RUNNER_TEMP}/adr066_report"' in t
    # it never echoes data files or the key
    assert not re.search(r"\b(cat|head|tail|print)\b[^\n]*data/", t)
    assert "echo" not in t.replace("# ", "")
