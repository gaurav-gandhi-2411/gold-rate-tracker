"""scripts/ci_health_judge.py: judge only the required checks that master runs produce; fail closed."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import ci_health_judge as cj

GREEN = [
    {"name": "lint", "conclusion": "success"},
    {"name": "pwa-js", "conclusion": "success"},
    {"name": "pwa-headless", "conclusion": "success"},
    {"name": "boundary-leak-check", "conclusion": "skipped"},
]
HISTORY = [[j["name"] for j in GREEN]] * 5
REQUIRED = ["lint", "pwa-js", "scraper-dependency-guard"]


def test_the_2026_10_10_case_scraper_guard_is_pr_only_and_nothing_pages() -> None:
    code, line, pr_only = cj.judge(REQUIRED, GREEN, HISTORY)
    assert (code, pr_only) == (0, ["scraper-dependency-guard"])
    assert line == "OK: lint, pwa-js"


def test_a_hypothetical_new_pr_only_required_check_does_not_page() -> None:
    required = [*REQUIRED, "brand-new-pr-only-check", "another-one"]
    code, line, pr_only = cj.judge(required, GREEN, HISTORY)
    assert code == 0 and line == "OK: lint, pwa-js"
    assert pr_only == ["scraper-dependency-guard", "brand-new-pr-only-check", "another-one"]


def test_a_real_failure_of_a_master_check_still_pages() -> None:
    red = [{"name": "lint", "conclusion": "failure"}, *GREEN[1:]]
    code, line, _ = cj.judge(REQUIRED, red, HISTORY)
    assert code == 1 and line == "FAILING: lint (failure)"


def test_a_master_check_missing_from_the_latest_run_is_a_failure_not_pr_only() -> None:
    # pwa-js appeared in earlier master runs, so its absence from the latest run is a real problem.
    latest = [j for j in GREEN if j["name"] != "pwa-js"]
    code, line, pr_only = cj.judge(REQUIRED, latest, HISTORY)
    assert code == 1 and line == "FAILING: pwa-js (missing)"
    assert pr_only == ["scraper-dependency-guard"]


def test_an_unfinished_job_is_not_success() -> None:
    running = [{"name": "lint", "conclusion": None}, *GREEN[1:]]
    assert cj.judge(REQUIRED, running, HISTORY)[1] == "FAILING: lint (missing)"


@pytest.mark.parametrize(
    ("required", "latest", "history"),
    [
        ([], GREEN, HISTORY),  # nothing required
        (REQUIRED, [], HISTORY),  # empty latest run
        (REQUIRED, GREEN, []),  # no history at all
        (["scraper-dependency-guard", "other-pr-only"], GREEN, HISTORY),  # nothing left to judge
    ],
)
def test_fails_closed_when_it_cannot_judge(required, latest, history) -> None:
    code, line, _ = cj.judge(required, latest, history)
    assert code == 2 and line.startswith("CANNOT JUDGE")


def test_a_renamed_master_job_cannot_silence_the_monitor() -> None:
    # If the lint job were renamed, the old required names would be "PR-only" and nothing would be
    # judged: that must be a loud "cannot judge", not a green run.
    renamed = [
        {"name": "lint-v2", "conclusion": "success"},
        {"name": "pwa-js-v2", "conclusion": "success"},
    ]
    code, line, _ = cj.judge(["lint", "pwa-js"], renamed, [["lint-v2", "pwa-js-v2"]])
    assert code == 2 and line.startswith("CANNOT JUDGE")


def test_main_reads_files_and_fails_closed_on_garbage(tmp_path: Path, capsys) -> None:
    req = tmp_path / "r.txt"
    req.write_text("\n".join(REQUIRED) + "\n", encoding="utf-8")
    latest, hist = tmp_path / "l.json", tmp_path / "h.json"
    latest.write_text(json.dumps(GREEN), encoding="utf-8")
    hist.write_text(json.dumps(HISTORY), encoding="utf-8")
    args = ["--required", str(req), "--latest", str(latest), "--history", str(hist)]
    assert cj.main(args) == 0
    out = capsys.readouterr().out
    assert "PR-ONLY" in out and "OK: lint, pwa-js" in out
    hist.write_text("not json", encoding="utf-8")
    assert cj.main(args) == 2
    assert "CANNOT JUDGE" in capsys.readouterr().out
    assert (
        cj.main(
            ["--required", str(tmp_path / "nope"), "--latest", str(latest), "--history", str(hist)]
        )
        == 2
    )
